-- V177: Extend the V156 canonical-audit family to nebula.roles_history.
--
-- Motivation (2026-09-17, DBA R2 d8d3a9c5): the tester capability grant
-- (architect ratification 2f9acb11, applied live 13:09:53Z) closed a
-- bitemporal snapshot and opened its successor on nebula.roles_history —
-- a canonical governance write with zero NEBULA_AUDIT rows. Catalog
-- inspection confirmed the gap: every other canonical nebula surface
-- gained statement triggers (V156 for agent_records/harvests; V167/V169/
-- V171/V172/V173 for their own surfaces), but roles_history was never
-- added to the family. A silent UPDATE/DELETE on the role-vocabulary
-- history table would leave no witness row — the exact failure mode the
-- 2026-09-14 incident (issue thread f6256e00, V155/V156) was fought over.
--
-- Every INSERT/UPDATE/DELETE statement on nebula.roles_history appends
-- one row to tackle.system_logs (category 'NEBULA_AUDIT'): operation,
-- row count, affected role names, application_name, client_addr, txid.
-- Grant events (close-then-insert) therefore become two attributable
-- audit rows: one UPDATE, one INSERT, same txid.
--
-- Pattern: statement-level triggers with transition tables (statement
-- triggers cannot reference NEW/OLD), reusing the V156 helper
-- tackle.fn_nebula_audit_log so all NEBULA_AUDIT rows share one shape.
-- Unlike V156 this migration does NOT create the helper: it GATES on its
-- presence instead (a fresh database that never ran V156 must apply it
-- first — the helper belongs to V156's ownership, not duplicated under
-- a second owner).
--
-- All steps idempotent (CREATE OR REPLACE, DROP TRIGGER IF EXISTS).
-- No grants change, no data movement, no service restart required.

BEGIN;

-- ── 1. Gate: target surface + V156 helper must exist ───────────────────
DO $gate$
BEGIN
  IF to_regclass('nebula.roles_history') IS NULL THEN
    RAISE EXCEPTION 'V177-GATE-001: nebula.roles_history does not exist — nothing to audit';
  END IF;
  IF to_regprocedure('tackle.fn_nebula_audit_log(text,text,bigint,text)') IS NULL THEN
    RAISE EXCEPTION 'V177-GATE-002: V156 helper tackle.fn_nebula_audit_log is missing — apply V156__nebula_canonical_audit_triggers.sql first (the helper belongs to V156; V177 refuses to duplicate it under a second owner)';
  END IF;

  -- Consistency notice: which canonical surfaces still lack the family?
  DECLARE
    v_missing text;
  BEGIN
    SELECT string_agg(t, ', ') INTO v_missing FROM unnest(ARRAY[
      'nebula.agent_records_history',
      'nebula.harvests_history',
      'nebula.roles_history'
    ]) AS t
    WHERE NOT EXISTS (
      SELECT 1 FROM pg_trigger
      WHERE tgrelid = t::regclass AND tgname LIKE '%audit%' AND NOT tgisinternal
    );
    IF v_missing IS NOT NULL THEN
      RAISE NOTICE 'V177: canonical surfaces still outside the NEBULA_AUDIT family after this migration: %', v_missing;
    END IF;
  END;
END
$gate$;

-- ── 2. roles_history audit functions (transition tables) ───────────────
-- Keys are the affected role names — the vocabulary this table IS.
CREATE OR REPLACE FUNCTION tackle.fn_audit_rh_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.name,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM inserted_rows r;
  PERFORM tackle.fn_nebula_audit_log('roles_history', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_rh_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.name,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM updated_rows r;
  PERFORM tackle.fn_nebula_audit_log('roles_history', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_rh_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(coalesce(r.name,'?'), 24), ', ')
    INTO v_rows, v_keys
  FROM deleted_rows r;
  PERFORM tackle.fn_nebula_audit_log('roles_history', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 3. Trigger declarations (drop-first for clean re-runs) ─────────────
DROP TRIGGER IF EXISTS trg_rh_audit_ins ON nebula.roles_history;
CREATE TRIGGER trg_rh_audit_ins
AFTER INSERT ON nebula.roles_history
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rh_ins();

DROP TRIGGER IF EXISTS trg_rh_audit_upd ON nebula.roles_history;
CREATE TRIGGER trg_rh_audit_upd
AFTER UPDATE ON nebula.roles_history
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rh_upd();

DROP TRIGGER IF EXISTS trg_rh_audit_del ON nebula.roles_history;
CREATE TRIGGER trg_rh_audit_del
AFTER DELETE ON nebula.roles_history
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rh_del();

-- ── 4. Postcondition: all three triggers installed ──────────────────────
DO $verify$
DECLARE v_count integer;
BEGIN
  SELECT count(*) INTO v_count FROM pg_trigger
  WHERE tgrelid = 'nebula.roles_history'::regclass
    AND tgname IN ('trg_rh_audit_ins','trg_rh_audit_upd','trg_rh_audit_del')
    AND NOT tgisinternal;
  IF v_count <> 3 THEN
    RAISE EXCEPTION 'V177-GATE-003: expected 3 audit triggers on nebula.roles_history, found %', v_count;
  END IF;
  RAISE NOTICE 'V177: nebula.roles_history joined the NEBULA_AUDIT family — INSERT/UPDATE/DELETE statement triggers installed (grant events now leave an attributable trail)';
END
$verify$;

COMMIT;
