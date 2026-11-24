#!/usr/bin/env python3
"""gate_shape.py — one classifier for `gate:<value>`: PR, NAME, or HELD.

Ranking item 10 (architect ruling 4963dee4, on top of 086cc2d1 / 0633f18d).
`gate:` carries two kinds of value: a PR number and a gate name. Until now each
writer re-derived that split from its own inline `^\\d+$` regex, so the shape
was inferred at the point of writing. This module is the single source of
truth: every writer asks it, and it never guesses.

Canonical shape rule (ruling 4963dee4, fork 1/2):

  gate_shape_pr         value is a FULL `^[0-9]+$` match -> PR number.
                        Leading zeros do not change the shape (`gate:0830` is
                        PR 830); a numeric *prefix* does (`830-scope` is a NAME).
  gate_shape_name       any other clean non-empty token -> gate NAME; the
                        canonical slug is lower(value).
  gate_shape_empty      value is "" -> HELD.
  gate_shape_malformed  value carries whitespace -> HELD.
  gate_shape_conflict   the same row also carries `gate-name:<value>`, so the
                        row claims that value is BOTH a PR and a name -> HELD.

A HELD verdict is the classifier refusing to decide. Writers must skip a held
row and report its reason code; they must never add `pr:` or `gate-name:` to
make the decision for it. Read-only callers may resolve a non-PR value to
"no PR" (None) and that is the refusal they need.

Nothing here writes. The `--audit` command only reads the corpus and reports
counts per verdict/reason plus the corpus invariants item 10 must keep.

Usage:
  gate_shape.py --audit            # counts + invariants, exit 0 when clean
  gate_shape.py --audit --json
  gate_shape.py --audit --report PATH

Exit codes: 0 audit clean; 1 a shape hold / orphan / numeric slug exists, or a
gate value is unclassified; 2 environment error (nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

PR = "PR"
NAME = "NAME"
HELD = "HELD"

PR_SHAPE = re.compile(r"^[0-9]+$")

GATE_PREFIX = "gate:"
NAME_PREFIX = "gate-name:"

# Exact reason-code vocabulary fixed by ruling 4963dee4 (fork 6).
R_PR = "gate_shape_pr"
R_NAME = "gate_shape_name"
R_EMPTY = "gate_shape_empty"
R_MALFORMED = "gate_shape_malformed"
R_CONFLICT = "gate_shape_conflict"

SHAPE_HOLDS = (R_EMPTY, R_MALFORMED, R_CONFLICT)

REASON_DOC = {
    R_PR: "full ^[0-9]+$ value: the gate's PR number",
    R_NAME: "clean non-numeric value: a gate name; canonical slug = lower(value)",
    R_EMPTY: "empty gate: value — held, never guessed",
    R_MALFORMED: "gate: value carries whitespace — held, never guessed",
    R_CONFLICT: "row also carries gate-name:<value>: the value is claimed as both "
                "PR and name — held, never resolved by precedence",
}


@dataclass(frozen=True)
class GateShape:
    """The verdict for one `gate:<value>` token."""

    value: str
    verdict: str
    reason_code: str
    slug: Optional[str] = None

    @property
    def is_pr(self) -> bool:
        return self.verdict == PR

    @property
    def is_name(self) -> bool:
        return self.verdict == NAME

    @property
    def is_held(self) -> bool:
        return self.verdict == HELD

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "verdict": self.verdict,
                "reason_code": self.reason_code, "slug": self.slug}


def classify_gate_value(value: Any) -> GateShape:
    """Shape of one gate value, from the value itself and nothing else."""
    raw = "" if value is None else str(value)
    if raw == "":
        return GateShape(raw, HELD, R_EMPTY)
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        return GateShape(raw, HELD, R_MALFORMED)
    if PR_SHAPE.match(raw):
        return GateShape(raw, PR, R_PR)
    return GateShape(raw, NAME, R_NAME, raw.lower())


def classify_row_gate(value: Any, tags: Sequence[str]) -> GateShape:
    """Row-aware verdict: a value the row also claims as a name is HELD.

    `gate:830` plus `gate-name:830` on one row says both "PR 830" and "gate
    named 830". Picking one would be a vocabulary decision, so the classifier
    holds the value (ruling 4963dee4, fork 3) and no `pr:` may be fabricated.
    """
    shape = classify_gate_value(value)
    if shape.is_pr and (NAME_PREFIX + shape.value) in set(tags or ()):
        return GateShape(shape.value, HELD, R_CONFLICT, None)
    return shape


def gate_values(tags: Iterable[str]) -> List[str]:
    return [str(t)[len(GATE_PREFIX):] for t in tags or ()
            if str(t).startswith(GATE_PREFIX)]


def gate_name_slugs(tags: Iterable[str]) -> List[str]:
    return [str(t)[len(NAME_PREFIX):] for t in tags or ()
            if str(t).startswith(NAME_PREFIX)]


def classify_row(tags: Sequence[str]) -> List[GateShape]:
    """Every gate value on the row, classified in tag order."""
    return [classify_row_gate(v, tags) for v in gate_values(tags)]


def resolve_gate_pr(value: Any, tags: Sequence[str] = ()) -> Tuple[Optional[int], str]:
    """Single-value caller: (PR number, reason) — never a guess.

    Returns the int for a PR-shaped value and `(None, reason_code)` for a NAME
    or a held value, so a caller need not re-derive shape or coerce a name.
    """
    shape = classify_row_gate(value, tags)
    if shape.is_pr:
        return int(shape.value), shape.reason_code
    return None, shape.reason_code


# ── corpus audit (read-only) ──────────────────────────────────────────────

@dataclass
class AuditRow:
    id: str
    title: str
    tags: List[str]


@dataclass
class Audit:
    gate_rows: int = 0
    gate_occurrences: int = 0
    verdicts: Dict[str, int] = field(default_factory=lambda: {PR: 0, NAME: 0, HELD: 0})
    reasons: Dict[str, int] = field(default_factory=dict)
    held_rows: List[Dict[str, str]] = field(default_factory=list)
    named_without_slug: List[Dict[str, str]] = field(default_factory=list)
    numeric_without_pr: List[Dict[str, str]] = field(default_factory=list)
    orphan_gate_names: List[Dict[str, str]] = field(default_factory=list)
    numeric_name_slugs: List[Dict[str, str]] = field(default_factory=list)

    @property
    def unclassified(self) -> int:
        """Gate values with no verdict at all — always 0 by construction."""
        return max(0, self.gate_occurrences - sum(self.verdicts.values()))

    @property
    def shape_holds(self) -> int:
        return sum(self.reasons.get(r, 0) for r in SHAPE_HOLDS)

    @property
    def ok(self) -> bool:
        return (self.unclassified == 0 and self.shape_holds == 0
                and not self.orphan_gate_names and not self.numeric_name_slugs)

    def summary(self) -> Dict[str, Any]:
        return {
            "gate_rows": self.gate_rows,
            "gate_occurrences": self.gate_occurrences,
            "verdicts": dict(self.verdicts),
            "reasons": dict(sorted(self.reasons.items())),
            "unclassified_values": self.unclassified,
            "shape_holds": self.shape_holds,
            "held_rows": len(self.held_rows),
            "named_without_slug": len(self.named_without_slug),
            "numeric_without_pr": len(self.numeric_without_pr),
            "orphan_gate_names": len(self.orphan_gate_names),
            "numeric_name_slugs": len(self.numeric_name_slugs),
            "ok": self.ok,
            "checks": {
                "zero_unclassified": self.unclassified == 0,
                "zero_shape_holds": self.shape_holds == 0,
                "zero_orphan_gate_names": not self.orphan_gate_names,
                "zero_numeric_name_slugs": not self.numeric_name_slugs,
            },
        }


def audit_rows(rows: Sequence[AuditRow]) -> Audit:
    out = Audit()
    for row in rows:
        tags = list(row.tags or [])
        values = gate_values(tags)
        if not values:
            continue
        out.gate_rows += 1
        shapes = [classify_row_gate(v, tags) for v in values]
        out.gate_occurrences += len(shapes)
        slugs = set(gate_name_slugs(tags))
        prs = {str(t)[3:] for t in tags if str(t).startswith("pr:")}
        pr_ints = {int(p) for p in prs if PR_SHAPE.match(p or "")}
        for shape in shapes:
            out.verdicts[shape.verdict] = out.verdicts.get(shape.verdict, 0) + 1
            out.reasons[shape.reason_code] = out.reasons.get(shape.reason_code, 0) + 1
            if shape.is_held:
                out.held_rows.append({"id": row.id, "value": shape.value,
                                      "reason": shape.reason_code,
                                      "title": (row.title or "")[:80]})
            elif shape.is_name and shape.slug not in slugs:
                out.named_without_slug.append({"id": row.id, "value": shape.value,
                                               "slug": shape.slug or "",
                                               "title": (row.title or "")[:80]})
            elif shape.is_pr and int(shape.value) not in pr_ints:
                out.numeric_without_pr.append({"id": row.id, "value": shape.value,
                                               "title": (row.title or "")[:80]})
        for slug in gate_name_slugs(tags):
            if PR_SHAPE.match(slug):
                out.numeric_name_slugs.append({"id": row.id, "slug": slug,
                                               "title": (row.title or "")[:80]})
        legacy = {v.lower() for v in values}
        for slug in gate_name_slugs(tags):
            if PR_SHAPE.match(slug):
                continue  # reported as numeric_name_slug, one class per fact
            if slug.lower() not in legacy:
                out.orphan_gate_names.append({"id": row.id, "slug": slug,
                                              "title": (row.title or "")[:80]})
    out.verdicts.setdefault(PR, 0)
    out.verdicts.setdefault(NAME, 0)
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
             where exists (select 1 from unnest(tags) t where t like 'gate:%')
                or exists (select 1 from unnest(tags) t where t like 'gate-name:%')
             order by created_at asc
            """
        )
        return [AuditRow(r[0], r[1], list(r[2] or [])) for r in cur.fetchall()]


