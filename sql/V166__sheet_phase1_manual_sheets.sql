-- =============================================================================
-- V166 (Sheet phase 1 — manual sheets): DBA draft — NOT APPLIED TO LIVE.
-- =============================================================================
-- ⚠️  DRAFT — DO NOT APPLY UNTIL THE ROUNDTABLE SETTLES THE PROPOSAL ⚠️
-- Pre-staged per the user's direction so the DDL exists and is executable-
-- tested before review. Governing discussion: Assembly `discussions` thread
-- a558efc7 ("Sheet — a spreadsheet API over shrapnel"), DBA analysis agent
-- record 5073d217 (level 3). Nothing here overrides the proposal's open
-- questions; where the proposal stated a DBA recommendation, this draft
-- implements the recommendation so reviewers argue against runnable code.
--
-- Scope: PHASE 1 — MANUAL SHEETS ONLY.
--   - sheet registry (sheets are first-class shrapnel objects — named windows)
--   - per-sheet column projections (column order without data duplication)
--   - per-sheet row membership with fractional ranks (O(1) reorder)
--   - sheet_set_cell through the EXISTING encode path (value → value_<type>
--     → object_attribute_value); type-guard triggers stay intact
--   - minimal read views (v_sheet, v_sheet_grid, v_sheet_cell)
-- OUT OF SCOPE (later phases, per proposal sequencing): typed sheets
-- (stereotype admission gate), bound sheets (qbe + refresh), formula columns.
--
-- Doctrine honored (from 5073d217 — the delete asymmetry):
--   - Sheets are windows, never storage. DROP a sheet → projections and row
--     junctions die (ON DELETE CASCADE); objects, fields, and OAV facts
--     survive. Remove a row from a sheet → junction dies; the object and its
--     cells survive. Remove a column from a sheet → projection dies; the
--     field and its cells survive.
--   - No override shadowing: sheet_set_cell IS a direct OAV write. There is
--     no sheet-scoped value fork anywhere in this DDL.
--   - No storage duplication: sheet tables reference object_instance and
--     field by FK; they never copy values.
--
-- Ground truth this draft was verified against (live DB, 2026-09-16):
--   - shrapnel.object_instance(id, created_at, stereotype_id NULLABLE,
--     stereotype_revision_id NULLABLE) — bare inserts are legal; the
--     trg_objinst_membership_evidence trigger returns early on NULL
--     stereotype ("unclassified object; nothing to prove").
--   - shrapnel.field(id, field_type_code FK→field_type.code, field_index,
--     is_calculated, ...); field_type codes 1..7 = value code space
--     (Long,String,Double,Boolean,Timestamp,JSONB,UUID), all 7 in live use.
--   - shrapnel.value(id, value_type_code) + 1:1 extension tables
--     value_long/string/double/boolean/timestamp/jsonb/uuid; V128's
--     assert_extension_type_matches trigger enforces code match on INSERT
--     OR UPDATE of every extension.
--   - uq_oav_object_field UNIQUE (object_id, field_id): single-writer-
--     per-cell — the property the whole design rests on.
--   - Extension rows are mutable (V128 guards INSERT OR UPDATE), so in-place
--     cell updates reuse the existing value/extension rows.
-- =============================================================================
-- Review-note (correction vs the proposal text): 5073d217 lists
-- "oav.created_at (per-cell history)" among free hooks. uq_oav_object_field
-- means one OAV row per cell, updated in place — created_at is the cell's
-- BIRTH time, not a history. True cell history needs an audit trigger
-- (phase-2 candidate; V156/V159 statement-trigger pattern). This draft
-- exposes it honestly as cell_created_at.
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- §1  Sheet registry — the sheet is a first-class shrapnel object.
-- The 1:1 FK to object_instance IS the "sheets are objects" claim, enforced
-- by the engine rather than by convention. object deletion cascades here.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS shrapnel.sheet (
    id          bigint      PRIMARY KEY
                            REFERENCES shrapnel.object_instance(id) ON DELETE CASCADE,
    name        text        NOT NULL,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sheet_name UNIQUE (name)
);

