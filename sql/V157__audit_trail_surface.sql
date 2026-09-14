-- V157: Browsable audit-trail surface over the V155/V156 audit categories.
--
-- V155 (PR #239) and V156 (PR #240) made the canonical governance surfaces
-- fail loud: tackle.memory / tackle.role_memory (REGISTRY_AUDIT) and
-- nebula.agent_records_history / nebula.harvests_history (NEBULA_AUDIT)
-- write one attributable row per statement into tackle.system_logs. But
-- only SQL-literate roles can read the trail, and DELETE /logs (tackle-srv
-- routes/logs.ts clearLogs) truncates system_logs unconditionally — the
-- audit trail was one unauthenticated call away from erasure, with no
-- record that erasure happened.
--
-- This migration adds, self-contained (no dependency on V155/V156 objects):
--
--   1. tackle.audit_trail        — friendly view over both audit categories:
--                                  timestamp, table, operation, row_count,
--                                  keys, application_name, client_addr, txid,
--                                  raw details.
--   2. tackle.recent_audit()     — parameterized helper (age window, table
--                                  filter, row cap) for ad-hoc inspection.
--   3. Partial indexes on both  — category + recency for fast views/queries.
--
--   4. THE GUARD: system_logs DELETE rows whose details says 'audit' are
--      refused (exception P0001) unless the session sets
--      `SET LOCAL tackle.allow_audit_erase = 'on'` inside the same
--      transaction (a deliberate, all-or-nothing DB-level escape hatch).
--      The clearLogs() path then fails loudly until it adopts the escape
--      hatch — which the paired tackle-srv change in this branch does.
--     Erasure remains possible for genuine operational need (retention,
--      incident remediation) but never silently and never by accident.

BEGIN;

-- ── 1. Friendly view over both audit categories ─────────────────────────
CREATE OR REPLACE VIEW tackle.audit_trail AS
SELECT
  id,
  timestamp,
  category,
  -- REGISTRY_AUDIT rows say 'on <table>' after the op; NEBULA_AUDIT rows
  -- carry the table in details. Derive uniformly from details when present.
  COALESCE(details->>'table',
           substring(message from ' on ([a-z_]+) ')) AS audited_table,
  COALESCE(details->>'op',
           split_part(message, ' ', 1))               AS operation,
  COALESCE((details->>'row_count')::bigint, 0)        AS row_count,
  details->>'keys'                                     AS keys,
  details->>'application_name'                         AS application_name,
  details->>'client_addr'                              AS client_addr,
  (details->>'txid')::bigint                           AS txid,
  message,
  details
FROM tackle.system_logs
WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT');

-- ── 2. Parameterized inspection helper ──────────────────────────────────
CREATE OR REPLACE FUNCTION tackle.recent_audit(
  p_max_age interval DEFAULT interval '24 hours',
  p_table  text DEFAULT NULL,
  p_limit  integer DEFAULT 50
) RETURNS TABLE (
  "timestamp" timestamptz,
  category  text,
  audited_table text,
  operation text,
  row_count bigint,
  keys      text,
  application_name text,
  client_addr text,
  txid      bigint,
  message   text
)
LANGUAGE sql STABLE AS $fn$
  SELECT t.timestamp, t.category, t.audited_table, t.operation,
         t.row_count, t.keys, t.application_name, t.client_addr,
         t.txid, t.message
  FROM tackle.audit_trail t
  WHERE t.timestamp > now() - p_max_age
    AND (p_table IS NULL OR t.audited_table = p_table)
  ORDER BY t.timestamp DESC
  LIMIT GREATEST(LEAST(COALESCE(p_limit, 50), 500), 1);
$fn$;

-- ── 3. Recency indexes for the view / helper ────────────────────────────
CREATE INDEX IF NOT EXISTS idx_system_logs_audit_recent
  ON tackle.system_logs (timestamp DESC)
  WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT');

-- ── 4. The erase guard ──────────────────────────────────────────────────
-- Any DELETE against system_logs whose target rows carry an audit category
-- is refused unless the current transaction opted in via
-- SET LOCAL tackle.allow_audit_erase = 'on'.
CREATE OR REPLACE FUNCTION tackle.fn_guard_audit_erase() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  v_audit_rows bigint;
BEGIN
  SELECT count(*) INTO v_audit_rows
  FROM deleted_rows
  WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT');

  IF v_audit_rows > 0
     AND COALESCE(current_setting('tackle.allow_audit_erase', true), 'off') <> 'on'
  THEN
    RAISE EXCEPTION
      'audit-erase refused: % audit rows (REGISTRY_AUDIT/NEBULA_AUDIT) in DELETE scope; to erase deliberately, run inside a transaction with SET LOCAL tackle.allow_audit_erase = ''on''',
      v_audit_rows
      USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END; $fn$;

DROP TRIGGER IF EXISTS trg_guard_audit_erase ON tackle.system_logs;
CREATE TRIGGER trg_guard_audit_erase
AFTER DELETE ON tackle.system_logs
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_guard_audit_erase();

COMMIT;
