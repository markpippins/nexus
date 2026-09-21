-- V194: Decommission QBE-era empty tables (shrapnel schema)
-- Per architect ruling 4dc4459e: decommission QBE-era empty tables in shrapnel.*
-- Tables: data_source, field_type, value_type, qbe_table, qbe_column, qbe_join, qbe_join_type
-- JPA entities removed in shrapnel-data module (coordinated PR)
-- Row-count guard: verify 0 rows before DROP (fail-fast if any data present)

-- ============================================================================
-- 1. VERIFY 0 ROWS (FAIL-FAST IF ANY DATA PRESENT)
-- ============================================================================

DO $$
DECLARE
    v_count INTEGER;
    v_table TEXT;
BEGIN
    -- Check each table for 0 rows
    FOR v_table IN SELECT unnest(ARRAY['data_source', 'field_type', 'value_type', 'qbe_table', 'qbe_column', 'qbe_join', 'qbe_join_type']) LOOP
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
DROP TABLE IF EXISTS shrapnel.field_type CASCADE;
DROP TABLE IF EXISTS shrapnel.data_source CASCADE;

-- ============================================================================
-- 5. DROP SEQUENCES (if any)
-- ============================================================================

DROP SEQUENCE IF EXISTS shrapnel.data_source_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_table_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_column_id_seq CASCADE;
DROP SEQUENCE IF EXISTS shrapnel.qbe_join_id_seq CASCADE;

-- ============================================================================
-- 6. VERIFY CLEANUP
-- ============================================================================

DO $$
DECLARE
    v_count INTEGER;
    v_table TEXT;
BEGIN
    FOR v_table IN SELECT unnest(ARRAY['data_source', 'field_type', 'value_type', 'qbe_table', 'qbe_column', 'qbe_join', 'qbe_join_type']) LOOP
        EXECUTE format('SELECT count(*) FROM information_schema.tables WHERE table_schema = ''shrapnel'' AND table_name = %L', v_table) INTO v_count;
        IF v_count > 0 THEN
            RAISE EXCEPTION 'Table shrapnel.% still exists after DROP', v_table;
        END IF;
    END LOOP;
    RAISE NOTICE 'All QBE-era tables successfully decommissioned';
END $$;