def report_tsv(audit: Audit) -> str:
    lines = ["record_id\tvalue\tverdict_or_class\treason\ttitle"]
    for held in audit.held_rows:
        lines.append(f"{held['id']}\t{held['value']}\tHELD\t{held['reason']}\t{held['title']}")
    for item in audit.named_without_slug:
        lines.append(f"{item['id']}\t{item['value']}\tNAME\tnamed_without_slug\t{item['title']}")
    for item in audit.numeric_without_pr:
        lines.append(f"{item['id']}\t{item['value']}\tPR\tnumeric_without_pr\t{item['title']}")
    for item in audit.orphan_gate_names:
        lines.append(f"{item['id']}\t{item['slug']}\tgate-name\torphan_gate_name\t{item['title']}")
    for item in audit.numeric_name_slugs:
        lines.append(f"{item['id']}\t{item['slug']}\tgate-name\tnumeric_name_slug\t{item['title']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows) -> int:
    ap = argparse.ArgumentParser(description="Audit gate:<value> shape classification.")
    ap.add_argument("--audit", action="store_true", help="read the corpus and report (default)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", metavar="PATH")
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        rows = load(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    audit = audit_rows(rows)
    summary = audit.summary()
    if args.report:
        Path(args.report).write_text(report_tsv(audit), encoding="utf-8")
        summary["report"] = args.report
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("gate shape audit — " + ("CLEAN" if audit.ok else "FINDINGS"))
        print(f"gate rows: {audit.gate_rows}  values: {audit.gate_occurrences}  "
              f"PR: {audit.verdicts.get(PR, 0)}  NAME: {audit.verdicts.get(NAME, 0)}  "
              f"HELD: {audit.verdicts.get(HELD, 0)}")
        for reason, count in sorted(audit.reasons.items()):
            print(f"  {reason:<22} {count}")
        print(f"  named without gate-name   {len(audit.named_without_slug)}")
        print(f"  numeric without pr:       {len(audit.numeric_without_pr)}")
        print(f"  orphan gate-name tags:    {len(audit.orphan_gate_names)}")
        print(f"  numeric gate-name slugs:  {len(audit.numeric_name_slugs)}")
        for held in audit.held_rows:
            print(f"  HELD {held['id'][:8]} gate:{held['value']} "
                  f"({held['reason']}) — {held['title']}", file=sys.stderr)
    return 0 if audit.ok else 1


if __name__ == "__main__":
    sys.exit(main())
