#!/usr/bin/env python3
"""Read-only verifier for the ratified WorkRequest disposition ledger.

The Planner ratified a 46-row amendment on 2026-11-12
(record 97b650f0-5398-428d-91ea-67c122f3164b) and the Architect approved the
surface and this verifier's contract (record 64e1aa2b-1af1-49d0-a1ab-8b997ba8be87).

What this verifies
------------------
1. The ratified artifact is internally coherent: 46 unique rows, the ratified
   verdict distribution (COMPILE 1 / KEEP-COMPLETED 2 / MERGE 8 / PROMOTE 12 /
   RETIRE 23), every MERGE carries a canonical target, every non-MERGE target
   is null, and every target exists with a PROMOTE verdict.
2. The artifact still matches the live table: all 46 ids exist, business
   statuses and plan mappings are unchanged, and no `plan_id` backfill has
   happened (it is deferred to the admin-gated apply step).
3. The live queue is still correctly described: pending plan 8261650 projects
   through `6217db7f`, and pending plan 8261668 is reported as the known gap
   (it has no WorkRequest).

What this must never do
-----------------------
No INSERT/UPDATE/DELETE, no DDL, no `business_status` or `plan_id` mutation,
no backfill, and no write to the artifact. The database session is opened
read-only; the only statements are SELECTs.

Exit codes
----------
0  verified (warnings are reported but do not fail unless --strict)
1  verification failure
2  usage or database/tool error

Usage
-----
    python3 bin/verify-workrequest-disposition.py
    python3 bin/verify-workrequest-disposition.py --json
    python3 bin/verify-workrequest-disposition.py --strict
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO / "docs" / "workrequest-disposition-amendment.json"

ARTIFACT = "work_request_disposition_ledger"
VERSION = 2
SUPERSEDES = "39443dee-5787-4dc7-a450-1bedc218a08e"
EXPECTED_TOTAL = 46
EXPECTED_COUNTS = {
    "COMPILE": 1,
    "KEEP-COMPLETED": 2,
    "MERGE": 8,
    "PROMOTE": 12,
    "RETIRE": 23,
}
EXPECTED_STATUS = {"DRAFT": 30, "CANCELLED": 14, "COMPLETED": 2}
EXPECTED_PLAN_STATUS = {"completed": 30, "archived": 4, "pending": 1, "": 11}
VERDICTS = frozenset(EXPECTED_COUNTS)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Read-only by construction. Do not add a mutating statement here.
SQL_LIVE = """
SELECT w.id::text,
       w.business_status,
       COALESCE(w.legacy_id, ''),
       COALESCE(substring(w.legacy_id from 'wr-([0-9]+)'), ''),
       COALESCE(p.status, ''),
       COALESCE(w.plan_id::text, '')
  FROM resolution.work_request w
  LEFT JOIN nebula.implementation_plans p
    ON p.plan_number = substring(w.legacy_id from 'wr-([0-9]+)')
 ORDER BY w.id
"""
SQL_PENDING_WITHOUT_WR = """
SELECT p.plan_number
  FROM nebula.implementation_plans p
 WHERE p.status = 'pending'
   AND NOT EXISTS (
       SELECT 1
         FROM resolution.work_request w
        WHERE substring(w.legacy_id from 'wr-([0-9]+)') = p.plan_number
   )
 ORDER BY p.plan_number
