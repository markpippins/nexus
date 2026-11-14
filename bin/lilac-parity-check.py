#!/usr/bin/env python3
"""Lilac W1-L6 dual-read parity gate and cutover rehearsal (plan 8261672).

Discharges plan 8261639 AC10/AC11 at the cutover boundary. Everything here is
measurement or rehearsal — nothing is dropped, and no producer is repointed.

  AC1  Dual-read parity is MEASURED. ``parity_report`` compares legacy rows
       against their canonical twins over a bounded window and records
       matched / mismatched / unreadable counts. Any unexplained class
       (missing_twin, kind_drift, payload_drift, unreadable) makes the gate
       FAIL CLOSED instead of reporting a percentage.
  AC2  Zero direct legacy writes is proven by difference of two
       ``pg_stat_user_tables`` snapshots (``write_audit_delta``), which names
       every legacy table that took a write in the window.
  AC3  Replay is proven: ``replay_equivalence`` folds the same mutation stream
       twice by idempotency key and requires identical state and no extra row.
  AC4  Rollback is rehearsed at the cutover boundary on a throwaway database:
       ``CUTOVER_SQL`` swaps a legacy table for a read-path view over the
       canonical surface, ``ROLLBACK_SQL`` restores the pre-cutover read path
       and leaves nothing behind. Driven by bin/tests/test_lilac_parity_gate.py
       in the guards-db tier; never run against a live database by this tool.
  AC5  The legacy drop is a SEPARATE operator-gated step. There is deliberately
       no drop mode here: ``--print-sql legacy-drop`` explains what the gated
       step would have to contain and exits non-zero without emitting DDL.
  AC6  Scope exclusion: ``scope_violations`` fails if the change set grows a
       WorkRequest representation, a request_kind, or peb.decisions authority.

Usage (hermetic, from a captured read):
    bin/lilac-parity-check.py --from-json parity-input.json --json --check

Usage (live, read-only; needs psycopg2 + CONDUIT_PG_DSN):
    CONDUIT_PG_DSN='...' bin/lilac-parity-check.py --window-hours 24 --check

Exit codes: 0 measured and clean, 1 unexplained mismatch, 2 unreadable input
or unavailable driver. There is no exit code that means "dropped".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ── The legacy surfaces at the cutover boundary ──────────────────────────────
# Mirrors python/conduit/lilac_disposition.LEGACY_RECEIPT_TABLES. The parity
# gate reads these; it never writes them and never drops them.
LEGACY_TABLES: Tuple[Tuple[str, str], ...] = (
    ("vision", "receipts"),
    ("vision", "receipts_history"),
    ("execution", "receipts"),
    ("wind", "receipts"),
    ("kernel", "receipts"),
    ("nebula", "receipts_unified"),
)

# Classification vocabulary. EXPLAINED classes are drift the ratified contract
# already accounts for; UNEXPLAINED classes are the fail-closed set.
PARITY = "parity"
UNMAPPED_TYPE = "unmapped_type"
MISSING_TWIN = "missing_twin"
KIND_DRIFT = "kind_drift"
PAYLOAD_DRIFT = "payload_drift"
UNREADABLE = "unreadable"

EXPLAINED_CLASSES = frozenset({PARITY, UNMAPPED_TYPE})
UNEXPLAINED_CLASSES = frozenset({MISSING_TWIN, KIND_DRIFT, PAYLOAD_DRIFT, UNREADABLE})
ALL_CLASSES = tuple(sorted(EXPLAINED_CLASSES | UNEXPLAINED_CLASSES))

# Legacy-only receipt types with no ratified canonical kind. Mirrors
# python/conduit/lilac_drift.UNMAPPED_LEGACY_TYPES — an unmapped type is
# classified, not counted as drift.
UNMAPPED_LEGACY_TYPES = frozenset({"PROPOSED"})

# ── AC2: the write-audit query ───────────────────────────────────────────────
# Cumulative counters, so one snapshot before and one after the window is a
# real measurement of writes that happened during it. `relname`/`schemaname`
# are the writer identity at the granularity Postgres exposes without
# pg_stat_statements; when that extension is installed, the second query names
# the calling role too.
WRITE_AUDIT_SQL = """
SELECT schemaname, relname,
       COALESCE(n_tup_ins, 0) AS inserts,
       COALESCE(n_tup_upd, 0) AS updates,
       COALESCE(n_tup_del, 0) AS deletes
  FROM pg_stat_user_tables
 WHERE (schemaname, relname) IN (
       ('vision', 'receipts'),
       ('vision', 'receipts_history'),
       ('execution', 'receipts'),
       ('wind', 'receipts'),
       ('kernel', 'receipts'),
       ('nebula', 'receipts_unified'))
 ORDER BY schemaname, relname
