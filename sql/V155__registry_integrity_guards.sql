-- V155: Procedure-registry integrity guards
--
-- Motivation (2026-09-14 incident: 447 orphaned tackle.role_memory rows
-- inserted at 16:08:13Z referencing 53 card UUIDs foreign to this database;
-- issue thread f6256e00, agent records 2d2f0b0f + 969f7702): the registry
-- accepted structurally invalid assignments silently, and PostgreSQL
-- statement logging was off, so the writer left no trace.
--
-- Guards:
--   1. FK role_memory.memory_id -> memory.id ON DELETE CASCADE
--      (fail-closed against foreign-UUID assignments; CASCADE keeps
--      wipe-and-reseed admin flows working).
--   2. Partial UNIQUE (memory_id, role) WHERE expiration_dt IS NULL
--      (no duplicate ACTIVE assignments; bitemporal history preserved;
--      compatible with tackle-srv assignProcedures' NOT EXISTS guard).
--   3. Statement-level audit triggers on memory + role_memory ->
--      tackle.system_logs: op, row count, affected keys (slugs/roles),
--      application_name, client_addr, txid. Transition tables supply the
--      per-statement rows (statement triggers cannot reference NEW/OLD).
--   4. log_statement=ddl + slow-statement log (ALTER SYSTEM, applied AFTER
--      the transaction — ALTER SYSTEM cannot run inside one).
--
-- All steps are idempotent (DROP/CREATE OR REPLACE, IF NOT EXISTS).

BEGIN;

-- ── 1. Foreign key ─────────────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'tackle.role_memory'::regclass AND conname = 'fk_role_memory_memory'
  ) THEN
    -- Fails here if orphaned rows exist; clean them first (see incident R2).
    ALTER TABLE tackle.role_memory
      ADD CONSTRAINT fk_role_memory_memory
      FOREIGN KEY (memory_id) REFERENCES tackle.memory(id) ON DELETE CASCADE;
  END IF;
END $$;

-- ── 2. Uniqueness of active assignments ────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_role_memory_active
  ON tackle.role_memory (memory_id, role)
  WHERE expiration_dt IS NULL;

-- ── 3. Audit: shared log-write helper ─────────────────────────────────
CREATE OR REPLACE FUNCTION tackle.fn_registry_audit_log(
  p_table text, p_op text, p_rows int, p_keys text
) RETURNS void
LANGUAGE sql AS $fn$
  INSERT INTO tackle.system_logs (level, category, message, source, details)
  VALUES (
    'INFO',
    'REGISTRY_AUDIT',
    format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
           COALESCE(' [' || p_keys || ']', '')),
    'tackle-audit-trigger',
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

-- ── 3a. tackle.memory audit functions (transition-table based) ────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_memory_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT slug, ', ') INTO v_rows, v_keys FROM inserted_rows;
  PERFORM tackle.fn_registry_audit_log('memory', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_memory_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT NEW.slug, ', ') INTO v_rows, v_keys FROM updated_rows NEW;
  PERFORM tackle.fn_registry_audit_log('memory', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_memory_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT slug, ', ') INTO v_rows, v_keys FROM deleted_rows;
  PERFORM tackle.fn_registry_audit_log('memory', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 3b. tackle.role_memory audit functions ────────────────────────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_rm_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT role, ', ') INTO v_rows, v_keys FROM inserted_rows;
  PERFORM tackle.fn_registry_audit_log('role_memory', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_rm_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT NEW.role, ', ') INTO v_rows, v_keys FROM updated_rows NEW;
  PERFORM tackle.fn_registry_audit_log('role_memory', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_rm_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT role, ', ') INTO v_rows, v_keys FROM deleted_rows;
  PERFORM tackle.fn_registry_audit_log('role_memory', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 3c. Trigger declarations (drop-first so re-runs update cleanly) ───
DROP TRIGGER IF EXISTS trg_memory_audit_ins ON tackle.memory;
CREATE TRIGGER trg_memory_audit_ins
AFTER INSERT ON tackle.memory
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_memory_ins();

DROP TRIGGER IF EXISTS trg_memory_audit_upd ON tackle.memory;
CREATE TRIGGER trg_memory_audit_upd
AFTER UPDATE ON tackle.memory
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_memory_upd();

DROP TRIGGER IF EXISTS trg_memory_audit_del ON tackle.memory;
CREATE TRIGGER trg_memory_audit_del
AFTER DELETE ON tackle.memory
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_memory_del();

DROP TRIGGER IF EXISTS trg_rm_audit_ins ON tackle.role_memory;
CREATE TRIGGER trg_rm_audit_ins
AFTER INSERT ON tackle.role_memory
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rm_ins();

DROP TRIGGER IF EXISTS trg_rm_audit_upd ON tackle.role_memory;
CREATE TRIGGER trg_rm_audit_upd
AFTER UPDATE ON tackle.role_memory
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rm_upd();

DROP TRIGGER IF EXISTS trg_rm_audit_del ON tackle.role_memory;
CREATE TRIGGER trg_rm_audit_del
AFTER DELETE ON tackle.role_memory
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION tackle.fn_audit_rm_del();

COMMIT;

-- ── 4. Statement logging (must be OUTSIDE the transaction block) ──────
ALTER SYSTEM SET log_statement = 'ddl';
ALTER SYSTEM SET log_min_duration_statement = 5000;
SELECT pg_reload_conf();
