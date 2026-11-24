#!/usr/bin/env python3
"""status_vocab.py — one classifier for `status:<value>`: canonical, alias, or HELD.

Ranking item 11 (architect ruling 963501f2, on top of item 7's 89064e45 and
item 10's 4963dee4). Tags carry lifecycle state in `status:*`, and the corpus
used 141 distinct spellings of it. Item 11 decides which spellings are the
same state and which are not, and makes that decision explicit and shared —
the same discipline `bin/gate_shape.py` applies to gate values.

Canonical vocabulary (ruling §11.1): 128 values written verbatim. A value is
canonical iff it is in CANONICAL; nothing is synonym-folded by "closest match".

Ratified alias table (ruling §11.3.A) — the ONLY collapses:

    complete            -> done              (equivalence)
    completed           -> done              (equivalence)
    in_progress         -> in-progress       (format)
    inprogress          -> in-progress       (format)
    review-request      -> review-requested  (format)

Held values (ruling §11.4) — could belong to more than one family, so they are
never inferred: conditional, disposition, standing, ok, partial, review,
advanced, mixed. Held is a named class, not an error: the audit reports it and
`--strict` is what makes it fatal.

Reason codes (exact): status_canonical, status_alias, status_ambiguous_family,
status_unknown, status_malformed.

Handling (ruling §11.2): map-on-read for existing tags (the physical tag is
never touched), preserve-and-flag for every non-canonical tag, and
normalize-on-write only for new writes — `require_canonical_for_write` raises
`StatusWriteRefused` for an alias or a held value, so a writer cannot introduce
a new alias or guess a held one.

Item 11 writes nothing. `--audit` is read-only and accounts for every
`status:` occurrence in the corpus: one verdict per occurrence, per-record TSV
via --report, exit 0 when unclassified/unknown/malformed are all zero.

Usage:
  status_vocab.py --audit [--json] [--report PATH] [--strict]

Exit codes: 0 clean; 1 findings; 2 environment error (nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

CANONICAL = "CANONICAL"
ALIAS = "ALIAS"
HELD = "HELD"

R_CANONICAL = "status_canonical"
R_ALIAS = "status_alias"
R_AMBIGUOUS = "status_ambiguous_family"
R_UNKNOWN = "status_unknown"
R_MALFORMED = "status_malformed"

PREFIX = "status:"

# Ruling §11.1 — the canonical vocabulary, verbatim (128 values).
CANONICAL_VALUES: frozenset = frozenset("""
accepted accepted-risk accepted-with-conditions ack acknowledged action-needed
action-required active addressed advisory all-clear amended answered applied
approved approved-within-scope assigned attested attested-complete authorized
awaiting-approval awaiting-attestation awaiting-ratification awaiting-review
banked binding blocked blocked-infra blocked-needs-rebase blocked-needs-work
blocker carried changes-applied changes-requested closed closed-as-decided
closed-partial committed compliant confirmed corrected correction decided
decision-made deferred degraded design diagnosed discussion dismissed dispatched
done draft failed flagged goal-met guidance held hold hypothesis imminent
implementation-authorized implemented in-progress info information invalid
linked-not-mutated merged merging muted needs-answer needs-dba-ruling
needs-evidence needs-reconciliation needs-response needs-work nominal not-ready
open parked partial-open partially-remediated partially-resolved pass planned
posted pr-open principle proposal proposed proposed-implementation ratified
ratified-analyst-seat ratified-conditional ratified-core
ratified-with-conditions ratified-with-open-questions ready
ready-for-implementation ready-for-review ready-with-one-disclosure-item
reconciled refused registered resolved retired review-dispatched
review-requested review-required reviewed revised ruled ruled-not-authorized
ruling scope-complete signed-off stale-draft started superseded unblocked
unchanged unresolved validated verified verified-with-owed-items void working
""".split())

# Ruling §11.3.A — the only ratified collapses, with their kind.
ALIASES: Dict[str, tuple] = {
    "complete": ("done", "equivalence"),
    "completed": ("done", "equivalence"),
    "in_progress": ("in-progress", "format"),
    "inprogress": ("in-progress", "format"),
    "review-request": ("review-requested", "format"),
}

# Ruling §11.4 — ambiguous families: held, never inferred.
AMBIGUOUS: Dict[str, str] = {
    "conditional": "approval-conditional vs ratification-conditional",
    "disposition": "act of dispositioning vs resulting decision state vs record of it",
    "standing": "in effect (binding-like) vs still standing/open",
    "ok": "acknowledgement-OK vs operational-health OK; the token names no referent",
    "partial": "partial completion vs partial resolution vs partial approval",
    "review": "under review (in-progress-like) vs review requested/needed",
    "advanced": "advanced to what stage — the destination is unstated",
    "mixed": "mixed result (pass/fail) vs mixed scope — the referent is unstated",
}

REASON_DOC = {
    R_CANONICAL: "value is in the canonical vocabulary verbatim",
    R_ALIAS: "value is a ratified alias; canonical names its target",
    R_AMBIGUOUS: "value could belong to more than one family — held, never inferred",
    R_UNKNOWN: "value is not in the vocabulary — held",
    R_MALFORMED: "empty or whitespace-bearing value — held",
}


class StatusWriteRefused(ValueError):
    """A writer tried to emit a non-canonical status. Carries the reason code."""

    def __init__(self, value: str, reason_code: str):
        self.value = value
        self.reason_code = reason_code
        super().__init__(f"cannot write status:{value!r}: {reason_code} ({REASON_DOC[reason_code]})")


@dataclass(frozen=True)
class StatusClass:
    value: str
    verdict: str
    reason_code: str
    canonical: Optional[str] = None      # canonical "status:<x>" tag
    family: Optional[str] = None         # canonical name without prefix
    alias_kind: Optional[str] = None     # "format" | "equivalence" | None

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "verdict": self.verdict,
                "reason": self.reason_code, "canonical": self.canonical,
                "family": self.family, "alias_kind": self.alias_kind}


def status_values(tags: Iterable[str]) -> List[str]:
    return [str(t)[len(PREFIX):] for t in tags or () if str(t).startswith(PREFIX)]


def classify_status_value(value: Any) -> StatusClass:
    """Verdict for one status value, from the value itself and the ruling."""
    raw = "" if value is None else str(value)
    if raw == "" or raw != raw.strip() or any(ch.isspace() for ch in raw):
        return StatusClass(raw, HELD, R_MALFORMED)
    if raw in ALIASES:
        family, kind = ALIASES[raw]
        return StatusClass(raw, ALIAS, R_ALIAS, PREFIX + family, family, kind)
    if raw in AMBIGUOUS:
        return StatusClass(raw, HELD, R_AMBIGUOUS)
    if raw in CANONICAL_VALUES:
        return StatusClass(raw, CANONICAL, R_CANONICAL, PREFIX + raw, raw)
    return StatusClass(raw, HELD, R_UNKNOWN)


def classify_tag(tag: Any) -> Optional[StatusClass]:
    s = str(tag or "")
    if not s.startswith(PREFIX):
        return None
    return classify_status_value(s[len(PREFIX):])


def status_classes(tags: Iterable[str]) -> List[StatusClass]:
    return [classify_status_value(v) for v in status_values(tags)]


def canonical_status(tags: Iterable[str]) -> Optional[str]:
    """The row's single canonical status tag, or None (refuse, never choose).

    0 tags -> None; any held tag -> None; one distinct canonical target ->
    that tag; more than one target -> None (multiplicity is item 7's domain).
    """
    classes = status_classes(tags)
    if not classes:
        return None
    if any(c.verdict == HELD for c in classes):
        return None
    targets = {c.canonical for c in classes}
    if len(targets) != 1:
        return None
    return targets.pop()


def is_canonical_status(tag: Any) -> bool:
    c = classify_tag(tag)
    return c is not None and c.verdict == CANONICAL


def require_canonical_for_write(value: Any) -> str:
    """Return the canonical `status:<x>` a writer may emit, or raise.

    Normalize-on-write (ruling §11.2.3): new writes may only be canonical, so
    an alias or a held value raises with its exact reason code.
    """
    c = classify_status_value(value)
    if c.verdict != CANONICAL:
        raise StatusWriteRefused(c.value, c.reason_code)
    return PREFIX + c.value


# ── corpus audit (read-only) ──────────────────────────────────────────────

@dataclass
class AuditRow:
    id: str
    title: str
    tags: List[str]


@dataclass
class Audit:
    status_rows: int = 0
    occurrences: int = 0
    verdicts: Dict[str, int] = field(default_factory=lambda: {CANONICAL: 0, ALIAS: 0, HELD: 0})
    reasons: Dict[str, int] = field(default_factory=dict)
    distinct: Dict[str, StatusClass] = field(default_factory=dict)
    families: Dict[str, int] = field(default_factory=dict)
    held: List[Dict[str, str]] = field(default_factory=list)
    unknown: List[Dict[str, str]] = field(default_factory=list)
    malformed: List[Dict[str, str]] = field(default_factory=list)
    occurrences_report: List[Dict[str, str]] = field(default_factory=list)

    @property
    def unclassified(self) -> int:
        return self.occurrences - sum(self.verdicts.values())

    @property
    def ok(self) -> bool:
        return self.unclassified == 0 and not self.unknown and not self.malformed

    def summary(self, strict: bool = False) -> Dict[str, Any]:
        checks = {
            "zero_unclassified": self.unclassified == 0,
            "zero_unknown": not self.unknown,
            "zero_malformed": not self.malformed,
        }
        if strict:
            checks["zero_held"] = not self.held
        return {
            "status_rows": self.status_rows,
            "occurrences": self.occurrences,
            "distinct_values": len(self.distinct),
            "verdicts": dict(self.verdicts),
            "verdict_values": {
                CANONICAL: sum(1 for c in self.distinct.values() if c.verdict == CANONICAL),
                ALIAS: sum(1 for c in self.distinct.values() if c.verdict == ALIAS),
                HELD: sum(1 for c in self.distinct.values() if c.verdict == HELD),
            },
            "reasons": dict(sorted(self.reasons.items())),
            "families": dict(sorted(self.families.items(), key=lambda kv: (-kv[1], kv[0]))),
            "held": self.held,
            "unknown": self.unknown,
            "malformed": self.malformed,
            "unclassified": self.unclassified,
            "ok": self.ok and (not self.held if strict else True),
            "strict": strict,
            "checks": checks,
        }


def audit_rows(rows: Sequence[AuditRow], strict: bool = False) -> Audit:
    out = Audit()
    for row in rows:
        tags = list(row.tags or [])
        values = status_values(tags)
        if not values:
            continue
        out.status_rows += 1
        for value in values:
            c = classify_status_value(value)
            out.occurrences += 1
            out.distinct.setdefault(value, c)
            out.verdicts[c.verdict] = out.verdicts.get(c.verdict, 0) + 1
            out.reasons[c.reason_code] = out.reasons.get(c.reason_code, 0) + 1
            if c.family:
                out.families[c.family] = out.families.get(c.family, 0) + 1
            entry = {"id": row.id, "value": value, "reason": c.reason_code,
                     "canonical": c.canonical or "", "title": (row.title or "")[:80]}
            out.occurrences_report.append(entry)
            if c.reason_code == R_UNKNOWN:
                out.unknown.append(entry)
            elif c.reason_code == R_MALFORMED:
                out.malformed.append(entry)
            elif c.verdict == HELD:
                out.held.append(entry)
    out.verdicts.setdefault(CANONICAL, 0)
    out.verdicts.setdefault(ALIAS, 0)
    out.verdicts.setdefault(HELD, 0)
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
            select id::text, coalesce(title,''), coalesce(tags, '{}'::text[])
              from nebula.agent_records
             where exists (select 1 from unnest(tags) t where t like 'status:%')
             order by created_at asc
            """
        )
        return [AuditRow(r[0], r[1], list(r[2] or [])) for r in cur.fetchall()]


