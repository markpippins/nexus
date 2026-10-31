"""C4 historical backfill (Lilac plan 8261639) — time-boxed, idempotent, best-effort.

Per the ratified C4 ruling (decision e29eb6a1, folded into ratification
1b02c07c): historical import is a BEST-EFFORT backfill, else DISCARD; no
row-by-row reconciliation mandate; the existing audit trail is accepted as
the historical record.

What this pass does (receipts only — ticket dispositions are the C6 lane):

  For every vision.receipts row whose type has a ratified canonical kind
  (lilac_drift.KIND_BY_TYPE — the same list the V141 gate condition 2
  counts):
    1. A canonical twin already exists (ANY source_system) →
         fingerprint matches legacy identity  → disposition 'mapped'
         (via shadow/natural — NO second row is written)
         fingerprint differs                  → disposition 'quarantined'
         (divergence preserved, never overwritten — R4/R5)
    2. No twin → INSERT via LilacAdapter
         (source_system='import:vision.receipts', producer_id=
         'nexus-conduit-python' — the kernel process performing the import;
         V142-registered lifecycle authority)
         accepted / duplicate-equivalent      → disposition 'mapped'
         LilacPersistenceError (conflict etc.)→ disposition 'quarantined'
    3. Legacy-only type (no ratified kind, e.g. PROPOSED) → disposition
       'discarded' — first-class per the C4 ruling, never invented.

Every disposition row: source_schema='vision', source_table='receipts',
source_pk=<legacy id>, migration_version='C4', recorded_by='backfill_c4',
target_refs carrying the canonical receipt id or refusal reason.

Idempotent: rows with an existing 'C4' disposition are skipped; a rerun
processes zero rows. Bounded: --limit N rows and/or --max-seconds T per
run; rerun until 'remaining' reaches 0. --dry-run classifies without
writing anything.

Usage:
  CONDUIT_PG_DSN='...' python3 backfill_c4.py [--dry-run] [--limit 500]
                                              [--max-seconds 120]
                                              [--legacy-schema vision]
                                              [--canonical-schema resolution]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

import lilac
import lilac_drift
from lilac import LilacAdapter, LilacPersistenceError

MIGRATION_VERSION = "C4"
RECORDED_BY = "backfill_c4"
IMPORT_SOURCE_SYSTEM = "import:vision.receipts"
IMPORT_PRODUCER = "nexus-conduit-python"  # V142-registered lifecycle authority

_DISPOSITION_SQL = """
    INSERT INTO {canonical_schema}.migration_disposition
      (source_schema, source_table, source_pk, migration_version,
       disposition_class, target_refs, recorded_by)
    VALUES ('vision', 'receipts', %s, %s, %s, %s, %s)
    ON CONFLICT (source_schema, source_table, source_pk, migration_version)
    DO NOTHING
