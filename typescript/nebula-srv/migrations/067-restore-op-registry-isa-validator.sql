-- Migration 067: restore the op_registry ISA validator trigger (lost in the
-- bitemporal conversion).
--
-- Migration 017 attached trg_validate_opcode_template to nebula.op_registry
-- when it was a table. A later conversion made op_registry a view over
-- nebula.op_registry_history and the trigger was not re-created on the new
-- storage relation — CREATE TRIGGER cannot target a view, so ISA validation
-- silently disappeared. This migration re-attaches the validator to
-- nebula.op_registry_history so every writer through the view is validated
-- again.
--
-- The function body is copied from migration 017 verbatim (CREATE OR REPLACE,
-- so re-applying 017's function definition on any DB is safe and idempotent);
-- only the trigger target differs.
--
-- Surfaced by the service-test-gates suite: tests/op-registry.test.ts asserts
-- an invalid opcode is rejected, which failed with HTTP 201 because the
-- trigger no longer existed anywhere.
--
-- No explicit BEGIN/COMMIT here: the runner (nebula-srv migrate.ts or a
-- `psql -1` invocation) already wraps the file in a single transaction, and a
-- nested BEGIN would abort it.

CREATE OR REPLACE FUNCTION nebula.validate_opcode_template() RETURNS trigger
    AS $$
DECLARE
    v_op           TEXT;
    v_entry        JSONB;
    v_valid_ops    TEXT[] := ARRAY[
        -- Filesystem
        'CREATE_DIR', 'DELETE_DIR', 'MOVE_PATH', 'COPY_PATH',
        'WRITE_FILE', 'APPEND_FILE', 'READ_FILE', 'RENAME_PATH',
        -- Environment
        'INIT_VENV', 'INSTALL_DEPENDENCIES', 'SET_ENV_VAR',
        'CONFIGURE_RUNTIME', 'SELECT_PYTHON_VERSION', 'RUN_SHELL_COMMAND',
        -- Code construction
        'CREATE_MODULE', 'WRITE_SOURCE_FILE', 'APPLY_TEMPLATE',
        'GENERATE_CLASS', 'GENERATE_FUNCTION', 'PATCH_FILE',
        -- Service registration
        'REGISTER_SERVICE', 'UPDATE_SERVICE_REGISTRY', 'CONFIGURE_ROUTE',
        'DEFINE_ENDPOINT', 'BIND_PORT', 'DEPLOY_SERVICE',
        -- Validation
        'VALIDATE_SYNTAX', 'CHECK_DEPENDENCIES', 'RUN_TYPECHECK',
        'RUN_TEST_SUITE', 'VERIFY_SCHEMA', 'DRY_RUN_EXECUTION',
        -- Event / observability
        'EMIT_EVENT', 'LOG_ARTIFACT', 'REGISTER_TRACEPOINT', 'PUBLISH_STATE'
    ];
BEGIN
    -- Only validate if opcode_template is a JSON array
    IF jsonb_typeof(NEW.opcode_template) != 'array' THEN
        RAISE EXCEPTION 'opcode_template must be a JSON array, got %', jsonb_typeof(NEW.opcode_template);
    END IF;

    FOR v_entry IN SELECT * FROM jsonb_array_elements(NEW.opcode_template) LOOP
        v_op := v_entry->>'op';
        IF v_op IS NULL THEN
            RAISE EXCEPTION 'Template entry is missing an "op" field';
        END IF;

        IF NOT (v_op = ANY(v_valid_ops)) THEN
            RAISE EXCEPTION 'Invalid opcode: %', v_op;
        END IF;

        IF v_entry->>'target' IS NULL THEN
            RAISE EXCEPTION 'Template entry for opcode "%" is missing a "target" field', v_op;
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Re-attach to the bitemporal storage relation (op_registry is now a view;
-- triggers cannot be created on views).
DROP TRIGGER IF EXISTS trg_validate_opcode_template ON nebula.op_registry_history;
CREATE TRIGGER trg_validate_opcode_template
    BEFORE INSERT OR UPDATE OF opcode_template
    ON nebula.op_registry_history
    FOR EACH ROW
    EXECUTE FUNCTION nebula.validate_opcode_template();

INSERT INTO nebula.schema_version (version, description)
VALUES (67, 'restore op_registry ISA validator trigger on op_registry_history (lost in bitemporal conversion)')
ON CONFLICT (version) DO NOTHING;
