"""Read-only collectors for the W-A duplicate-history inventory.

Plan 8261653. Every function here is **read-only** — the collectors never
write to a canonical table. The reconciliation ledger is a separate
append-only surface (``resolution.reconciliation_disposition``,
V205) and is written through the DBA lane, per the docket's
"DB-layer steps route through the DBA" rule.

The collectors return plain dicts so the detectors in
``duplicate_history`` stay free of any DB dependency.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# Default connection string for the local Nexus instance.
DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"

# The promotion-manifest sediment lives beside the pipeline state.
DEFAULT_MANIFEST_DIR = "state/promotion-flow"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(dsn: str = DEFAULT_DSN):
    """Open a read-only-ish connection (psycopg2, autocommit off).

    The collectors issue SELECTs only; the caller is responsible for
    closing the connection.
    """
    import psycopg2  # imported lazily so the package stays dependency-free

    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _rows(conn, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Surface collectors ────────────────────────────────────────────────────


def collect_receipts(conn) -> list[dict[str, Any]]:
    """Resolution receipts with their payload + fingerprint.

    The fingerprint is recomputed by the detector — the DB value is only
    ever compared, never trusted.
    """
    return _rows(
        conn,
        """
        SELECT id::text            AS id,
               payload_fingerprint  AS payload_fingerprint,
               payload,
               created_at::text     AS created_at
          FROM resolution.receipt
         ORDER BY created_at, id
        """,
    )


def collect_canonical_assets(conn) -> list[dict[str, Any]]:
    """Canonical assets — the C2 surface whose canonical_key is the identity."""
    return _rows(
        conn,
        """
        SELECT id::text                   AS id,
               canonical_key              AS canonical_key,
               canonical_asset_id         AS canonical_asset_id,
               asset_kind                 AS asset_kind,
               created_at::text           AS created_at
          FROM resolution.canonical_asset
         ORDER BY created_at, id
        """,
    )


def collect_claims(conn) -> list[dict[str, Any]]:
    """Execution claims — the C3 surface (subject_ref vs normalized column)."""
    return _rows(
        conn,
        """
        SELECT id::text        AS id,
               attempt_id      AS attempt_id,
               subject_ref     AS subject_ref,
               created_at::text AS created_at
          FROM resolution.execution_claim
         ORDER BY created_at, id
        """,
    )


def collect_outbox_rows(conn) -> list[dict[str, Any]]:
    """Keychain event outbox — the PC7 surface (read_set inside payload)."""
    return _rows(
        conn,
        """
        SELECT id::text                  AS id,
               read_set                  AS read_set,
               created_at::text           AS created_at,
               recorded_at::text          AS recorded_at
          FROM resolution.keychain_event_outbox
         ORDER BY id
        """,
    )


def collect_legacy_receipts(conn) -> list[dict[str, Any]]:
    """Legacy flat receipts — the C4 wrap-not-drop surface."""
    return _rows(
        conn,
        """
        SELECT id::text        AS id,
               type            AS type,
               plan_id::text   AS plan_id,
               created_at::text AS created_at
          FROM vision.receipts
         ORDER BY created_at, id
        """,
    )


def collect_legacy_tickets(conn) -> list[dict[str, Any]]:
    """Legacy flat tickets — the C6 lane surface."""
    return _rows(
        conn,
        """
        SELECT id::text        AS id,
               status          AS status,
               plan_id::text   AS plan_id,
               created_at::text AS created_at
          FROM vision.tickets
         ORDER BY created_at, id
        """,
    )


def collect_dispositions(conn) -> list[dict[str, Any]]:
    """The disposition ledger — both the coverage evidence and the audit gap."""
    return _rows(
        conn,
        """
        SELECT id::text            AS id,
               source_schema       AS source_schema,
               source_table        AS source_table,
               source_pk           AS source_pk,
               migration_version   AS migration_version,
               disposition_class   AS disposition_class,
               target_refs         AS target_refs,
               created_at::text    AS created_at
          FROM resolution.migration_disposition
         ORDER BY created_at, id
        """,
    )


def collect_census(conn) -> dict[str, Any]:
    """A row-count census used as the inventory's evidence header."""
    sql = """
        SELECT 'resolution.receipt'               AS surface, count(*) FROM resolution.receipt
        UNION ALL SELECT 'resolution.canonical_asset',       count(*) FROM resolution.canonical_asset
        UNION ALL SELECT 'resolution.execution_claim',       count(*) FROM resolution.execution_claim
        UNION ALL SELECT 'resolution.keychain_event_outbox', count(*) FROM resolution.keychain_event_outbox
        UNION ALL SELECT 'resolution.migration_disposition', count(*) FROM resolution.migration_disposition
        UNION ALL SELECT 'resolution.promotion_batch',        count(*) FROM resolution.promotion_batch
        UNION ALL SELECT 'resolution.promotion_batch_candidate', count(*) FROM resolution.promotion_batch_candidate
        UNION ALL SELECT 'resolution.candidate',             count(*) FROM resolution.candidate
        UNION ALL SELECT 'resolution.ticket',                count(*) FROM resolution.ticket
        UNION ALL SELECT 'vision.receipts',                   count(*) FROM vision.receipts
        UNION ALL SELECT 'vision.tickets',                    count(*) FROM vision.tickets
    """
    return {r["surface"]: r["count"] for r in _rows(conn, sql)}


