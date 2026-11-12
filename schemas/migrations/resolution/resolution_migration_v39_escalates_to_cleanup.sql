-- Resolution v39 — remove the unreferenced illustrative escalates_to call.
-- Plan 8261673; Architect decision from one-shot review 7fd4f716; DBA review
-- c7836f9e. This is a data cleanup only; it does NOT register escalates_to
-- and does NOT apply V217.
--
-- Live application is gated on explicit admin approval in Drafts thread
-- 198b6cbd-50d7-4eef-8779-d2d992c8f181. Until then this file is inert.
--
-- The row is an orphaned function_call with no operands, concept relationship,
-- verified statement, rule, requirement, or invariant references. The preflight
-- below rechecks every FK that targets resolution.expression(id), excluding
-- expression_operand rows which are archived/deleted as owned children. Any
-- newly appeared non-owned reference aborts before deletion.
--
-- Rollback source: resolution.expression_cleanup_tombstone.expression_row and
-- .operand_rows hold exact JSONB row images. Before a live apply, take a
-- logical backup/PITR point (DBA requirement), e.g.:
--   pg_dump -h localhost -U pguser -d nexus --data-only --table=resolution.expression \
--     --table=resolution.expression_operand --table=resolution.verified_statement \
--     --table=resolution.rule --table=resolution.requirement \
--     --table=aegis.invariant -f /secure/approved/path/pre_v39_resolution_rows.sql
-- Keep that backup outside version control and verify it is readable.
--
-- Rollback after cleanup (within an approved recovery transaction):
--   INSERT INTO resolution.expression
--   SELECT (jsonb_populate_record(NULL::resolution.expression, expression_row)).*
--     FROM resolution.expression_cleanup_tombstone WHERE expression_id = '<id>';
--   INSERT INTO resolution.expression_operand
--   SELECT * FROM jsonb_populate_recordset(NULL::resolution.expression_operand,
--     (SELECT operand_rows FROM resolution.expression_cleanup_tombstone WHERE expression_id = '<id>'));
-- The expression is restored before its operand rows. Keep the tombstone until
-- rollback has been ruled out; do not discard the recovery source casually.

BEGIN;

CREATE TABLE IF NOT EXISTS resolution.expression_cleanup_tombstone (
    expression_id uuid PRIMARY KEY,
    expression_row jsonb NOT NULL,
    operand_rows jsonb NOT NULL DEFAULT '[]'::jsonb,
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    cleanup_migration text NOT NULL
);

DO $cleanup$
DECLARE
    v_id uuid := '77777777-7777-7777-7777-777777777701';
    v_expression jsonb;
    v_operands jsonb;
    v_ref record;
    v_count bigint;
    v_had_tombstone boolean;
    v_saved_expression jsonb;
    v_saved_operands jsonb;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM resolution.expression_cleanup_tombstone
        WHERE expression_id = v_id
    ) INTO v_had_tombstone;

    SELECT to_jsonb(e) INTO v_expression
    FROM resolution.expression e
    WHERE e.id = v_id
    FOR UPDATE;

    IF v_expression IS NULL THEN
        IF v_had_tombstone THEN
            RETURN; -- already cleaned by this migration
        END IF;
        RAISE EXCEPTION 'v39 refused: expected expression % is absent and has no tombstone', v_id;
    END IF;

    IF v_expression->>'kind' <> 'function_call'
       OR v_expression->>'function_name' <> 'escalates_to' THEN
        RAISE EXCEPTION 'v39 refused: expression % no longer has the reviewed shape', v_id;
    END IF;

    SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.position), '[]'::jsonb)
      INTO v_operands
      FROM resolution.expression_operand o
     WHERE o.parent_expression_id = v_id;

    -- Fail closed if any FK to expression(id), other than the owned operand
    -- rows handled above, currently points at this expression.
    FOR v_ref IN
        SELECT ns.nspname AS schema_name,
               rel.relname AS table_name,
               att.attname AS column_name
          FROM pg_constraint c
          JOIN pg_class rel ON rel.oid = c.conrelid
          JOIN pg_namespace ns ON ns.oid = rel.relnamespace
          JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY
               AS k(fk_attnum, pk_attnum, ord) ON true
          JOIN pg_attribute att
            ON att.attrelid = c.conrelid AND att.attnum = k.fk_attnum
          JOIN pg_attribute pkatt
            ON pkatt.attrelid = c.confrelid AND pkatt.attnum = k.pk_attnum
         WHERE c.contype = 'f'
           AND c.confrelid = 'resolution.expression'::regclass
           AND pkatt.attname = 'id'
           AND NOT (c.conrelid = 'resolution.expression_operand'::regclass
                    AND att.attname = 'parent_expression_id')
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I = $1',
                       v_ref.schema_name, v_ref.table_name, v_ref.column_name)
           INTO v_count USING v_id;
        IF v_count <> 0 THEN
            RAISE EXCEPTION
                'v39 refused: % row(s) in %.% reference expression % through column %',
                v_count, v_ref.schema_name, v_ref.table_name, v_id, v_ref.column_name;
        END IF;
    END LOOP;

    IF v_had_tombstone THEN
        SELECT expression_row, operand_rows
          INTO v_saved_expression, v_saved_operands
          FROM resolution.expression_cleanup_tombstone
         WHERE expression_id = v_id;
        IF v_saved_expression IS DISTINCT FROM v_expression
           OR v_saved_operands IS DISTINCT FROM v_operands THEN
            RAISE EXCEPTION
                'v39 refused: live expression % differs from its existing cleanup tombstone', v_id;
        END IF;
    ELSE
        INSERT INTO resolution.expression_cleanup_tombstone
            (expression_id, expression_row, operand_rows, cleanup_migration)
        VALUES (v_id, v_expression, v_operands,
                'resolution_migration_v39_escalates_to_cleanup.sql');
    END IF;

    DELETE FROM resolution.expression_operand
     WHERE parent_expression_id = v_id;
    DELETE FROM resolution.expression
     WHERE id = v_id;
END;
$cleanup$;

COMMIT;