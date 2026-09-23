-- =============================================================================
-- V197 (DBA): role-vocabulary widening — add engineer-iii (Engineer III).
-- =============================================================================
-- First vocabulary growth performed THROUGH the V190 chain of custody rather
-- than ad-hoc: this migration moves nebula's live constraint, scratch's
-- mirror constraint, and the marker-designated in-repo authority together,
-- atomically.
--
-- Prior state: 24-role set (V190 pin, verified LIVE==PIN==BOOT green on
-- titanium 2026-09-23). New state: same set + the new engineer role (25
-- roles). Rationale: operator request to add Engineer III with the same
-- config as Engineer II (harness file, whitelists, seeds, and Assembly user
-- land in the same change set; intent record 70020763).
--
-- IDEMPOTENT by design (replays happen: wr-conf-042's throwaway-DB E2E
-- applies V-series after a bootstrap that may already carry the widened
-- vocabulary):
--   live == prior 24-role set  -> widen (normal path)
--   live == widened 25-role set -> NOTICE + no-op success
--   anything else               -> loud refusal (drift without a migration)
--
-- Safety (V190 audit still applies): the new set is a strict superset of the
-- pinned 24, so the swap cannot violate existing rows; no data is touched;
-- the change is metadata-only (constraint swaps).
--
-- NOT APPLIED TO LIVE AUTOMATICALLY: V-series applies on explicit operator
-- go, per house doctrine. (DBA applied this one same-session on the
-- operator's request to make the role functional; see change-log entry.)
--
-- Apply:  psql -d nexus -f sql/V197__role_vocabulary_widening_engineer_iii.sql
-- =============================================================================

-- Historical role-vocabulary pin for the engineer-iii widening. The unique
-- in-repo authority marker moved to V200 when Supervisor was added.

BEGIN;

DO $$
DECLARE
    target_vocab text[] := ARRAY[
        '', 'architect', 'planner', 'builder', 'reviewer', 'critic',
        'analyst', 'inspector', 'engineer', 'engineer-ii', 'engineer-iii',
        'devops', 'topologist', 'auditor', 'dba', 'epistemologist',
        'operator', 'sysadmin', 'DBA', 'tester', 'analyst-ii',
        'design-synthesist', 'layout-mechanic', 'ontologist',
        'lead-engineer', 'sound-technician'
    ];
    prior_vocab  text[] := array_remove(target_vocab, 'engineer-iii');
    live_def     text;
    live_vocab   text[];
    scratch_def  text;
    scratch_vocab text[];
    literal_list text;
BEGIN
    -- ── locate nebula's live constraint (the source of truth) ──
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
            'V197 PREFLIGHT FAIL: nebula.agent_records_history.agent_records_role_check not found — source of truth missing';
    END IF;

    -- Set-based comparison (the house convention: bin/role-vocab-drift.py's
    -- _vocab is a set) — robust against textual duplicates across replay
    -- generations, while still refusing genuinely different vocabularies.
    SELECT array_agg(x ORDER BY x)
      INTO live_vocab
      FROM (SELECT DISTINCT (regexp_matches(live_def, '''([^'']*)''', 'g'))[1] AS x
            FROM generate_series(1,1)) sub;

    IF live_vocab IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(prior_vocab) AS x)
       AND live_vocab IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(target_vocab) AS x) THEN
        RAISE EXCEPTION
            'V197 PREFLIGHT FAIL: nebula live role vocabulary matches neither the prior 24-role set nor the widened 25-role set (live: %). Resolve the drift before widening.',
            left(live_def, 300);
    END IF;

    IF live_vocab = (SELECT array_agg(x ORDER BY x) FROM unnest(target_vocab) AS x) THEN
        RAISE NOTICE
            'V197: nebula already carries the widened 25-role vocabulary — idempotent no-op (nothing to do).';
        RETURN;
    END IF;

    -- Build the literal list ONCE: 'role'::text, ... — used by both swaps.
    -- The '' structural escape is emitted separately (as role = ''::text),
    -- NEVER inside the array — otherwise the rebuilt constraint carries a
    -- duplicate '' and re-apply preflights refuse (idempotency breaks).
    SELECT string_agg(quote_literal(r) || '::text', ', ' ORDER BY ord)
      INTO literal_list
      FROM unnest(target_vocab) WITH ORDINALITY AS t(r, ord)
      WHERE r <> '';

    -- ── widen nebula (source of truth) ──
    ALTER TABLE nebula.agent_records_history
        DROP CONSTRAINT agent_records_role_check;
    EXECUTE 'ALTER TABLE nebula.agent_records_history '
         || 'ADD CONSTRAINT agent_records_role_check '
         || 'CHECK (((role = ''''::text) OR (role = ANY (ARRAY[' || literal_list || ']))))';

    -- ── widen scratch's mirror (keep it in lockstep, V190's defect class) ──
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
                RAISE NOTICE 'V197: scratch mirror already widened — skipping.';
            ELSE
                ALTER TABLE scratch.agent_records_history
                    DROP CONSTRAINT agent_records_role_check;
                EXECUTE 'ALTER TABLE scratch.agent_records_history '
                     || 'ADD CONSTRAINT agent_records_role_check '
                     || 'CHECK (((role = ''''::text) OR (role = ANY (ARRAY[' || literal_list || ']))))';
            END IF;
        END IF;
    END IF;

    RAISE NOTICE 'V197: role vocabulary widened to 25 roles (engineer-iii added) on nebula + scratch.';
END $$;

COMMIT;

-- -----------------------------------------------------------------------------
-- Post-apply verification (manual, read-only):
--
--   python3 bin/role-vocab-drift.py --json   -- must report drifted:false,
--                                            -- 25 roles on all three surfaces
--
--   -- engineer-iii writes now pass through nebula and the scratch mirror:
--   BEGIN; INSERT INTO nebula.agent_records_history (record_type, role, title, content)
--     VALUES ('report','engineer-iii','probe','probe'); ROLLBACK;
-- -----------------------------------------------------------------------------
