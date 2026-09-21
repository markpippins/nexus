-- ============================================================================
-- shrapnel migration 0006: reconcile legacy (QBE-era) field/value shape
--                          to the canonical 0001 contract (idempotent)
-- ============================================================================
-- Background (DBA verification 2026-09-21, record b7b8cf73):
--   On hosts where the QBE-era shrapnel store pre-dates the SQL migration
--   chain, 0001's CREATE TABLE IF NOT EXISTS no-op'd against the legacy
--   tables while the migration ledger stamped 0001 "applied". Live fallout:
--
--     - shrapnel.field / shrapnel.value have NO created_at/updated_at;
--     - the 0001 trigger trg_field_set_updated_at WAS installed on the
--       legacy table and references the missing column, so every
--       UPDATE shrapnel.field fails with
--         ERROR: record "new" has no field "updated_at";
--     - field_type_code / value_type_code are nullable integers with no FK
--       (canonical: smallint NOT NULL + FK);
--     - label / name / value_string.value are varchar(255) NOT NULL where
--       canonical uses text (label nullable);
--     - field_type lacks UNIQUE(name) and pg_type is nullable.
--
--   The same drift exists in the `sol` database (identical ledger stamps).
--
-- This migration aligns BOTH a legacy-QBE shape and an already-canonical
-- shape to the 0001 contract: every step is guarded, and canonical DBs
-- no-op through the whole file. It fails loudly (readable messages) if
-- live data violates the constraints it must install.
--
-- One-way changes (data verified clean pre-apply):
--   NOT NULL tightening on field_type_code / value_type_code / pg_type.
-- Additive changes (revertible by dropping columns/defaults/FKs):
--   created_at / updated_at / FKs / label nullability / varchar->text widen.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0. Park objects that pin shrapnel.field column definitions:
--    - the 0005-era views (type changes under a view are not allowed);
--    - trg_sync_field_metadata_to_resolution, whose UPDATE OF
--      (property_name, field_type_code) column list blocks the
--      varchar->text / integer->smallint alters on exactly those columns.
--    Both are recreated verbatim in steps 7-8. (Simple SELECT views /
--    plain AFTER trigger: no data loss.)
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS shrapnel.v_stereotype_contract;
DROP VIEW IF EXISTS shrapnel.v_object_stereotype;
DROP TRIGGER IF EXISTS trg_sync_field_metadata_to_resolution ON shrapnel.field;

-- ----------------------------------------------------------------------------
-- 1. shrapnel.field — timestamp columns + text widths + label/name nullability
-- ----------------------------------------------------------------------------
ALTER TABLE shrapnel.field
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE shrapnel.field ALTER COLUMN label         TYPE text;
ALTER TABLE shrapnel.field ALTER COLUMN name          TYPE text;
ALTER TABLE shrapnel.field ALTER COLUMN property_name TYPE text;
ALTER TABLE shrapnel.field ALTER COLUMN label DROP NOT NULL;
ALTER TABLE shrapnel.field ALTER COLUMN name DROP NOT NULL;

-- ----------------------------------------------------------------------------
-- 2. shrapnel.field — smallint + NOT NULL + FK on field_type_code (guarded)
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_null_type bigint;
    v_orphans   bigint;
BEGIN
    SELECT count(*) INTO v_null_type
    FROM shrapnel.field WHERE field_type_code IS NULL;
    IF v_null_type > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.field has % row(s) with NULL field_type_code; set a type before reconciling', v_null_type;
    END IF;

    SELECT count(*) INTO v_orphans
    FROM shrapnel.field f
    LEFT JOIN shrapnel.field_type ft ON ft.code = f.field_type_code
    WHERE ft.code IS NULL;
    IF v_orphans > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.field has % row(s) whose field_type_code has no field_type row', v_orphans;
    END IF;

    -- integer -> smallint normalization (canonical width; values are 1..7)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='shrapnel' AND table_name='field'
          AND column_name='field_type_code' AND data_type='integer'
    ) THEN
        ALTER TABLE shrapnel.field ALTER COLUMN field_type_code TYPE smallint;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='shrapnel' AND table_name='field'
          AND column_name='field_type_code' AND is_nullable='NO'
    ) THEN
        ALTER TABLE shrapnel.field ALTER COLUMN field_type_code SET NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'shrapnel.field'::regclass
          AND contype = 'f'
          AND confrelid = 'shrapnel.field_type'::regclass
    ) THEN
        ALTER TABLE shrapnel.field
            ADD CONSTRAINT fk_field_field_type
            FOREIGN KEY (field_type_code)
            REFERENCES shrapnel.field_type (code)
            ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 3. shrapnel.field — id default alignment
