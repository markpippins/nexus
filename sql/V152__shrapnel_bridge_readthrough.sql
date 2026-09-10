-- V152 (plan 8261644 / requirement ab45c18c): shrapnel EAV read-through bridge.
--
-- Re-issues schemas/migrations/resolution/resolution_migration_v36_shrapnel_bridge.sql
-- (377 lines, written + transactionally tested but NEVER applied: function absent
-- from pg_proc, zero function_binding rows, not in ledger) via the current sql/V*.sql
-- migration path. V152 is the next free number (V151 is wind_execution_request).
--
-- Provides the missing plan 0015 AC3/AC4 machinery (verified absent live 2026-09-10):
--   * resolution.read_shrapnel_state_member(asset_id, member_name, as_of) -> jsonb
--     (read-only typed EAV read-through; Shrapnel authoritative, no copies to
--     resolution.concept_attribute_value)
--   * resolution.shrapnel_state_member_true(...) -> boolean (fail-closed predicate)
--   * Two resolution.function_binding rows (compiler registration)
--   * compile_root replacement (bridge calls as complete expressions)
--
-- Idempotent: CREATE OR REPLACE + ON CONFLICT. Safe to re-run.
--
-- R9: schema change (functions + bindings). MUST be replicated to vanadium
-- after apply, per AGENTS.md R9 — confirm with operator; never assume.

BEGIN;

CREATE OR REPLACE FUNCTION resolution.read_shrapnel_state_member(
    p_asset_id   text,
    p_member_name text,
    p_as_of      timestamptz
) RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_object_count integer;
    v_field_count  integer;
    v_type_code    smallint;
    v_value        jsonb;
    v_object_id    bigint;
    v_value_id     bigint;
    v_created_at   timestamptz;
    v_reason       text;
BEGIN
    IF p_asset_id IS NULL OR btrim(p_asset_id) = '' THEN
        RETURN jsonb_build_object(
            'status', 'refusal',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'asset_id is required'
        );
    END IF;

    IF p_member_name IS NULL OR p_member_name NOT IN (
        'asset_id',
        'partial_implementation',
        'detailed_analysis',
        'inspection_or_ir_exists',
        'system_mapped',
        'has_open_questions',
        'sandbox_scaffolded'
    ) THEN
        RETURN jsonb_build_object(
            'status', 'refusal',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'member_not_allowlisted'
        );
    END IF;

    IF p_as_of IS NULL THEN
        RETURN jsonb_build_object(
            'status', 'refusal',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'reason', 'as_of is required'
        );
    END IF;

    -- Find objects whose authoritative asset_id field matches. The EAV
    -- joins are intentionally explicit; field names never become SQL
    -- identifiers, and only the allow-listed member above is queried.
    SELECT count(*), min(object_id), min(created_at)
    INTO v_object_count, v_object_id, v_created_at
    FROM (
        SELECT oi.id AS object_id, oi.created_at
        FROM shrapnel.object_instance oi
        JOIN shrapnel.object_attribute_value asset_oav
          ON asset_oav.object_id = oi.id
        JOIN shrapnel.field asset_field
          ON asset_field.id = asset_oav.field_id
         AND asset_field.property_name = 'asset_id'
        JOIN shrapnel.value asset_value
          ON asset_value.id = asset_oav.value_id
         AND asset_value.value_type_code = 2
        JOIN shrapnel.value_string asset_text
          ON asset_text.id = asset_value.id
        WHERE asset_text.value = p_asset_id
    ) matches;

    IF v_object_count = 0 THEN
        RETURN jsonb_build_object(
            'status', 'unknown',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'shrapnel_fact_not_found'
        );
    END IF;

    IF v_object_count <> 1 THEN
        RETURN jsonb_build_object(
            'status', 'unavailable',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'ambiguous_shrapnel_membership'
        );
    END IF;

    -- The current EAV store has no valid_until column. Its created_at is the
    -- only temporal boundary available, so an object created after the
    -- caller's as_of is explicitly stale rather than silently visible.
    IF v_created_at > p_as_of THEN
        RETURN jsonb_build_object(
            'status', 'stale',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'shrapnel_fact_not_effective_at_as_of'
        );
    END IF;

    -- Resolve the requested field and enforce its declared EAV type. The
    -- approved state members are booleans; asset_id is text. A malformed or
    -- duplicate value is unavailable, never silently false.
    SELECT count(*), min(v.value_type_code), min(oav.value_id)
    INTO v_field_count, v_type_code, v_value_id
    FROM shrapnel.object_attribute_value oav
    JOIN shrapnel.field f ON f.id = oav.field_id
       AND f.property_name = p_member_name
    JOIN shrapnel.value v ON v.id = oav.value_id
    WHERE oav.object_id = v_object_id;

    IF v_field_count = 0 THEN
        RETURN jsonb_build_object(
            'status', 'unavailable',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'required_field_missing'
        );
    END IF;

    IF v_field_count <> 1 THEN
        RETURN jsonb_build_object(
            'status', 'unavailable',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'ambiguous_field_value'
        );
    END IF;

    IF p_member_name = 'asset_id' AND v_type_code <> 2 THEN
        v_reason := 'field_type_mismatch';
    ELSIF p_member_name <> 'asset_id' AND v_type_code <> 4 THEN
        v_reason := 'field_type_mismatch';
    END IF;

    IF v_reason IS NOT NULL THEN
        RETURN jsonb_build_object(
            'status', 'unavailable',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', v_reason
        );
    END IF;

    IF v_type_code = 2 THEN
        SELECT to_jsonb(value) INTO v_value
        FROM shrapnel.value_string WHERE id = v_value_id;
    ELSE
        SELECT to_jsonb(value) INTO v_value
        FROM shrapnel.value_boolean WHERE id = v_value_id;
    END IF;

    IF v_value IS NULL THEN
        RETURN jsonb_build_object(
            'status', 'unavailable',
            'asset_id', p_asset_id,
            'member_name', p_member_name,
            'as_of', p_as_of,
            'reason', 'typed_value_missing'
        );
    END IF;

    RETURN jsonb_build_object(
        'status', 'resolved',
        'asset_id', p_asset_id,
        'member_name', p_member_name,
        'as_of', p_as_of,
        'value', v_value,
        'source_refs', jsonb_build_array(
            jsonb_build_object(
                'source', 'shrapnel',
                'object_id', v_object_id,
                'field', p_member_name,
                'created_at', v_created_at,
                'as_of', p_as_of
            )
        ),
        'reason', 'bridge_read_resolved'
    );