COMMENT ON TABLE  shrapnel.sheet IS
'Sheet phase 1 (V166): a named window over shrapnel EAV data. 1:1 with object_instance (sheets ARE shrapnel objects). Windows, never storage: dropping a sheet removes only projections + row junctions; objects, fields, and OAV facts survive. Discussion a558efc7 / analysis 5073d217.';
COMMENT ON COLUMN shrapnel.sheet.id IS
'The anchoring shrapnel object id. Sheet identity and object identity are the same integer.';
COMMENT ON COLUMN shrapnel.sheet.name IS
'Unique display name (phase 1 has no folder/scope concept; uniqueness is global until a scoping need is proven).';

-- ─────────────────────────────────────────────────────────────────────────────
-- §2  Column projections — per-sheet column ORDER over shared fields.
-- Two sheets may expose the same field; the data has one home. Deleting the
-- FIELD is RESTRICTed while any sheet projects it: the window's dependence on
-- shared vocabulary is explicit, and field retirement must remove projections
-- deliberately first.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS shrapnel.sheet_column (
    id            bigserial PRIMARY KEY,
    sheet_id      bigint    NOT NULL REFERENCES shrapnel.sheet(id) ON DELETE CASCADE,
    field_id      bigint    NOT NULL REFERENCES shrapnel.field(id) ON DELETE RESTRICT,
    display_label text,
    column_index  double precision NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sheet_column UNIQUE (sheet_id, field_id)
);

CREATE INDEX IF NOT EXISTS idx_sheet_column_sheet
    ON shrapnel.sheet_column (sheet_id);
CREATE INDEX IF NOT EXISTS idx_sheet_column_order
    ON shrapnel.sheet_column (sheet_id, column_index);
-- Reverse lookups: FK RESTRICT enforcement (field DELETE probes this table
-- by field_id) and "which sheets project this field" queries.
CREATE INDEX IF NOT EXISTS idx_sheet_column_field
    ON shrapnel.sheet_column (field_id);

COMMENT ON TABLE  shrapnel.sheet_column IS
'Sheet phase 1 (V166): per-sheet projection of a shared shrapnel field — column identity stays the field; two sheets can expose the same field without duplicating data.';
COMMENT ON COLUMN shrapnel.sheet_column.column_index IS
'Fractional rank (O(1) reorder): to move a column between neighbors a and b, set index to (a+b)/2.';

-- ─────────────────────────────────────────────────────────────────────────────
-- §3  Row membership — junction objects per (sheet, row).
-- Recommended mechanism from the proposal's open question (a): junctions over
-- per-sheet row_index fields, because they keep sheet cardinality out of the
-- global field table, make multi-sheet membership natural, and give rows a
-- place for per-annotation columns later. Row objects leave every sheet
-- automatically when deleted (CASCADE) — window semantics.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS shrapnel.sheet_row (
    id            bigserial PRIMARY KEY,
    sheet_id      bigint    NOT NULL REFERENCES shrapnel.sheet(id) ON DELETE CASCADE,
    row_object_id bigint    NOT NULL REFERENCES shrapnel.object_instance(id) ON DELETE CASCADE,
    row_index     double precision NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sheet_row UNIQUE (sheet_id, row_object_id)
);

CREATE INDEX IF NOT EXISTS idx_sheet_row_sheet
    ON shrapnel.sheet_row (sheet_id);
CREATE INDEX IF NOT EXISTS idx_sheet_row_order
    ON shrapnel.sheet_row (sheet_id, row_index);
-- Reverse lookup: object-deletion CASCADE probes this table by row_object_id;
-- also serves "which sheets is this object a row of".
CREATE INDEX IF NOT EXISTS idx_sheet_row_object
    ON shrapnel.sheet_row (row_object_id);

COMMENT ON TABLE  shrapnel.sheet_row IS
'Sheet phase 1 (V166): row membership of an (existing) shrapnel object in a sheet, with fractional-rank ordering. Deleting the OBJECT cascades the junction only — object and its cells survive everywhere else.';
COMMENT ON COLUMN shrapnel.sheet_row.row_index IS
'Fractional rank (O(1) reorder). Members of a sheet need not carry any cell values — empty rows are legal.';

-- -----------------------------------------------------------------------------
-- §3b  Anchor cleanup — "sheets ARE objects" enforced in both directions.
-- The 1:1 FK makes object deletion cascade to the sheet; this trigger makes
-- sheet deletion remove the anchoring object too (which then cascades the
-- sheet_row junctions). Recursion guard: when the CASCADE delete of the
-- object re-fires this trigger, the sheet row is already gone (the FK
-- cascade processed it first) — no-op, no infinite loop.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shrapnel.trg_sheet_anchor_cleanup()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM shrapnel.object_instance WHERE id = OLD.id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_sheet_anchor_cleanup
    AFTER DELETE ON shrapnel.sheet
    FOR EACH ROW EXECUTE FUNCTION shrapnel.trg_sheet_anchor_cleanup();

