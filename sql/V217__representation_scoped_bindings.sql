-- V217 — Representation-scoped bindings and fail-closed resolution (staged inert).
--
-- Phase 3 of docs/nebula-sol-absorption.md. Ratified by
--   DBA       record 93d3f092-5e6f-476d-8ec7-41cf587360f9 (APPROVED_WITH_AMENDMENTS)
--   Architect record 9b532211-da40-4b52-9729-b273523ff3ab (RATIFIED_WITH_AMENDMENTS)
--
-- NOT APPLIED BY THIS CHANGE. Live application is operator-gated (DBA
-- amendment 5 / Architect A5): ratification + throwaway-DB proof + Drafts
-- forum admin approval if routed as type:db-change + low-traffic execution
-- with rollback. This file is the staged artifact; bin/tests proves it on a
-- throwaway database only.
--
-- What changes:
--   1. representation.evaluation_role (executable|descriptive, default
--      descriptive) and representation.read_adapter.
--   2. binding tables gain a surrogate id PK and a nullable representation_id
--      FK, with per-representation uniqueness and a preserved legacy NULL leg.
--   3. Deterministic backfill: a binding is scoped only when its
--      (schema_name, table_name) matches EXACTLY ONE representation of its
--      concept. No arbitration. A representation becomes executable only if a
--      binding was scoped to it.
--   4. Fail-closed resolution: explicit representation wins; otherwise exactly
--      one executable representation; otherwise the legacy NULL leg only for
--      concepts with zero representations; ambiguity fails closed.
--   5. expression.function_name is FK-constrained to resolution.function_binding
--      NOT VALID (tolerates the legacy unregistered escalates_to row, enforces
--      new rows), and a preflight refuses application while any function_call
--      still names an unregistered function.
--
-- The four compiler functions are replaced with representation-scoped lookups
-- through resolution.attribute_binding_for / relationship_binding_for. Their
-- bodies are the live definitions with only the binding lookup changed.

BEGIN;

-- ── 0. Preflight: refuse while a function_call names an unregistered function ─
-- Architect A3 condition. This is deliberately fatal: applying V217 while
-- `escalates_to` is unlisted would leave the allow-list enforcement gap open.

DO $$
DECLARE
    v_missing text;
BEGIN
    SELECT string_agg(DISTINCT e.function_name, ', ') INTO v_missing
    FROM resolution.expression e
    WHERE e.kind = 'function_call'
      AND e.function_name IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM resolution.function_binding f
          WHERE f.function_name = e.function_name
      );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'V217 refused: expression.function_call names unregistered function(s): %. Register them in resolution.function_binding with a real template, or remove the call, before applying.',
            v_missing;
    END IF;
END $$;

-- ── 1. Representation selector ───────────────────────────────────────────────

ALTER TABLE resolution.representation
    ADD COLUMN IF NOT EXISTS evaluation_role text NOT NULL DEFAULT 'descriptive',
    ADD COLUMN IF NOT EXISTS read_adapter text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'representation_evaluation_role_check'
          AND conrelid = 'resolution.representation'::regclass
    ) THEN
        ALTER TABLE resolution.representation
            ADD CONSTRAINT representation_evaluation_role_check
            CHECK (evaluation_role IN ('executable', 'descriptive'));
    END IF;
END $$;

-- ── 2. Binding keys ──────────────────────────────────────────────────────────
-- Surrogate PKs: nothing references either binding table's old PK (the
-- expression FK points at concept_attribute / concept_relationship, not the
-- binding). representation_id stays NULLABLE so the legacy NULL leg survives.

ALTER TABLE resolution.concept_attribute_binding
    ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS representation_id uuid;

UPDATE resolution.concept_attribute_binding
   SET id = gen_random_uuid()
 WHERE id IS NULL;

ALTER TABLE resolution.concept_attribute_binding
    ALTER COLUMN id SET NOT NULL;
ALTER TABLE resolution.concept_attribute_binding
    DROP CONSTRAINT IF EXISTS concept_attribute_binding_pkey;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'resolution.concept_attribute_binding'::regclass
          AND contype = 'p'
    ) THEN
        ALTER TABLE resolution.concept_attribute_binding
            ADD CONSTRAINT concept_attribute_binding_pkey PRIMARY KEY (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'concept_attribute_binding_representation_fk'
    ) THEN
        ALTER TABLE resolution.concept_attribute_binding
            ADD CONSTRAINT concept_attribute_binding_representation_fk
            FOREIGN KEY (representation_id)
            REFERENCES resolution.representation(id);
    END IF;
END $$;

