-- ============================================================================
-- V207 (Engineer): F3 uniqueness guard — one NON-projection IMPLEMENTATION
-- receipt per (request_id, attempt_id) in execution.receipts (review
-- 48f213bc).
-- ============================================================================
-- NOT APPLIED YET — installs only after the reconciliation lane lands.
--
-- The live census (2026-10-30) found 40 historical duplicate groups:
-- non-projection (lineage_source <> 'conduit') IMPLEMENTATION rows sharing
-- one (request_id, attempt_id), written pre-dedupe (restart sweeps / retry
-- finalize paths). The preflight refuses loudly until those are explicitly
-- reconciled under wrap-not-drop (explicit dispositions, never silent
-- deletion):
--
--   Reconciliation inventory (read-only, 2026-10-30):
--     SELECT request_id, attempt_id, count(*)
--     FROM execution.receipts
--     WHERE attempt_id IS NOT NULL
--       AND type = 'IMPLEMENTATION'
--       AND COALESCE(lineage_source, '') <> 'conduit'
--     GROUP BY request_id, attempt_id HAVING count(*) > 1;
--
-- Reconciliation contract (per row: keep the oldest issued_at; ignore the
-- rest behind an explicit disposition record; nothing is deleted here —
-- the disposition lane decides). Once the group count is 0, apply:
--   psql "<CONDUIT_PG_DSN>" -f sql/V207__execution_receipt_impl_uniqueness.sql
--
-- Scope exclusions (must stay excluded by the partial index predicate):
--   * lineage_source = 'conduit' — the mirrored canonical projection the
--     V140 unified view and the ticket fan-out read only that branch
--     (deleting the 8261655 attempt bacef8dd projection would have broken
--     it; the code-side dedupe contract now rides metadata attempt_id
--     carried onto the projection row by insert_receipt).
--   * non-IMPLEMENTATION types — historical multi-receipt-per-attempt
--     noise (REVIEW_REJECT/BLOCK/REQUEUED/API_LIMIT re-review and retry
--     cycles) predates this work; enforcing there is a separate lane.
--
-- Replication (AGENTS.md R9): schema change — confirm with the operator
-- before replicating (barium 192.168.1.212 canonical target).
--
-- Idempotency: index uses IF NOT EXISTS.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF to_regclass('execution.receipts') IS NULL THEN
        RAISE EXCEPTION 'V207 PREFLIGHT FAIL: execution.receipts does not exist';
    END IF;
    IF EXISTS (
        SELECT 1 FROM execution.receipts
        WHERE attempt_id IS NOT NULL
          AND type = 'IMPLEMENTATION'
          AND COALESCE(lineage_source, '') <> 'conduit'
        GROUP BY request_id, attempt_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'V207 PREFLIGHT FAIL: duplicate non-projection IMPLEMENTATION rows per (request_id, attempt_id) exist in execution.receipts — run the reconciliation inventory, disposition explicitly, then re-apply';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_receipts_one_impl_per_attempt
    ON execution.receipts (request_id, attempt_id)
    WHERE attempt_id IS NOT NULL
      AND type = 'IMPLEMENTATION'
      AND COALESCE(lineage_source, '') <> 'conduit';

COMMIT;

-- Post-apply verification (read-only):
--   SELECT indexname FROM pg_indexes
--    WHERE schemaname='execution'
--      AND indexname='ux_execution_receipts_one_impl_per_attempt';  -- 1 row