def report_tsv(audit: Audit) -> str:
    lines = ["record_id\traw_status_tag\tverdict\tcanonical\treason\ttitle"]
    for e in audit.occurrences_report:
        verdict = "HELD" if e["reason"] in (R_AMBIGUOUS, R_UNKNOWN, R_MALFORMED) else (
            "ALIAS" if e["reason"] == R_ALIAS else "CANONICAL")
        lines.append("\t".join([e["id"], PREFIX + e["value"], verdict,
                                e["canonical"], e["reason"], e["title"]]))
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows) -> int:
    ap = argparse.ArgumentParser(description="Audit status: value classification (ruling 963501f2).")
    ap.add_argument("--audit", action="store_true", help="read the corpus and report (default)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", metavar="PATH")
    ap.add_argument("--strict", action="store_true", help="treat held values as findings")
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        rows = load(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    audit = audit_rows(rows, args.strict)
    summary = audit.summary(args.strict)
    if args.report:
        Path(args.report).write_text(report_tsv(audit), encoding="utf-8")
        summary["report"] = args.report
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("status vocabulary audit — " + ("CLEAN" if summary["ok"] else "FINDINGS"))
        print(f"status rows: {audit.status_rows}  occurrences: {audit.occurrences}  "
              f"distinct: {len(audit.distinct)}")
        print(f"  values: canonical {summary['verdict_values'][CANONICAL]}, "
              f"alias {summary['verdict_values'][ALIAS]}, held {summary['verdict_values'][HELD]}")
        print(f"  occurrences: canonical {audit.verdicts[CANONICAL]}, "
              f"alias {audit.verdicts[ALIAS]}, held {audit.verdicts[HELD]}")
        for reason, count in sorted(audit.reasons.items()):
            print(f"  {reason:<24} {count}")
        if audit.held:
            print(f"  held (named, not rewritten): {len(audit.held)}")
        if audit.unknown:
            print(f"  unknown: {len(audit.unknown)}", file=sys.stderr)
        if audit.malformed:
            print(f"  malformed: {len(audit.malformed)}", file=sys.stderr)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
