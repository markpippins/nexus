-- V181: wind.node_requirements — the demand-side table behind the #314
-- NodeRequirement contract and the wind resolver (#317/#318).
--
-- Why: the resolver's requirements route queries wind.node_requirements,
-- which does not exist on live (verified 2026-09-18: GET /nodes/<real-uuid>/
-- requirements → 500, "relation does not exist"; the #317 suite mocked the
-- DB so the SQL shape drifted once already — see #318's SQL-shape pins).
-- This migration is soak precondition P0 from the enforce-flip gate design
-- (thread 1adce409, comment 0402e9b2). Without it the warn-mode soak cannot
-- measure the demand side at all.
--
-- Shape: contract columns (node_id, capability_key, role_credential,
-- last_verdict) + the house bitemporal pair (valid_*/recorded_*_dt with the
-- V175 house sentinel '9999-12-31 00:00:00+00') so requirements themselves
-- carry bitemporal history, matching nebula.roles_history naming exactly.
-- The V174 vocabulary is enforced by CHECK — the collapse-guard becomes
-- structural, not just classifier behavior.
--
-- Seeding (P2): the four stable f0000000-* Requirement Lifecycle nodes
-- carry the flow-#1 demands so the soak resolves real semantics:
--   triage    → planner credential (who may triage incoming work)
--   decide    → can_greenlight credential (authority at the decision gate)
--   implement → engineer credential (implementation capability holder)
--   review    → can_verify_work_requests capability + verify-holder class
--
-- Idempotency: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING seeds.
-- Staged inert: creates one table + triggers; no service restart required
-- (the resolver picks the table up on its next query); no data movement.

BEGIN;

-- ── 1. Gate ────────────────────────────────────────────────────────────
DO $gate$
BEGIN
  IF to_regclass('wind.workflow_nodes') IS NULL THEN
    RAISE EXCEPTION 'V181-GATE-001: wind.workflow_nodes does not exist — nothing to attach requirements to';
  END IF;
  IF to_regclass('nebula.capabilities') IS NULL THEN
    RAISE EXCEPTION 'V181-GATE-002: nebula.capabilities does not exist — apply V172 first (capability rows are referenced by FK)';
  END IF;
END
$gate$;

-- ── 2. Table ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wind.node_requirements (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id         uuid NOT NULL REFERENCES wind.workflow_nodes(id) ON DELETE CASCADE,
  capability_key  text,
  role_credential text,
  last_verdict    text
    CHECK (last_verdict IN ('satisfied','satisfied-stale','unsatisfied',
                            'unreachable','refused','unknown')),
  -- bitemporal pair, house V175 sentinel for open intervals; naming
  -- matches nebula.roles_history (recorded_on_dt / recorded_until_dt)
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_until     timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00'::timestamptz,
  recorded_on_dt  timestamptz NOT NULL DEFAULT now(),
  recorded_until_dt timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00'::timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  -- a requirement names at least one demand: what the work needs
  CONSTRAINT node_requirements_demand_required
    CHECK (capability_key IS NOT NULL OR role_credential IS NOT NULL),
  -- capability demands must name a registered capability (V172 registry,
  -- close-then-insert convention: old capability rows close rather than
  -- update, so FK-by-name stays stable across renames)
  CONSTRAINT node_requirements_capability_fk
    FOREIGN KEY (capability_key) REFERENCES nebula.capabilities(name)
    ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS node_requirements_node_idx
  ON wind.node_requirements (node_id);

-- One open demand per (node, capability) and per (node, role): re-seeding
-- and manual inserts cannot fork the open interval. Closed rows carry
-- finite valid_until and are exempt (V175 partial-index convention).
CREATE UNIQUE INDEX IF NOT EXISTS node_requirements_open_node_capability_uq
  ON wind.node_requirements (node_id, capability_key)
  WHERE capability_key IS NOT NULL
    AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS node_requirements_open_node_role_uq
  ON wind.node_requirements (node_id, role_credential)
  WHERE role_credential IS NOT NULL
    AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

-- ── 3. NEBULA_AUDIT family (V177 pattern, statement triggers) ──────────
-- Gates on the V156 helper rather than duplicating it under a second owner.
-- Three per-op functions + drop-first triggers, exactly the V177 shape.
CREATE OR REPLACE FUNCTION wind.fn_node_requirements_audit()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE v_rows bigint; v_keys text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT count(*), '(statement-level)' INTO v_rows, v_keys FROM deleted_rows;
  ELSIF TG_OP = 'UPDATE' THEN
    SELECT count(*), string_agg(DISTINCT
             coalesce(capability_key, '~') || '/' || coalesce(role_credential, '~'), ', ')
      INTO v_rows, v_keys FROM updated_rows;
  ELSE
    SELECT count(*), string_agg(DISTINCT
             coalesce(capability_key, '~') || '/' || coalesce(role_credential, '~'), ', ')
      INTO v_rows, v_keys FROM inserted_rows;
  END IF;
  PERFORM tackle.fn_nebula_audit_log('node_requirements', TG_OP, v_rows, v_keys);
  RETURN NULL;
END; $fn$;

DROP TRIGGER IF EXISTS trg_node_req_audit_ins ON wind.node_requirements;
CREATE TRIGGER trg_node_req_audit_ins
AFTER INSERT ON wind.node_requirements
REFERENCING NEW TABLE AS inserted_rows
FOR EACH STATEMENT EXECUTE FUNCTION wind.fn_node_requirements_audit();

DROP TRIGGER IF EXISTS trg_node_req_audit_upd ON wind.node_requirements;
CREATE TRIGGER trg_node_req_audit_upd
AFTER UPDATE ON wind.node_requirements
REFERENCING OLD TABLE AS updated_rows
FOR EACH STATEMENT EXECUTE FUNCTION wind.fn_node_requirements_audit();

DROP TRIGGER IF EXISTS trg_node_req_audit_del ON wind.node_requirements;
CREATE TRIGGER trg_node_req_audit_del
AFTER DELETE ON wind.node_requirements
REFERENCING OLD TABLE AS deleted_rows
FOR EACH STATEMENT EXECUTE FUNCTION wind.fn_node_requirements_audit();

DO $verify$
DECLARE v_count integer;
BEGIN
  SELECT count(*) INTO v_count FROM pg_trigger
  WHERE tgrelid = 'wind.node_requirements'::regclass
    AND tgname IN ('trg_node_req_audit_ins','trg_node_req_audit_upd','trg_node_req_audit_del')
    AND NOT tgisinternal;
  IF v_count <> 3 THEN
    RAISE EXCEPTION 'V181-GATE-003: expected 3 audit triggers on wind.node_requirements, found %', v_count;
  END IF;
END
$verify$;

-- ── 4. Seed the Requirement Lifecycle demands (P2) ─────────────────────
-- Stable f0000000-* nodes only; ON CONFLICT DO NOTHING keeps re-runs
-- silent. role_credential demands name LIVE roles (verified 2026-09-18:
-- planner/lead-engineer hold can_greenlight; engineer/tester hold
-- can_verify_work_requests) so every seeded demand resolves for real.
-- The review node carries both demand kinds (V172 capability + a specific
-- verify-holder) so the soak exercises both resolution paths.
INSERT INTO wind.node_requirements (node_id, capability_key, role_credential)
SELECT n.id, v.capability_key, v.role_credential
FROM (VALUES
  ('f0000000-0000-0000-0000-000000000001', NULL, NULL, 'planner'),        -- triage
  ('f0000000-0000-0000-0000-000000000002', NULL, NULL, 'lead-engineer'),  -- decide: greenlight holder
  ('f0000000-0000-0000-0000-000000000003', NULL, NULL, 'engineer'),       -- implement
  ('f0000000-0000-0000-0000-000000000004', 'has-active-shrapnel-protocol', NULL, NULL), -- review: capability demand
  ('f0000000-0000-0000-0000-000000000004', NULL, NULL, 'tester')          -- review: verify-holder demand
) AS v(node_key, capability_key, placeholder, role_credential)
JOIN wind.workflow_nodes n ON n.id::text = v.node_key
ON CONFLICT DO NOTHING;

COMMIT;