--    Legacy DBs: keep the existing field_seq (not referenced by name in the
--    codebase), just realign it past max(id). Canonical DBs (bigserial
--    field_id_seq, default already set): skip entirely.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('shrapnel.field_seq') IS NOT NULL THEN
        PERFORM setval('shrapnel.field_seq',
            GREATEST((SELECT COALESCE(max(id), 0) FROM shrapnel.field), 1));
        ALTER TABLE shrapnel.field ALTER COLUMN id
            SET DEFAULT nextval('shrapnel.field_seq'::regclass);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 4. shrapnel.value — created_at + smallint + NOT NULL + FK (guarded)
-- ----------------------------------------------------------------------------
ALTER TABLE shrapnel.value
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

DO $$
DECLARE
    v_null_type bigint;
    v_orphans   bigint;
BEGIN
    SELECT count(*) INTO v_null_type
    FROM shrapnel.value WHERE value_type_code IS NULL;
    IF v_null_type > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.value has % row(s) with NULL value_type_code; backfill before reconciling', v_null_type;
    END IF;

    SELECT count(*) INTO v_orphans
    FROM shrapnel.value v
    LEFT JOIN shrapnel.field_type ft ON ft.code = v.value_type_code
    WHERE ft.code IS NULL;
    IF v_orphans > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.value has % row(s) whose value_type_code has no field_type row', v_orphans;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='shrapnel' AND table_name='value'
          AND column_name='value_type_code' AND data_type='integer'
    ) THEN
        ALTER TABLE shrapnel.value ALTER COLUMN value_type_code TYPE smallint;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='shrapnel' AND table_name='value'
          AND column_name='value_type_code' AND is_nullable='NO'
    ) THEN
        ALTER TABLE shrapnel.value ALTER COLUMN value_type_code SET NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'shrapnel.value'::regclass
          AND contype = 'f'
          AND confrelid = 'shrapnel.field_type'::regclass
    ) THEN
        ALTER TABLE shrapnel.value
            ADD CONSTRAINT fk_value_field_type
            FOREIGN KEY (value_type_code)
            REFERENCES shrapnel.field_type (code)
            ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 5. shrapnel.value_string — widen varchar(255) -> text (canonical)
-- ----------------------------------------------------------------------------
ALTER TABLE shrapnel.value_string ALTER COLUMN value TYPE text;

-- ----------------------------------------------------------------------------
-- 6. shrapnel.field_type — smallint code, text name, UNIQUE(name),
--    pg_type NOT NULL (guarded for canonical DBs that already have these)
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_null_pg_type bigint;
    v_dup_names    bigint;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='shrapnel' AND table_name='field_type'
          AND column_name='code' AND data_type='integer'
    ) THEN
        ALTER TABLE shrapnel.field_type ALTER COLUMN code TYPE smallint;
    END IF;

    ALTER TABLE shrapnel.field_type ALTER COLUMN name TYPE text;

    SELECT count(*) INTO v_null_pg_type FROM shrapnel.field_type WHERE pg_type IS NULL;
    IF v_null_pg_type > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.field_type has % row(s) with NULL pg_type', v_null_pg_type;
    END IF;
    SELECT count(*) INTO v_dup_names FROM (
        SELECT name FROM shrapnel.field_type GROUP BY name HAVING count(*) > 1
    ) d;
    IF v_dup_names > 0 THEN
        RAISE EXCEPTION '0006: shrapnel.field_type has % duplicate name(s); dedupe before reconciling', v_dup_names;
    END IF;

    ALTER TABLE shrapnel.field_type ALTER COLUMN pg_type SET NOT NULL;
END $$;

-- UNIQUE(name): canonical 0001 declares it inline (constraint name varies),
-- so probe by covered column set, not by name.
DO $$
DECLARE
    v_has_name_unique boolean;
