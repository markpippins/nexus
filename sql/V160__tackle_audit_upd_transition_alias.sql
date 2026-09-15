-- V160: Fix tackle audit UPDATE triggers — transition-table alias collides
-- with the plpgsql NEW record.
--
-- Motivation (2026-09-15): the engineer's nexus-boot-procedure card fix
-- (stale .agents/ paths, quarantined by 4f176f04) required an UPDATE on
-- tackle.memory and failed with:
--
--   ERROR: column reference "new.slug" is ambiguous
--   CONTEXT: PL/pgSQL function tackle.fn_audit_memory_upd() line 4
--
-- Root cause, V155__registry_integrity_guards.sql: the two UPDATE-audit
-- functions alias their transition table as NEW:
--
--   SELECT count(*), string_agg(DISTINCT NEW.slug, ', ')
--     INTO v_rows, v_keys FROM updated_rows NEW;
--
-- Inside a statement-level trigger function, `NEW` is also the implicit
-- plpgsql record; when the alias shadows it, the unqualified reference
-- `NEW.slug` becomes ambiguous and every statement fails. Effect since
-- V155: ALL updates to tackle.memory (procedure cards) and
-- tackle.role_memory (role grants) have been blocked — silently, for any
-- writer that swallowed the error.
--
-- Fix: reference the transition table with a non-colliding alias (r) and
-- qualify columns with it — the exact form already used by the sibling
-- INSERT/DELETE audit functions in V155, and by the V156/V159 canonical
-- audit triggers. No data changes; audit rows already logged for failed
-- statements are unaffected (failed statements never fire the trigger).
--
-- Function bodies only; trigger DDL unchanged.

CREATE OR REPLACE FUNCTION tackle.fn_audit_memory_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT r.slug, ', ') INTO v_rows, v_keys FROM updated_rows r;
  PERFORM tackle.fn_registry_audit_log('memory', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_rm_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows int; v_keys text;
BEGIN
  SELECT count(*), string_agg(DISTINCT r.role, ', ') INTO v_rows, v_keys FROM updated_rows r;
  PERFORM tackle.fn_registry_audit_log('role_memory', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- Ledger entry (idempotent).
INSERT INTO resolution.migration_ledger (schema_name, migration_label, description)
VALUES ('tackle', 'V160__tackle_audit_upd_transition_alias',
        'Fix fn_audit_memory_upd/fn_audit_rm_upd: transition-table alias NEW collided with plpgsql NEW record, blocking all UPDATEs on tackle.memory and tackle.role_memory since V155.')
ON CONFLICT (schema_name, migration_label) DO NOTHING;
