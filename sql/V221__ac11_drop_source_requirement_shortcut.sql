-- =============================================================================
-- V221 — AC11 cutover: remove the single-source requirement shortcut
-- Plan 8261650 AC11 · STAGED · NOT APPLIED TO LIVE
-- =============================================================================
-- Preconditions:
--   * V220 requested requirement/artifact substrate is present.
--   * Every non-null resolution.work_request.source_requirement_id has a
--     matching resolution.request_requirement_link row with relation='source'.
--     If not, this migration refuses before dropping anything.
--
-- This migration is the live half of AC11. It is intentionally not applied by
-- the code PR: the code consumers are repointed first, and the live apply is
-- the separately approved step.
--
-- It removes the shortcut column ONLY after proving the normalized link path
-- is complete. It rebuilds the two dependent views without that column:
--   * resolution.work_request_backlog
--   * nebula.work_requests
--   * nebula.v_work_request_overview (depends on nebula.work_requests)
--
-- The vision views do not expose source_requirement_id and are left intact.
-- No data rows are deleted. No ledger row is written by this file.
-- =============================================================================

BEGIN;

-- ── preflight: link substrate and completeness ─────────────────────────────
DO $$
DECLARE
    v_missing bigint;
    v_has_column boolean;
BEGIN
    IF to_regclass('resolution.work_request') IS NULL THEN
        RAISE EXCEPTION 'V221-PREFLIGHT-001: resolution.work_request is missing';
    END IF;
    IF to_regclass('resolution.request_requirement_link') IS NULL THEN
        RAISE EXCEPTION 'V221-PREFLIGHT-002: resolution.request_requirement_link is missing; apply V220 first';
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'resolution'
           AND table_name = 'work_request'
           AND column_name = 'source_requirement_id'
    ) INTO v_has_column;

    IF v_has_column THEN
        SELECT count(*) INTO v_missing
          FROM resolution.work_request w
         WHERE w.source_requirement_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM resolution.request_requirement_link l
                WHERE l.request_id = w.id
                  AND l.requirement_id = w.source_requirement_id
                  AND l.relation = 'source'
           );

        IF v_missing > 0 THEN
            RAISE EXCEPTION
                'V221-PREFLIGHT-003: % non-null source_requirement_id rows lack a normalized source link; refusing to drop the shortcut',
                v_missing;
        END IF;
    END IF;
END $$;

-- ── normalized read projection used by the application consumers ──────────
-- Static SQL in the services joins this view; no consumer interpolates a SQL
-- fragment. The view only exposes current bitemporal link rows.
CREATE OR REPLACE VIEW nebula.v_work_request_requirement AS
SELECT rrl.request_id,
       rrl.requirement_id,
       rrl.relation,
       rrl.provenance,
       req.system_id,
       req.subsystem_id,
       req.feature_id
  FROM resolution.request_requirement_link rrl
  JOIN nebula.requirements req ON req.id = rrl.requirement_id
 WHERE rrl.valid_until = 'infinity'::timestamptz
   AND rrl.recorded_until_dt = 'infinity'::timestamptz;

COMMENT ON VIEW nebula.v_work_request_requirement IS
    'Plan 8261650 AC11: current normalized request-to-requirement links with the requirement hierarchy. Read-only projection; consumers never use the retired single-source shortcut.';

-- ── remove the shortcut and the views that expose it ───────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'resolution'
           AND table_name = 'work_request'
           AND column_name = 'source_requirement_id'
    ) THEN
        -- dependent first, then the exposing views
        DROP VIEW IF EXISTS scratch.v_work_request_overview;
        DROP VIEW IF EXISTS nebula.v_work_request_overview;
        DROP VIEW IF EXISTS nebula.work_requests;
        DROP VIEW IF EXISTS resolution.work_request_backlog;

        ALTER TABLE resolution.work_request
            DROP COLUMN source_requirement_id;
    END IF;
END $$;

-- ── rebuild the backlog view without the shortcut ──────────────────────────
CREATE OR REPLACE VIEW resolution.work_request_backlog AS
SELECT id,
    asset_id,
    title,
    description,
    source_specification_id,
    business_status,
    intent,
    context,
    constraints,
    created_by,
    dco_json,
    legacy_id,
    plan_id,
    step_outputs,
    consumed_at,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt,
    request_kind,
    ARRAY( SELECT e.role
           FROM resolution.request_role_eligibility e
          WHERE e.request_id = wr.id
          ORDER BY e.role) AS eligible_roles
   FROM resolution.work_request wr
  WHERE (business_status = ANY (ARRAY['DRAFT'::text, 'APPROVED'::text])) AND consumed_at IS NULL AND (EXISTS ( SELECT 1
           FROM resolution.request_role_eligibility e
          WHERE e.request_id = wr.id));

COMMENT ON VIEW resolution.work_request_backlog IS
    'Plan 8261650 AC11: open, consumed-null, role-eligible work requests. The source requirement is obtained through resolution.request_requirement_link; the single-source shortcut column is gone.';

-- ── rebuild nebula.work_requests without the shortcut ──────────────────────
CREATE OR REPLACE VIEW nebula.work_requests AS
SELECT c.id,
    c.title,
    c.description,
    c.source_specification_id,
    c.business_status,
    c.intent,
    c.context,
    c.constraints,
    c.created_by,
    c.created_at,
    c.updated_at,
    c.dco_json,
    c.legacy_id,
    c.plan_id,
    c.step_outputs,
    c.consumed_at,
    c.valid_from,
    c.valid_until,
    c.recorded_on_dt,
    c.recorded_until_dt,
    c.asset_id,
    coalesce(c.entity_key, (c.dco_json::jsonb)->>'entity_key') AS entity_key