BEGIN
    SELECT bool_or(true) INTO v_has_name_unique
    FROM pg_constraint c
    WHERE c.conrelid = 'shrapnel.field_type'::regclass
      AND c.contype IN ('u', 'p')
      AND c.conkey = ARRAY[
            (SELECT attnum::smallint FROM pg_attribute
             WHERE attrelid = 'shrapnel.field_type'::regclass
               AND attname = 'name' AND NOT attisdropped)
          ]::smallint[];

    IF NOT COALESCE(v_has_name_unique, false) THEN
        ALTER TABLE shrapnel.field_type ADD CONSTRAINT uq_field_type_name UNIQUE (name);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 7. Recreate the 0001 update trigger — valid now that updated_at exists
--    (set_updated_at() itself is CREATE OR REPLACE: no-op if already present)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shrapnel.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_field_set_updated_at ON shrapnel.field;
CREATE TRIGGER trg_field_set_updated_at
    BEFORE UPDATE ON shrapnel.field
    FOR EACH ROW
    EXECUTE FUNCTION shrapnel.set_updated_at();

-- Reintroduce the shrapnel->resolution metadata-sync trigger (originated in
-- the python V-series; captured verbatim from the live catalog):
-- AFTER INSERT OR UPDATE OF property_name, field_type_code, delegating to
-- resolution.sync_shrapnel_field(NEW.id).
CREATE TRIGGER trg_sync_field_metadata_to_resolution
    AFTER INSERT OR UPDATE OF property_name, field_type_code
    ON shrapnel.field
    FOR EACH ROW
    EXECUTE FUNCTION shrapnel.sync_field_metadata_to_resolution();

-- ----------------------------------------------------------------------------
-- 8. Recreate the 0005-era views (verbatim from 0005_stereotype_api.sql)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW shrapnel.v_stereotype_contract AS
SELECT s.name              AS stereotype_name,
       r.id                AS revision_id,
       r.version,
       r.depth,
       r.contract_fingerprint,
       f.property_name,
       sf.required
FROM shrapnel.stereotype_revision r
JOIN shrapnel.stereotype s        ON s.id = r.stereotype_id
JOIN shrapnel.stereotype_field sf ON sf.stereotype_revision_id = r.id
JOIN shrapnel.field f             ON f.id = sf.field_id;

CREATE OR REPLACE VIEW shrapnel.v_object_stereotype AS
SELECT o.id        AS object_id,
       o.created_at,
       s.name      AS stereotype_name,
       r.id        AS revision_id,
       r.version,
       r.depth
FROM shrapnel.object_instance o
JOIN shrapnel.stereotype_revision r ON r.id = o.stereotype_revision_id
JOIN shrapnel.stereotype s          ON s.id = r.stereotype_id;

-- ----------------------------------------------------------------------------
-- 9. Post-conditions (fail the migration loudly on partial drift)
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_missing text;
    v_bad     text;
BEGIN
    FOR v_missing IN
        SELECT c FROM unnest(ARRAY['id','is_calculated','field_index','label','name',
                                   'property_name','field_type_code','created_at','updated_at']) AS c
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='shrapnel' AND table_name='field' AND column_name = c
        )
    LOOP
        RAISE EXCEPTION '0006 post-condition: shrapnel.field.% missing', v_missing;
    END LOOP;

    SELECT column_name INTO v_bad
    FROM information_schema.columns
    WHERE table_schema='shrapnel' AND table_name='field'
      AND column_name IN ('created_at','updated_at','field_type_code')
      AND is_nullable <> 'NO'
    LIMIT 1;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0006 post-condition: shrapnel.field.% is still nullable', v_bad;
    END IF;

    SELECT column_name INTO v_bad
    FROM information_schema.columns
    WHERE table_schema='shrapnel' AND table_name='value'
      AND column_name IN ('created_at','value_type_code')
      AND is_nullable <> 'NO'
    LIMIT 1;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0006 post-condition: shrapnel.value.% is still nullable', v_bad;
    END IF;
END $$;

-- ============================================================================
-- Done. Field UPDATEs restored; legacy shape reconciled to 0001.
-- ============================================================================
