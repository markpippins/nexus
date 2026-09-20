-- =============================================================================
-- V190 (DBA): scratch role-vocabulary widening — G1 ratified-12 sweep close-out.
-- =============================================================================
-- Closes the architect's BLOCKER (thread 443f826e, R1 11c57bbd): the Sep-16+
-- ratified-12 vocabulary widening was applied ad-hoc to
-- nebula.agent_records_history only. Two copies of the role CHECK remained
-- stale:
--
--   1. scratch.agent_records_history.agent_records_role_check
--      (21 roles — writes for ontologist / lead-engineer / sound-technician
--      through the scratch mirror-writer are rejected; the operator hit this
--      on lead-engineer)
--   2. sql/ci-bootstrap/nexus-ci-bootstrap.sql:9039
--      (same stale list — every fresh deploy is born-regressed; patched in
--      the same PR as this migration)
--
-- nebula.agent_records_history.agent_records_role_check is the SINGLE SOURCE
-- OF TRUTH (24 roles + ''). This migration pins that exact set and swaps
-- scratch's CHECK to it. The pin is NOT free to drift: the preflight reads
-- nebula's live constraint and refuses (loud, transaction-aborting) if it
-- differs from the pin — future vocabulary growth must land here and in
-- nebula together, or the gate stops the migration.
--
-- Safety (census 2026-09-20, titanium live):
--   - scratch.agent_records_history has NO triggers and NO dependent views
--     (scratch.agent_records reads NEBULA's history table, not scratch's)
--   - all 7849 existing rows satisfy the old (narrower) CHECK, and the new
--     set is a strict superset — the swap cannot violate existing rows
--   - no data is touched; the swap is metadata-only
--
-- NOT APPLIED TO LIVE AUTOMATICALLY: V-series applies on explicit operator
-- go, per house doctrine.
--
-- Apply:  psql -d nexus -f sql/V190__scratch_role_vocabulary_widening.sql
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Preflight 1: nebula's live constraint MUST equal the pinned set. Anything
-- else means the vocabulary has grown (or drifted) on the source of truth
-- without this migration being updated — refuse loudly.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    live_def text;
BEGIN
    SELECT pg_get_constraintdef(con.oid)
      INTO live_def
      FROM pg_constraint con
      JOIN pg_class c      ON c.oid = con.conrelid
      JOIN pg_namespace n  ON n.oid = c.relnamespace
     WHERE n.nspname = 'nebula'
       AND c.relname = 'agent_records_history'
       AND con.conname = 'agent_records_role_check';

    IF live_def IS NULL THEN
        RAISE EXCEPTION
            'V190 PREFLIGHT FAIL: nebula.agent_records_history.agent_records_role_check not found — source of truth missing';
    END IF;

    -- Normalized comparison: extract the role literals from both the live
    -- definition and the pin, sort them, and require set equality. The
    -- zero-or-more class captures the '' escape hatch at `role = ''::text`
    -- ('+' would miss it and make every comparison look drifted).
    IF (
        SELECT array_agg(x ORDER BY x)
          FROM unnest(ARRAY(
              SELECT (regexp_matches(live_def, '''([^'']*)''', 'g'))[1]
          )) AS x
    ) IS DISTINCT FROM (
        SELECT array_agg(x ORDER BY x)
          FROM unnest(ARRAY[
              '', 'architect', 'planner', 'builder', 'reviewer', 'critic',
              'analyst', 'inspector', 'engineer', 'engineer-ii', 'devops',
              'topologist', 'auditor', 'dba', 'epistemologist', 'operator',
              'sysadmin', 'DBA', 'tester', 'analyst-ii', 'design-synthesist',
              'layout-mechanic', 'ontologist', 'lead-engineer',
              'sound-technician'
          ]) AS x
    ) THEN
        RAISE EXCEPTION
            'V190 PREFLIGHT FAIL: nebula live role vocabulary has drifted from the pinned 24-role set (live: %). Update V190''s pin together with nebula, then re-apply.',
            left(live_def, 300);
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- Preflight 2: the scratch target must exist. (A missing table means a
-- foreign topology — refuse rather than guess.)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('scratch.agent_records_history') IS NULL THEN
        RAISE EXCEPTION
            'V190 PREFLIGHT FAIL: scratch.agent_records_history does not exist on this database';
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- Swap scratch's CHECK to the pinned (nebula-equal) set.
-- -----------------------------------------------------------------------------
ALTER TABLE scratch.agent_records_history
    DROP CONSTRAINT agent_records_role_check;

ALTER TABLE scratch.agent_records_history
    ADD CONSTRAINT agent_records_role_check
    CHECK (((role = ''::text) OR (role = ANY (ARRAY['architect'::text, 'planner'::text, 'builder'::text, 'reviewer'::text, 'critic'::text, 'analyst'::text, 'inspector'::text, 'engineer'::text, 'engineer-ii'::text, 'devops'::text, 'topologist'::text, 'auditor'::text, 'dba'::text, 'epistemologist'::text, 'operator'::text, 'sysadmin'::text, 'DBA'::text, 'tester'::text, 'analyst-ii'::text, 'design-synthesist'::text, 'layout-mechanic'::text, 'ontologist'::text, 'lead-engineer'::text, 'sound-technician'::text]))));

COMMIT;

-- -----------------------------------------------------------------------------
-- Post-apply verification (manual, read-only):
--
--   -- the three previously-rejected roles now pass through the scratch path:
--   INSERT INTO scratch.agent_records_history (record_type, role, title, content, ...)
--     VALUES (...,'lead-engineer',...) -- etc. (wrap in BEGIN/ROLLBACK)
--
--   -- constraint parity with the source of truth:
--   SELECT pg_get_constraintdef(con.oid)
--     FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
--    WHERE c.oid = 'scratch.agent_records_history'::regclass
--      AND con.conname = 'agent_records_role_check';
--   -- must be the nebula definition modulo whitespace
-- -----------------------------------------------------------------------------
