-- =============================================================================
-- V200: role-vocabulary widening — add Supervisor.
-- =============================================================================
-- Operator directive: add a full Supervisor role responsible initially for
-- role registration/configuration and doctrine + OpenCode projection
-- regeneration. WorkRequest execution authority remains deferred.
--
-- This migration moves the marker-designated role-vocabulary authority from
-- V197 to V200, widens nebula's live CHECK, keeps scratch's mirror in
-- lockstep, and is idempotent. It is NOT auto-applied: apply only after the
-- open role-memory restore/security incident is resolved and with explicit DBA
-- authorization.
--
-- Apply:
--   psql -d nexus -f sql/V200__add_supervisor_role.sql
-- =============================================================================

-- =============================================================================
-- ROLE-VOCAB PIN — the marker-designated in-repo authority (2026-09-23).
--
-- WIDENING THE VOCABULARY: move this marker into the next migration together
-- with its new pin, update sql/ci-bootstrap/nexus-ci-bootstrap.sql to the same
-- list, and remove the marker from the prior file. Exactly one marker may
-- exist repo-wide. CI enforces the invariant in
-- bin/tests/test_role_vocab_parity.py.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    target_vocab text[] := ARRAY[
        '', 'architect', 'planner', 'builder', 'reviewer', 'critic',
        'analyst', 'inspector', 'engineer', 'engineer-ii', 'engineer-iii',
        'devops', 'topologist', 'auditor', 'dba', 'epistemologist',
        'operator', 'sysadmin', 'DBA', 'tester', 'analyst-ii',
        'design-synthesist', 'layout-mechanic', 'ontologist',
        'lead-engineer', 'sound-technician', 'supervisor'
    ];
    prior_vocab  text[] := array_remove(target_vocab, 'supervisor');
    live_def     text;
    live_vocab   text[];
    scratch_def  text;
    scratch_vocab text[];
    literal_list text;
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
            'V200 PREFLIGHT FAIL: nebula.agent_records_history.agent_records_role_check not found';
    END IF;

    SELECT array_agg(x ORDER BY x)
      INTO live_vocab
      FROM (SELECT DISTINCT (regexp_matches(live_def, '''([^'']*)''', 'g'))[1] AS x
            FROM generate_series(1,1)) sub;

    IF live_vocab IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(prior_vocab) AS x)
       AND live_vocab IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(target_vocab) AS x) THEN
        RAISE EXCEPTION
            'V200 PREFLIGHT FAIL: live role vocabulary matches neither the prior 25-role set nor the widened 26-role set (live: %). Resolve drift before widening.',
            left(live_def, 300);
    END IF;

    -- Always compute the target literal list. Even when nebula already carries
    -- the target vocabulary, the scratch mirror may still be stale; returning
    -- early here would leave that drift permanently unrepaired.
    SELECT string_agg(quote_literal(r) || '::text', ', ' ORDER BY ord)
      INTO literal_list
      FROM unnest(target_vocab) WITH ORDINALITY AS t(r, ord)
     WHERE r <> '';

    IF live_vocab = (SELECT array_agg(x ORDER BY x) FROM unnest(target_vocab) AS x) THEN
        RAISE NOTICE 'V200: nebula already carries Supervisor — skipping nebula swap and checking scratch mirror.';
    ELSE
        ALTER TABLE nebula.agent_records_history
            DROP CONSTRAINT agent_records_role_check;
        EXECUTE 'ALTER TABLE nebula.agent_records_history '
             || 'ADD CONSTRAINT agent_records_role_check '
             || 'CHECK (((role = ''''::text) OR (role = ANY (ARRAY[' || literal_list || ']))))';
    END IF;

    IF to_regclass('scratch.agent_records_history') IS NOT NULL THEN
        SELECT pg_get_constraintdef(con.oid)
          INTO scratch_def
          FROM pg_constraint con
          JOIN pg_class c      ON c.oid = con.conrelid
          JOIN pg_namespace n  ON n.oid = c.relnamespace
         WHERE n.nspname = 'scratch'
           AND c.relname = 'agent_records_history'
           AND con.conname = 'agent_records_role_check';

        IF scratch_def IS NOT NULL THEN
            SELECT array_agg(x ORDER BY x)
              INTO scratch_vocab
              FROM (SELECT DISTINCT (regexp_matches(scratch_def, '''([^'']*)''', 'g'))[1] AS x
                    FROM generate_series(1,1)) sub;

            IF scratch_vocab = (SELECT array_agg(x ORDER BY x) FROM unnest(target_vocab) AS x) THEN
                RAISE NOTICE 'V200: scratch mirror already widened — skipping.';
            ELSE
                ALTER TABLE scratch.agent_records_history
                    DROP CONSTRAINT agent_records_role_check;
                EXECUTE 'ALTER TABLE scratch.agent_records_history '
                     || 'ADD CONSTRAINT agent_records_role_check '
                     || 'CHECK (((role = ''''::text) OR (role = ANY (ARRAY[' || literal_list || ']))))';
            END IF;
        END IF;
    END IF;

    RAISE NOTICE 'V200: role vocabulary widened to 26 roles (supervisor added) on nebula + scratch.';
END $$;

COMMIT;

-- Post-apply verification:
--   python3 bin/role-vocab-drift.py --json
--   must report drifted:false and 26 roles on LIVE/PIN/BOOT after bootstrap
--   regeneration.
