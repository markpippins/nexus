"""W-A — duplicate-history inventory: pure detectors over plain data.

Plan 8261653. Docket thread c7c26160-5b22-463f-9ee0-6e4784bae0e8.

A *duplicate-history set* is a group of historical records that share an
identity and therefore cannot both stand as the canonical record, plus the
two structurally identical families that need an explicit disposition for
the same reason:

  duplicate    — two or more records share an identity (the classic case).
  uncovered    — legacy records exist that carry NO disposition at all
                 (the C4/C6 wrap-not-drop lanes have a gap).
  unidentified — records exist whose identity column is unusable, so the
                 set cannot even be grouped (C2's dead ``canonical_key``).
  unverified   — records carry an identity that is not verifiable against
                 the canonical function (C1's missing DB-layer check).

Every detector is a pure function over plain Python structures. The DB and
filesystem collectors live in ``collect``; nothing here imports psycopg2.

Determinism is a hard requirement: the same inputs must always yield the
same ``set_id``, the same member order, and the same fingerprints. Every
ordering below is total (ties broken by a stable key), never by dict
insertion order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# ── Envelope freeze conditions (DBA record e8faa405) ──────────────────────
# C1 payload_fingerprint NOT NULL + deterministic DB-layer verification
# C2 canonical_asset.canonical_key backfill at envelope-apply time
# C3 execution_claim.subject_ref promoted to a normalized link
# C4 legacy flat evidence normalizes wrap-not-drop with explicit disposition
C1 = "C1"
C2 = "C2"
C3 = "C3"
C4 = "C4"
PC7 = "PC7"
ST02 = "ST.02"

# ── The three explicit dispositions W-A may record ────────────────────────
# merge   — the duplicate is folded into the primary (content-identical).
# preserve — the duplicate keeps its own identity (diverged or live state).
# archive  — the duplicate is retired from active views, retained as history.
MERGE = "merge"
PRESERVE = "preserve"
ARCHIVE = "archive"
DISPOSITIONS = (MERGE, PRESERVE, ARCHIVE)

# ── Set kinds ─────────────────────────────────────────────────────────────
KIND_DUPLICATE = "duplicate"
KIND_UNCOVERED = "uncovered"
KIND_UNIDENTIFIED = "unidentified"
KIND_UNVERIFIED = "unverified"
SET_KINDS = (KIND_DUPLICATE, KIND_UNCOVERED, KIND_UNIDENTIFIED, KIND_UNVERIFIED)


def canonical_json(obj: Any) -> str:
    """Canonical JSON text: sorted keys, no insignificant whitespace.

    Mirrors the house fingerprint discipline used by
    ``python/conduit/lilac.py::payload_fingerprint`` — one key discipline
    across the repository.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(obj: Any) -> str:
    """sha256 over the canonical JSON text of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def candidate_set_identity(candidates: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic candidate-set identity — sha256 over the sorted ids.

    Set-order independent and duplicate collapsing, so the same card set
    hashes identically regardless of emission order or the batch metadata
    that distinguishes re-emissions. This is the same identity discipline
    as ``resolution.candidate_set_key`` (V198, ruling R1.3) and as
    ``python/promotion-flow/promotion_common.py::candidate_set_identity``;
    ``tests/test_duplicate_history.py`` asserts the two agree.
    """
    ids = sorted({str(c["id"]) for c in candidates if c.get("id")})
    return hashlib.sha256(",".join(ids).encode("utf-8")).hexdigest()


def _member_fingerprint(candidates: Sequence[Mapping[str, Any]]) -> str:
    """Content fingerprint of a candidate set — proves byte-identity.

    Only the identity-bearing fields participate (id, title, readiness), so
    two emissions of the same set fingerprint equal even when their
    lifecycle metadata (timestamps, verdicts, thread ids) differs.
    """
    rows = sorted(
        [c.get("id"), c.get("title"), c.get("readiness")]
        for c in candidates
        if c.get("id")
    )
    return fingerprint(rows)