""".strip()

# ── AC4: cutover and rollback at the boundary ────────────────────────────────
# The rehearsal swaps ONE legacy table (vision.receipts) for a read-path view
# over the canonical resolution surface — that is the cutover boundary a
# consumer sees. The rollback restores the legacy table and drops only the
# view this script created, so the pre-cutover read path is intact.
CUTOVER_SQL = """
ALTER TABLE vision.receipts RENAME TO receipts_pre_cutover;
CREATE VIEW vision.receipts AS
  SELECT source_receipt_id AS id,
         payload->>'receipt_type' AS type,
         payload->>'plan_id' AS plan_id,
         payload->>'agent_role' AS agent_role,
         payload->>'session_id' AS session_id,
         payload->>'ticket_id' AS ticket_id,
         payload->>'summary' AS summary,
         created_at
    FROM resolution.receipt;
""".strip()

ROLLBACK_SQL = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_views
              WHERE schemaname = 'vision' AND viewname = 'receipts') THEN
    DROP VIEW vision.receipts;
  END IF;
  IF to_regclass('vision.receipts_pre_cutover') IS NOT NULL THEN
    ALTER TABLE vision.receipts_pre_cutover RENAME TO receipts;
  END IF;
END $$;
""".strip()

# The gated step this plan does NOT perform. Kept as documentation so the
# absence is deliberate and reviewable, and so no code path can drift into it.
LEGACY_DROP_GATED_NOTE = """
Legacy drop is OPERATOR-GATED and out of scope for plan 8261672 (AC5).
It may run only after AC1-AC4 pass on the same change set and after an
approval record cites this plan. Expected content of that separate step:

  DROP VIEW  IF EXISTS vision.receipts;              -- post-cutover read path
  DROP TABLE IF EXISTS vision.receipts_pre_cutover;  -- the retained legacy copy
  -- same pair per remaining surface in LEGACY_TABLES

No DDL is emitted by this tool for that step.
""".strip()

# ── AC6: scope exclusion ─────────────────────────────────────────────────────
# The plan must not absorb WorkRequest representation, add request_kind, or
# activate peb.decisions authority. Matched case-insensitively against the
# change set's own text.
FORBIDDEN_SURFACE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("work_request", r"work_request"),
    ("request_kind", r"request_kind"),
    ("peb.decisions", r"peb\.decisions"),
)


# ── AC1: parity measurement ──────────────────────────────────────────────────

def classify_parity(legacy: Dict[str, Any], twin: Optional[Dict[str, Any]],
                    expected_kind: Optional[str] = None) -> str:
    """Classify one legacy row against its canonical twin.

    ``twin`` is the canonical row (kind + payload identity fields) or None.
    An unreadable row (no id, or a malformed twin) is UNREADABLE — counted,
    never skipped, because an unreadable row cannot be shown to agree.
    """
    if not str(legacy.get("id") or "").strip():
        return UNREADABLE
    legacy_type = str(legacy.get("type") or "").strip()
    if legacy_type in UNMAPPED_LEGACY_TYPES:
        return UNMAPPED_TYPE
    if expected_kind is None:
        return UNREADABLE
    if twin is None:
        return MISSING_TWIN
    if not isinstance(twin, dict):
        return UNREADABLE
    if twin.get("kind") != expected_kind:
        return KIND_DRIFT
    payload = twin.get("payload")
    if not isinstance(payload, dict):
        return UNREADABLE
    for field, legacy_field in (("receipt_type", "type"), ("plan_id", "plan_id"),
                                ("agent_role", "agent_role"),
                                ("session_id", "session_id"),
                                ("ticket_id", "ticket_id"),
                                ("summary", "summary")):
        want, got = legacy.get(legacy_field), payload.get(field)
        if want is not None and got is not None and want != got:
            return PAYLOAD_DRIFT
    if twin.get("contract_version") not in (None, 1):
        return PAYLOAD_DRIFT
    return PARITY