ALTER TABLE resolution.concept_relationship_binding
    ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS representation_id uuid;

UPDATE resolution.concept_relationship_binding
   SET id = gen_random_uuid()
 WHERE id IS NULL;

ALTER TABLE resolution.concept_relationship_binding
    ALTER COLUMN id SET NOT NULL;
ALTER TABLE resolution.concept_relationship_binding
    DROP CONSTRAINT IF EXISTS concept_relationship_binding_pkey;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'resolution.concept_relationship_binding'::regclass
          AND contype = 'p'
    ) THEN
        ALTER TABLE resolution.concept_relationship_binding
            ADD CONSTRAINT concept_relationship_binding_pkey PRIMARY KEY (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'concept_relationship_binding_representation_fk'
    ) THEN
        ALTER TABLE resolution.concept_relationship_binding
            ADD CONSTRAINT concept_relationship_binding_representation_fk
            FOREIGN KEY (representation_id)
            REFERENCES resolution.representation(id);
    END IF;
END $$;

-- ── 3. Deterministic backfill (exactly one (schema, table) match) ────────────
-- No arbitration: a concept with two representations that BOTH match the
-- binding's table stays NULL. A concept with one matching representation is
-- scoped, which preserves the evaluation it already had.

UPDATE resolution.concept_attribute_binding b
   SET representation_id = m.rep_id
  FROM (
      SELECT b2.id AS binding_id,
             (array_agg(r.id ORDER BY r.id))[1] AS rep_id
        FROM resolution.concept_attribute_binding b2
        JOIN resolution.concept_attribute a ON a.id = b2.attribute_id
        JOIN resolution.representation r
          ON r.concept_id = a.concept_id
         AND r.schema_name = b2.schema_name
         AND r.table_name = b2.table_name
       WHERE b2.representation_id IS NULL
       GROUP BY b2.id
      HAVING count(*) = 1
  ) m
 WHERE b.id = m.binding_id;

-- Relationships scope on the FROM concept's representation: from_schema/
-- from_table are the physical side the join starts from. The asset bridge
-- (`..asset_id`, empty schema/table) matches nothing and keeps the NULL leg.
UPDATE resolution.concept_relationship_binding b
   SET representation_id = m.rep_id
  FROM (
      SELECT b2.id AS binding_id,
             (array_agg(r.id ORDER BY r.id))[1] AS rep_id
        FROM resolution.concept_relationship_binding b2
        JOIN resolution.concept_relationship cr
          ON cr.id = b2.concept_relationship_id
        JOIN resolution.representation r
          ON r.concept_id = cr.from_concept_id
         AND r.schema_name = b2.from_schema
         AND r.table_name = b2.from_table
       WHERE b2.representation_id IS NULL
         AND b2.from_schema IS NOT NULL
         AND b2.from_table IS NOT NULL
       GROUP BY b2.id
      HAVING count(*) = 1
  ) m
 WHERE b.id = m.binding_id;

-- Executable iff a binding was scoped to it. Nothing else is promoted.
UPDATE resolution.representation r
   SET evaluation_role = 'executable'
 WHERE r.evaluation_role = 'descriptive'
   AND (
       EXISTS (SELECT 1 FROM resolution.concept_attribute_binding b
                WHERE b.representation_id = r.id)
       OR EXISTS (SELECT 1 FROM resolution.concept_relationship_binding b
                   WHERE b.representation_id = r.id)
   );

-- ── 4. Uniqueness ────────────────────────────────────────────────────────────

CREATE UNIQUE INDEX IF NOT EXISTS cab_rep_attr_uq
    ON resolution.concept_attribute_binding (representation_id, attribute_id)
    WHERE representation_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS cab_attr_legacy_uq
    ON resolution.concept_attribute_binding (attribute_id)
    WHERE representation_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS crb_rep_rel_uq
    ON resolution.concept_relationship_binding (representation_id, concept_relationship_id)
    WHERE representation_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS crb_rel_legacy_uq
    ON resolution.concept_relationship_binding (concept_relationship_id)
    WHERE representation_id IS NULL;

-- ── 5. Function-name allow-list enforcement (Architect A3, option a) ─────────
-- NOT VALID: new rows are checked, the legacy unregistered row is tolerated
-- until the preflight condition above is satisfied.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'expression_function_name_fk'
    ) THEN
        ALTER TABLE resolution.expression
            ADD CONSTRAINT expression_function_name_fk
            FOREIGN KEY (function_name)
            REFERENCES resolution.function_binding(function_name)
            NOT VALID;
    END IF;
END $$;

