"""Durable run registry with fail-closed identity pinning (Structure S3).

Pins the S1/S2 identity scheme into durable, replayable run records per
to-do thread 904c2694 and architect ruling 143b0e04:

- Deterministic run records: content-addressed by ``run_id`` (already a
  digest of read-set fingerprint + parser identity + revisions) and
  ``snapshot_hash`` (canonical snapshot digest). Persisting the same run
  twice is an idempotent no-op; a differing record under the same identity
  fails closed with an identity collision.
- Fact identity ≠ referenced domain identity: the registry stores
  ``source_fact_id``/``observation_id`` digests and *never* resolves a
  table/name to a governed UUID. Mapping is Resolution/Aspects work at the
  S4 boundary; a run record that arrives with pre-populated governed
  identities on its observations is rejected (identity_escalation), and a
  ``domain_ref`` slot may only carry the explicit unmapped/registry-mapped
  shape from the S1 contract — never a silently inferred UUID.
- Stale source revisions fail closed: registering a run whose read set
  carries content hashes that disagree with the pinned revision of the
  same source in an existing verified run raises ``StaleSourceRevision``.
- Replay provenance: every registration can carry a replay attestation —
  the record of re-running ``replay()`` against the persisted snapshot at a
  named verifier revision. Attestations are append-only.
- Storage behind a port: ``RunStore`` is the minimal interface
  (``read_index``, ``read_record``, ``write_record``, ``append_event``).
  Backends pluggable; the in-repo default is an in-memory store plus a
  JSONL-file store. Structure performs no database writes — persistence to
  the canonical DB is downstream tooling, not this layer.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

try:
    from . import contract as sc  # package context
    from . import sql_parser as sp  # package context
except ImportError:  # pragma: no cover - direct-run / test harness context
    import contract as sc  # type: ignore[no-redef]
    import sql_parser as sp  # type: ignore[no-redef]


class RegistryError(Exception):
    """Base class for registry failures."""


class IdentityCollision(RegistryError):
    """A record with the same identity but different content exists."""

    def __init__(self, run_id: str, kind: str = "run") -> None:
        self.run_id = run_id
        self.kind = kind
        super().__init__(
            f"identity collision: {kind} {run_id} already exists with "
            "different content (fail closed)"
        )


class IdentityEscalation(RegistryError):
    """A run record smuggles governed/domain identities into observations."""

    def __init__(self, run_id: str, detail: str) -> None:
        self.run_id = run_id
        self.detail = detail
        super().__init__(
            f"identity escalation in run {run_id}: {detail} — Structure "
            "never maps names to governed UUIDs (S4 boundary)"
        )


class StaleSourceRevision(RegistryError):
    """A read-set source's content hash disagrees with its pinned revision."""

    def __init__(self, source_uri: str, revision: str, expected_hash: str,
                 actual_hash: str) -> None:
        self.source_uri = source_uri
        self.revision = revision
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"stale source revision: {source_uri} @ {revision} has content "
            f"hash {actual_hash[:12]}… but pinned revision carries "
            f"{expected_hash[:12]}… (fail closed)"
        )


class ReplayFailure(RegistryError):
    """A persisted record failed its byte-stable replay on load."""


# ---------------------------------------------------------------------------
# Domain-identity guard (fact identity ≠ referenced domain identity)


def check_domain_identity(run: dict[str, Any]) -> None:
    """Reject governed/domain identity smuggling in a run record.

    Observations may carry the S1 ``relation_mapping`` slot only in its
    contract shape: ``{"status": "unmapped"}`` or ``{"status": "mapped",
    "governed_relation_id": ..., "evidence_refs": [...]}`` where the mapped
    form was produced by the S4 boundary owner (Resolution/Aspects) — never
    by Structure itself. The registry enforces the structural part: a
    mapping whose evidence refs are empty, or whose governed id appears
    without evidence, is treated as a silent mapping attempt and rejected.
    Observations must never carry any other governed-identity field.
    """
    forbidden_fields = (
        "governed_tag_id", "governed_id", "resolution_uuid", "asset_id",
        "concept_id", "proposition_id",
    )
    for obs in run.get("observations", []):
        for field_name in forbidden_fields:
            if field_name in obs or field_name in (obs.get("payload") or {}):
                raise IdentityEscalation(
                    run.get("run_id", "?"),
                    f"observation carries governed identity field {field_name!r}",
                )
        mapping = obs.get("relation_mapping")
        if mapping is not None:
            status = mapping.get("status")
            if status == "mapped":
                if not mapping.get("evidence_refs"):
                    raise IdentityEscalation(
                        run.get("run_id", "?"),
                        "'mapped' relation_mapping without evidence_refs is a "
                        "silent name→UUID mapping",
                    )


