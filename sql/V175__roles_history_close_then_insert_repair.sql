-- =============================================================================
--  V175 — roles_history close-then-insert repair (DBA)
--
--  Captures in canonical DDL the constraint repair applied live during the
--  auditor capability grant v0.1 (R2 79feb142, 2026-09-17). Two structural
--  facts were discovered empirically and are now doctrine:
--
--  1. nebula.roles is a VIEW over nebula.roles_history (bitemporal current-
--     row predicate). Role writes MUST target the history table: view-based
--     UPDATE/INSERT silently match nothing once the row's snapshot closes
--     mid-transaction (UPDATE 0 / INSERT 0 0).
--
--  2. roles_history carried a FULL UNIQUE(name), which made history itself
--     impossible (one snapshot per role, ever) and therefore made the
--     architect-ruled close-then-insert convention (thread 225dfbbb) structurally
--     unimplementable. Repair: drop the full unique, install a PARTIAL unique
--     index on OPEN snapshots only — exactly one open row per role name,
--     closed history permitted. Same guarantee for current-state reads; the
--     bitemporal chain becomes possible.
--
--  Live empirical proof: the auditor role's history chain (closed vocabulary-
--  only snapshot + open granted snapshot, valid_until meeting valid_from at
--  2026-09-17T01:25:52Z) is the convention working as designed.
--
--  Detection predicate (both the repair and the verify gate): a FULL unique
--  is a unique, non-primary, NON-PARTIAL index whose key is EXACTLY (name).
--  The primary key (roles_history_pkey) and any partial index are correctly
--  ignored — the naive "unique index without WHERE" heuristic also matches
--  the pkey and is wrong.
--
--  IMPORTANT for fresh bootstraps: sql/ci-bootstrap/nexus-ci-bootstrap.sql
--  is corrected in the same PR so CI-built databases carry the repair from
--  birth. Sections 1-2 additionally converge any database shape at apply
--  time, so both paths are safe.
--
--  Additive/repair only; idempotent on re-apply.
-- =============================================================================

BEGIN;

-- ── 1. Drop the full UNIQUE(name) if present (constraint OR index form) ─────
DROP INDEX IF EXISTS nebula.roles_name_open_key;  -- re-created below, order-independent

DO $$
DECLARE
    v_full_constraint text;
    v_full_index      text;
BEGIN
    -- constraint form: a unique constraint whose backing index is a FULL
    -- unique exactly on (name)
    SELECT c.conname INTO v_full_constraint
    FROM pg_constraint c
    WHERE c.conrelid = 'nebula.roles_history'::regclass
      AND c.contype = 'u'
      AND EXISTS (
            SELECT 1
            FROM pg_index ix
            WHERE ix.indexrelid = c.conindid
              AND ix.indisunique
              AND NOT ix.indisprimary
              AND ix.indpred IS NULL
              AND ix.indnkeyatts = 1
              AND (SELECT a.attname FROM pg_attribute a
                   WHERE a.attrelid = c.conrelid
                     AND a.attnum = ix.indkey[0]) = 'name');

    IF v_full_constraint IS NOT NULL THEN
        EXECUTE format('ALTER TABLE nebula.roles_history DROP CONSTRAINT %I', v_full_constraint);
        RAISE NOTICE 'V175: dropped full unique constraint % on roles_history', v_full_constraint;
    END IF;

    -- standalone index form (any remaining full unique exactly on (name);
    -- constraint-backed ones are gone after the branch above)
    SELECT ic.relname INTO v_full_index
    FROM pg_index ix
    JOIN pg_class ic ON ic.oid = ix.indexrelid
    JOIN pg_class tc ON tc.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE n.nspname = 'nebula'
      AND tc.relname = 'roles_history'
      AND ix.indisunique
      AND NOT ix.indisprimary
      AND ix.indpred IS NULL
      AND ix.indnkeyatts = 1
      AND (SELECT a.attname FROM pg_attribute a
           WHERE a.attrelid = tc.oid AND a.attnum = ix.indkey[0]) = 'name'
    LIMIT 1;

    IF v_full_index IS NOT NULL THEN
        EXECUTE format('DROP INDEX nebula.%I', v_full_index);
        RAISE NOTICE 'V175: dropped full unique index % on roles_history', v_full_index;
    END IF;

    IF v_full_constraint IS NULL AND v_full_index IS NULL THEN
        RAISE NOTICE 'V175: no full unique on roles_history (already repaired) — ok';
    END IF;