"""


def load_ledger(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(UUID_RE.match(value))


def validate_artifact(doc: dict[str, Any]) -> list[str]:
    """Validate the ratified artifact without touching a database."""
    errors: list[str] = []

    if doc.get("artifact") != ARTIFACT:
        errors.append(f"artifact is {doc.get('artifact')!r}, expected {ARTIFACT!r}")
    if doc.get("version") != VERSION:
        errors.append(f"version is {doc.get('version')!r}, expected {VERSION}")
    if doc.get("status") != "ratified":
        errors.append(f"status is {doc.get('status')!r}, expected 'ratified'")
    if doc.get("supersedes") != SUPERSEDES:
        errors.append(
            f"supersedes is {doc.get('supersedes')!r}, expected {SUPERSEDES!r}"
        )

    ratified_by = doc.get("ratified_by") or {}
    for role in ("planner", "architect"):
        entry = ratified_by.get(role) or {}
        if not _is_uuid(entry.get("record_id")):
            errors.append(f"ratified_by.{role}.record_id is not a UUID")
    rules = doc.get("architecture_rules") or {}
    expected_rules = {
        "authority_model": "derived_read_model",
        "authoritative_surface": "nebula.implementation_plans",
        "apply_shape": "append_only_disposition_registry",
        "business_status_mutation": False,
        "merge_requires_target": True,
        "non_merge_target_must_be_null": True,
        "current_row_partial_unique_index": "WHERE supersedes_id IS NULL",
        "plan_id_backfill": "deferred_to_admin_gated_apply",
        "live_schema_data_application": "admin_gated_separate_step",
    }
    for key, want in expected_rules.items():
        if rules.get(key) != want:
            errors.append(f"architecture_rules.{key} is {rules.get(key)!r}, expected {want!r}")

    rows = doc.get("rows")
    if not isinstance(rows, list):
        return errors + ["rows is not a list"]
    if len(rows) != EXPECTED_TOTAL:
        errors.append(f"row count is {len(rows)}, expected {EXPECTED_TOTAL}")

    ids: list[str] = []
    verdicts: collections.Counter[str] = collections.Counter()
    statuses: collections.Counter[str] = collections.Counter()
    plan_statuses: collections.Counter[str] = collections.Counter()
    by_id: dict[str, dict[str, Any]] = {}

    for index, row in enumerate(rows):
        label = f"row[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} is not an object")
            continue
        rid = row.get("row_id")
        if not _is_uuid(rid):
            errors.append(f"{label}.row_id is not a UUID: {rid!r}")
            continue
        label = f"row {rid[:8]}"
        ids.append(rid)
        by_id[rid] = row

        verdict = row.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{label}: unknown verdict {verdict!r}")
        else:
            verdicts[verdict] += 1

        status = row.get("business_status")
        if not isinstance(status, str) or not status:
            errors.append(f"{label}: business_status is empty")
        else:
            statuses[status] += 1

        plan_status = row.get("plan_status") or ""
        plan_statuses[plan_status] += 1

        if not str(row.get("rationale") or "").strip():
            errors.append(f"{label}: rationale is empty")
        if not str(row.get("ledger_ref") or "").strip():
            errors.append(f"{label}: ledger_ref is empty")

        target = row.get("target")
        if verdict == "MERGE":
            if not _is_uuid(target):
                errors.append(f"{label}: MERGE requires a UUID target")
            elif target == rid:
                errors.append(f"{label}: MERGE target is itself")
        elif target is not None:
            errors.append(f"{label}: non-MERGE verdict {verdict!r} must have a null target")

    duplicates = sorted(rid for rid, n in collections.Counter(ids).items() if n > 1)
    if duplicates:
        errors.append(f"duplicate row ids: {', '.join(d[:8] for d in duplicates)}")

    if len(ids) == EXPECTED_TOTAL and len(by_id) == EXPECTED_TOTAL:
        for rid, row in by_id.items():
            target = row.get("target")
            if row.get("verdict") != "MERGE":
                continue
            target_row = by_id.get(target) if isinstance(target, str) else None
            if target_row is None:
                errors.append(f"row {rid[:8]}: MERGE target {str(target)[:8]} is not in the ledger")
            elif target_row.get("verdict") != "PROMOTE":
                errors.append(
                    f"row {rid[:8]}: MERGE target {target[:8]} has verdict "
                    f"{target_row.get('verdict')!r}, expected PROMOTE"
                )

    if dict(verdicts) != EXPECTED_COUNTS:
        errors.append(f"verdict counts are {dict(verdicts)}, expected {EXPECTED_COUNTS}")
    if dict(statuses) != EXPECTED_STATUS:
        errors.append(f"business status counts are {dict(statuses)}, expected {EXPECTED_STATUS}")
    if dict(plan_statuses) != EXPECTED_PLAN_STATUS:
        errors.append(
            f"plan status counts are {dict(plan_statuses)}, expected {EXPECTED_PLAN_STATUS}"
        )

    live = by_id.get("6217db7f-577d-4a6d-9708-61167279aa2e")
    if live is None:
        errors.append("the live queue row 6217db7f is missing")
    elif (live.get("plan_number"), live.get("plan_status")) != ("8261650", "pending"):
        errors.append("row 6217db7f must project pending plan 8261650")

    return errors


def live_rows(conn) -> list[dict[str, Any]]:
    """Read the live projection. SELECT-only; the session is set read-only by main."""
    with conn.cursor() as cur:
        cur.execute(SQL_LIVE)
        rows = cur.fetchall()
    return [
        {
            "row_id": r[0],
            "business_status": r[1],
            "legacy_id": r[2],
            "plan_number": r[3],
            "plan_status": r[4],
            "plan_id": r[5],
        }
        for r in rows
    ]


def pending_plans_without_wr(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(SQL_PENDING_WITHOUT_WR)
        return [r[0] for r in cur.fetchall()]


def validate_live(
    doc: dict[str, Any],
    rows: list[dict[str, Any]],
    pending_without_wr: list[str],
) -> tuple[list[str], list[str]]:
    """Compare the artifact to a live read. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    expected = {r["row_id"]: r for r in doc.get("rows", [])}
    actual = {r["row_id"]: r for r in rows}

    if len(rows) != EXPECTED_TOTAL:
        errors.append(f"live row count is {len(rows)}, expected {EXPECTED_TOTAL}")

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"live table is missing {len(missing)} row(s): {', '.join(x[:8] for x in missing)}")
    if extra:
        errors.append(f"live table has {len(extra)} row(s) not in the artifact: {', '.join(x[:8] for x in extra)}")

    statuses: collections.Counter[str] = collections.Counter()
    plan_statuses: collections.Counter[str] = collections.Counter()
    plan_ids: collections.Counter[str] = collections.Counter()
    for rid, row in actual.items():
        exp = expected.get(rid)
        if exp is None:
            continue
        statuses[row["business_status"]] += 1
        plan_statuses[row["plan_status"] or ""] += 1
        plan_ids[row["plan_id"] or ""] += 1
        if row["business_status"] != exp.get("business_status"):
            errors.append(
                f"row {rid[:8]}: business_status is {row['business_status']!r}, "
                f"artifact recorded {exp.get('business_status')!r}"
            )
        if (row["plan_number"] or "") != (exp.get("plan_number") or ""):
            errors.append(
                f"row {rid[:8]}: plan_number is {row['plan_number']!r}, "
                f"artifact recorded {exp.get('plan_number')!r}"
            )
        if (row["plan_status"] or "") != (exp.get("plan_status") or ""):
            errors.append(
                f"row {rid[:8]}: plan_status is {row['plan_status']!r}, "
                f"artifact recorded {exp.get('plan_status')!r}"
            )

    if len(rows) == EXPECTED_TOTAL and dict(statuses) != EXPECTED_STATUS:
        errors.append(f"live business status counts are {dict(statuses)}, expected {EXPECTED_STATUS}")
    if len(rows) == EXPECTED_TOTAL and dict(plan_statuses) != EXPECTED_PLAN_STATUS:
        errors.append(f"live plan status counts are {dict(plan_statuses)}, expected {EXPECTED_PLAN_STATUS}")

    backfilled = sum(n for value, n in plan_ids.items() if value)
    if backfilled:
        warnings.append(
            f"plan_id is populated on {backfilled}/{len(rows)} row(s); the ratified "
            "snapshot expects 0/46 and the backfill is a separate admin-gated step"
        )
    else:
        warnings.append("plan_id is still 0/46; the backfill remains deferred")

    if pending_without_wr:
        warnings.append(
            "pending plan(s) with no WorkRequest: " + ", ".join(pending_without_wr)
            + " — this is why the table is a derived read model, not the queue"
        )
    else:
        errors.append("no pending plan without a WorkRequest; the recorded 8261668 gap disappeared")

    live = actual.get("6217db7f-577d-4a6d-9708-61167279aa2e")
    if live is None or live.get("plan_status") != "pending":
        errors.append("row 6217db7f no longer projects a pending plan")

    return errors, warnings