FROM resolution.work_request c
WHERE c.legacy_id LIKE 'nebula.work_requests_history:%';

-- ── rebuild the overview view against the rebuilt surface ──────────────────
CREATE OR REPLACE VIEW nebula.v_work_request_overview AS
SELECT wr.id,
    wr.legacy_id,
    wr.title,
    wr.plan_id,
    wr.business_status,
    wr.consumed_at,
    wr.created_at AS business_created_at,
    er.status AS execution_status,
    er.id AS execution_request_id,
    er.business_key,
    vr.status AS runtime_status,
    vr.work_request_uuid AS vision_id,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'CANCELLED'
        WHEN vr.status = 'rejected' THEN 'REJECTED'
        WHEN vr.status = 'failed' THEN 'FAILED'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'COMPLETE'
        WHEN wr.business_status = 'COMPLETED' THEN 'AWAITING_REVIEW'
        WHEN vr.status = 'settled' THEN 'SETTLED_AWAITING_BUSINESS'
        WHEN vr.status = 'claimed' THEN 'RUNNING'
        WHEN vr.status = 'validated' THEN 'VALIDATED'
        WHEN vr.status = 'queued' THEN 'QUEUED'
        WHEN vr.status = 'deferred' THEN 'DEFERRED'
        WHEN vr.status = 'noop' THEN 'NOOP'
        WHEN er.status = 'READY' THEN 'READY_FOR_EXECUTION'
        WHEN er.status = 'ADMITTED' THEN 'ADMITTED'
        WHEN er.status = 'VALIDATED' THEN 'EXECUTION_VALIDATED'
        WHEN er.status = 'COMPLETED' THEN 'EXECUTION_COMPLETE'
        WHEN er.status = 'FAILED' THEN 'EXECUTION_FAILED'
        WHEN wr.business_status = 'DISPATCHED' THEN 'DISPATCHED'
        WHEN wr.business_status = 'APPROVED' THEN 'APPROVED'
        WHEN wr.business_status = 'DRAFT' AND wr.consumed_at IS NULL THEN 'PENDING'
        WHEN wr.business_status = 'DRAFT' THEN 'DRAFT'
        ELSE 'UNKNOWN'
    END AS effective_status,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'Business intent cancelled'
        WHEN vr.status = 'rejected' THEN 'Execution rejected'
        WHEN vr.status = 'failed' THEN 'Execution failed'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'Complete - business objective satisfied'
        WHEN wr.business_status = 'COMPLETED' THEN 'Awaiting business review'
        WHEN vr.status = 'settled' THEN 'Settled - awaiting business confirmation'
        WHEN vr.status = 'claimed' THEN 'Worker actively executing'
        WHEN vr.status = 'validated' THEN 'Validated - awaiting execution'
        WHEN vr.status = 'queued' THEN 'Queued for execution'
        WHEN er.status = 'READY' THEN 'Ready for execution'
        WHEN wr.business_status = 'DISPATCHED' THEN 'Dispatched to execution layer'
        WHEN wr.business_status = 'APPROVED' THEN 'Approved - awaiting compilation'
        WHEN wr.consumed_at IS NULL THEN 'Pending - not yet consumed'
        ELSE 'Draft'
    END AS effective_status_description
FROM nebula.work_requests wr
LEFT JOIN execution.requests er
       ON er.source_wr_id::text = substring(wr.legacy_id from 30)
LEFT JOIN vision.work_requests vr
       ON vr.work_request_uuid = substring(wr.legacy_id from 30)
WHERE wr.legacy_id LIKE 'nebula.work_requests_history:%';

-- V187 left this postcondition mirror in place; rebuild it against the same
-- surface so the drop does not orphan it.
CREATE SCHEMA IF NOT EXISTS scratch;
CREATE OR REPLACE VIEW scratch.v_work_request_overview AS
SELECT * FROM nebula.v_work_request_overview;

-- ── postconditions ─────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'resolution'
           AND table_name = 'work_request'
           AND column_name = 'source_requirement_id'
    ) THEN
        RAISE EXCEPTION 'V221-POST-001: resolution.work_request.source_requirement_id still exists';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema IN ('resolution', 'nebula')
           AND table_name IN ('work_request_backlog', 'work_requests')
           AND column_name = 'source_requirement_id'
    ) THEN
        RAISE EXCEPTION 'V221-POST-002: a rebuilt view still exposes source_requirement_id';
    END IF;

    IF to_regclass('resolution.request_requirement_link') IS NULL THEN
        RAISE EXCEPTION 'V221-POST-003: normalized requirement link table is missing';
    END IF;

    IF to_regclass('nebula.v_work_request_requirement') IS NULL THEN
        RAISE EXCEPTION 'V221-POST-004: normalized requirement read view is missing';
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- ROLLBACK (rehearsal only; NOT executed by this file)
-- =============================================================================
-- The shortcut values are recoverable from the normalized source links:
--   ALTER TABLE resolution.work_request ADD COLUMN source_requirement_id uuid;
--   UPDATE resolution.work_request w
--      SET source_requirement_id = l.requirement_id
--     FROM resolution.request_requirement_link l
--    WHERE l.request_id = w.id
--      AND l.relation = 'source'
--      AND l.valid_until = 'infinity'::timestamptz
--      AND l.recorded_until_dt = 'infinity'::timestamptz;
-- Then recreate the pre-V221 views from V187. A rollback must be rehearsed on a
-- throwaway database; this file does not perform it.
-- =============================================================================