COMMENT ON FUNCTION shrapnel.trg_sheet_anchor_cleanup() IS
'Sheet phase 1 (V166): deleting a sheet removes its anchoring shrapnel object (symmetry with the object→sheet FK cascade). Recursion-safe: on the cascade re-entry the sheet row is already gone.';

-- ─────────────────────────────────────────────────────────────────────────────
-- §4  The encode path — sheet_set_cell goes THROUGH the EAV machinery.
-- Same value → value_<type> → object_attribute_value chain as every other
-- writer; V128''s assert_extension_type_matches guard stays in the loop.
-- Refusals use SHEETS-xxx message prefixes (caller logs on its own connection;
-- no in-trigger ledger — V163 C6 lesson).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.sheet_encode_value(
    p_type_code smallint,
    p_value     text,
    p_json      jsonb DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_value_id bigint;
BEGIN
    INSERT INTO shrapnel.value (value_type_code)
    VALUES (p_type_code)
    RETURNING id INTO v_value_id;

    CASE p_type_code
        WHEN 1 THEN
            INSERT INTO shrapnel.value_long (id, value)
            VALUES (v_value_id, p_value::bigint);
        WHEN 2 THEN
            INSERT INTO shrapnel.value_string (id, value)
            VALUES (v_value_id, p_value);
        WHEN 3 THEN
            INSERT INTO shrapnel.value_double (id, value)
            VALUES (v_value_id, p_value::double precision);
        WHEN 4 THEN
            IF lower(COALESCE(p_value,'')) NOT IN ('true','false') THEN
                RAISE EXCEPTION 'SHEETS-005: boolean cell literal must be true/false (got %)', COALESCE(p_value,'<null>');
            END IF;
            INSERT INTO shrapnel.value_boolean (id, value)
            VALUES (v_value_id, lower(p_value)::boolean);
        WHEN 5 THEN
            INSERT INTO shrapnel.value_timestamp (id, value)
            VALUES (v_value_id, p_value::timestamptz);
        WHEN 6 THEN
            INSERT INTO shrapnel.value_jsonb (id, value)
            VALUES (v_value_id, COALESCE(p_json, p_value::jsonb));
        WHEN 7 THEN
            INSERT INTO shrapnel.value_uuid (id, value)
            VALUES (v_value_id, p_value::uuid);
        ELSE
            RAISE EXCEPTION 'SHEETS-005: unknown value type code % (registry is 1..7)', p_type_code;
    END CASE;

    RETURN v_value_id;
EXCEPTION
    WHEN invalid_text_representation OR invalid_datetime_format
         OR invalid_parameter_value OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'SHEETS-005: literal % is not a valid % value', COALESCE(p_value, p_json::text), p_type_code;
END;
$$;

COMMENT ON FUNCTION shrapnel.sheet_encode_value(smallint, text, jsonb) IS
'Sheet phase 1 (V166): encode a canonical text/jsonb literal into value + value_<type> via the V128 registry; V128 type-guard triggers fire on the extension INSERT. Refuses bad literals as SHEETS-005.';

CREATE OR REPLACE FUNCTION shrapnel.sheet_set_cell(
    p_sheet_id  bigint,
    p_object_id bigint,
    p_field_id  bigint,
    p_type_code smallint,
    p_value     text,
    p_json      jsonb DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_is_column   boolean;
    v_is_row      boolean;
    v_field_type  smallint;
    v_oav_id      bigint;
    v_value_id    bigint;
    v_cur_type    smallint;
    v_bindings    integer;
BEGIN
    -- (1) the cell must be inside the window: column projected AND row member.
    SELECT EXISTS (SELECT 1 FROM shrapnel.sheet_column
                   WHERE sheet_id = p_sheet_id AND field_id = p_field_id)
      INTO v_is_column;
    IF NOT v_is_column THEN
        RAISE EXCEPTION 'SHEETS-001: field % is not projected by sheet % — add the column before writing cells', p_field_id, p_sheet_id;
    END IF;

    SELECT EXISTS (SELECT 1 FROM shrapnel.sheet_row
                   WHERE sheet_id = p_sheet_id AND row_object_id = p_object_id)
      INTO v_is_row;
    IF NOT v_is_row THEN
        RAISE EXCEPTION 'SHEETS-002: object % is not a row of sheet % — add the row before writing cells', p_object_id, p_sheet_id;
    END IF;

    -- (2) the sheet cannot be used to smuggle a type change: the write must
    -- match the field''s declared type (field_type_code IS the value code space).
    SELECT f.field_type_code INTO v_field_type
      FROM shrapnel.field f WHERE f.id = p_field_id;
    IF v_field_type IS DISTINCT FROM p_type_code::integer THEN
        RAISE EXCEPTION 'SHEETS-003: field % declares type % but write supplies type %', p_field_id, v_field_type, p_type_code;
    END IF;

    -- (3) existing cell? update in place; retype refused (clear first).
    SELECT oav.id, oav.value_id, v.value_type_code
      INTO v_oav_id, v_value_id, v_cur_type
      FROM shrapnel.object_attribute_value oav
      JOIN shrapnel.value v ON v.id = oav.value_id
     WHERE oav.object_id = p_object_id AND oav.field_id = p_field_id;

    IF FOUND THEN
        IF v_cur_type <> p_type_code THEN
            RAISE EXCEPTION 'SHEETS-004: cell (%,%) holds type %; retype refused — clear the cell first (shrapnel.sheet_clear_cell)', p_object_id, p_field_id, v_cur_type;
        END IF;
        -- Copy-on-write guard: in-place UPDATE is only safe while values are
        -- private to one binding. Live data has zero shared values (15,382
        -- bindings / 15,382 distinct, verified 2026-09-16) — but if sharing
        -- ever emerges ("one fact cited by many objects"), an in-place write
        -- would mutate OTHER objects' cells. Copy-on-write keeps the write
        -- local no matter what later writers do.
        SELECT count(*) INTO v_bindings
          FROM shrapnel.object_attribute_value WHERE value_id = v_value_id;
        IF v_bindings > 1 THEN
            v_value_id := shrapnel.sheet_encode_value(p_type_code, p_value, p_json);
            UPDATE shrapnel.object_attribute_value
               SET value_id = v_value_id WHERE id = v_oav_id;
            RETURN v_oav_id;
        END IF;
        CASE p_type_code
            WHEN 1 THEN UPDATE shrapnel.value_long      SET value = p_value::bigint          WHERE id = v_value_id;
            WHEN 2 THEN UPDATE shrapnel.value_string    SET value = p_value                  WHERE id = v_value_id;
            WHEN 3 THEN UPDATE shrapnel.value_double    SET value = p_value::double precision WHERE id = v_value_id;
            WHEN 4 THEN
                IF lower(COALESCE(p_value,'')) NOT IN ('true','false') THEN
                    RAISE EXCEPTION 'SHEETS-005: boolean cell literal must be true/false (got %)', COALESCE(p_value,'<null>');
                END IF;
                UPDATE shrapnel.value_boolean SET value = lower(p_value)::boolean WHERE id = v_value_id;
            WHEN 5 THEN UPDATE shrapnel.value_timestamp SET value = p_value::timestamptz     WHERE id = v_value_id;
            WHEN 6 THEN UPDATE shrapnel.value_jsonb     SET value = COALESCE(p_json, p_value::jsonb) WHERE id = v_value_id;
            WHEN 7 THEN UPDATE shrapnel.value_uuid      SET value = p_value::uuid            WHERE id = v_value_id;
        END CASE;
        RETURN v_oav_id;
    END IF;

    -- (4) new cell: encode through the registry and bind via OAV.
    v_value_id := shrapnel.sheet_encode_value(p_type_code, p_value, p_json);
    INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
    VALUES (p_object_id, p_field_id, v_value_id)
    RETURNING id INTO v_oav_id;
    RETURN v_oav_id;
END;
$$;

COMMENT ON FUNCTION shrapnel.sheet_set_cell(bigint, bigint, bigint, smallint, text, jsonb) IS
'Sheet phase 1 (V166): THE cell write. Direct OAV write (no override shadowing), gated on window membership (SHEETS-001/002), field-type agreement (SHEETS-003), no-retype (SHEETS-004), literal validity (SHEETS-005). Flows through value + value_<type> + OAV with V128 guards intact.';

CREATE OR REPLACE FUNCTION shrapnel.sheet_clear_cell(
    p_sheet_id  bigint,
    p_object_id bigint,
    p_field_id  bigint
) RETURNS void
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_value_id bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM shrapnel.sheet_column
                   WHERE sheet_id = p_sheet_id AND field_id = p_field_id) THEN
        RAISE EXCEPTION 'SHEETS-001: field % is not projected by sheet %', p_field_id, p_sheet_id;
    END IF;
    -- Sparse semantics: an empty cell is an ABSENT OAV row. The value row is
    -- GC'd only if no other OAV binding references it (reference-counted, not
    -- blind cascade); extensions cascade with the value row.
    DELETE FROM shrapnel.object_attribute_value oav
     WHERE oav.object_id = p_object_id
       AND oav.field_id  = p_field_id
    RETURNING value_id INTO v_value_id;
    IF v_value_id IS NULL THEN
        RETURN;  -- already empty: sparse no-op (idempotent)
    END IF;
    DELETE FROM shrapnel.value v
     WHERE v.id = v_value_id
       AND NOT EXISTS (
            SELECT 1 FROM shrapnel.object_attribute_value other
             WHERE other.value_id = v.id);
