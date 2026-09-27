-- wf-lint-allow: migration-dup-prefix — superseded rewrite of 026-migrate-receipts, kept as a historical record; conduit's schema ships via src/db.ts inline migrations, no file-runner applies these
-- Migration 026-v2 — Migrate vision.receipts → execution.receipts (corrected)
--
-- CORRECTED VERSION: Joins with nebula.plans instead of conduit.plans.
-- The original 026 joined with conduit.plans which was empty, resulting
-- in 0 rows migrated. This version uses nebula.plans which has the data.
--
-- This migration is idempotent: it checks for existing legacy records
-- before inserting. Safe to re-run.
--
-- Prerequisite: 025-execution-schema.sql must be applied first.

BEGIN;

-- ============================================================
-- 1. Create legacy execution.requests for each plan with receipts
--
-- Uses nebula.plans (not conduit.plans) as the plan source.
-- ============================================================

INSERT INTO execution.requests (
    business_key, title, intent_type, objective, status,
    source_plan_id, created_at, updated_at
)
SELECT
    'legacy-plan-' || p.id AS business_key,
    COALESCE(p.title, 'Legacy plan ' || p.id) AS title,
    'legacy' AS intent_type,
    COALESCE(p.goal, '') AS objective,
    -- Derive status from the plan's latest receipt
    CASE
        WHEN EXISTS (
            SELECT 1 FROM vision.receipts r2
            WHERE r2.plan_id = p.id AND r2.type = 'REVIEW_PASS'
        ) THEN 'COMPLETED'
        WHEN EXISTS (
            SELECT 1 FROM vision.receipts r3
            WHERE r3.plan_id = p.id AND r3.type IN ('CANCELLED','ABANDONED')
        ) THEN 'CANCELLED'
        ELSE 'READY'
    END AS status,
    p.id AS source_plan_id,
    MIN(r.created_at) AS created_at,
    MAX(r.created_at) AS updated_at
FROM nebula.plans p
JOIN vision.receipts r ON r.plan_id = p.id
WHERE NOT EXISTS (
    -- Skip if already migrated
    SELECT 1 FROM execution.requests er
    WHERE er.source_plan_id = p.id
)
GROUP BY p.id, p.title, p.goal;

-- ============================================================
-- 2. Create synthetic leases for legacy requests
-- (required by FK constraint on attempts.lease_id)
-- ============================================================

INSERT INTO execution.leases (
    request_id, executor_id, status, ttl_seconds,
    acquired_at, expires_at, released_at, created_at
)
SELECT
    er.id AS request_id,
    'legacy' AS executor_id,
    'RELEASED' AS status,
    0 AS ttl_seconds,
    er.created_at AS acquired_at,
    er.created_at AS expires_at,
    er.updated_at AS released_at,
    er.created_at AS created_at
FROM execution.requests er
WHERE er.source_plan_id IS NOT NULL
  AND er.intent_type = 'legacy'
  AND NOT EXISTS (
    SELECT 1 FROM execution.leases el
    WHERE el.request_id = er.id
);

-- ============================================================
-- 3. Create a legacy execution.attempts row for each request
-- ============================================================

INSERT INTO execution.attempts (
    lease_id, request_id, executor_id, status,
    started_at, completed_at, created_at
)
SELECT
    el.id AS lease_id,
    er.id AS request_id,
    'legacy' AS executor_id,
    CASE
        WHEN er.status = 'COMPLETED' THEN 'SUCCEEDED'
        WHEN er.status = 'CANCELLED' THEN 'FAILED'
        ELSE 'RUNNING'
    END AS status,
    er.created_at AS started_at,
    er.updated_at AS completed_at,
    er.created_at AS created_at
FROM execution.requests er
JOIN execution.leases el ON el.request_id = er.id
WHERE er.source_plan_id IS NOT NULL
  AND er.intent_type = 'legacy'
  AND NOT EXISTS (
    SELECT 1 FROM execution.attempts ea
    WHERE ea.request_id = er.id
  );

-- ============================================================
-- 4. Migrate vision.receipts → execution.receipts
-- ============================================================

INSERT INTO execution.receipts (
    attempt_id, request_id, type, agent_role,
    summary, metadata, lineage_source, lineage_original_id,
    issued_at
)
SELECT
    ea.id AS attempt_id,
    er.id AS request_id,
    vr.type AS type,
    vr.agent_role AS agent_role,
    COALESCE(vr.summary, '') AS summary,
    COALESCE(
        vr.metadata_json::jsonb,
        '{}'::jsonb
    ) AS metadata,
    'vision.receipts' AS lineage_source,
    vr.id AS lineage_original_id,
    vr.created_at AS issued_at
FROM vision.receipts vr
JOIN execution.requests er ON er.source_plan_id = vr.plan_id
JOIN execution.attempts ea ON ea.request_id = er.id
WHERE NOT EXISTS (
    -- Skip if already migrated
    SELECT 1 FROM execution.receipts er2
    WHERE er2.lineage_original_id = vr.id
);

-- ============================================================
-- 5. Verification
-- ============================================================

DO $$ DECLARE
    v_count INTEGER;
    e_count INTEGER;
    req_count INTEGER;
    attempt_count INTEGER;
    lease_count INTEGER;
BEGIN
    SELECT count(*) INTO v_count FROM vision.receipts;
    SELECT count(*) INTO e_count FROM execution.receipts;
    SELECT count(*) INTO req_count FROM execution.requests WHERE source_plan_id IS NOT NULL AND intent_type = 'legacy';
    SELECT count(*) INTO attempt_count FROM execution.attempts WHERE executor_id = 'legacy';
    SELECT count(*) INTO lease_count FROM execution.leases WHERE executor_id = 'legacy';

    RAISE NOTICE 'Migration 026-v2 complete:';
    RAISE NOTICE '  vision.receipts: % rows', v_count;
    RAISE NOTICE '  execution.receipts: % rows (migrated)', e_count;
    RAISE NOTICE '  execution.requests: % legacy requests created', req_count;
    RAISE NOTICE '  execution.attempts: % legacy attempts created', attempt_count;
    RAISE NOTICE '  execution.leases: % legacy leases created', lease_count;

    IF e_count < v_count THEN
        RAISE WARNING 'Not all vision.receipts were migrated (% of %)', e_count, v_count;
    END IF;
END $$;

COMMIT;
