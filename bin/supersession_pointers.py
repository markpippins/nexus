#!/usr/bin/env python3
"""supersession_pointers.py — one resolver for supersession pointer tags.

Ranking item 12 (architect ruling aa7d33dd, on top of item 11's 963501f2 and
item 10's 4963dee4). The corpus carries three supersession-shaped tag
families, and until now nothing said which of them is a pointer, what a
pointer value is allowed to look like, or what a reader should do when a
pointer names no record. This module makes those decisions explicit, shared
and read-only, in the same discipline `bin/status_vocab.py` applies to
`status:` and `bin/gate_shape.py` applies to `gate:`.

Families (ruling §12.1, §12.5):

    superseded-by:<v>        RATIFIED    the canonical inbound pointer:
                                          "the record that replaced me".
    supersedes-relation:<v>  DEPRECATED  a second inbound channel that
                                          duplicates `superseded-by:`; still
                                          resolved so existing rows stay
                                          readable, and flagged deprecated.
    supersedes:<v>           UNRATIFIED  NOT a pointer family: free-form
                                          metadata. Resolution is reported for
                                          information, never treated as a
                                          structural pointer.

Pointer grammar (ruling §12.1): an 8-character lowercase hex prefix is the
canonical form; a full lowercase UUID is also allowed. Both must resolve to
exactly one record.

Resolution (ruling §12.2): exact UUID match -> resolved; prefix matching one
record -> resolved; prefix matching several -> `ptr_prefix_ambiguous` (the
reader never picks one); no match -> `ptr_unresolved`; empty, whitespace or
uppercase-hex -> `ptr_malformed`; a pointer naming its own record ->
`ptr_self`. "Dangling" means the named row is absent — the same condition the
resolver reports as `ptr_unresolved`.

The empty value deserves its own guard: the only empty `supersedes:` value in
the corpus sits on record 33333333-3333-4333-8333-333333333333 and, matched as
a prefix, would resolve to every one of the 17,567 records. `ptr_malformed` is
decided from the value's shape *before* any lookup, so an empty value can never
enter prefix matching. The three-character `supersedes:767` is genuinely
ambiguous: it matches eight records and is reported as such, not guessed.

`status:superseded` and pointers are independent (ruling §12.3). A
`status:superseded` row is not required to carry a pointer, and a
`superseded-by:` pointer is not required to carry that status; the six rows
that violate either direction (three each) are reported as accepted
exceptions, never rewritten. `supersedes:` does not participate in this check
at all, because it is not an inbound pointer (a row that supersedes another is
not itself superseded).

Chains (ruling §12.4): one successor per row is a convention, not a rule; two
rows carry two successors. Transitive resolution is defined — follow
`superseded-by:` until a record that is not itself superseded-by — and a
cycle or self-pointer is reported (`ptr_malformed` / `ptr_self`) instead of
looping. Free-form `supersedes-<word>` tags are left alone (§12.6): they are
inventoried, given no structural meaning and never rewritten.

Item 12 writes nothing. Every tag stays byte-identical; `--audit` is read-only.
Normalize-on-write is the only write-side obligation: `require_pointer_for_write`
returns the canonical `superseded-by:<first8>` form and raises
`PointerWriteRefused` for a malformed or ambiguous-at-write value.

Usage:
  supersession_pointers.py --audit [--json] [--report PATH] [--strict]

Exit codes: 0 clean (ratified family fully resolved); 1 findings; 2 environment
error (nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

# ── families (ruling §12.1 / §12.5) ──────────────────────────────────────
PREFIX_RATIFIED = "superseded-by:"
PREFIX_DEPRECATED = "supersedes-relation:"
PREFIX_UNRATIFIED = "supersedes:"

FAMILY_RATIFIED = "family_ratified"
FAMILY_DEPRECATED = "family_deprecated"
FAMILY_UNRATIFIED = "family_unratified"

FAMILIES: Dict[str, Tuple[str, str]] = {
    PREFIX_RATIFIED: ("superseded-by", FAMILY_RATIFIED),
    PREFIX_DEPRECATED: ("supersedes-relation", FAMILY_DEPRECATED),
    PREFIX_UNRATIFIED: ("supersedes", FAMILY_UNRATIFIED),
}

# ── reason codes (ruling §12.2, plus one the deprecation decision needs) ──
R_RESOLVED = "ptr_resolved"
R_AMBIGUOUS = "ptr_prefix_ambiguous"
R_UNRESOLVED = "ptr_unresolved"
R_MALFORMED = "ptr_malformed"
R_SELF = "ptr_self"
R_DEPRECATED = "ptr_family_deprecated"

REASON_DOC = {
    R_RESOLVED: "pointer names exactly one record",
    R_AMBIGUOUS: "prefix names more than one record — never resolved arbitrarily",
    R_UNRESOLVED: "no record carries this id/prefix; prose values land here (§12.5)",
    R_MALFORMED: "empty, whitespace or uppercase-hex value; decided before any lookup",
    R_SELF: "pointer names its own record",
    R_DEPRECATED: "value resolves, but the family is deprecated in favour of superseded-by:",
}

STATUS_SUPERSEDED = "status:superseded"

HEX_RE = re.compile(r"^[0-9a-f]+$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UPPER_HEXISH_RE = re.compile(r"^[0-9A-Fa-f-]+$")
CANONICAL_PREFIX_LEN = 8


class PointerWriteRefused(ValueError):
    """A writer tried to emit a pointer that is not canonically writable."""

    def __init__(self, value: str, reason_code: str):
        self.value = value
        self.reason_code = reason_code
        super().__init__(
            f"cannot write a supersession pointer {value!r}: {reason_code} "
            f"({REASON_DOC[reason_code]})"
        )


@dataclass(frozen=True)
class PointerClass:
    """Verdict for one pointer-shaped tag."""

    tag: str
    family: str
    family_status: str
    value: str
    reason_code: str
    target: Optional[str] = None        # resolved record id
    hits: int = 0                       # how many records matched the value
    canonical_form: bool = False        # 8-char prefix or full UUID

    @property
    def resolved(self) -> bool:
        return self.reason_code in (R_RESOLVED, R_DEPRECATED)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tag": self.tag,
            "family": self.family,
            "family_status": self.family_status,
            "value": self.value,
            "reason": self.reason_code,
            "target": self.target,
            "hits": self.hits,
            "canonical_form": self.canonical_form,
        }


class RecordIndex:
    """Id index the resolver matches prefixes against.

    Built from the live corpus (17,567 ids) or, in tests, from a synthetic id
    set. Nothing here writes.
    """

    def __init__(self, ids: Iterable[str]):
        self.ids: List[str] = [str(i) for i in ids]
        self._set = set(self.ids)
        self._by8: Dict[str, List[str]] = {}
        for i in self.ids:
            self._by8.setdefault(i[:CANONICAL_PREFIX_LEN], []).append(i)

    def __len__(self) -> int:
        return len(self.ids)

    def resolve(self, value: str) -> List[str]:
        """Every record whose id starts with `value` (exact match first)."""
        if value == "":
            # Guard: an empty prefix would match the entire corpus.
            return []
        if value in self._set:
            return [value]
        if len(value) >= CANONICAL_PREFIX_LEN:
            return [i for i in self._by8.get(value[:CANONICAL_PREFIX_LEN], ()) if i.startswith(value)]
        return [i for i in self.ids if i.startswith(value)]


def pointer_families(tags: Iterable[str]) -> List[Tuple[str, str]]:
    """(family, value) for every supersession-shaped tag, in tag order."""
    out: List[Tuple[str, str]] = []
    for tag in tags or ():
        s = str(tag)
        for prefix, (family, _status) in FAMILIES.items():
            if s.startswith(prefix):
                out.append((family, s[len(prefix):]))
                break
    return out


def family_status(family: str) -> str:
    for _prefix, (name, status) in FAMILIES.items():
        if name == family:
            return status
    raise KeyError(family)


def classify_pointer(
    family: str,
    value: Any,
    index: Optional[RecordIndex] = None,
    subject_id: Optional[str] = None,
) -> PointerClass:
    """Verdict for one pointer value, from its shape and the record index."""
    raw = "" if value is None else str(value)
    status = family_status(family)
    prefix = next(p for p, (name, _s) in FAMILIES.items() if name == family)
    tag = prefix + raw

    def mk(reason: str, target: Optional[str] = None, hits: int = 0,
           canonical: bool = False) -> PointerClass:
        code = R_DEPRECATED if (reason == R_RESOLVED and status == FAMILY_DEPRECATED) else reason
        return PointerClass(tag, family, status, raw, code, target, hits, canonical)

    # Shape first — this is what keeps the empty value out of prefix matching.
    if raw == "" or raw != raw.strip() or any(ch.isspace() for ch in raw):
        return mk(R_MALFORMED)
    if raw != raw.lower() and UPPER_HEXISH_RE.match(raw):
        return mk(R_MALFORMED)

    is_uuid = bool(UUID_RE.match(raw))
    is_hex = bool(HEX_RE.match(raw))
    if not (is_uuid or is_hex):
        # Prose, a PR number, a branch name — not an id (§12.5).
        return mk(R_UNRESOLVED)

    canonical = is_uuid or len(raw) == CANONICAL_PREFIX_LEN
    if index is None:
        # No corpus to check: only the canonical shape can be called resolved.
        return mk(R_RESOLVED if canonical else R_MALFORMED, canonical=canonical)

    hits = index.resolve(raw)
    if len(hits) == 1:
        target = hits[0]
        if subject_id is not None and target == subject_id:
            return mk(R_SELF, target, 1, canonical)
        return mk(R_RESOLVED, target, 1, canonical)
    if len(hits) > 1:
        return mk(R_AMBIGUOUS, None, len(hits), canonical)
    return mk(R_UNRESOLVED, None, 0, canonical)


def classify_tag(
    tag: Any,
    index: Optional[RecordIndex] = None,
    subject_id: Optional[str] = None,
) -> Optional[PointerClass]:
    s = str(tag or "")
    for prefix, (family, _status) in FAMILIES.items():
        if s.startswith(prefix):
            return classify_pointer(family, s[len(prefix):], index, subject_id)
    return None


def pointer_classes(
    tags: Iterable[str],
    index: Optional[RecordIndex] = None,
    subject_id: Optional[str] = None,
) -> List[PointerClass]:
    return [c for c in (classify_tag(t, index, subject_id) for t in (tags or ())) if c is not None]


def canonical_successor(
    tags: Iterable[str],
    index: RecordIndex,
    subject_id: Optional[str] = None,
) -> Optional[str]:
    """The one record the ratified pointer names, or None (refuse, never choose).

    Only `superseded-by:` counts here: a deprecated or unratified family is
    readable but is not the structural successor.
    """
    targets = {
        c.target
        for c in pointer_classes(tags, index, subject_id)
        if c.family == "superseded-by" and c.resolved and c.target
    }
    return targets.pop() if len(targets) == 1 else None


def resolve_chain(
    tags_by_id: Dict[str, List[str]],
    index: RecordIndex,
    start_id: str,
    max_depth: int = 64,
) -> Tuple[Optional[str], int, Optional[str]]:
    """Follow `superseded-by:` to a record that is not itself superseded.

    Returns (terminal_id, steps, problem). `problem` is `ptr_cycle` when a
    record repeats before the chain terminates and `ptr_self` when a record
    points at itself; in both cases the ruling asks for a flag rather than a
    loop (§12.4).
    """
    seen = {start_id}
    current = start_id
    steps = 0
    while steps < max_depth:
        # A self-pointer is a pointer that resolved to its own record; report it
        # rather than letting canonical_successor() silently return nothing.
        if any(c.reason_code == R_SELF
               for c in pointer_classes(tags_by_id.get(current, []), index, current)
               if c.family == "superseded-by"):
            return current, steps, R_SELF
        successor = canonical_successor(tags_by_id.get(current, []), index, current)
        if successor is None:
            return current, steps, None
        if successor == current:
            return current, steps, R_SELF
        if successor in seen:
            return current, steps, "ptr_cycle"
        seen.add(successor)
        current = successor
        steps += 1
    return current, steps, "ptr_cycle"


def require_pointer_for_write(
    value: Any,
    index: Optional[RecordIndex] = None,
    subject_id: Optional[str] = None,
) -> str:
    """Return the canonical `superseded-by:<first8>` tag a writer may emit.

    Normalize-on-write (§12.7): a full UUID is shortened to its canonical
    8-character prefix, and anything malformed, unresolved, ambiguous or
    self-referential raises with its exact reason code.
    """
    c = classify_pointer("superseded-by", value, index, subject_id)
    if c.reason_code != R_RESOLVED or c.target is None:
        raise PointerWriteRefused(c.value, c.reason_code)
    return PREFIX_RATIFIED + c.target[:CANONICAL_PREFIX_LEN]


# ── corpus audit (read-only) ──────────────────────────────────────────────

@dataclass
class AuditRow:
    id: str
    title: str
    tags: List[str]


@dataclass
class Audit:
    pointer_rows: int = 0
    occurrences: int = 0
    by_family: Dict[str, int] = field(default_factory=dict)
    by_family_rows: Dict[str, int] = field(default_factory=dict)
    reasons: Dict[str, int] = field(default_factory=dict)
    family_statuses: Dict[str, int] = field(default_factory=dict)
    ratified_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    deprecated_resolved: int = 0
    unratified_nonid: int = 0
    status_superseded_rows: int = 0
    status_without_pointer: List[Dict[str, Any]] = field(default_factory=list)
    pointer_without_status: List[Dict[str, Any]] = field(default_factory=list)
    multi_pointer_rows: List[Dict[str, Any]] = field(default_factory=list)
    chains: List[Dict[str, Any]] = field(default_factory=list)
    chains_exact_uuid: int = 0
    self_pointers: List[Dict[str, Any]] = field(default_factory=list)
    cycles: List[Dict[str, Any]] = field(default_factory=list)
    freeform: Dict[str, int] = field(default_factory=dict)
    occurrences_report: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def unclassified(self) -> int:
        return self.occurrences - sum(self.reasons.values())

    @property
    def ok(self) -> bool:
        return self.unclassified == 0 and not self.ratified_anomalies

    def summary(self, strict: bool = False) -> Dict[str, Any]:
        checks = {
            "zero_unclassified": self.unclassified == 0,
            "zero_ratified_anomalies": not self.ratified_anomalies,
        }
        if strict:
            checks["zero_unratified_nonid"] = self.unratified_nonid == 0
        return {
            "pointer_rows": self.pointer_rows,
            "occurrences": self.occurrences,
            "by_family": dict(sorted(self.by_family.items())),
            "by_family_rows": dict(sorted(self.by_family_rows.items())),
            "reasons": dict(sorted(self.reasons.items())),
            "family_statuses": dict(sorted(self.family_statuses.items())),
            "ratified_anomalies": self.ratified_anomalies,
            "deprecated_resolved": self.deprecated_resolved,
            "unratified_nonid": self.unratified_nonid,
            "status_superseded_rows": self.status_superseded_rows,
            "status_without_pointer": self.status_without_pointer,
            "pointer_without_status": self.pointer_without_status,
            "multi_pointer_rows": self.multi_pointer_rows,
            "chains": self.chains,
            "chains_count": len(self.chains),
            "chains_exact_uuid": self.chains_exact_uuid,
            "self_pointers": self.self_pointers,
            "cycles": self.cycles,
            "freeform": dict(sorted(self.freeform.items())),
            "freeform_count": len(self.freeform),
            "unclassified": self.unclassified,
            "ok": self.ok and (not self.unratified_nonid if strict else True),
            "strict": strict,
            "checks": checks,
        }


Classifier = Callable[[str, str, Optional[str]], PointerClass]


def audit_rows(
    rows: Sequence[AuditRow],
    index: Optional[RecordIndex] = None,
    strict: bool = False,
    classifier: Optional[Classifier] = None,
) -> Audit:
    out = Audit()
    if index is None:
        index = RecordIndex(r.id for r in rows)
    classify: Classifier = classifier or (
        lambda family, value, subject: classify_pointer(family, value, index, subject)
    )
    tags_by_id = {r.id: list(r.tags or []) for r in rows}
    canonical_pointer_rows = set()

    for row in rows:
        tags = list(row.tags or [])
        # Invariant and inventory facts are collected before the pointer gate:
        # a row with NO pointer is exactly the row the status invariant is
        # about, and the free-form supersedes-<word> tags live on rows that
        # carry no structured family at all.
        has_status_superseded = STATUS_SUPERSEDED in tags
        if has_status_superseded:
            out.status_superseded_rows += 1
        for tag in tags:
            s = str(tag)
            if s.startswith("supersedes-") and not any(s.startswith(p) for p in FAMILIES):
                out.freeform[s] = out.freeform.get(s, 0) + 1
        families = pointer_families(tags)
        if not families:
            if has_status_superseded:
                out.status_without_pointer.append({"id": row.id,
                                                   "title": (row.title or "")[:80],
                                                   "tags": tags})
            continue
        out.pointer_rows += 1
        classes: List[PointerClass] = []
        seen_families = set()
        for family, value in families:
            c = classify(family, value, row.id)
            classes.append(c)
            seen_families.add(family)
            out.occurrences += 1
            out.by_family[c.family] = out.by_family.get(c.family, 0) + 1
            out.reasons[c.reason_code] = out.reasons.get(c.reason_code, 0) + 1
            out.family_statuses[c.family_status] = out.family_statuses.get(c.family_status, 0) + 1
            entry = {"id": row.id, "family": c.family, "value": c.value,
                     "reason": c.reason_code, "target": c.target or "",
                     "hits": c.hits, "canonical_form": c.canonical_form,
                     "title": (row.title or "")[:80]}
            out.occurrences_report.append(entry)
            if c.family_status == FAMILY_RATIFIED and c.reason_code != R_RESOLVED:
                out.ratified_anomalies.append(entry)
            if c.reason_code == R_DEPRECATED:
                out.deprecated_resolved += 1
            if c.family_status == FAMILY_UNRATIFIED and c.reason_code != R_RESOLVED:
                out.unratified_nonid += 1
            if c.reason_code == R_SELF:
                out.self_pointers.append(entry)
        for family in seen_families:
            out.by_family_rows[family] = out.by_family_rows.get(family, 0) + 1

        nsb = [c for c in classes if c.family == "superseded-by"]
        if nsb:
            canonical_pointer_rows.add(row.id)
        if len(nsb) > 1:
            out.multi_pointer_rows.append({"id": row.id, "title": (row.title or "")[:80],
                                           "pointers": [c.value for c in nsb]})
        if nsb and any(c.resolved for c in nsb):
            if STATUS_SUPERSEDED not in tags:
                out.pointer_without_status.append({"id": row.id,
                                                   "title": (row.title or "")[:80],
                                                   "pointers": [c.value for c in nsb]})
        if has_status_superseded and not nsb:
            out.status_without_pointer.append({"id": row.id,
                                               "title": (row.title or "")[:80],
                                               "tags": tags})

        # Chains and cycles over the ratified pointer only (§12.4).
        for c in nsb:
            if not c.resolved or not c.target:
                continue
            target_tags = tags_by_id.get(c.target, [])
            if any(t.startswith(PREFIX_RATIFIED) for t in target_tags):
                out.chains.append({"id": row.id, "target": c.target,
                                   "pointer": c.value,
                                   "exact_uuid": bool(UUID_RE.match(c.value))})
                if UUID_RE.match(c.value):
                    out.chains_exact_uuid += 1


    # Cycles: a chain that revisits a record (§12.4).
    for row_id in list(tags_by_id):
        if not any(t.startswith(PREFIX_RATIFIED) for t in tags_by_id[row_id]):
            continue
        _terminal, _steps, problem = resolve_chain(tags_by_id, index, row_id)
        if problem == "ptr_cycle":
            out.cycles.append({"id": row_id})
    return out


def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg2 is required to read the corpus") from exc
    return psycopg2.connect(dsn)


def load_rows(dsn: str = DSN) -> List[AuditRow]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            -- Every record: the audit has to see rows that carry no pointer
            -- at all, or the pointerless `status:superseded` rows and the
            -- free-form supersedes-<word> tags would be invisible.
            select id::text, coalesce(title,''), coalesce(tags, '{}'::text[])
              from nebula.agent_records
             order by created_at asc
            """
        )
        return [AuditRow(r[0], r[1], list(r[2] or [])) for r in cur.fetchall()]