END;
$$;

COMMENT ON FUNCTION shrapnel.sheet_clear_cell(bigint, bigint, bigint) IS
'Sheet phase 1 (V166): empty a cell = delete the OAV binding (sparse-by-construction; extension rows cascade). Object and field survive.';

-- ─────────────────────────────────────────────────────────────────────────────
-- §5  Shape operations — minimal phase-1 surface.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.sheet_create(
    p_name        text,
    p_description text DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_object_id bigint;
    v_sheet_id  bigint;
BEGIN
    IF COALESCE(btrim(p_name), '') = '' THEN
        RAISE EXCEPTION 'SHEETS-006: sheet name must be a non-empty string';
    END IF;
    -- The anchor object is a plain, unclassified shrapnel object (NULL
    -- stereotype passes trg_objinst_membership_evidence: nothing to prove).
    INSERT INTO shrapnel.object_instance DEFAULT VALUES
    RETURNING id INTO v_object_id;
    BEGIN
        INSERT INTO shrapnel.sheet (id, name, description)
        VALUES (v_object_id, p_name, p_description)
        RETURNING id INTO v_sheet_id;
    EXCEPTION
        WHEN unique_violation THEN
            RAISE EXCEPTION 'SHEETS-007: a sheet named % already exists (uq_sheet_name)', p_name;
    END;
    RETURN v_sheet_id;
END;
$$;

CREATE OR REPLACE FUNCTION shrapnel.sheet_add_column(
    p_sheet_id     bigint,
    p_field_id     bigint,
    p_column_index double precision DEFAULT NULL,
    p_display_label text DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM shrapnel.sheet WHERE id = p_sheet_id) THEN
        RAISE EXCEPTION 'SHEETS-008: sheet % does not exist', p_sheet_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM shrapnel.field WHERE id = p_field_id) THEN
        RAISE EXCEPTION 'SHEETS-009: field % does not exist', p_field_id;
    END IF;
    IF p_column_index IS NULL THEN
        SELECT COALESCE(MAX(column_index), 0) + 1 INTO p_column_index
          FROM shrapnel.sheet_column WHERE sheet_id = p_sheet_id;
    END IF;
    BEGIN
        INSERT INTO shrapnel.sheet_column (sheet_id, field_id, display_label, column_index)
        VALUES (p_sheet_id, p_field_id, p_display_label, p_column_index)
        RETURNING id INTO v_id;
    EXCEPTION
        WHEN unique_violation THEN
            RAISE EXCEPTION 'SHEETS-010: field % is already projected by sheet % (uq_sheet_column)', p_field_id, p_sheet_id;
    END;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION shrapnel.sheet_remove_column(
    p_sheet_id bigint,
    p_field_id bigint
) RETURNS void
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
BEGIN
    DELETE FROM shrapnel.sheet_column
     WHERE sheet_id = p_sheet_id AND field_id = p_field_id;
    -- Cells (OAV) are deliberately untouched: removing a window column never
    -- deletes data. See §2 COMMENT / doctrine.