# ---------------------------------------------------------------------------
# Registry


@dataclass
class RegistryEntry:
    run_id: str
    snapshot_hash: str
    contract_fingerprint: str
    parser_identity: str
    parser_revision: str
    grammar_revision: str
    read_set_fingerprint: str
    source_uris: list[str] = field(default_factory=list)
    observation_count: int = 0
    finding_count: int = 0
    registered_at_seq: int = 0  # monotonic sequence, not a clock


def _entry_from_run(snapshot: dict[str, Any]) -> RegistryEntry:
    run = snapshot["run"]
    return RegistryEntry(
        run_id=run["run_id"],
        snapshot_hash=snapshot["snapshot_hash"],
        contract_fingerprint=snapshot.get(
            "contract_fingerprint", sc.contract_fingerprint()
        ),
        parser_identity=run["parser"]["parser_identity"],
        parser_revision=run["parser"]["parser_revision"],
        grammar_revision=run["parser"]["grammar_revision"],
        read_set_fingerprint=run["read_set_fingerprint"],
        source_uris=[e["source_uri"] for e in run["read_set"]],
        observation_count=len(run.get("observations", [])),
        finding_count=len(run.get("findings", [])),
    )


class RunRegistry:
    """Append-only registry of durable run records (fail-closed)."""

    def __init__(self, store: "RunStore | None" = None, *,
                 known_source_hashes: dict[str, str] | None = None) -> None:
        self._store = store if store is not None else InMemoryRunStore()
        self._lock = threading.RLock()
        self._known_source_hashes: dict[str, str] = dict(known_source_hashes or {})

    # -- pins ---------------------------------------------------------------

    def pin_source_revision(self, source_uri: str, content_hash: str) -> None:
        """Declare the verified content hash of a pinned source revision."""
        with self._lock:
            self._known_source_hashes[source_uri] = content_hash

    def _check_stale_sources(self, run: dict[str, Any]) -> None:
        for entry in run.get("read_set", []):
            uri = entry.get("source_uri", "")
            pinned = self._known_source_hashes.get(uri)
            if pinned and pinned != entry.get("content_hash"):
                raise StaleSourceRevision(
                    uri, entry.get("revision", "?"), pinned, entry.get("content_hash", "")
                )

    # -- registration ---------------------------------------------------------

    def register_run(
        self,
        snapshot: dict[str, Any],
        *,
        replay_report: dict[str, Any] | None = None,
        verify_replay: bool = True,
    ) -> RegistryEntry:
        """Register a run snapshot durably (idempotent, fail-closed).

        - Re-registers of the identical snapshot are no-ops.
        - Same run_id with different content raises ``IdentityCollision``.
        - Governed-identity smuggling raises ``IdentityEscalation``.
        - Stale source revisions (against pinned hashes) raise
          ``StaleSourceRevision``.
        - ``verify_replay`` re-runs the byte-stable replay against the
          snapshot's own sources and refuses registration on failure; the
          resulting replay attestation is appended as provenance.
        """
        run = snapshot.get("run") or {}
        run_id = run.get("run_id", "")
        if not run_id:
            raise RegistryError("snapshot has no run_id")
        with self._lock:
            check_domain_identity(run)
            self._check_stale_sources(run)
            existing = self._store.read_record(run_id)
            replay_report = dict(replay_report) if replay_report else None
            if verify_replay:
                # Two independent integrity proofs:
                #  1. canonical content hash of the snapshot itself — catches
                #     ANY tampering (observations included) of a snapshot
                #     claiming a known identity;
                #  2. rebuild-from-sources — proves the run is byte-stably
                #     reproducible from its source texts alone.
                declared = snapshot.get("snapshot_hash", "")
                computed = _content_hash(snapshot)
                if computed != declared:
                    if existing is not None:
                        raise IdentityCollision(run_id)
                    raise ReplayFailure(
                        f"declared snapshot_hash does not match snapshot "
                        f"content ({declared[:12]}… != {computed[:12]}…)"
                    )
                sources = _sources_from_snapshot(snapshot)
                rebuilt = sp.snapshot_run(sp.build_run(sources), sources)
                rebuilt_hash = rebuilt["snapshot_hash"]
                if rebuilt_hash != declared:
                    raise ReplayFailure(
                        f"run {run_id} is not reproducible from its sources "
                        f"({rebuilt_hash[:12]}… != {declared[:12]}…)"
                    )
                replay_report = {
                    "byte_stable": True,
                    "snapshot_hash_matches": True,
                    "observation_set_matches": True,
                    "grammar_revision": run["parser"]["grammar_revision"],
                    "parser_revision": run["parser"]["parser_revision"],
                    "original_snapshot_hash": declared,
                    "rebuilt_snapshot_hash": rebuilt_hash,
                }
            if existing is not None:
                if existing.get("snapshot_hash") != snapshot.get("snapshot_hash"):
                    raise IdentityCollision(run_id)
                if verify_replay:
                    # Stored-record tamper check: the persisted snapshot must
                    # match its recorded hash AND rebuild from its sources.
                    stored_declared = existing.get("snapshot_hash", "")
                    if _content_hash(existing["snapshot"]) != stored_declared:
                        raise ReplayFailure(
                            f"stored record for {run_id} was tampered with "
                            "(content hash mismatch)"
                        )
                    stored_sources = _sources_from_snapshot(existing["snapshot"])
                    stored_rebuilt = sp.snapshot_run(
                        sp.build_run(stored_sources), stored_sources
                    )
                    if stored_rebuilt["snapshot_hash"] != stored_declared:
                        raise ReplayFailure(
                            f"stored record for {run_id} is internally "
                            "inconsistent (content does not rebuild to its "
                            "recorded hash)"
                        )
                entry = _entry_from_run(existing["snapshot"])
            else:
                record = {
                    "record_version": 1,
                    "run_id": run_id,
                    "snapshot_hash": snapshot.get("snapshot_hash", ""),
                    "snapshot": snapshot,
                    "replay_attestations": [],
                }
                self._store.write_record(record)
                entry = _entry_from_run(snapshot)
            if replay_report:
                self.append_replay_attestation(run_id, replay_report)
            return entry

    def append_replay_attestation(
        self, run_id: str, report: dict[str, Any], *,
        verifier: str = "structure-registry",
        verifier_revision: str = sc.STRUCTURE_CONTRACT_REVISION,
    ) -> dict[str, Any]:
        """Append a replay-provenance event to a registered run (append-only)."""
        with self._lock:
            record = self._store.read_record(run_id)
            if record is None:
                raise RegistryError(f"unknown run {run_id}")
            attestation = {
                "attestation_version": 1,
                "verifier": verifier,
                "verifier_revision": verifier_revision,
                "byte_stable": bool(report.get("byte_stable")),
                "snapshot_hash": record.get("snapshot_hash"),
                "observation_set_matches": bool(report.get("observation_set_matches")),
            }
            self._store.append_event(run_id, "replay_attestation", attestation)
            return attestation

    # -- reads ----------------------------------------------------------------

    def load_run(self, run_id: str, *, verify: bool = True) -> dict[str, Any]:
        """Load a persisted run, re-verifying integrity and replay fail-closed."""
        with self._lock:
            record = self._store.read_record(run_id)
            if record is None:
                raise RegistryError(f"unknown run {run_id}")
            snapshot = record["snapshot"]
            if verify:
                stored_declared = record.get("snapshot_hash", "")
                if _content_hash(snapshot) != stored_declared:
                    raise ReplayFailure(
                        f"run {run_id} was tampered with on disk "
                        "(content hash mismatch)"
                    )
                sources = _sources_from_snapshot(snapshot)
                rebuilt = sp.snapshot_run(sp.build_run(sources), sources)
                if rebuilt.get("snapshot_hash") != stored_declared:
                    raise ReplayFailure(
                        f"run {run_id} content hash mismatch on load "
                        f"({rebuilt.get('snapshot_hash')[:12]}… != "
                        f"{stored_declared[:12]}…)"
                    )
            check_domain_identity(snapshot["run"])
            return snapshot

    def lookup_by_snapshot_hash(self, snapshot_hash: str) -> str | None:
        for entry in self.index():
            if entry.snapshot_hash == snapshot_hash:
                return entry.run_id
        return None

    def index(self) -> list[RegistryEntry]:
        entries = []
        for run_id in self._store.read_index():
            record = self._store.read_record(run_id)
            if record is not None:
                entries.append(_entry_from_run(record["snapshot"]))
        return entries

    def attestations(self, run_id: str) -> list[dict[str, Any]]:
        return self._store.read_events(run_id, "replay_attestation")


