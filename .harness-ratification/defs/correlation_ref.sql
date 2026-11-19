CREATE OR REPLACE FUNCTION resolution.correlation_ref(current_alias text, child_expr_id uuid)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_kind        text;
    v_crid        uuid;
    v_from_column text;
BEGIN
    SELECT kind, concept_relationship_id INTO v_kind, v_crid
    FROM resolution.expression WHERE id = child_expr_id;

    IF v_kind IS DISTINCT FROM 'relationship_ref' THEN
        RAISE EXCEPTION 'correlation_ref only applies to a relationship_ref child, got % for %', v_kind, child_expr_id;
    END IF;

    SELECT from_column INTO v_from_column
    FROM resolution.concept_relationship_binding WHERE concept_relationship_id = v_crid;
    IF v_from_column IS NULL THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    RETURN format('%I.%I', current_alias, v_from_column);
END;
$function$