@dataclass(frozen=True)
class Member:
    """One historical record inside a duplicate-history set."""

    ref: str
    fingerprint: str
    created_at: str | None = None
    disposition: str | None = None
    proposed_disposition: str = PRESERVE
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "disposition": self.disposition,
            "proposed_disposition": self.proposed_disposition,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DuplicateSet:
    """A group of historical records that requires one reconciliation package."""

    surface: str
    kind: str
    condition: str
    identity_kind: str
    identity_value: str
    members: tuple[Member, ...]
    detail: str = ""
    set_id: str = field(default="", compare=False)

    @property
    def id(self) -> str:
        """Deterministic set id — stable across runs and machines."""
        if self.set_id:
            return self.set_id
        return fingerprint(
            [self.surface, self.kind, self.condition, self.identity_kind,
             self.identity_value]
        )

    @property
    def dispositioned(self) -> bool:
        """True when *every* member (the primary included) carries a disposition.

        This mirrors the DB-side status rule in V205
        (``record_reconciliation_disposition`` counts distinct
        ``member_ref`` against ``member_count``). A set whose only member is
        an unverified/uncovered record is therefore still OPEN — the
        surviving member must itself be preserved explicitly, never assumed.
        """
        if not self.members:
            return False
        return all(m.disposition for m in self.members)

    @property
    def package_id(self) -> str:
        """The M-sized package that owns this set."""
        return f"W-A/{self.id[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.id,
            "package_id": self.package_id,
            "surface": self.surface,
            "kind": self.kind,
            "condition": self.condition,
            "identity_kind": self.identity_kind,
            "identity_value": self.identity_value,
            "member_count": len(self.members),
            "dispositioned": self.dispositioned,
            "detail": self.detail,
            "members": [m.to_dict() for m in self.members],
        }


def _sorted_members(members: Iterable[Member]) -> tuple[Member, ...]:
    """Total order: created_at, then ref. Never dict insertion order."""
    return tuple(sorted(members, key=lambda m: (m.created_at or "", m.ref)))


def _mk_set(
    surface: str,
    kind: str,
    condition: str,
    identity_kind: str,
    identity_value: str,
    members: Iterable[Member],
    detail: str = "",
) -> DuplicateSet:
    return DuplicateSet(
        surface=surface,
        kind=kind,
        condition=condition,
        identity_kind=identity_kind,
        identity_value=identity_value,
        members=_sorted_members(members),
        detail=detail,
    )


# ════════════════════════════════════════════════════════════════════════════
# Detector 1 — promotion manifests (the random-batch-id era sediment)
# ════════════════════════════════════════════════════════════════════════════
# Surface: state/promotion-flow/batch-*.json
# Condition: ST.02 (the precedent this wave generalises)
#
# The random-batch-id era re-emitted the same candidate set under a fresh
# batch id roughly hourly. ST.02 reconciled the file corpus by appending a
# ``superseded_by`` marker to every duplicate; the historical candidate rows
# were never rewritten. This detector re-derives that grouping from first
# principles so the W-A inventory can cite it as evidence rather than trust
# the marker.