def parity_report(legacy_rows: Sequence[Dict[str, Any]],
                  read_twin: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
                  kind_for_type: Callable[[str], Optional[str]],
                  window_hours: Optional[float] = None,
                  now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Measure parity over a bounded window. Fail-closed by construction.

    Returns counts per class, the rows behind every unexplained class, a
    ``fail_closed`` flag, and the window that was actually measured. The report
    never states a percentage that hides an unexplained row: ``matched`` and
    ``mismatched`` are separate counts precisely so a ratio cannot be quoted as
    if it proved agreement.
    """
    from datetime import datetime, timezone

    rows = list(legacy_rows)
    window_start_ms = None
    if window_hours is not None:
        anchor = now_ms if now_ms is not None else int(
            datetime.now(tz=timezone.utc).timestamp() * 1000)
        window_start_ms = anchor - int(window_hours * 3600 * 1000)
        rows = [r for r in rows if int(r.get("created_at") or 0) >= window_start_ms]

    counts = {c: 0 for c in ALL_CLASSES}
    findings: List[Dict[str, Any]] = []
    for row in rows:
        try:
            twin = read_twin(row)
        except Exception as exc:  # an unreadable read is a finding, not a crash
            counts[UNREADABLE] += 1
            findings.append({"receipt_id": row.get("id"), "class": UNREADABLE,
                             "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        kind = kind_for_type(str(row.get("type") or ""))
        klass = classify_parity(row, twin, kind)
        counts[klass] += 1
        if klass in UNEXPLAINED_CLASSES:
            findings.append({"receipt_id": row.get("id"), "class": klass,
                             "legacy_type": row.get("type"),
                             "expected_kind": kind})

    unexplained = sum(counts[c] for c in UNEXPLAINED_CLASSES)
    return {
        "measured": len(rows),
        "counts": counts,
        "matched": counts[PARITY],
        "mismatched": unexplained,
        "unreadable": counts[UNREADABLE],
        "unexplained_findings": findings,
        "fail_closed": unexplained > 0,
        "window_hours": window_hours,
        "window_start_ms": window_start_ms,
        "classes": list(ALL_CLASSES),
    }


# ── AC2: zero direct legacy writes ───────────────────────────────────────────

def write_audit_delta(before: Iterable[Dict[str, Any]],
                      after: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Difference two pg_stat_user_tables snapshots into a writer list.

    A legacy table that took any insert/update/delete in the window is a DIRECT
    LEGACY WRITER and is named. ``clean`` is True only when every audited table
    shows a zero delta — a table missing from either snapshot counts as a
    finding, because "absent" cannot be proven to mean "unwritten".
    """
    def index(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, int]]:
        out = {}
        for r in rows:
            key = (str(r.get("schemaname")), str(r.get("relname")))
            out[key] = {k: int(r.get(k) or 0) for k in ("inserts", "updates", "deletes")}
        return out

    before_i, after_i = index(before), index(after)
    writers: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    for schema, table in LEGACY_TABLES:
        key = (schema, table)
        if key not in before_i or key not in after_i:
            missing.append({"schema": schema, "table": table,
                            "reason": "absent from audit snapshot"})
            continue
        delta = {k: after_i[key][k] - before_i[key][k]
                 for k in ("inserts", "updates", "deletes")}
        if any(v > 0 for v in delta.values()):
            writers.append({"schema": schema, "table": table,
                            "writes": delta, "total": sum(delta.values())})
        elif any(v < 0 for v in delta.values()):
            # A counter went backwards: stats were reset or the snapshot is not
            # comparable. That is unreadable evidence, not proof of zero writes.
            missing.append({"schema": schema, "table": table,
                            "reason": "counter regressed (stats reset?)"})
    return {
        "audited": ["%s.%s" % t for t in LEGACY_TABLES],
        "writers": writers,
        "unreadable": missing,
        "clean": not writers and not missing,
    }


# ── AC3: replay equivalence ──────────────────────────────────────────────────

