-- ============================================================================
-- V206 (Engineer): receipt-integrity store guards — review 48f213bc.
--   F2 (this file): blank artifact_path backfill + CHECK guard.
--   F3 (V207 file): one NON-projection IMPLEMENTATION receipt per
--     (request_id, attempt_id) — split into its own migration because the
--     live census (2026-10-30) found 40 historical duplicate groups of
--     non-projection IMPLEMENTATION receipts per (request_id, attempt_id)
--     that PREDATE this work. Installing the index without explicit
--     reconciliation would either launder history (deleting rows — a
--     wrap-not-drop violation) or fail closed forever. V207 carries the
--     guarded index with a refusing preflight and lands together with the
--     reconciliation lane.
-- ============================================================================
-- Closes the receipt-store defects that let the W-C false success
-- (plan 8261655) pass silently. Line anchors are against the reviewer
-- record 48f213bc; F1 was closed by the #824 deploy refresh and F4 by the
-- evidence_contract.expected_files the WorkRequestFactory already builds.
--
-- WHAT THIS FILE DOES
--   1. F2 backfill: every vision.receipts row whose artifact_path is blank
--      becomes NULL. In the live census (2026-10-30) that is 889 rows —
--      each was persisted as if a (blank) path had been claimed. Null is
--      the honest value: "no artifact claimed". A CHECK guard afterwards
--      makes a re-introduced blank claim structurally impossible.
--   2. F2 guard: CHECK (artifact_path IS NULL OR btrim(artifact_path) <> '')
--      on vision.receipts (the frozen synthetic/standalone write surface —
--      request-backed rows carry artifact_path in metadata, normalized to
--      null at the insert_receipt write funnel in code).
--
-- WHAT THIS FILE DOES NOT DO
--   It never deletes rows (wrap-not-drop). No F3 index here — see above.
--   The SQL and python write funnels already normalize blank claims to
--   NULL (insert_receipt, backfill_c4, routes_receipts read path), so the
--   CHECK only defends against writers that bypass the funnel.
--
-- Apply:  psql "<CONDUIT_PG_DSN>" -f sql/V206__receipt_integrity_f2_f3_guards.sql
--
-- Replication (AGENTS.md R9): this is a schema change — confirm with the
-- operator before replicating (barium 192.168.1.212 canonical target).
--
-- Idempotency: backfill is a no-op once everything is null; the CHECK is
-- installed under a DO existence guard.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF to_regclass('vision.receipts') IS NULL THEN
        RAISE EXCEPTION 'V206 PREFLIGHT FAIL: vision.receipts does not exist';
    END IF;
END $$;

-- ── Step 1 — F2 backfill: blank claimed paths become NULL ─────────────────
UPDATE vision.receipts
SET artifact_path = NULL
WHERE artifact_path IS NOT NULL AND btrim(artifact_path) = '';

-- ── Step 2 — F2 guard: blank claimed path is structurally impossible ───────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_vision_receipts_artifact_path_nonblank'
          AND conrelid = 'vision.receipts'::regclass
    ) THEN
        ALTER TABLE vision.receipts ADD CONSTRAINT
            chk_vision_receipts_artifact_path_nonblank
            CHECK (artifact_path IS NULL OR btrim(artifact_path) <> '');
    END IF;
END $$;

COMMIT;

-- Post-apply verification (read-only):
--   SELECT count(*) FROM vision.receipts
--    WHERE artifact_path IS NOT NULL AND btrim(artifact_path) = '';   -- 0
--   SELECT count(*) FROM vision.receipts WHERE artifact_path = '';    -- 0

-- ════════════════════════════════════════════════════════════════════════════
-- DEFERRED (V207): F3 uniqueness guard. The index DDL below is carried in
-- sql/V207__execution_receipt_impl_uniqueness.sql and installs only after
-- the 40 historical duplicate groups are explicitly reconciled:
--   CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_receipts_one_impl_per_attempt
--       ON execution.receipts (request_id, attempt_id)
--       WHERE attempt_id IS NOT NULL
--         AND type = 'IMPLEMENTATION'
--         AND COALESCE(lineage_source, '') <> 'conduit';
-- Rationale for the scope: projection rows (lineage_source = 'conduit',
-- the V140 unified-view branch the ticket fan-out reads) are excluded —
-- the 8261655 attempt bacef8dd duplicate was load-bearing; deleting the
-- projection would have broken create_next_tickets /
-- advanceTicketsOnReceipt. The code-side dedupe contract rides on
-- (attempt_id, type): insert_receipt now carries the caller's metadata
-- attempt_id onto the mirrored projection row. Other receipt types are NOT
-- indexed — the same census shows historical multi-receipt-per-attempt
-- noise for REVIEW_REJECT/BLOCK/REQUEUED/API_LIMIT (re-reviews, retry
-- cycles) predating this work; enforcing there is a separate lane.
-- ════════════════════════════════════════════════════════════════════════════