def manifest_sets(
    manifests: Sequence[Mapping[str, Any]],
) -> list[DuplicateSet]:
    """Group promotion manifests by candidate-set identity.

    ``manifests`` are the decoded ``batch-*.json`` documents. The primary of
    a set is its earliest emission (tie-broken by batch id); every later
    emission is a duplicate that ST.02 marked ``superseded``.
    """
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for m in manifests:
        ident = candidate_set_identity(m.get("candidates", []) or [])
        groups.setdefault(ident, []).append(m)

    out: list[DuplicateSet] = []
    for ident, members in groups.items():
        if len(members) < 2:
            continue
        ordered = _sorted_members(
            Member(
                ref=str(m.get("batch_id")),
                fingerprint=_member_fingerprint(m.get("candidates", []) or []),
                created_at=m.get("created_at"),
                disposition=(
                    "archive" if m.get("superseded_by") else None
                ),
                detail=(
                    f"superseded_by={m.get('superseded_by')}"
                    if m.get("superseded_by")
                    else ""
                ),
            )
            for m in members
        )
        primary = ordered[0]
        content_identical = len({m.fingerprint for m in ordered}) == 1
        out.append(
            _mk_set(
                surface="promotion-manifest",
                kind=KIND_DUPLICATE,
                condition=ST02,
                identity_kind="candidate_set_identity",
                identity_value=ident,
                members=[
                    Member(
                        ref=m.ref,
                        fingerprint=m.fingerprint,
                        created_at=m.created_at,
                        disposition=m.disposition,
                        proposed_disposition=(
                            PRESERVE
                            if m.ref == primary.ref
                            else (ARCHIVE if m.disposition == "archive"
                                  else (MERGE if content_identical else PRESERVE))
                        ),
                        detail=m.detail,
                    )
                    for m in ordered
                ],
                detail=(
                    f"{len(ordered)} emissions of one candidate set; "
                    f"primary={primary.ref}; content_identical={content_identical}"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 2 — Resolution receipts (C1)
# ════════════════════════════════════════════════════════════════════════════
# Surface: resolution.receipt
# Condition: C1 — payload_fingerprint must be NOT NULL *and* deterministically
# verified, so that payload/fingerprint drift is structurally impossible.


def receipt_sets(
    receipts: Sequence[Mapping[str, Any]],
) -> list[DuplicateSet]:
    """Group receipts by payload fingerprint; verify each against the payload.

    A receipt whose stored fingerprint does not match the canonical
    recomputation is drift — it is surfaced, never silently normalized.
    """
    out: list[DuplicateSet] = []
    by_fp: dict[str, list[Mapping[str, Any]]] = {}
    unverifiable: list[Mapping[str, Any]] = []

    for r in receipts:
        stored = r.get("payload_fingerprint")
        payload = r.get("payload")
        if not stored or payload is None:
            unverifiable.append(r)
            continue
        recomputed = fingerprint(payload)
        if recomputed != stored:
            unverifiable.append(r)
            continue
        by_fp.setdefault(stored, []).append(r)

    for fp, members in by_fp.items():
        if len(members) < 2:
            continue
        out.append(
            _mk_set(
                surface="resolution.receipt",
                kind=KIND_DUPLICATE,
                condition=C1,
                identity_kind="payload_fingerprint",
                identity_value=fp,
                members=[
                    Member(
                        ref=str(r.get("id")),
                        fingerprint=fp,
                        created_at=r.get("created_at"),
                        proposed_disposition=PRESERVE,
                    )
                    for r in members
                ],
                detail=f"{len(members)} receipts share one payload fingerprint",
            )
        )

    if unverifiable:
        out.append(
            _mk_set(
                surface="resolution.receipt",
                kind=KIND_UNVERIFIED,
                condition=C1,
                identity_kind="payload_fingerprint",
                identity_value="missing-or-mismatched",
                members=[
                    Member(
                        ref=str(r.get("id")),
                        fingerprint=r.get("payload_fingerprint") or "",
                        created_at=r.get("created_at"),
                        proposed_disposition=PRESERVE,
                        detail=(
                            "no fingerprint" if not r.get("payload_fingerprint")
                            else "fingerprint does not match payload"
                        ),
                    )
                    for r in unverifiable
                ],
                detail=(
                    f"{len(unverifiable)} receipts cannot be verified against the "
                    "canonical fingerprint function"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 3 — canonical assets (C2)
# ════════════════════════════════════════════════════════════════════════════
# Surface: resolution.canonical_asset
# Condition: C2 — canonical_key must be backfilled at envelope-apply time.
#
# A NULL identity column is not merely "missing data": it makes the set
# *ungroupable*, so no duplicate detection for authority assets is possible
# at all until the key exists.


def canonical_asset_sets(
    assets: Sequence[Mapping[str, Any]],
) -> list[DuplicateSet]:
    """Group canonical assets by canonical_key; surface the NULL-key set."""
    out: list[DuplicateSet] = []
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    null_keyed: list[Mapping[str, Any]] = []

    for a in assets:
        key = a.get("canonical_key")
        if key in (None, {}, []):
            null_keyed.append(a)
        else:
            # canonical_key is JSONB on the live surface, so group by its
            # canonical text rather than by Python object identity.
            by_key.setdefault(canonical_json(key), []).append(a)

    for key, members in by_key.items():
        if len(members) < 2:
            continue
        out.append(
            _mk_set(
                surface="resolution.canonical_asset",
                kind=KIND_DUPLICATE,
                condition=C2,
                identity_kind="canonical_key",
                identity_value=key,
                members=[
                    Member(
                        ref=str(m.get("id")),
                        fingerprint=fingerprint([m.get("id"), key]),
                        created_at=m.get("created_at"),
                        proposed_disposition=PRESERVE,
                    )
                    for m in members
                ],
                detail=f"{len(members)} canonical assets share one canonical_key",
            )
        )

    if null_keyed:
        out.append(
            _mk_set(
                surface="resolution.canonical_asset",
                kind=KIND_UNIDENTIFIED,
                condition=C2,
                identity_kind="canonical_key",
                identity_value="null",
                members=[
                    Member(
                        ref=str(a.get("id")),
                        fingerprint=fingerprint(
                            [a.get("id"), a.get("canonical_asset_id"),
                             a.get("asset_kind")]
                        ),
                        created_at=a.get("created_at"),
                        proposed_disposition=PRESERVE,
                        detail="canonical_key IS NULL — set is ungroupable",
                    )
                    for a in null_keyed
                ],
                detail=(
                    f"{len(null_keyed)} canonical assets carry no canonical_key; "
                    "duplicate detection is impossible for this surface"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 4 — execution claims (C3)
# ════════════════════════════════════════════════════════════════════════════
# Surface: resolution.execution_claim
# Condition: C3 — subject_ref must be promoted to a real link.


def claim_sets(claims: Sequence[Mapping[str, Any]]) -> list[DuplicateSet]:
    """Group claims by attempt id; surface JSONB/column divergence.

    C3 asked for ``subject_ref`` (a JSONB lineage reference) to become a
    normalized link. The normalized ``attempt_id`` column already exists, so
    the residual work is *divergence* — rows where the JSONB copy and the
    column disagree, or where the JSONB copy is empty.
    """
    out: list[DuplicateSet] = []
    by_attempt: dict[str, list[Mapping[str, Any]]] = {}
    divergent: list[Mapping[str, Any]] = []

    for c in claims:
        ref = c.get("subject_ref") or {}
        ref_attempt = ref.get("attempt_id") if isinstance(ref, Mapping) else None
        col_attempt = c.get("attempt_id")
        col_attempt = str(col_attempt) if col_attempt else None
        if ref_attempt and col_attempt and str(ref_attempt) != col_attempt:
            divergent.append(c)
            continue
        if ref_attempt is None and col_attempt is None:
            divergent.append(c)
            continue
        by_attempt.setdefault(col_attempt or str(ref_attempt), []).append(c)

    for attempt, members in by_attempt.items():
        if len(members) < 2:
            continue
        out.append(
            _mk_set(
                surface="resolution.execution_claim",
                kind=KIND_DUPLICATE,
                condition=C3,
                identity_kind="attempt_id",
                identity_value=attempt,
                members=[
                    Member(
                        ref=str(m.get("id")),
                        fingerprint=fingerprint([m.get("id"), attempt]),
                        created_at=m.get("created_at"),
                        proposed_disposition=PRESERVE,
                    )
                    for m in members
                ],
                detail=f"{len(members)} claims share one attempt_id",
            )
        )

    if divergent:
        out.append(
            _mk_set(
                surface="resolution.execution_claim",
                kind=KIND_UNVERIFIED,
                condition=C3,
                identity_kind="subject_ref",
                identity_value="divergent-or-empty",
                members=[
                    Member(
                        ref=str(c.get("id")),
                        fingerprint=fingerprint(
                            [c.get("id"), c.get("subject_ref"), c.get("attempt_id")]
                        ),
                        created_at=c.get("created_at"),
                        proposed_disposition=PRESERVE,
                        detail=(
                            "subject_ref and attempt_id disagree"
                            if (c.get("subject_ref") or {}).get("attempt_id")
                            else "no attempt_id in subject_ref nor column"
                        ),
                    )
                    for c in divergent
                ],
                detail=(
                    f"{len(divergent)} claims carry a subject_ref that is not "
                    "backed by the normalized attempt_id column"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 5 — keychain event outbox (PC7)
# ════════════════════════════════════════════════════════════════════════════
# Surface: resolution.keychain_event_outbox
# Condition: PC7 — read_set refs live inside the payload (JSONB).


def outbox_sets(rows: Sequence[Mapping[str, Any]]) -> list[DuplicateSet]:
    """Group outbox rows by their canonical read_set."""
    by_read_set: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        read_set = r.get("read_set")
        if not read_set:
            continue
        by_read_set.setdefault(canonical_json(read_set), []).append(r)

    out: list[DuplicateSet] = []
    for key, members in by_read_set.items():
        if len(members) < 2:
            continue
        out.append(
            _mk_set(
                surface="resolution.keychain_event_outbox",
                kind=KIND_DUPLICATE,
                condition=PC7,
                identity_kind="read_set",
                identity_value=key,
                members=[
                    Member(
                        ref=str(m.get("id")),
                        fingerprint=fingerprint([m.get("id"), key]),
                        created_at=m.get("created_at") or m.get("recorded_at"),
                        proposed_disposition=PRESERVE,
                    )
                    for m in members
                ],
                detail=f"{len(members)} outbox rows share one read_set",
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 6 — legacy flat-evidence coverage (C4 receipts / C6 tickets)
# ════════════════════════════════════════════════════════════════════════════
# Condition: C4 — legacy flat evidence normalizes wrap-not-drop with an
# explicit disposition; discard is a first-class disposition, never silence.
#
# A legacy row with NO disposition row at all is the exact failure mode C4
# forbids: the row exists in history and nothing says what happened to it.


def legacy_coverage_sets(
    legacy_rows: Sequence[Mapping[str, Any]],
    dispositions: Sequence[Mapping[str, Any]],
    *,
    source_table: str,
    migration_version: str,
    surface: str,
) -> list[DuplicateSet]:
    """Find legacy rows that carry no disposition for the given lane.

    ``legacy_rows`` carry at least ``id`` (and optionally ``status``).
    ``dispositions`` carry ``source_table``, ``source_pk`` and
    ``migration_version``.
    """
    covered = {
        str(d.get("source_pk"))
        for d in dispositions
        if d.get("source_table") == source_table
        and d.get("migration_version") == migration_version
    }
    uncovered = [r for r in legacy_rows if str(r.get("id")) not in covered]
    if not uncovered:
        return []

    # Group by lifecycle status so each package targets a coherent cohort
    # (terminal rows retire; live rows must stay open). Surfaces without a
    # status column (vision.receipts) fall back to their record type, which
    # is the stable cohorting attribute there.
    by_status: dict[str, list[Mapping[str, Any]]] = {}
    for r in uncovered:
        cohort = str(r.get("status") or r.get("type") or "unknown")
        by_status.setdefault(cohort, []).append(r)

    out: list[DuplicateSet] = []
    for status, members in by_status.items():
        out.append(
            _mk_set(
                surface=surface,
                kind=KIND_UNCOVERED,
                condition=C4,
                identity_kind=f"{source_table}@{migration_version}:cohort",
                identity_value=status,
                members=[
                    Member(
                        ref=str(m.get("id")),
                        fingerprint=fingerprint(
                            [m.get("id"), m.get("status") or m.get("type"),
                             m.get("type")]
                        ),
                        created_at=m.get("created_at"),
                        proposed_disposition=PRESERVE,
                        detail=f"cohort={status}",
                    )
                    for m in members
                ],
                detail=(
                    f"{len(members)} {source_table} rows have no "
                    f"{migration_version} disposition (cohort={status})"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Detector 7 — disposition fingerprint audit gap (C4)
# ════════════════════════════════════════════════════════════════════════════
# The disposition ledger is the wrap-not-drop evidence surface. Its
# ``target_refs`` column carries the lineage, but NOT a per-row fingerprint —
# so two different legacy rows can share one identical ``target_refs``
# value and the ledger cannot then prove which row it dispositioned.
#
# This is the "fingerprint audit trail" evidence output W-A owes: every
# disposition must be individually addressable by fingerprint.


def disposition_audit_sets(
    dispositions: Sequence[Mapping[str, Any]],
) -> list[DuplicateSet]:
    """Surface disposition groups that share one identical target_refs."""
    by_target: dict[str, list[Mapping[str, Any]]] = {}
    for d in dispositions:
        target = d.get("target_refs")
        if target is None:
            continue
        by_target.setdefault(canonical_json(target), []).append(d)

    out: list[DuplicateSet] = []
    for key, members in by_target.items():
        if len(members) < 2:
            continue
        out.append(
            _mk_set(
                surface="resolution.migration_disposition",
                kind=KIND_UNVERIFIED,
                condition=C4,
                identity_kind="target_refs",
                identity_value=key,
                members=[
                    Member(
                        ref=str(m.get("id")),
                        fingerprint=fingerprint(
                            [m.get("id"), m.get("source_pk"), m.get("target_refs")]
                        ),
                        created_at=m.get("created_at"),
                        proposed_disposition=PRESERVE,
                        detail=(
                            f"source={m.get('source_schema')}."
                            f"{m.get('source_table')}:{m.get('source_pk')}"
                        ),
                    )
                    for m in members
                ],
                detail=(
                    f"{len(members)} dispositions share one target_refs value; "
                    "the ledger carries no per-row fingerprint to tell them apart"
                ),
            )
        )
    return sorted(out, key=lambda s: s.id)


# ════════════════════════════════════════════════════════════════════════════
# Aggregation
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Inventory:
    """The full W-A inventory: every duplicate-history set that needs a package."""

    sets: tuple[DuplicateSet, ...]
    generated_at: str = ""
    census: Mapping[str, Any] = field(default_factory=dict)

    @property
    def package_ids(self) -> tuple[str, ...]:
        return tuple(sorted({s.package_id for s in self.sets}))

    @property
    def open_packages(self) -> tuple[DuplicateSet, ...]:
        return tuple(s for s in self.sets if not s.dispositioned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "census": dict(self.census),
            "set_count": len(self.sets),
            "package_count": len(self.package_ids),
            "open_package_count": len(self.open_packages),
            "sets": [s.to_dict() for s in self.sets],
        }


def build_inventory(
    *,
    manifests: Sequence[Mapping[str, Any]] = (),
    receipts: Sequence[Mapping[str, Any]] = (),
    canonical_assets: Sequence[Mapping[str, Any]] = (),
    claims: Sequence[Mapping[str, Any]] = (),
    outbox_rows: Sequence[Mapping[str, Any]] = (),
    legacy_receipts: Sequence[Mapping[str, Any]] = (),
    legacy_tickets: Sequence[Mapping[str, Any]] = (),
    dispositions: Sequence[Mapping[str, Any]] = (),
    generated_at: str = "",
    census: Mapping[str, Any] | None = None,
) -> Inventory:
    """Run every detector and return the aggregated inventory.

    The set order is total (by set id) so two runs over the same data
    produce byte-identical output — the property the reconciliation log's
    hash chain depends on.
    """
    sets: list[DuplicateSet] = []
    sets.extend(manifest_sets(manifests))
    sets.extend(receipt_sets(receipts))
    sets.extend(canonical_asset_sets(canonical_assets))
    sets.extend(claim_sets(claims))
    sets.extend(outbox_sets(outbox_rows))
    sets.extend(
        legacy_coverage_sets(
            legacy_receipts,
            dispositions,
            source_table="receipts",
            migration_version="C4",
            surface="vision.receipts",
        )
    )
    sets.extend(
        legacy_coverage_sets(
            legacy_tickets,
            dispositions,
            source_table="tickets",
            migration_version="C6",
            surface="vision.tickets",
        )
    )
    sets.extend(disposition_audit_sets(dispositions))

    sets.sort(key=lambda s: s.id)
    return Inventory(
        sets=tuple(sets),
        generated_at=generated_at,
        census=census or {},
    )
