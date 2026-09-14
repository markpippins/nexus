-- V159: Canonical-store audit for the knowledge graph (graph_entities, graph_edges).
--
-- Extends the V155/V156/V157 audit arc to the third data surface. The
-- knowledge graph is now governed by the ratified PC6 contract (draft
-- 7cdf5120; ratification 35482577; re-ratification 9b2ffd4b; collision
-- ruling c0345945; Track 1 remap + Track 2 metadata DDL applied 2026-09-15),
-- which makes it a canonical governance surface: if rows can be mutated
-- silently, the graph's assertion/governance story and its authority
-- bindings are writable without witness.
--
-- Same mechanics as V156 (verified against origin/main):
--   * statement-level triggers with transition tables → one
--     tackle.system_logs row per statement (category 'KG_AUDIT'):
--     operation, row count, affected keys, application_name, client_addr,
--     txid.
--   * self-contained: own helper (fn_kg_audit_log) distinct from the
--     V155/V156 helpers, so this migration bootstraps a fresh database
--     alone.
--   * V157 surface + erase-guard integration (section 5): extends
--     tackle.audit_trail, tackle.recent_audit, the recency partial index,
--     and the guard trigger's refusal predicate so KG_AUDIT rows are
--     visible everywhere and undeletable without the
--     SET LOCAL tackle.allow_audit_erase = 'on' hatch.
--
-- Keys per table:
--   * graph_entities: section/entity_id (the ratified identity contract)
--   * graph_edges: relation_type/source/target tails — DISTINCT values
--     collapse heavily post-remap (12,894 backbone rows → 6 terms) but
--     each statement's row_count is the load-bearing number; the DISTINCT
--     key cap mirrors V156's role/record_type bounding.
--
-- All steps idempotent (CREATE OR REPLACE, DROP TRIGGER IF EXISTS, the
-- V157 view/index/guard are CREATE OR REPLACE'd with the extended
-- predicate). No grants change, no data movement, no service restart.

BEGIN;

-- ── 1. KG log-write helper (distinct category, V156 shape) ─────────────
CREATE OR REPLACE FUNCTION tackle.fn_kg_audit_log(
  p_table text, p_op text, p_rows bigint, p_keys text
) RETURNS void
LANGUAGE sql AS $fn$
  INSERT INTO tackle.system_logs
    (id, timestamp, level, category, message, source, details)
  VALUES (
    gen_random_uuid()::text,
    now(),
    'INFO',
    'KG_AUDIT',
    format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
           COALESCE(' [' || left(p_keys, 900) || ']', '')),
    'kg-audit-trigger',
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

-- ── 2. knowledge.graph_entities audit functions ───────────────────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_ge_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.section, 24) || '/' || left(r.entity_id, 48), ', ')
    INTO v_rows, v_keys
  FROM inserted_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_entities', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_ge_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.section, 24) || '/' || left(r.entity_id, 48), ', ')
    INTO v_rows, v_keys
  FROM updated_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_entities', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_ge_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.section, 24) || '/' || left(r.entity_id, 48), ', ')
    INTO v_rows, v_keys
  FROM deleted_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_entities', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 3. knowledge.graph_edges audit functions ──────────────────────────
CREATE OR REPLACE FUNCTION tackle.fn_audit_goe_ins() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.relation_type, 32) || ':' ||
                    left(r.source_id, 40) || '->' || left(r.target_id, 40), ', ')
    INTO v_rows, v_keys
  FROM inserted_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_edges', 'INSERT', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_goe_upd() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.relation_type, 32) || ':' ||
                    left(r.source_id, 40) || '->' || left(r.target_id, 40), ', ')
    INTO v_rows, v_keys
  FROM updated_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_edges', 'UPDATE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

