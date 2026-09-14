-- V156: Canonical-store audit for nebula (agent_records, harvests_history).
--
-- Motivation (2026-09-14, issue thread f6256e00 / agent records 2d2f0b0f,
-- 969f7702): the 16:08Z role_memory corruption investigation died because
-- PostgreSQL statement logging was off and no table-level audit existed.
-- V155 gave the procedure registry (tackle.memory / tackle.role_memory)
-- statement-level audit with client attribution. This migration extends
-- the same pattern to the two canonical nebula governance surfaces:
--
--   * nebula.agent_records_history    — the agent audit/intent trail itself (R1/R2,
--                               status updates, decisions). If this surface
--                               can be mutated silently, the audit trail is
--                               writable by its subjects.
--   * nebula.harvests_history — canonical harvest store behind the
--                               temporal view nebula.harvests (feeds the
--                               transcripts forum via nebula-ui).
--
-- Every INSERT/UPDATE/DELETE statement on either table appends one row to
-- tackle.system_logs (category 'NEBULA_AUDIT'): operation, row count,
-- affected keys, application_name, client_addr, txid — the witness data the
-- 16:08 investigation lacked.
--
-- Statement-level triggers with transition tables (statement triggers
-- cannot reference NEW/OLD). Self-contained: defines its own helper +
-- audit functions (deliberately independent of V155's registry functions
-- so either migration can bootstrap a fresh database alone; the two
-- helpers write distinct system_logs categories, REGISTRY_AUDIT vs
-- NEBULA_AUDIT).
--
-- All steps idempotent (CREATE OR REPLACE, DROP TRIGGER IF EXISTS).
-- No grants change, no data movement, no service restart required.

BEGIN;

-- ── 1. Shared log-write helper (nebula-specific category) ──────────────
CREATE OR REPLACE FUNCTION tackle.fn_nebula_audit_log(
  p_table text, p_op text, p_rows bigint, p_keys text
) RETURNS void
LANGUAGE sql AS $fn$
  INSERT INTO tackle.system_logs
    (id, timestamp, level, category, message, source, details)
  VALUES (
    gen_random_uuid()::text,
    now(),
    'INFO',
    'NEBULA_AUDIT',
    format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
           COALESCE(' [' || left(p_keys, 900) || ']', '')),
    'nebula-audit-trigger',
    jsonb_build_object(
      'table', p_table,
      'op', p_op,
      'row_count', p_rows,
      'keys', p_keys,
      'application_name', current_setting('application_name', true),
      'client_addr', inet_client_addr()::text,
      'txid', txid_current()
    )
  );
$fn$;

-- ── 2. nebula.agent_records_history audit functions (transition tables) ────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_arh_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.role,'?'), 24) || '/' ||
                    left(coalesce(r.record_type,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM inserted_rows r;
  PERFORM tackle.fn_nebula_audit_log('agent_records_history', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_arh_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.role,'?'), 24) || '/' ||
                    left(coalesce(r.record_type,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM updated_rows r;
  PERFORM tackle.fn_nebula_audit_log('agent_records_history', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_arh_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.role,'?'), 24) || '/' ||
                    left(coalesce(r.record_type,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM deleted_rows r;
  PERFORM tackle.fn_nebula_audit_log('agent_records_history', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 3. nebula.harvests_history audit functions ─────────────────────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_hh_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.source_filename,'?'), 48), ', ')
    INTO v_rows, v_keys
  FROM inserted_rows r;
  PERFORM tackle.fn_nebula_audit_log('harvests_history', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_hh_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.source_filename,'?'), 48), ', ')
    INTO v_rows, v_keys
  FROM updated_rows r;
  PERFORM tackle.fn_nebula_audit_log('harvests_history', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_hh_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.source_filename,'?'), 48), ', ')
    INTO v_rows, v_keys
  FROM deleted_rows r;
  PERFORM tackle.fn_nebula_audit_log('harvests_history', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 4. Trigger declarations (drop-first for clean re-runs) ─────────────
DROP TRIGGER IF EXISTS trg_arh_audit_ins ON nebula.agent_records_history;
CREATE TRIGGER trg_arh_audit_ins
AFTER INSERT ON nebula.agent_records_history
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_arh_ins();

DROP TRIGGER IF EXISTS trg_arh_audit_upd ON nebula.agent_records_history;
CREATE TRIGGER trg_arh_audit_upd
AFTER UPDATE ON nebula.agent_records_history
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_arh_upd();

DROP TRIGGER IF EXISTS trg_arh_audit_del ON nebula.agent_records_history;
CREATE TRIGGER trg_arh_audit_del
AFTER DELETE ON nebula.agent_records_history
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_arh_del();

DROP TRIGGER IF EXISTS trg_hh_audit_ins ON nebula.harvests_history;
CREATE TRIGGER trg_hh_audit_ins
AFTER INSERT ON nebula.harvests_history
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_hh_ins();

DROP TRIGGER IF EXISTS trg_hh_audit_upd ON nebula.harvests_history;
CREATE TRIGGER trg_hh_audit_upd
AFTER UPDATE ON nebula.harvests_history
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_hh_upd();

DROP TRIGGER IF EXISTS trg_hh_audit_del ON nebula.harvests_history;
CREATE TRIGGER trg_hh_audit_del
AFTER DELETE ON nebula.harvests_history
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_hh_del();

COMMIT;