END $$;

-- ── 2. Partial unique: exactly one OPEN snapshot per role name ──────────────
--    This table's house open-sentinel is valid_until = '9999-12-31'
--    (NOT infinity — verified live: 0 rows use infinity, 23 use the
--    sentinel). Closed rows carry a finite valid_until and are exempt,
--    so bitemporal chains accumulate.
CREATE UNIQUE INDEX IF NOT EXISTS roles_name_open_key
    ON nebula.roles_history (name)
    WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz;

COMMENT ON INDEX nebula.roles_name_open_key IS
'V175 close-then-insert enabler: exactly one OPEN snapshot per role name (valid_until at the 9999-12-31 house sentinel). Closed snapshots carry finite valid_until and are exempt, so the bitemporal chain accumulates. Supersedes the V081-era full UNIQUE(name) (roles_name_key), which permitted only one snapshot per role ever and made the architect-ruled close-then-insert convention (thread 225dfbbb) structurally impossible.';

-- ── 3. ci-bootstrap consistency ─────────────────────────────────────────────
--    The bootstrap (sql/ci-bootstrap/nexus-ci-bootstrap.sql) is corrected
--    IN THE PR as a file edit: its roles_name_key UNIQUE(name) declaration
--    is replaced by the partial open-snapshot index, so CI-built databases
--    carry the repair from birth. Sections 1-2 above additionally converge
--    any database shape at apply time, so both paths are safe.

-- ── 4. Verify gate: hard, V081 style ────────────────────────────────────────
DO $$
DECLARE
    v_full_left int;
    v_open_key_ok boolean;
    v_multi_open int;
BEGIN
    -- no FULL unique exactly on (name) may remain — constraint-backed or
    -- standalone; the pkey and partial indexes are excluded by the predicate
    SELECT count(*) INTO v_full_left
    FROM pg_index ix
    JOIN pg_class ic ON ic.oid = ix.indexrelid
    JOIN pg_class tc ON tc.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE n.nspname = 'nebula'
      AND tc.relname = 'roles_history'
      AND ix.indisunique
      AND NOT ix.indisprimary
      AND ix.indpred IS NULL
      AND ix.indnkeyatts = 1
      AND (SELECT a.attname FROM pg_attribute a
           WHERE a.attrelid = tc.oid AND a.attnum = ix.indkey[0]) = 'name';
    IF v_full_left > 0 THEN
        RAISE EXCEPTION 'V175 verify: full unique on (name) still present on roles_history (%)', v_full_left
            USING ERRCODE = 'P0001';
    END IF;

    -- exactly-one-open guarantee must be active
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'nebula'
          AND tablename = 'roles_history'
          AND indexname = 'roles_name_open_key'
          AND indexdef LIKE '%WHERE%'
    ) INTO v_open_key_ok;
    IF NOT v_open_key_ok THEN
        RAISE EXCEPTION 'V175 verify: partial open-snapshot unique index roles_name_open_key missing'
            USING ERRCODE = 'P0001';
    END IF;

    -- no role may currently have more than one open snapshot
    SELECT count(*) INTO v_multi_open FROM (
        SELECT name FROM nebula.roles_history
        WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
        GROUP BY name HAVING count(*) > 1
    ) m;
    IF v_multi_open > 0 THEN
        RAISE EXCEPTION 'V175 verify: % role(s) with multiple OPEN snapshots', v_multi_open
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE '✅ V175 applied — roles_history permits close-then-insert history; exactly one open snapshot per role.';
END $$;

COMMIT;
