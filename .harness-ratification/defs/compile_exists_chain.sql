CREATE OR REPLACE FUNCTION resolution.compile_exists_chain(expr_id uuid, parent_ref text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_kind        text;
    v_crid        uuid;
    v_quantifier  text;
    v_binding     resolution.concept_relationship_binding%ROWTYPE;
    v_alias       text;
    v_child_id    uuid;
    v_child_sql   text;
BEGIN
    SELECT kind, concept_relationship_id, quantifier INTO v_kind, v_crid, v_quantifier
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind IS NULL THEN
        RAISE EXCEPTION 'no expression row for id %', expr_id;
    ELSIF v_kind <> 'relationship_ref' THEN
        RAISE EXCEPTION 'compile_exists_chain only supports relationship_ref nodes, got % for %', v_kind, expr_id;
    END IF;

    SELECT * INTO v_binding
    FROM resolution.concept_relationship_binding WHERE concept_relationship_id = v_crid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    v_alias := 't_' || replace(expr_id::text, '-', '');

    SELECT child_expression_id INTO v_child_id
    FROM resolution.expression_operand
    WHERE parent_expression_id = expr_id
    ORDER BY position LIMIT 1;

    IF v_child_id IS NOT NULL THEN
        v_child_sql := resolution.compile_condition(v_child_id, v_alias);
    ELSE
        v_child_sql := 'TRUE';
    END IF;

    IF v_quantifier = 'EXISTS' THEN
        RETURN format(
            'EXISTS (SELECT 1 FROM %I.%I %I WHERE %I.%I = %s AND (%s))',
            v_binding.to_schema, v_binding.to_table, v_alias,
            v_alias, v_binding.to_column, parent_ref, v_child_sql
        );
    ELSIF v_quantifier = 'ALL' THEN
        -- universal quantification: no matching row may VIOLATE the
        -- condition. Vacuously true when there are no matching rows at all
        -- (e.g. a leaf requirement with no children).
        RETURN format(
            'NOT EXISTS (SELECT 1 FROM %I.%I %I WHERE %I.%I = %s AND NOT (%s))',
            v_binding.to_schema, v_binding.to_table, v_alias,
            v_alias, v_binding.to_column, parent_ref, v_child_sql
        );
    ELSE
        RAISE EXCEPTION 'quantifier % not implemented (COUNT still unimplemented)', v_quantifier;
    END IF;
END;
$function$