END;
$$;

CREATE OR REPLACE FUNCTION shrapnel.sheet_add_row(
    p_sheet_id  bigint,
    p_object_id bigint,
    p_row_index double precision DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM shrapnel.sheet WHERE id = p_sheet_id) THEN
        RAISE EXCEPTION 'SHEETS-008: sheet % does not exist', p_sheet_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM shrapnel.object_instance WHERE id = p_object_id) THEN
        RAISE EXCEPTION 'SHEETS-011: object % does not exist', p_object_id;
    END IF;
    IF p_row_index IS NULL THEN
        SELECT COALESCE(MAX(row_index), 0) + 1 INTO p_row_index
          FROM shrapnel.sheet_row WHERE sheet_id = p_sheet_id;
    END IF;
    BEGIN
        INSERT INTO shrapnel.sheet_row (sheet_id, row_object_id, row_index)
        VALUES (p_sheet_id, p_object_id, p_row_index)
        RETURNING id INTO v_id;
    EXCEPTION
        WHEN unique_violation THEN
            RAISE EXCEPTION 'SHEETS-012: object % is already a row of sheet % (uq_sheet_row)', p_object_id, p_sheet_id;
    END;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION shrapnel.sheet_remove_row(
    p_sheet_id  bigint,
    p_object_id bigint
) RETURNS void
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
BEGIN
    DELETE FROM shrapnel.sheet_row
     WHERE sheet_id = p_sheet_id AND row_object_id = p_object_id;
    -- Object + its cells survive (delete asymmetry). No cell deletion here.