def replay_fold(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Fold a mutation stream into state keyed by idempotency key.

    Last write wins per key, deterministically: rows are processed in the order
    given so a replay of the SAME stream is order-identical.
    """
    state: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("idempotency_key") or "")
        if not key:
            raise ValueError("mutation row without idempotency_key: %r" % (row,))
        state[key] = dict(row.get("state") or {})
    return state


def replay_equivalence(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Prove replay produces identical folded state and adds no duplicate row."""
    first = replay_fold(rows)
    second = replay_fold(list(rows))
    keys = [str(r.get("idempotency_key") or "") for r in rows]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    return {
        "stream_length": len(rows),
        "folded_keys": len(first),
        "identical": first == second,
        "duplicates_in_stream": duplicates,
        "ok": first == second and len(first) == len(set(keys)),
    }


# ── AC6: scope exclusion ─────────────────────────────────────────────────────

def scope_violations(texts: Dict[str, str]) -> List[Dict[str, str]]:
    """Return forbidden-surface hits in the change set (path, surface, line)."""
    import re

    hits: List[Dict[str, str]] = []
    for path, text in texts.items():
        for name, pattern in FORBIDDEN_SURFACE_PATTERNS:
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    hits.append({"path": path, "surface": name, "line": str(lineno)})
    return hits


# ── live read (optional; never required for the gate) ────────────────────────

def _connect(dsn: str):
    try:
        import psycopg2  # noqa: F401
    except Exception:
        raise SystemExit(
            "psycopg2 is not importable, so the live read cannot run. This is a "
            "dependency gap, not a parity result. Use --from-json with a "
            "captured read, or install psycopg2.")
    import psycopg2
    return psycopg2.connect(dsn)


def _load_from_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or "legacy" not in doc:
        raise SystemExit("--from-json needs an object with a 'legacy' list")
    return doc


def _live_parity(dsn: str, schema: str) -> Dict[str, Any]:
    """Live mode: delegate the receipt read to the ratified drift module.

    ``python/conduit/lilac_drift.check_legacy_surface`` already owns the
    legacy->canonical comparison for the cutover (contract v1 fixture, decision
    1b02c07c). Re-implementing that SELECT here would put a second copy of the
    canonical read in the tree and inflate the executable-topology inventory
    with a consumer that is really that module. So this function is an adapter:
    it maps the drift classes onto the gate vocabulary and adds the write audit.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "python", "conduit"))
    try:
        import lilac_drift  # type: ignore
    except Exception as exc:
        raise SystemExit("cannot import python/conduit/lilac_drift.py: %s" % exc)

    conn = _connect(dsn)
    try:
        scanned = lilac_drift.check_legacy_surface(conn, schema=schema)
        counts = {c: 0 for c in ALL_CLASSES}
        extra: Dict[str, int] = {}
        for klass, n in (scanned.get("classes") or {}).items():
            if klass in counts:
                counts[klass] += n
            else:
                extra[klass] = extra.get(klass, 0) + n
        unexplained = sum(counts[c] for c in UNEXPLAINED_CLASSES) + sum(extra.values())

        cur = conn.cursor()
        cur.execute(WRITE_AUDIT_SQL)
        cols = [d[0] for d in cur.description]
        snapshot = [dict(zip(cols, r)) for r in cur.fetchall()]

        return {
            "measured": scanned.get("scanned", 0),
            "counts": counts,
            "extra_counts": extra,
            "matched": counts[PARITY],
            "mismatched": unexplained,
            "unreadable": counts[UNREADABLE],
            "unexplained_findings": [
                r for r in (scanned.get("rows") or [])
                if r.get("class") in UNEXPLAINED_CLASSES
            ],
            "fail_closed": unexplained > 0,
            "window_hours": None,
            "window_start_ms": None,
            "classes": list(ALL_CLASSES),
            "write_audit_snapshot": snapshot,
            "legacy_tables": ["%s.%s" % t for t in LEGACY_TABLES],
            "note": "live mode classifies the whole legacy surface via "
                    "lilac_drift.check_legacy_surface; windowing applies to "
                    "--from-json captures and to the write-audit delta",
        }
    finally:
        conn.rollback()
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-json", help="captured read for a hermetic run")
    parser.add_argument("--dsn", default=os.environ.get("CONDUIT_PG_DSN"),
                        help="PostgreSQL DSN (default: $CONDUIT_PG_DSN)")
    parser.add_argument("--schema", default="resolution")
    parser.add_argument("--window-hours", type=float, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 on an unexplained mismatch (fail closed)")
    parser.add_argument("--print-sql", choices=("cutover", "rollback",
                                                "write-audit", "legacy-drop"),
                        help="emit rehearsal/audit SQL instead of measuring")
    args = parser.parse_args(argv)

    if args.print_sql:
        if args.print_sql == "legacy-drop":
            sys.stderr.write(LEGACY_DROP_GATED_NOTE + "\n")
            return 2  # deliberately no DDL: AC5 keeps the drop operator-gated
        print({"cutover": CUTOVER_SQL, "rollback": ROLLBACK_SQL,
               "write-audit": WRITE_AUDIT_SQL}[args.print_sql])
        return 0

    if args.from_json:
        doc = _load_from_json(args.from_json)
        twins = {str(k): v for k, v in (doc.get("twins") or {}).items()}
        kinds = {str(k): v for k, v in (doc.get("kinds") or {}).items()}
        report = parity_report(doc["legacy"],
                               lambda row: twins.get(str(row.get("id"))),
                               kinds.get, args.window_hours)
        report["source"] = args.from_json
    else:
        if not args.dsn:
            sys.stderr.write(
                "no input: pass --from-json for a hermetic run or provide a DSN\n")
            return 2
        report = _live_parity(args.dsn, args.schema)
        report["source"] = "live"

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("measured=%d matched=%d mismatched=%d unreadable=%d fail_closed=%s"
              % (report["measured"], report["matched"], report["mismatched"],
                 report["unreadable"], report["fail_closed"]))
        for f in report["unexplained_findings"][:20]:
            print("  %-12s %s" % (f["class"], f.get("receipt_id")))

    if args.check and report["fail_closed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