# ---------------------------------------------------------------------------
# Storage ports


class RunStore:
    """Minimal persistence port for run records and events."""

    def read_index(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def read_record(self, run_id: str) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def write_record(self, record: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    def append_event(self, run_id: str, kind: str, event: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    def read_events(self, run_id: str, kind: str) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


class InMemoryRunStore(RunStore):
    """Thread-safe in-memory backend (default; tests, tools, one-shot runs)."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def read_index(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def read_record(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(run_id)
            return json.loads(json.dumps(rec)) if rec else None

    def write_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            run_id = record["run_id"]
            if run_id in self._records:
                raise IdentityCollision(run_id, "store-level")
            self._records[run_id] = json.loads(json.dumps(record))
            self._order.append(run_id)

    def append_event(self, run_id: str, kind: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.setdefault(run_id, []).append((kind, json.loads(json.dumps(event))))

    def read_events(self, run_id: str, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e for k, e in self._events.get(run_id, []) if k == kind]


class JsonlRunStore(RunStore):
    """File-backed backend: one canonical JSON record per line (append-only).

    The file is the durable projection; records are keyed by run_id and a
    rewrite of the same identity with different content fails closed on
    compaction/load. Events live in ``<path>.events.jsonl``.
    """

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.events_path = pathlib.Path(str(self.path) + ".events.jsonl")
        self._lock = threading.RLock()

    def read_index(self) -> list[str]:
        if not self.path.exists():
            return []
        seen: list[str] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            run_id = json.loads(line)["run_id"]
            if run_id not in seen:
                seen.append(run_id)
        return seen

    def read_record(self, run_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        found: dict[str, Any] | None = None
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["run_id"] == run_id:
                if found is not None and found.get("snapshot_hash") != rec.get("snapshot_hash"):
                    raise IdentityCollision(run_id, "store-level")
                found = rec
        return found

    def write_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            if self.read_record(record["run_id"]) is not None:
                raise IdentityCollision(record["run_id"], "store-level")
            with self.path.open("a") as fh:
                fh.write(sp.json_canonical(record) + "\n")

    def append_event(self, run_id: str, kind: str, event: dict[str, Any]) -> None:
        with self._lock:
            with self.events_path.open("a") as fh:
                fh.write(sp.json_canonical(
                    {"run_id": run_id, "kind": kind, "event": event}
                ) + "\n")

    def read_events(self, run_id: str, kind: str) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        out = []
        for line in self.events_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("run_id") == run_id and rec.get("kind") == kind:
                out.append(rec["event"])
        return out


def _content_hash(snapshot: dict[str, Any]) -> str:
    """Integrity hash over the snapshot's own canonical content.

    Independent of the rebuild-from-sources proof: catches tampering of
    persisted observations (which rebuild-from-sources cannot see, since it
    regenerates from the untouched source texts).
    """
    body = {k: v for k, v in snapshot.items() if k != "snapshot_hash"}
    return hashlib.sha256(sp.json_canonical(body).encode("utf-8")).hexdigest()


def _sources_from_snapshot(snapshot: dict[str, Any]) -> list[sp.SqlSource]:
    return [
        sp.SqlSource(
            source_uri=s["source_uri"], revision=s["revision"], text=s["text"],
            role=s.get("role", "migration"), language=s.get("language", "sql"),
            source_kind=s.get("source_kind", "sql_migration"),
        )
        for s in snapshot.get("sources", [])
    ]