"""


def _fetch_pending(cur, legacy_schema: str, limit: int,
                   canonical_schema: str = "resolution"):
    """Rows with no C4 disposition yet (mappable + legacy-only classes)."""
    cur.execute(
        f"""SELECT v.id, v.type, v.plan_id, v.agent_role, v.session_id,
                   v.ticket_id, v.summary, v.artifact_path, v.tokens_used,
                   v.metadata_json, v.created_at
            FROM {legacy_schema}.receipts v
            WHERE NOT EXISTS (
              SELECT 1 FROM {canonical_schema}.migration_disposition d
              WHERE d.source_schema = 'vision' AND d.source_table = 'receipts'
                AND d.source_pk = v.id AND d.migration_version = %s
            )
            ORDER BY v.created_at ASC
            LIMIT %s""",
        (MIGRATION_VERSION, limit),
    )
    cols = ("id", "type", "plan_id", "agent_role", "session_id", "ticket_id",
            "summary", "artifact_path", "tokens_used", "metadata_json",
            "created_at")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _existing_twin(cur, canonical_schema: str, legacy_id: str):
    """Any canonical row for this legacy id (natural shadow or prior import)."""
    cur.execute(
        f"SELECT id, payload_fingerprint, source_system, payload "
        f"FROM {canonical_schema}.receipt WHERE source_receipt_id = %s "
        f"ORDER BY created_at ASC LIMIT 1",
        (legacy_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "fingerprint": row[1],
            "source_system": row[2], "payload": row[3]}


def _legacy_payload(row: dict) -> dict:
    """Canonical payload reconstruction (shadow-seam field contract)."""
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
    except (TypeError, ValueError):
        metadata = {}
    return {
        "plan_id": row["plan_id"],
        "receipt_type": row["type"],
        "agent_role": row["agent_role"],
        "session_id": row["session_id"],
        "ticket_id": row["ticket_id"] or "",
        "summary": row["summary"] or "",
        # F2 (review 48f213bc): a blank claimed path is not a path — the
        # canonical payload carries "no artifact claimed" as None, never "".
        "artifact_path": (row["artifact_path"] or "").strip() or None,
        "tokens_used": int(row["tokens_used"] or 0),
        "metadata": metadata,
        "request_id": None,
        "import": {
            "source_created_at": str(row["created_at"]),
            "via": "backfill_c4",
            "migration_version": MIGRATION_VERSION,
        },
    }


def process_row(conn, canonical_schema: str, row: dict, dry_run: bool) -> dict:
    """Classify (and unless dry-run, import) one legacy row."""
    legacy_id = row["id"]
    rtype = row["type"]

    # Class 1: no ratified canonical kind → discarded (first-class).
    if rtype not in lilac_drift.KIND_BY_TYPE:
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    _DISPOSITION_SQL.format(canonical_schema=canonical_schema),
                    (legacy_id, MIGRATION_VERSION, "discarded",
                     json.dumps({"reason": f"legacy-only type {rtype}: no ratified canonical kind"}),
                     RECORDED_BY),
                )
            conn.commit()
        return {"id": legacy_id, "class": "discarded"}

    # Class 2: twin already exists (natural shadow or earlier import).
    twin = _existing_twin(conn.cursor(), canonical_schema, legacy_id)
    if twin is not None:
        # The shadow seam's payload carries richer fields (request_id,
        # provenance metadata, import provenance); identity-level parity is
        # decided by the SAME identity fields the drift checker uses
        # (lilac_drift.PAYLOAD_IDENTITY_FIELDS), not byte equality — a
        # natural shadow twin written from this very legacy row always
        # matches on them, while a genuinely divergent twin (any identity
        # field altered) quarantines. Divergence is preserved (R4/R5).
        payload = twin["payload"] if isinstance(twin["payload"], dict) else json.loads(twin["payload"] or "{}")
        reconstruction = _legacy_payload(row)
        mismatches = [f for f in lilac_drift.PAYLOAD_IDENTITY_FIELDS
                      if payload.get(f) is not None
                      and reconstruction.get(f) is not None
                      and payload.get(f) != reconstruction.get(f)]
        if not mismatches:
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        _DISPOSITION_SQL.format(canonical_schema=canonical_schema),
                        (legacy_id, MIGRATION_VERSION, "mapped",
                         json.dumps({"canonical_receipt_id": twin["id"],
                                     "via": twin["source_system"]}),
                         RECORDED_BY),
                    )
                conn.commit()
            return {"id": legacy_id, "class": "mapped",
                    "via": "existing-twin"}
        if not dry_run:
            with conn.cursor() as conn_cur:
                conn_cur.execute(
                    _DISPOSITION_SQL.format(canonical_schema=canonical_schema),
                    (legacy_id, MIGRATION_VERSION, "quarantined",
                     json.dumps({"reason": "existing twin diverges on identity fields",
                                 "fields": mismatches}),
                     RECORDED_BY),
                )
            conn.commit()
        return {"id": legacy_id, "class": "quarantined", "fields": mismatches}

    # Class 3: import the twin.
    if dry_run:
        return {"id": legacy_id, "class": "to_import"}
    cur = conn.cursor()
    try:
        adapter = LilacAdapter(lambda: conn, schema=canonical_schema,
                               producer_id=IMPORT_PRODUCER)
        outcome, canonical_id = adapter.insert_receipt(
            conn,
            kind=lilac_drift.KIND_BY_TYPE[rtype],
            source_receipt_id=legacy_id,
            payload=_legacy_payload(row),
            refs={"plan_id": row["plan_id"],
                  "ticket_id": row["ticket_id"] or None},
            source_system=IMPORT_SOURCE_SYSTEM,
        )
        disposition = "mapped"
        target = {"canonical_receipt_id": canonical_id,
                  "import_outcome": outcome}
    except LilacPersistenceError as exc:
        conn.rollback()
        disposition = "quarantined"
        target = {"reason": str(exc)}
        canonical_id = None
    with conn.cursor() as cur:
        cur.execute(
            _DISPOSITION_SQL.format(canonical_schema=canonical_schema),
            (legacy_id, MIGRATION_VERSION, disposition, json.dumps(target),
             RECORDED_BY),
        )
    conn.commit()
    return {"id": legacy_id, "class": disposition}


def run(conn, legacy_schema: str = "vision", canonical_schema: str = "resolution",
        dry_run: bool = False, limit: int = 500, max_seconds: float = 120.0) -> dict:
    started = time.monotonic()
    cur = conn.cursor()
    pending = _fetch_pending(cur, legacy_schema, limit, canonical_schema)
    counts = {}
    results = []
    for row in pending:
        result = process_row(conn, canonical_schema, row, dry_run)
        counts[result["class"]] = counts.get(result["class"], 0) + 1
        if result["class"] in ("quarantined",):
            results.append(result)
        if time.monotonic() - started > max_seconds:
            break

    # Remaining = undispositioned mappable rows (the V141 condition-2 shape).
    cur.execute(
        f"""SELECT count(*) FROM {legacy_schema}.receipts v
            WHERE v.type IN (
              'PLAN_CREATE','PLANNING','IMPLEMENTATION','REVIEW','REVIEW_PASS',
              'REVIEW_REJECT','CRITIQUE','CRITIQUE_PASS','CRITIQUE_REJECT','BLOCK',
              'HOLD','CCNF_EXECUTION','REQUEUED','API_LIMIT','ABANDONED',
              'CANCELLED','PLAN_BLOCK')
            AND NOT EXISTS (
              SELECT 1 FROM {canonical_schema}.migration_disposition d
              WHERE d.source_schema = 'vision' AND d.source_table = 'receipts'
                AND d.source_pk = v.id AND d.migration_version = %s
            )""",
        (MIGRATION_VERSION,),
    )
    remaining = cur.fetchone()[0]
    conn.rollback()
    return {"dry_run": dry_run, "processed": len(pending),
            "counts": counts, "remaining_mappable": remaining,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "quarantined": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--legacy-schema", default="vision")
    parser.add_argument("--canonical-schema",
                        default=os.environ.get("CONDUIT_LILAC_SCHEMA", "resolution"))
    args = parser.parse_args()

    dsn = os.environ.get("CONDUIT_PG_DSN", "")
    if not dsn:
        print("CONDUIT_PG_DSN must be set", file=sys.stderr)
        return 2
    conn = psycopg2.connect(dsn)
    report = run(conn, args.legacy_schema, args.canonical_schema,
                 dry_run=args.dry_run, limit=args.limit,
                 max_seconds=args.max_seconds)
    conn.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
