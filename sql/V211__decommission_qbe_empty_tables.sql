-- V211: Decommission QBE-era empty tables (shrapnel schema)
-- Per architect ruling 4dc4459e: decommission QBE-era empty tables in shrapnel.*
-- Tables IN RULING SCOPE: data_source, qbe_table, qbe_column, qbe_join,
--   qbe_join_type, qbe_table_column, value_type
-- Row-count guard: verify 0 rows before DROP (fail-fast if any data present)
--
-- ============================================================================
-- RENUMBERED V194 -> V211, AND SCOPED DOWN TO THE RULING (2026-10-04, DBA)
-- ============================================================================
-- This file was V194__decommission_qbe_empty_tables.sql, colliding with
-- V194__expression_register_producer.sql (#422). Renumbered per the V039
-- precedent in test_migration_number_uniqueness.py: the APPLIED member keeps
-- the number, the UNAPPLIED member is renumbered. Verified by live
-- introspection 2026-10-04 --
--   - expression_register_producer IS applied: the `expression-pipeline` row
--     exists in resolution.producer_registry with registered_by='V194'.
--   - THIS file was never applied: all eight QBE-era tables still exist.
-- So V194 belongs to #422 and this file moves. History preserved via git mv.
--
-- The same introspection found this migration was WRONG AS WRITTEN, in a way
-- that made it a live hazard rather than merely untidy:
--
--   1. SCOPE. It dropped shrapnel.field_type. Ruling 4dc4459e lists the
--      in-scope set as "data_source, qbe_*, value_type" -- field_type is not
--      in it, and no later ruling adds it.
--   2. field_type is NOT empty: 7 rows (the type-code lookup: Long, String,
--      Double, Boolean, Timestamp, JSONB, UUID).
--   3. It is STILL READ. nexus-core-shrapnel ShrapnelStore.java:61,66,68
--      serve listFieldTypes()/getFieldType() off it, and :51 counts it.
--   4. LIVE DEPENDENTS. shrapnel.field (50 rows) and shrapnel.value
--      (15,392 rows) hold FKs to it (fk_field_field_type,
--      fk_value_field_type). `DROP TABLE ... CASCADE` would have silently
--      torn down that constraint chain under real data.
--
-- The ruling's own row-count guard made this inert rather than destructive --
-- it aborts on field_type's 7 rows before reaching any DROP -- but only by
-- luck of ordering. This file was one edit away from cascading away a live
-- FK chain over 15k rows.
--
-- field_type is therefore REMOVED from this migration entirely: not from the
-- row guard, not from the DROP list. Ruling condition 3 required the orphaned
-- JPA entities to be removed in the same PR as the migration; for
-- field_type that never happened, so the ruling's precondition for dropping
-- it is unmet. Dropping it needs its own ruling, not a silent widening of
-- this one. See the disposition record for the open question.
--
-- qbe_table_column is likewise not named in the ruling's prose but IS a
-- `qbe_*` table, so it stays in scope -- it is empty (0 rows) and orphaned.
--
-- Net effect: this migration now drops exactly the tables ruling 4dc4459e
-- ruled on, all of which are empty on live, and touches nothing else.
-- ============================================================================

-- ============================================================================
-- 1. VERIFY 0 ROWS (FAIL-FAST IF ANY DATA PRESENT)
--     Ruling 4dc4459e scope only. shrapnel.field_type is deliberately NOT
--     checked and NOT dropped -- see the header note.
-- ============================================================================

DO $$
DECLARE
    v_count INTEGER;
    v_table TEXT;
BEGIN
    -- Check each in-scope table for 0 rows
    FOR v_table IN SELECT unnest(ARRAY['data_source', 'value_type', 'qbe_table', 'qbe_table_column', 'qbe_column', 'qbe_join', 'qbe_join_type']) LOOP
        EXECUTE format('SELECT count(*) FROM shrapnel.%I', v_table) INTO v_count;
        IF v_count > 0 THEN
            RAISE EXCEPTION 'Table shrapnel.% has % rows (expected 0) - aborting decommission', v_table, v_count;
        END IF;
    END LOOP;
    RAISE NOTICE 'All QBE-era tables verified empty (0 rows)';
END $$;

-- ============================================================================
-- 2. DROP FOREIGN KEY CONSTRAINTS (if any)
-- ============================================================================

-- Drop FK from qbe_column -> qbe_table
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_column DROP CONSTRAINT IF EXISTS qbe_column_table_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    -- Table or constraint doesn't exist, continue
    NULL;
END $$;

-- Drop FK from qbe_column -> field_type
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_column DROP CONSTRAINT IF EXISTS qbe_column_field_type_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- Drop FK from qbe_table_column join table
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_table_column DROP CONSTRAINT IF EXISTS qbe_table_column_table_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_table_column DROP CONSTRAINT IF EXISTS qbe_table_column_column_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- Drop FK from qbe_join -> qbe_column (join_column_a_id)
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_join DROP CONSTRAINT IF EXISTS qbe_join_join_column_a_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- Drop FK from qbe_join -> qbe_column (join_column_b_id)
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_join DROP CONSTRAINT IF EXISTS qbe_join_join_column_b_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- Drop FK from qbe_join -> qbe_join_type
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_join DROP CONSTRAINT IF EXISTS qbe_join_join_type_code_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- Drop FK from qbe_table -> qbe_join (if any reverse FKs)
DO $$
BEGIN
    ALTER TABLE shrapnel.qbe_table DROP CONSTRAINT IF EXISTS qbe_table_join_id_fkey;
EXCEPTION WHEN undefined_table OR undefined_object THEN
    NULL;
END $$;

-- ============================================================================
-- 3. DROP JOIN TABLES
-- ============================================================================

DROP TABLE IF EXISTS shrapnel.qbe_table_column CASCADE;

-- ============================================================================
-- 4. DROP MAIN TABLES (order matters due to FKs)
-- ============================================================================

DROP TABLE IF EXISTS shrapnel.qbe_join CASCADE;
DROP TABLE IF EXISTS shrapnel.qbe_column CASCADE;
DROP TABLE IF EXISTS shrapnel.qbe_table CASCADE;
DROP TABLE IF EXISTS shrapnel.qbe_join_type CASCADE;
DROP TABLE IF EXISTS shrapnel.value_type CASCADE;
-- shrapnel.field_type is NOT dropped here: out of ruling scope, 7 live rows,
-- read by ShrapnelStore.java, and FK-referenced by shrapnel.field (50) and
-- shrapnel.value (15,392). See the header note before "adding it back".
DROP TABLE IF EXISTS shrapnel.data_source CASCADE;

-- ============================================================================
-- 5. DROP SEQUENCES (if any)
-- ============================================================================

DROP SEQUENCE IF EXISTS shrapnel.data_source_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_table_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_column_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_join_id_seq CASCADE;

-- ============================================================================
-- 6. VERIFY CLEANUP (ruling scope only; field_type must SURVIVE)
-- ============================================================================

DO $$
DECLARE
    v_count INTEGER;
    v_table TEXT;
BEGIN
    FOR v_table IN SELECT unnest(ARRAY['data_source', 'value_type', 'qbe_table', 'qbe_table_column', 'qbe_column', 'qbe_join', 'qbe_join_type']) LOOP
        EXECUTE format('SELECT count(*) FROM information_schema.tables WHERE table_schema = ''shrapnel'' AND table_name = %L', v_table) INTO v_count;
        IF v_count > 0 THEN
            RAISE EXCEPTION 'Table shrapnel.% still exists after DROP', v_table;
        END IF;
    END LOOP;

    -- Negative assertion: this migration must NOT remove field_type. If this
    -- ever fires, the drop list was widened without a ruling.
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'shrapnel' AND table_name = 'field_type') THEN
        RAISE NOTICE 'shrapnel.field_type retained (out of ruling scope) - correct';
    ELSE
        RAISE EXCEPTION 'shrapnel.field_type is missing: out-of-scope drop detected, aborting';
    END IF;

    RAISE NOTICE 'All QBE-era tables successfully decommissioned';
END $$;