-- ── 6. Resolution ────────────────────────────────────────────────────────────
-- The session selects a representation with:
--   SET LOCAL resolution.representation_id = '<uuid>';
-- Unset = implicit resolution.

CREATE OR REPLACE FUNCTION resolution.resolve_binding_representation(p_concept_id uuid)
RETURNS uuid
LANGUAGE plpgsql
AS $function$
DECLARE
    v_explicit uuid;
    v_exec     uuid[];
    v_reps     integer;
BEGIN
    v_explicit := nullif(current_setting('resolution.representation_id', true), '')::uuid;

    IF v_explicit IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM resolution.representation r
            WHERE r.id = v_explicit AND r.concept_id = p_concept_id
        ) THEN
            RAISE EXCEPTION
                'representation % is not a representation of concept %',
                v_explicit, p_concept_id;
        END IF;
        RETURN v_explicit;
    END IF;

    SELECT array_agg(r.id ORDER BY r.id) INTO v_exec
    FROM resolution.representation r
    WHERE r.concept_id = p_concept_id
      AND r.expired_at IS NULL
      AND r.evaluation_role = 'executable';

    SELECT count(*) INTO v_reps
    FROM resolution.representation r
    WHERE r.concept_id = p_concept_id
      AND r.expired_at IS NULL;

    IF coalesce(array_length(v_exec, 1), 0) = 1 THEN
        RETURN v_exec[1];
    ELSIF coalesce(array_length(v_exec, 1), 0) > 1 THEN
        RAISE EXCEPTION
            'ambiguous representation for concept %: % executable representations; select one explicitly',
            p_concept_id, array_length(v_exec, 1);
    ELSIF v_reps = 0 THEN
        -- Legacy NULL leg: the concept has no representation at all, so the
        -- pre-registry binding stays resolvable (Proposition, asset bridge).
        RETURN NULL;
    ELSE
        RAISE EXCEPTION
            'concept % has % representation(s) and none is executable; select one explicitly',
            p_concept_id, v_reps;
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION resolution.attribute_binding_for(p_attribute_id uuid)
RETURNS resolution.concept_attribute_binding
LANGUAGE plpgsql
AS $function$
DECLARE
    v_concept uuid;
    v_rep     uuid;
    v_row     resolution.concept_attribute_binding;
BEGIN
    SELECT concept_id INTO v_concept
    FROM resolution.concept_attribute WHERE id = p_attribute_id;

    IF v_concept IS NULL THEN
        RAISE EXCEPTION 'unknown concept_attribute %', p_attribute_id;
    END IF;

    v_rep := resolution.resolve_binding_representation(v_concept);

    SELECT * INTO v_row
    FROM resolution.concept_attribute_binding b
    WHERE b.attribute_id = p_attribute_id
      AND b.representation_id IS NOT DISTINCT FROM v_rep;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'no concept_attribute_binding for attribute % in % representation',
            p_attribute_id,
            CASE WHEN v_rep IS NULL THEN 'legacy' ELSE v_rep::text END;
    END IF;

    RETURN v_row;
END;
$function$;

-- Relationship bindings resolve against the FROM concept's executable
-- representation, never the session selector: a hop crosses concepts, so the
-- root's selected representation does not scope the child join.
CREATE OR REPLACE FUNCTION resolution.relationship_binding_for(p_relationship_id uuid)
RETURNS resolution.concept_relationship_binding
LANGUAGE plpgsql
AS $function$
DECLARE
    v_from uuid;
    v_rep  uuid;
    v_exec uuid[];
    v_reps integer;
    v_row  resolution.concept_relationship_binding;
BEGIN
    SELECT from_concept_id INTO v_from
    FROM resolution.concept_relationship WHERE id = p_relationship_id;

    IF v_from IS NULL THEN
        RAISE EXCEPTION 'unknown concept_relationship %', p_relationship_id;
    END IF;

    SELECT array_agg(r.id ORDER BY r.id) INTO v_exec
    FROM resolution.representation r
    WHERE r.concept_id = v_from
      AND r.expired_at IS NULL
      AND r.evaluation_role = 'executable';

    SELECT count(*) INTO v_reps
    FROM resolution.representation r
    WHERE r.concept_id = v_from AND r.expired_at IS NULL;

    IF coalesce(array_length(v_exec, 1), 0) = 1 THEN
        v_rep := v_exec[1];
    ELSIF coalesce(array_length(v_exec, 1), 0) > 1 THEN
        RAISE EXCEPTION
            'ambiguous relationship representation for concept %: % executable representations',
            v_from, array_length(v_exec, 1);
    ELSIF v_reps = 0 THEN
        v_rep := NULL;
    ELSE
        RAISE EXCEPTION
            'concept % has % representation(s) and none is executable for relationship %',
            v_from, v_reps, p_relationship_id;
    END IF;

    SELECT * INTO v_row
    FROM resolution.concept_relationship_binding b
    WHERE b.concept_relationship_id = p_relationship_id
      AND b.representation_id IS NOT DISTINCT FROM v_rep;

    -- A missing binding is not an error here: most relationships are
    -- unbound and callers left-join through this function. The COMPILER
    -- raises when it actually needs the join.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    RETURN v_row;