END;
$$;

CREATE OR REPLACE FUNCTION shrapnel.sheet_move_row(
    p_sheet_id  bigint,
    p_object_id bigint,
    p_new_index double precision
) RETURNS void
LANGUAGE plpgsql
SET search_path = shrapnel, pg_temp
AS $$
BEGIN
    IF p_new_index IS NULL THEN
        RAISE EXCEPTION 'SHEETS-013: p_new_index must be a real number (fractional rank); use sheet_add_row for append';
    END IF;
    UPDATE shrapnel.sheet_row
       SET row_index = p_new_index
     WHERE sheet_id = p_sheet_id AND row_object_id = p_object_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SHEETS-014: object % is not a row of sheet %', p_object_id, p_sheet_id;
    END IF;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- §6  Reads — minimal views for phase 1 (service-layer API comes later).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW shrapnel.v_sheet AS
SELECT s.id, s.name, s.description, s.created_at, s.updated_at,
       o.created_at AS object_created_at,
       (SELECT count(*) FROM shrapnel.sheet_column c WHERE c.sheet_id = s.id) AS column_count,
       (SELECT count(*) FROM shrapnel.sheet_row r WHERE r.sheet_id = s.id)    AS row_count
  FROM shrapnel.sheet s
  JOIN shrapnel.object_instance o ON o.id = s.id;

COMMENT ON VIEW shrapnel.v_sheet IS
'Sheet phase 1 (V166): sheet registry with projected column/row counts.';

CREATE OR REPLACE VIEW shrapnel.v_sheet_grid AS
SELECT r.sheet_id,
       r.row_object_id,
       r.row_index,
       c.field_id,
       c.column_index,
       COALESCE(c.display_label, f.label, f.property_name) AS column_label,
       f.name                                              AS field_name,
       f.field_type_code,
       v.value_type_code                                   AS cell_type_code,
       oav.created_at                                      AS cell_created_at,
       CASE v.value_type_code
           WHEN 1 THEN vl.value::text
           WHEN 2 THEN vs.value
           WHEN 3 THEN vd.value::text
           WHEN 4 THEN vb.value::text
           WHEN 5 THEN vt.value::text
           WHEN 6 THEN vj.value::text
           WHEN 7 THEN vu.value::text
       END AS cell_text,
       vj.value AS cell_jsonb
  FROM shrapnel.sheet_row r
  JOIN shrapnel.sheet_column c ON c.sheet_id = r.sheet_id
  JOIN shrapnel.field f        ON f.id = c.field_id
  LEFT JOIN shrapnel.object_attribute_value oav
         ON oav.object_id = r.row_object_id AND oav.field_id = c.field_id
  LEFT JOIN shrapnel.value v        ON v.id  = oav.value_id
  LEFT JOIN shrapnel.value_long      vl ON vl.id = v.id
  LEFT JOIN shrapnel.value_string    vs ON vs.id = v.id
  LEFT JOIN shrapnel.value_double    vd ON vd.id = v.id
  LEFT JOIN shrapnel.value_boolean   vb ON vb.id = v.id
  LEFT JOIN shrapnel.value_timestamp vt ON vt.id = v.id
  LEFT JOIN shrapnel.value_jsonb     vj ON vj.id = v.id
  LEFT JOIN shrapnel.value_uuid      vu ON vu.id = v.id;