def summarize(doc: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = collections.Counter(r["verdict"] for r in doc.get("rows", []))
    statuses = collections.Counter(r.get("business_status") for r in rows)
    plans = collections.Counter(r.get("plan_status") or "" for r in rows)
    return {
        "artifact_status": doc.get("status"),
        "total": len(rows),
        "verdicts": dict(sorted(verdicts.items())),
        "business_status": dict(sorted(statuses.items())),
        "plan_status": dict(sorted(plans.items())),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="ratified JSON ledger")
    ap.add_argument(
        "--database-url",
        default=os.environ.get("NEXUS_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus"),
        help="Postgres DSN (read-only session; SELECT only)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args(argv)

    try:
        doc = load_ledger(args.ledger)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 2

    errors = validate_artifact(doc)
    warnings: list[str] = []

    try:
        import psycopg2  # local import: artifact-only checks work without the driver
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"database driver unavailable: {exc}", file=sys.stderr)
        return 2

    conn = None
    try:
        conn = psycopg2.connect(args.database_url)
        # Belt and braces: both the session default and the connection option.
        conn.set_session(readonly=True, autocommit=True)
        rows = live_rows(conn)
        gaps = pending_plans_without_wr(conn)
    except Exception as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()

    live_errors, live_warnings = validate_live(doc, rows, gaps)
    errors.extend(live_errors)
    warnings.extend(live_warnings)

    if args.strict:
        errors.extend(warnings)

    result = {
        "ok": not errors,
        "ledger": args.ledger,
        "summary": summarize(doc, rows),
        "errors": errors,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        s = result["summary"]
        print(f"WorkRequest disposition verifier — {s['total']} rows; artifact {s['artifact_status']}")
        print(f"verdicts: {s['verdicts']}")
        print(f"business status: {s['business_status']}")
        print(f"plan status: {s['plan_status']}")
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in errors:
            print(f"FAIL: {error}")
        print("OK: ratified artifact matches live state" if not errors else f"FAILED: {len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