# ── Filesystem collector ──────────────────────────────────────────────────


def collect_manifests(manifest_dir: str | Path) -> list[dict[str, Any]]:
    """Load the promotion manifests from ``state/promotion-flow``.

    Decoding errors are skipped rather than fatal — a half-written manifest
    must not blind the inventory; the census reports the skip count.
    """
    out: list[dict[str, Any]] = []
    for path in sorted(Path(manifest_dir).glob("batch-*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            doc.setdefault("batch_id", path.stem.removeprefix("batch-"))
            doc["_source_path"] = str(path)
            out.append(doc)
    return out


def collect_manifest_skip_count(manifest_dir: str | Path) -> int:
    """Count manifests that failed to decode (reported, never dropped silently)."""
    skipped = 0
    for path in sorted(Path(manifest_dir).glob("batch-*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped += 1
    return skipped


# ── Top-level assembly ────────────────────────────────────────────────────


def build_live_inventory(
    dsn: str = DEFAULT_DSN,
    manifest_dir: str | Path | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Collect every surface and build the inventory.

    Returns ``(inventory, census)``. The census carries the row counts and
    the manifest skip count — it is the evidence header that proves the
    inventory was taken over a known population.
    """
    from .duplicate_history import build_inventory

    conn = connect(dsn)
    try:
        census = collect_census(conn)
        receipts = collect_receipts(conn)
        canonical_assets = collect_canonical_assets(conn)
        claims = collect_claims(conn)
        outbox_rows = collect_outbox_rows(conn)
        legacy_receipts = collect_legacy_receipts(conn)
        legacy_tickets = collect_legacy_tickets(conn)
        dispositions = collect_dispositions(conn)
    finally:
        conn.close()

    manifests: list[dict[str, Any]] = []
    skipped = 0
    if manifest_dir:
        manifests = collect_manifests(manifest_dir)
        skipped = collect_manifest_skip_count(manifest_dir)

    census["promotion-manifest.files"] = len(manifests)
    census["promotion-manifest.undecodable"] = skipped

    inventory = build_inventory(
        manifests=manifests,
        receipts=receipts,
        canonical_assets=canonical_assets,
        claims=claims,
        outbox_rows=outbox_rows,
        legacy_receipts=legacy_receipts,
        legacy_tickets=legacy_tickets,
        dispositions=dispositions,
        generated_at=_now(),
        census=census,
    )
    return inventory, census


def resolve_manifest_dir(root: str | Path, manifest_dir: str | Path | None) -> str:
    """Resolve the manifest directory against the repository root."""
    if manifest_dir:
        return str(manifest_dir)
    return os.path.join(str(root), DEFAULT_MANIFEST_DIR)