END;
$function$;

COMMENT ON FUNCTION resolution.read_shrapnel_state_member(text, text, timestamptz) IS
    'Read-only, allow-listed, typed Shrapnel EAV state bridge. Shrapnel remains authoritative; Resolution stores no copied state value.';

-- Boolean evaluation is a deliberately narrow projection of the richer read
-- contract. It is suitable for Resolution proposition predicates, but it
-- never turns an unavailable/refused read into affirmative evidence.
CREATE OR REPLACE FUNCTION resolution.shrapnel_state_member_true(
    p_asset_id    text,
    p_member_name  text,
    p_as_of       timestamptz
) RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_read jsonb;
BEGIN
    v_read := resolution.read_shrapnel_state_member(
        p_asset_id, p_member_name, p_as_of
    );
    RETURN v_read->>'status' = 'resolved'
       AND v_read->'value' = 'true'::jsonb;
END;
$function$;

COMMENT ON FUNCTION resolution.shrapnel_state_member_true(text, text, timestamptz) IS
    'Fail-closed boolean projection of the Shrapnel state bridge for Resolution proposition evaluation; only resolved JSON boolean true returns true.';

-- Function bindings are declarative compiler metadata. The implementation
-- is allow-listed above; callers cannot select arbitrary EAV fields through
-- the binding.
INSERT INTO resolution.function_binding
    (function_name, sql_template, arg_count, return_type, notes)
VALUES (
    'shrapnel_state_member',
    'resolution.read_shrapnel_state_member(%s, %s, %s)',
    3,
    'jsonb',
    'v36 read-only Shrapnel bridge; args are asset_id, allow-listed member_name, and required as_of'
)
ON CONFLICT (function_name) DO UPDATE SET
    sql_template = EXCLUDED.sql_template,
    arg_count = EXCLUDED.arg_count,
    return_type = EXCLUDED.return_type,
    notes = EXCLUDED.notes;

INSERT INTO resolution.function_binding
    (function_name, sql_template, arg_count, return_type, notes)