CREATE OR REPLACE FUNCTION tackle.fn_audit_goe_del() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  SELECT count(*),
         string_agg(DISTINCT left(r.relation_type, 32) || ':' ||
                    left(r.source_id, 40) || '->' || left(r.target_id, 40), ', ')
    INTO v_rows, v_keys
  FROM deleted_rows r;
  PERFORM tackle.fn_kg_audit_log('graph_edges', 'DELETE', v_rows, v_keys);
  RETURN NULL;
END; $fn$;

-- ── 4. Trigger declarations (drop-first for clean re-runs) ─────────────
DROP TRIGGER IF EXISTS trg_kg_ge_audit_ins ON knowledge.graph_entities;
CREATE TRIGGER trg_kg_ge_audit_ins
AFTER INSERT ON knowledge.graph_entities
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_ge_ins();

DROP TRIGGER IF EXISTS trg_kg_ge_audit_upd ON knowledge.graph_entities;
CREATE TRIGGER trg_kg_ge_audit_upd
AFTER UPDATE ON knowledge.graph_entities
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_ge_upd();

DROP TRIGGER IF EXISTS trg_kg_ge_audit_del ON knowledge.graph_entities;
CREATE TRIGGER trg_kg_ge_audit_del
AFTER DELETE ON knowledge.graph_entities
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_ge_del();

DROP TRIGGER IF EXISTS trg_kg_goe_audit_ins ON knowledge.graph_edges;
CREATE TRIGGER trg_kg_goe_audit_ins
AFTER INSERT ON knowledge.graph_edges
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_goe_ins();

DROP TRIGGER IF EXISTS trg_kg_goe_audit_upd ON knowledge.graph_edges;
CREATE TRIGGER trg_kg_goe_audit_upd
AFTER UPDATE ON knowledge.graph_edges
REFERENCING NEW TABLE AS updated_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_goe_upd();

DROP TRIGGER IF EXISTS trg_kg_goe_audit_del ON knowledge.graph_edges;
CREATE TRIGGER trg_kg_goe_audit_del
AFTER DELETE ON knowledge.graph_edges
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT
EXECUTE FUNCTION tackle.fn_audit_goe_del();

-- ── 5. V157 surface + erase-guard extension to include KG_AUDIT ────────
-- Same definitions as origin/main V157, with the category lists widened.
-- CREATE OR REPLACE keeps recent_audit's signature and the guard trigger.

-- 5a. Friendly view now covers all three audit categories.
CREATE OR REPLACE VIEW tackle.audit_trail AS
SELECT
  id,
  timestamp,
  category,
  -- REGISTRY_AUDIT rows say 'on <table>' after the op; NEBULA_AUDIT and
  -- KG_AUDIT rows carry the table in details. Derive uniformly from
  -- details when present.
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
WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT', 'KG_AUDIT');

-- 5b. Recency partial index now covers KG_AUDIT too.
-- (Schema-qualified: indexes live in their table's schema, and an
-- unqualified DROP may not resolve through every role's search_path.)
DROP INDEX IF EXISTS tackle.idx_system_logs_audit_recent;
CREATE INDEX idx_system_logs_audit_recent
  ON tackle.system_logs (timestamp DESC)
  WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT', 'KG_AUDIT');

-- 5c. The erase guard now also refuses KG_AUDIT erasure.
CREATE OR REPLACE FUNCTION tackle.fn_guard_audit_erase() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  v_audit_rows bigint;
BEGIN
  SELECT count(*) INTO v_audit_rows
  FROM deleted_rows
  WHERE category IN ('REGISTRY_AUDIT', 'NEBULA_AUDIT', 'KG_AUDIT');

  IF v_audit_rows > 0
     AND COALESCE(current_setting('tackle.allow_audit_erase', true), 'off') <> 'on'
  THEN
    RAISE EXCEPTION
      'audit-erase refused: % audit rows (REGISTRY_AUDIT/NEBULA_AUDIT/KG_AUDIT) in DELETE scope; to erase deliberately, run inside a transaction with SET LOCAL tackle.allow_audit_erase = ''on''',
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