COMMENT ON VIEW shrapnel.v_sheet_grid IS
'Sheet phase 1 (V166): the grid — one row per (sheet row × projected column); absent OAV binding renders NULL (sparse-by-construction, no NULLs stored). Order by row_index, column_index at the consumer.';

CREATE OR REPLACE VIEW shrapnel.v_sheet_cell AS
SELECT oav.object_id, oav.field_id, oav.value_id,
       v.value_type_code, oav.created_at AS cell_created_at,
       CASE v.value_type_code
           WHEN 1 THEN vl.value::text
           WHEN 2 THEN vs.value
           WHEN 3 THEN vd.value::text
           WHEN 4 THEN vb.value::text
           WHEN 5 THEN vt.value::text
           WHEN 6 THEN vj.value::text
           WHEN 7 THEN vu.value::text
       END AS cell_text
  FROM shrapnel.object_attribute_value oav
  JOIN shrapnel.value v ON v.id = oav.value_id
  LEFT JOIN shrapnel.value_long      vl ON vl.id = v.id
  LEFT JOIN shrapnel.value_string    vs ON vs.id = v.id
  LEFT JOIN shrapnel.value_double    vd ON vd.id = v.id
  LEFT JOIN shrapnel.value_boolean   vb ON vb.id = v.id
  LEFT JOIN shrapnel.value_timestamp vt ON vt.id = v.id
  LEFT JOIN shrapnel.value_jsonb     vj ON vj.id = v.id
  LEFT JOIN shrapnel.value_uuid      vu ON vu.id = v.id;

COMMENT ON VIEW shrapnel.v_sheet_cell IS
'Sheet phase 1 (V166): all OAV cells in readable form (sheet-independent). NOTE: cell_created_at is the cell''s birth time — uq_oav_object_field + in-place update means there is no history at the storage layer (phase-2 audit-trigger candidate).';

-- ─────────────────────────────────────────────────────────────────────────────
-- §7  Postconditions — the migration asserts its own completeness.
-- ─────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM information_schema.tables
     WHERE table_schema = 'shrapnel'
       AND table_name IN ('sheet', 'sheet_column', 'sheet_row');
    IF n <> 3 THEN
        RAISE EXCEPTION 'V166 postcondition failed: expected 3 sheet tables, found %', n;
    END IF;

    SELECT count(*) INTO n FROM pg_proc p
      JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'shrapnel'
       AND p.proname IN ('sheet_encode_value', 'sheet_set_cell', 'sheet_clear_cell',
                         'sheet_create', 'sheet_add_column', 'sheet_remove_column',
                         'sheet_add_row', 'sheet_remove_row', 'sheet_move_row');
    IF n <> 9 THEN
        RAISE EXCEPTION 'V166 postcondition failed: expected 9 sheet functions, found %', n;
    END IF;

    SELECT count(*) INTO n FROM information_schema.views
     WHERE table_schema = 'shrapnel'
       AND table_name IN ('v_sheet', 'v_sheet_grid', 'v_sheet_cell');
    IF n <> 3 THEN
        RAISE EXCEPTION 'V166 postcondition failed: expected 3 sheet views, found %', n;
    END IF;
END;
$$;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- §8  Validation evidence
-- -----------------------------------------------------------------------------
-- 1. HERMETIC EXECUTION ONLY — this migration has NEVER been applied to the
--    live nexus database. Application is gated on the roundtable settling
--    discussion a558efc7 (architect/analyst constraints, ontologist concept-
--    kind ruling, engineer API-sequencing input) and an explicit apply order.
-- 2. Executable regression suite: python/shrapnel_sheet/tests/
--    test_sheet_phase1.py (sheet-conf-001) — per-run throwaway database,
--    applies V128 (the real EAV store) + this file, then exercises the
--    doctrine: encode-path writes, membership/type/retype/literal refusals,
--    the delete asymmetry (sheet/column/row removal vs data survival),
--    sparse grid reads, fractional reordering, first-class-object anchoring.
-- 3. Live-DB ground truth used for the draft is documented in the header;
--    no live write was performed at draft time.
-- ─────────────────────────────────────────────────────────────────────────────