END;
$function$;

-- ── 7. Compiler functions, scoped through the resolvers ─────────────────────
-- Bodies are the live definitions; only the binding lookup changed.

CREATE OR REPLACE FUNCTION resolution.compile_condition(expr_id uuid, current_alias text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_kind          text;
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
    SELECT kind, operator, literal_value, attribute_id, function_name
    INTO v_kind, v_operator, v_literal, v_attr_id, v_function_name
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind = 'literal' THEN
        RETURN quote_literal(v_literal);
    ELSIF v_kind = 'attribute_ref' THEN
        v_binding := resolution.attribute_binding_for(v_attr_id);
        RETURN format('%I.%I', current_alias, v_binding.column_name);
    ELSIF v_kind = 'relationship_ref' THEN
        RETURN resolution.compile_exists_chain(expr_id, resolution.correlation_ref(current_alias, expr_id));
    ELSIF v_kind = 'proposition_ref' THEN
        RETURN resolution.compile_proposition_ref(expr_id);
    ELSIF v_kind = 'function_call' THEN
        SELECT * INTO v_fn_binding FROM resolution.function_binding WHERE function_name = v_function_name;
        IF NOT FOUND THEN RAISE EXCEPTION 'no function_binding for function_name %', v_function_name; END IF;
        SELECT array_agg(resolution.compile_condition(eo.child_expression_id, current_alias) ORDER BY eo.position)
        INTO v_args FROM resolution.expression_operand eo WHERE eo.parent_expression_id = expr_id;
        IF coalesce(array_length(v_args, 1), 0) <> v_fn_binding.arg_count THEN
            RAISE EXCEPTION 'function % expects % arg(s), got %', v_function_name, v_fn_binding.arg_count, coalesce(array_length(v_args, 1), 0);
        END IF;
        RETURN format(v_fn_binding.sql_template, VARIADIC v_args);
    ELSIF v_kind = 'operator' THEN
        SELECT child_expression_id INTO v_left_id  FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 1;
        SELECT child_expression_id INTO v_right_id FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 2;
        IF v_left_id IS NULL OR v_right_id IS NULL THEN RAISE EXCEPTION 'operator node % missing an operand', expr_id; END IF;
        RETURN format('(%s %s %s)', resolution.compile_condition(v_left_id, current_alias), v_operator, resolution.compile_condition(v_right_id, current_alias));
    ELSE
        RAISE EXCEPTION 'compile_condition does not support kind %', v_kind;
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION resolution.compile_root(expr_id uuid, literal_root_ref text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
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
        v_binding := resolution.attribute_binding_for(v_attr_id);
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
$function$;

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

    v_binding := resolution.relationship_binding_for(v_crid);
    IF v_binding.id IS NULL THEN
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
        RETURN format(
            'NOT EXISTS (SELECT 1 FROM %I.%I %I WHERE %I.%I = %s AND NOT (%s))',
            v_binding.to_schema, v_binding.to_table, v_alias,
            v_alias, v_binding.to_column, parent_ref, v_child_sql
        );
    ELSE
        RAISE EXCEPTION 'quantifier % not implemented (COUNT still unimplemented)', v_quantifier;
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION resolution.correlation_ref(current_alias text, child_expr_id uuid)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_kind        text;
    v_crid        uuid;
    v_binding     resolution.concept_relationship_binding%ROWTYPE;
BEGIN
    SELECT kind, concept_relationship_id INTO v_kind, v_crid
    FROM resolution.expression WHERE id = child_expr_id;

    IF v_kind IS DISTINCT FROM 'relationship_ref' THEN
        RAISE EXCEPTION 'correlation_ref only applies to a relationship_ref child, got % for %', v_kind, child_expr_id;
    END IF;

    v_binding := resolution.relationship_binding_for(v_crid);
    IF v_binding.id IS NULL OR v_binding.from_column IS NULL THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    RETURN format('%I.%I', current_alias, v_binding.from_column);
END;
$function$;

COMMIT;
