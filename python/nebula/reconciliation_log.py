"""Append-only, hash-chained reconciliation log.

Plan 8261653. Docket thread c7c26160-5b22-463f-9ee0-6e4784bae0e8.

The W-A envelope rule is *wrap-not-drop*: every historical record that is
merged, preserved or archived keeps an explicit, individually addressable
disposition carrying the record id, its fingerprint, and a reason. Nothing
disappears silently.

This module is the builder-side ledger for those dispositions. It is:

  * **append-only** — the file is opened ``O_APPEND`` and never truncated;
    a second disposition for a record is a new entry, never an edit.
  * **hash-chained** — each entry commits to its predecessor, so a
    rewritten or removed entry breaks the chain and is detectable.
  * **deterministic** — the same inputs produce the same chain, so the
    log can be diffed across runs.

The corresponding DB surface is ``resolution.reconciliation_set`` /
``resolution.reconciliation_disposition`` / ``resolution.fingerprint_audit``
(V205). :meth:`ReconciliationLog.to_sql` renders the log as the exact
INSERT statements the DBA lane applies — the builder never applies them
itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .duplicate_history import (
    ARCHIVE,
    C1,
    DISPOSITIONS,
    MERGE,
    PRESERVE,
    DuplicateSet,
    Inventory,
    canonical_json,
    fingerprint,
)

LOG_VERSION = 1
GENESIS_HASH = "0" * 64


class ReconciliationError(RuntimeError):
    """Raised when a disposition would violate the envelope rules."""


@dataclass(frozen=True)
class Entry:
    """One append-only disposition record."""

    seq: int
    at: str
    set_id: str
    surface: str
    kind: str
    condition: str
    member_ref: str
    member_fingerprint: str
    disposition: str
    reason: str
    primary_ref: str
    prev_hash: str
    entry_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "set_id": self.set_id,
            "surface": self.surface,
            "kind": self.kind,
            "condition": self.condition,
            "member_ref": self.member_ref,
            "member_fingerprint": self.member_fingerprint,
            "disposition": self.disposition,
            "reason": self.reason,
            "primary_ref": self.primary_ref,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


def _compute_entry_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ReconciliationLog:
    """Append-only hash-chained JSONL disposition log."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ── reading ───────────────────────────────────────────────────────────

    def entries(self) -> list[Entry]:
        """Read every entry, in order. A missing file is an empty log."""
        if not self.path.exists():
            return []
        out: list[Entry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReconciliationError(
                    f"{self.path}: line is not valid JSON: {exc}"
                ) from exc
            out.append(Entry(**doc))
        return out

    def verify(self) -> tuple[bool, list[str]]:
        """Verify the hash chain.

        Returns ``(ok, problems)``. The chain is valid when every entry's
        stored ``entry_hash`` matches the recomputation and every
        ``prev_hash`` matches its predecessor.
        """
        problems: list[str] = []
        prev = GENESIS_HASH
        for i, e in enumerate(self.entries()):
            if e.seq != i:
                problems.append(f"seq {e.seq} out of order at position {i}")
            if e.prev_hash != prev:
                problems.append(f"seq {e.seq}: prev_hash does not chain")
            payload = {k: v for k, v in e.to_dict().items() if k != "entry_hash"}
            if _compute_entry_hash(payload) != e.entry_hash:
                problems.append(f"seq {e.seq}: entry_hash does not match content")
            prev = e.entry_hash
        return (not problems), problems

    # ── writing ───────────────────────────────────────────────────────────

    def append(
        self,
        *,
        set_id: str,
        surface: str,
        kind: str,
        condition: str,
        member_ref: str,
        member_fingerprint: str,
        disposition: str,
        reason: str,
        primary_ref: str = "",
        at: str | None = None,
    ) -> Entry:
        """Append one disposition. The file is opened O_APPEND, never truncated."""
        if disposition not in DISPOSITIONS:
            raise ReconciliationError(
                f"disposition must be one of {DISPOSITIONS}, got {disposition!r}"
            )
        if not member_ref:
            raise ReconciliationError("member_ref is required — no silent records")
        if not reason or not reason.strip():
            raise ReconciliationError("reason is required — every disposition is explained")
        if disposition == MERGE and not primary_ref:
            raise ReconciliationError("a merge disposition requires its primary_ref")
        if disposition == MERGE and primary_ref == member_ref:
            raise ReconciliationError("a record cannot be merged into itself")

        existing = self.entries()
        seq = len(existing)
        prev_hash = existing[-1].entry_hash if existing else GENESIS_HASH
        payload = {
            "seq": seq,
            "at": at or datetime.now(timezone.utc).isoformat(),
            "set_id": set_id,
            "surface": surface,
            "kind": kind,
            "condition": condition,
            "member_ref": member_ref,
            "member_fingerprint": member_fingerprint,
            "disposition": disposition,
            "reason": reason,
            "primary_ref": primary_ref,
            "prev_hash": prev_hash,
        }
        entry_hash = _compute_entry_hash(payload)
        entry = Entry(**payload, entry_hash=entry_hash)

        line = canonical_json(entry.to_dict()) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return entry

    # ── projection to the DB ledger ───────────────────────────────────────

    def to_sql(self) -> list[str]:
        """Render the log as the INSERT statements the DBA lane applies.

        The builder emits SQL; it does not execute it against the live
        database. ``resolution.reconciliation_disposition`` is append-only
        (enforced by trigger), so a repeated entry is a new generation
        rather than an update.
        """
        stmts: list[str] = []
        seen_sets: set[str] = set()
        for e in self.entries():
            if e.set_id not in seen_sets:
                seen_sets.add(e.set_id)
                stmts.append(
                    "INSERT INTO resolution.reconciliation_set "
                    "(set_id, surface, set_kind, condition_ref, identity_kind, "
                    " identity_value, member_count, status) VALUES ("
                    f"'{_q(e.set_id)}', '{_q(e.surface)}', '{_q(e.kind)}', "
                    f"'{_q(e.condition)}', '{_q(e.surface)}_identity', "
                    f"'{_q(e.set_id)}', 0, 'dispositioning') "
                    "ON CONFLICT (set_id) DO NOTHING;"
                )
            stmts.append(
                "INSERT INTO resolution.reconciliation_disposition "
                "(set_id, member_ref, member_fingerprint, disposition, reason, "
                " primary_ref, recorded_by) VALUES ("
                f"'{_q(e.set_id)}', '{_q(e.member_ref)}', "
                f"'{_q(e.member_fingerprint)}', '{_q(e.disposition)}', "
                f"'{_q(e.reason)}', '{_q(e.primary_ref)}', 'builder');"
            )
            stmts.append(
                "INSERT INTO resolution.fingerprint_audit "
                "(surface, source_ref, fingerprint, set_id, recorded_by) VALUES ("
                f"'{_q(e.surface)}', '{_q(e.member_ref)}', "
                f"'{_q(e.member_fingerprint)}', '{_q(e.set_id)}', 'builder');"
            )
        return stmts


def _q(value: str) -> str:
    """Escape a value for a single-quoted SQL literal."""
    return str(value).replace("'", "''")


def dispositions_for(
    inventory: Inventory,
) -> Iterator[tuple[DuplicateSet, str, str, str]]:
    """Yield ``(set, member_ref, disposition, reason)`` proposals.

    The rules are deliberately conservative — the log records what the
    evidence supports, not what a bulk operation would prefer:

      * the primary of a set is ``preserve`` — it is the surviving record;
      * a content-identical duplicate is ``merge`` — folding it is safe
        because the content fingerprint already proves identity;
      * a duplicate whose content diverged is ``preserve`` — the divergence
        is signal and is never overwritten;
      * a manifest duplicate already marked superseded is ``archive`` —
        retired from active views, retained as history;
      * an uncovered or unidentified member is ``preserve`` — it has no
        proven primary to merge into, so it stays open for judgement.
    """
    for s in inventory.sets:
        primary = s.members[0].ref if s.members else ""
        content_fps = {m.fingerprint for m in s.members}
        identical = len(content_fps) == 1
        for m in s.members:
            if m.ref == primary:
                yield (
                    s,
                    m.ref,
                    PRESERVE,
                    f"primary of {s.surface} set {s.id[:12]} — surviving record",
                )
                continue
            if m.disposition == "archive":
                yield (
                    s,
                    m.ref,
                    ARCHIVE,
                    f"already superseded by {m.detail or 'a later emission'} — "
                    "archived, retained as history",
                )
                continue
            if s.kind == "duplicate" and identical:
                yield (
                    s,
                    m.ref,
                    MERGE,
                    f"content-identical to {primary} "
                    f"(fingerprint {m.fingerprint[:16]}) — folded, not dropped",
                )
                continue
            if s.kind == "duplicate":
                yield (
                    s,
                    m.ref,
                    PRESERVE,
                    f"content diverges from {primary} — divergence preserved, "
                    "never overwritten",
                )
                continue
            yield (
                s,
                m.ref,
                PRESERVE,
                f"{s.kind} set {s.id[:12]} — no proven primary; kept open for judgement",
            )
