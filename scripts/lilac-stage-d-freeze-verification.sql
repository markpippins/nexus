-- =============================================================================
-- Lilac Stage D freeze re-verification (companion to V160).
-- Run any time to verify the legacy surfaces are still frozen; exits
-- non-zero on the first drift (psql ON_ERROR_STOP + \if error handling).
--
-- Usage:
--   psql -h <host> -p 5432 -U pguser -d nexus -X -v ON_ERROR_STOP=1 \
--        -f scripts/lilac-stage-d-freeze-verification.sql
-- =============================================================================

\set ON_ERROR_STOP on

-- ── 1. Guard triggers exist and are enabled ─────────────────────────────────
SELECT CASE WHEN count(*) = 2 THEN 'OK  guard triggers present (receipts + tickets, one multi-event each)'::text
       ELSE 'FAIL guard triggers missing/incomplete: ' || count(*) || '/2' END AS check_triggers
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'vision' AND c.relname IN ('receipts', 'tickets')
  AND t.tgname LIKE 'trg_vision_%_no_write' AND NOT t.tgisinternal;

-- ── 2. A live INSERT must be refused (run in its own aborted transaction) ───
-- NOTE the flag pattern: a successful insert must reach the FAIL raise
-- OUTSIDE any exception handler — otherwise the FAIL would be swallowed by
-- the very handler meant to detect refusal (false-pass trap).
BEGIN;
DO $$
DECLARE v_refused boolean := false;
BEGIN
  BEGIN
    INSERT INTO vision.receipts (id, plan_id, type, agent_role, session_id, summary, created_at)
    VALUES ('freeze-check-probe', 'freeze-check', 'PROBE', 'probe', 'freeze-check', 'must be refused', now());
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLSTATE IN ('P0001', '42501') THEN
        v_refused := true;
        RAISE NOTICE 'OK   live INSERT refused (%)', SQLSTATE;
      ELSE
        RAISE;
      END IF;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'FAIL: write to vision.receipts was NOT refused (guard missing or hatched connection)';
  END IF;
END $$;
ROLLBACK;

-- ── 3. Gate state (informational + hard failure on regression) ──────────────
SELECT jsonb_pretty(resolution.c6_retirement_gate()) AS c6_gate;
DO $$
DECLARE g jsonb;
BEGIN
  g := resolution.c6_retirement_gate();
  IF NOT COALESCE((g->>'satisfied')::boolean, false) THEN
    RAISE EXCEPTION 'FAIL: c6_retirement_gate no longer satisfied — Stage D contract regressed';
  END IF;
  RAISE NOTICE 'OK   c6_retirement_gate satisfied';
END $$;

-- ── 4. No new legacy writes since Stage D (the freeze's purpose) ────────────
-- The V160 apply stamps the table comment with a leading ISO timestamp;
-- extract JUST that prefix (the comment continues with prose, so the whole
-- string cannot be cast directly).
SELECT CASE WHEN count(*) = 0 THEN 'OK   no vision.receipts rows newer than the V160 apply'::text
       ELSE 'FAIL ' || count(*) || ' vision.receipts rows newer than V160 apply (hatched or unguarded writes!)' END AS check_new_rows
FROM vision.receipts
WHERE created_at > COALESCE(
  (SELECT substring(obj_description('vision.receipts'::regclass)
                    FROM '^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})')::timestamptz),
  '-infinity'::timestamptz);

-- ── 5. Twin coverage still complete (C4 condition holds) ────────────────────
SELECT CASE WHEN count(*) = 0 THEN 'OK   vision_receipts_missing_twin = 0'::text
       ELSE 'FAIL ' || count(*) || ' legacy rows missing canonical twins' END AS check_twins
FROM vision.receipts v
WHERE v.type IN (
  'PLAN_CREATE','PLANNING','IMPLEMENTATION','REVIEW','REVIEW_PASS',
  'REVIEW_REJECT','CRITIQUE','CRITIQUE_PASS','CRITIQUE_REJECT','BLOCK',
  'HOLD','CCNF_EXECUTION','REQUEUED','API_LIMIT','ABANDONED',
  'CANCELLED','PLAN_BLOCK')
  AND NOT EXISTS (
    SELECT 1 FROM resolution.receipt r
    WHERE r.source_receipt_id = v.id
      AND r.source_system IN ('conduit', 'import:vision.receipts'));