def load_index(dsn: str = DSN) -> RecordIndex:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select id::text from nebula.agent_records")
        return RecordIndex(r[0] for r in cur.fetchall())


def report_tsv(audit: Audit) -> str:
    lines = ["record_id\tfamily\traw_tag\treason\ttarget\thits\ttitle"]
    for e in audit.occurrences_report:
        prefix = next(p for p, (name, _s) in FAMILIES.items() if name == e["family"])
        lines.append("\t".join([e["id"], e["family"], prefix + e["value"], e["reason"],
                                e["target"], str(e["hits"]), e["title"]]))
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows, load_ids=load_index) -> int:
    ap = argparse.ArgumentParser(
        description="Audit supersession pointer integrity (ruling aa7d33dd)."
    )
    ap.add_argument("--audit", action="store_true", help="read the corpus and report (default)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", metavar="PATH")
    ap.add_argument("--strict", action="store_true",
                    help="treat non-id values inside the unratified supersedes: family as findings")
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        rows = load(args.dsn)
        index = load_ids(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    audit = audit_rows(rows, index, args.strict)
    summary = audit.summary(args.strict)
    if args.report:
        Path(args.report).write_text(report_tsv(audit), encoding="utf-8")
        summary["report"] = args.report
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("supersession pointer audit — " + ("CLEAN" if summary["ok"] else "FINDINGS"))
        print(f"pointer rows: {audit.pointer_rows}  occurrences: {audit.occurrences}")
        for family in sorted(audit.by_family):
            status = family_status(family)
            print(f"  {family:<20} {audit.by_family[family]:>4} occ  "
                  f"{audit.by_family_rows.get(family, 0):>4} rows  [{status}]")
        for reason, count in sorted(audit.reasons.items()):
            print(f"  {reason:<24} {count}")
        print(f"  status:superseded rows: {audit.status_superseded_rows} "
              f"(without pointer: {len(audit.status_without_pointer)})")
        print(f"  superseded-by without status:superseded: {len(audit.pointer_without_status)}")
        print(f"  multi-pointer rows: {len(audit.multi_pointer_rows)}")
        print(f"  chains: {len(audit.chains)} (exact-uuid pointers: {audit.chains_exact_uuid})")
        print(f"  self pointers: {len(audit.self_pointers)}  cycles: {len(audit.cycles)}")
        print(f"  freeform supersedes-<word> tags: {len(audit.freeform)} (left alone)")
        if audit.ratified_anomalies:
            print(f"  RATIFIED-FAMILY ANOMALIES: {len(audit.ratified_anomalies)}", file=sys.stderr)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