VALUES (
    'shrapnel_state_member_true',
    'resolution.shrapnel_state_member_true(%s, %s, %s)',
    3,
    'boolean',
    'v36 fail-closed boolean projection of the Shrapnel bridge for proposition evaluation'
)
ON CONFLICT (function_name) DO UPDATE SET
    sql_template = EXCLUDED.sql_template,
    arg_count = EXCLUDED.arg_count,
    return_type = EXCLUDED.return_type,
    notes = EXCLUDED.notes;

-- v19 added function calls to compile_condition, but root-level function
-- calls were not yet accepted. This replacement lets a declarative bridge
-- call be used as a complete expression as well as a nested operand.
CREATE OR REPLACE FUNCTION resolution.compile_root(expr_id uuid, literal_root_ref text)
RETURNS text AS $function$
DECLARE
    v_kind          text;
    v_quantifier    text;
    v_operator      text;
    v_literal       text;
    v_attr_id       uuid;
    v_function_name text;
    v_binding       resolution.concept_attribute_binding%ROWTYPE;
    v_fn_binding    resolution.function_binding%ROWTYPE;
    v_left_id       uuid;
    v_right_id      uuid;
    v_args          text[];
BEGIN
    SELECT kind, quantifier, operator, literal_value, attribute_id,
           function_name
    INTO v_kind, v_quantifier, v_operator, v_literal, v_attr_id,
         v_function_name
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind = 'relationship_ref' THEN
        IF v_quantifier IN ('EXISTS', 'ALL') THEN
            RETURN resolution.compile_exists_chain(expr_id, literal_root_ref);
        ELSIF v_quantifier = 'COUNT' THEN
            RETURN resolution.compile_count_scalar(expr_id, literal_root_ref);
        ELSE
            RAISE EXCEPTION 'unknown quantifier %', v_quantifier;
        END IF;
    ELSIF v_kind = 'attribute_ref' THEN
        SELECT * INTO v_binding
        FROM resolution.concept_attribute_binding
        WHERE attribute_id = v_attr_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no concept_attribute_binding for attribute %', v_attr_id;
        END IF;
        RETURN format('(SELECT %I FROM %I.%I WHERE id = %s)',
            v_binding.column_name, v_binding.schema_name,
            v_binding.table_name, literal_root_ref);
    ELSIF v_kind = 'proposition_ref' THEN
        RETURN resolution.compile_proposition_ref(expr_id);
    ELSIF v_kind = 'function_call' THEN
        SELECT * INTO v_fn_binding
        FROM resolution.function_binding
        WHERE function_name = v_function_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no function_binding for function_name %', v_function_name;
        END IF;

        SELECT array_agg(
            resolution.compile_root(eo.child_expression_id, literal_root_ref)
            ORDER BY eo.position
        )
        INTO v_args
        FROM resolution.expression_operand eo
        WHERE eo.parent_expression_id = expr_id;

        IF coalesce(array_length(v_args, 1), 0) <> v_fn_binding.arg_count THEN
            RAISE EXCEPTION 'function % expects % arg(s), got %',
                v_function_name, v_fn_binding.arg_count,
                coalesce(array_length(v_args, 1), 0);
        END IF;

        RETURN format(v_fn_binding.sql_template, VARIADIC v_args);
    ELSIF v_kind = 'operator' THEN
        SELECT child_expression_id INTO v_left_id
        FROM resolution.expression_operand
        WHERE parent_expression_id = expr_id AND position = 1;
        SELECT child_expression_id INTO v_right_id
        FROM resolution.expression_operand
        WHERE parent_expression_id = expr_id AND position = 2;
        IF v_left_id IS NULL OR v_right_id IS NULL THEN
            RAISE EXCEPTION 'operator node % missing an operand', expr_id;
        END IF;
        RETURN format('(%s %s %s)',
            resolution.compile_root(v_left_id, literal_root_ref),
            v_operator,
            resolution.compile_root(v_right_id, literal_root_ref));
    ELSIF v_kind = 'literal' THEN
        RETURN quote_literal(v_literal);
    ELSE
        RAISE EXCEPTION 'compile_root: unsupported root-level kind %', v_kind;
    END IF;
END;
$function$ LANGUAGE plpgsql;

COMMIT;
