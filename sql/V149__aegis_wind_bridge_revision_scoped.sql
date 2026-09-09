-- V149 — Revision-scoped Aegis → Wind bridge
--
-- Aegis authoring rows remain mutable. A compilation binds to one immutable
-- registry_revision and records the exact source/model/compiler identities
-- used to produce a Wind workflow version. Wind remains the runtime owner;
-- Aegis remains advisory and does not grant execution authority.
--
-- Cross-schema foreign keys are used where Wind identity is stable and
-- validation is local (tasks, outcomes, workflow versions, nodes, edges).
-- Compilation lineage is immutable and remains advisory; it does not grant
-- Wind runtime authority or change Resolution/PEB admission.

BEGIN;

-- Composite parent keys make registry ownership explicit in every bridge row.
CREATE UNIQUE INDEX IF NOT EXISTS state_registry_id_unique
    ON aegis.state (registry_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS transition_registry_id_unique
    ON aegis.transition (registry_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS wind_edge_version_composite
    ON wind.workflow_edges (id, workflow_version_id);
CREATE UNIQUE INDEX IF NOT EXISTS validation_result_registry_id_unique
    ON aegis.validation_result (registry_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS registry_revision_registry_id_unique
    ON aegis.registry_revision (registry_id, id);

CREATE TABLE IF NOT EXISTS aegis.wind_task_mapping (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registry_id         uuid NOT NULL,
    registry_revision_id uuid NOT NULL,
    state_id            uuid NOT NULL,
    wind_task_id        uuid NOT NULL,
    is_check_only       boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT wind_task_mapping_registry_fkey
        FOREIGN KEY (registry_id) REFERENCES aegis.registry(id) ON DELETE RESTRICT,
    CONSTRAINT wind_task_mapping_revision_fkey
        FOREIGN KEY (registry_revision_id) REFERENCES aegis.registry_revision(id) ON DELETE RESTRICT,
    CONSTRAINT wind_task_mapping_revision_registry_fkey
        FOREIGN KEY (registry_id, registry_revision_id)
        REFERENCES aegis.registry_revision(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_task_mapping_state_fkey
        FOREIGN KEY (registry_id, state_id) REFERENCES aegis.state(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_task_mapping_wind_task_fkey
        FOREIGN KEY (wind_task_id) REFERENCES wind.tasks(id) ON DELETE RESTRICT,
    CONSTRAINT wind_task_mapping_state_unique
        UNIQUE (registry_revision_id, state_id)
);

COMMENT ON TABLE aegis.wind_task_mapping IS
    'Revision-scoped design-time mapping from an Aegis state to a Wind task; check-only semantics are validated against wind.tasks.tackle_task_id';

CREATE INDEX IF NOT EXISTS idx_wind_task_mapping_revision
    ON aegis.wind_task_mapping (registry_revision_id);
CREATE INDEX IF NOT EXISTS idx_wind_task_mapping_wind_task
    ON aegis.wind_task_mapping (wind_task_id);

CREATE TABLE IF NOT EXISTS aegis.wind_outcome_mapping (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registry_id         uuid NOT NULL,
    registry_revision_id uuid NOT NULL,
    transition_id       uuid NOT NULL,
    wind_task_id        uuid NOT NULL,
    wind_outcome_id     uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT wind_outcome_mapping_registry_fkey
        FOREIGN KEY (registry_id) REFERENCES aegis.registry(id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_revision_fkey
        FOREIGN KEY (registry_revision_id) REFERENCES aegis.registry_revision(id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_revision_registry_fkey
        FOREIGN KEY (registry_id, registry_revision_id)
        REFERENCES aegis.registry_revision(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_transition_fkey
        FOREIGN KEY (registry_id, transition_id) REFERENCES aegis.transition(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_transition_unique
        UNIQUE (registry_revision_id, transition_id),
    CONSTRAINT wind_outcome_mapping_wind_task_fkey
        FOREIGN KEY (wind_task_id) REFERENCES wind.tasks(id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_wind_outcome_fkey
        FOREIGN KEY (wind_outcome_id, wind_task_id)
        REFERENCES wind.task_outcomes(id, task_id) ON DELETE RESTRICT,
    CONSTRAINT wind_outcome_mapping_outcome_unique
        UNIQUE (registry_revision_id, wind_outcome_id)
);

COMMENT ON TABLE aegis.wind_outcome_mapping IS
    'Revision-scoped mapping from an Aegis transition to one enumerable outcome of its source-state Wind task';

CREATE INDEX IF NOT EXISTS idx_wind_outcome_mapping_revision
    ON aegis.wind_outcome_mapping (registry_revision_id);
CREATE INDEX IF NOT EXISTS idx_wind_outcome_mapping_wind_outcome
    ON aegis.wind_outcome_mapping (wind_outcome_id);

CREATE TABLE IF NOT EXISTS aegis.wind_compilation (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registry_id           uuid NOT NULL,
    registry_revision_id  uuid NOT NULL,
    wind_workflow_id      uuid NOT NULL,
    wind_workflow_version_id uuid NOT NULL,
    wind_workflow_version_number integer NOT NULL,
    source_digest         text NOT NULL CHECK (source_digest ~ '^sha256:[0-9a-f]{64}$'),
    model_digest          text NOT NULL CHECK (model_digest ~ '^sha256:[0-9a-f]{64}$'),
    wind_graph_digest     text NOT NULL CHECK (wind_graph_digest ~ '^sha256:[0-9a-f]{64}$'),
    compiler_version      text NOT NULL,
    compiler_config_digest text NOT NULL CHECK (compiler_config_digest ~ '^sha256:[0-9a-f]{64}$'),
    compiled_at           timestamptz NOT NULL DEFAULT now(),
    compiled_by           uuid,
    status                text NOT NULL DEFAULT 'succeeded',
    validation_result_id  uuid,
    validation_result_digest text,

    CONSTRAINT wind_compilation_registry_fkey
        FOREIGN KEY (registry_id) REFERENCES aegis.registry(id) ON DELETE RESTRICT,
    CONSTRAINT wind_compilation_revision_fkey
        FOREIGN KEY (registry_revision_id) REFERENCES aegis.registry_revision(id) ON DELETE RESTRICT,
    CONSTRAINT wind_compilation_revision_registry_fkey
        FOREIGN KEY (registry_id, registry_revision_id)
        REFERENCES aegis.registry_revision(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_compilation_wind_version_fkey
        FOREIGN KEY (wind_workflow_version_id, wind_workflow_id)
        REFERENCES wind.workflow_versions(id, workflow_id) ON DELETE RESTRICT,
    CONSTRAINT wind_compilation_validation_fkey
        FOREIGN KEY (registry_id, validation_result_id)
        REFERENCES aegis.validation_result(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT wind_compilation_status_check
        CHECK (status IN ('succeeded', 'failed', 'stale', 'invalid')),
    CONSTRAINT wind_compilation_version_unique
        UNIQUE (wind_workflow_version_id),
    CONSTRAINT wind_compilation_source_revision_unique
        UNIQUE (registry_revision_id, wind_workflow_version_id),
    CONSTRAINT wind_compilation_id_scope_unique
        UNIQUE (id, registry_id, registry_revision_id),
    CONSTRAINT wind_compilation_id_version_unique
        UNIQUE (id, wind_workflow_version_id),
    CONSTRAINT wind_compilation_validation_digest_check
        CHECK (validation_result_digest IS NULL OR validation_result_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT wind_compilation_validation_pair_check
        CHECK (validation_result_id IS NULL OR validation_result_digest IS NOT NULL)
);

COMMENT ON TABLE aegis.wind_compilation IS
    'Immutable lineage record for compiling one Aegis registry revision into one Wind workflow version; status is advisory and does not grant runtime authority';

CREATE INDEX IF NOT EXISTS idx_wind_compilation_registry_revision
    ON aegis.wind_compilation (registry_id, registry_revision_id, compiled_at DESC);
CREATE INDEX IF NOT EXISTS idx_wind_compilation_wind_workflow
    ON aegis.wind_compilation (wind_workflow_id, wind_workflow_version_id);

CREATE TABLE IF NOT EXISTS aegis.compiled_node (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compilation_id       uuid NOT NULL,
    registry_id          uuid NOT NULL,
    registry_revision_id uuid NOT NULL,
    state_id             uuid NOT NULL,
    wind_workflow_version_id uuid NOT NULL,
    wind_node_id         uuid NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT compiled_node_compilation_fkey
        FOREIGN KEY (compilation_id, registry_id, registry_revision_id)
        REFERENCES aegis.wind_compilation(id, registry_id, registry_revision_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_node_compilation_version_fkey
        FOREIGN KEY (compilation_id, wind_workflow_version_id)
        REFERENCES aegis.wind_compilation(id, wind_workflow_version_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_node_revision_fkey
        FOREIGN KEY (registry_id, registry_revision_id)
        REFERENCES aegis.registry_revision(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT compiled_node_state_fkey
        FOREIGN KEY (registry_id, state_id) REFERENCES aegis.state(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT compiled_node_wind_node_fkey
        FOREIGN KEY (wind_node_id, wind_workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_node_unique
        UNIQUE (compilation_id, state_id),
    CONSTRAINT compiled_node_wind_unique
        UNIQUE (compilation_id, wind_node_id)
);

COMMENT ON TABLE aegis.compiled_node IS
    'Immutable compilation lineage from a revision-scoped Aegis state to a Wind workflow node';

CREATE INDEX IF NOT EXISTS idx_compiled_node_revision
    ON aegis.compiled_node (registry_id, registry_revision_id);
CREATE INDEX IF NOT EXISTS idx_compiled_node_wind_node
    ON aegis.compiled_node (wind_node_id);

CREATE TABLE IF NOT EXISTS aegis.compiled_edge (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compilation_id       uuid NOT NULL,
    registry_id          uuid NOT NULL,
    registry_revision_id uuid NOT NULL,
    transition_id        uuid NOT NULL,
    wind_workflow_version_id uuid NOT NULL,
    wind_edge_id         uuid NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT compiled_edge_compilation_fkey
        FOREIGN KEY (compilation_id, registry_id, registry_revision_id)
        REFERENCES aegis.wind_compilation(id, registry_id, registry_revision_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_edge_compilation_version_fkey
        FOREIGN KEY (compilation_id, wind_workflow_version_id)
        REFERENCES aegis.wind_compilation(id, wind_workflow_version_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_edge_revision_fkey
        FOREIGN KEY (registry_id, registry_revision_id)
        REFERENCES aegis.registry_revision(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT compiled_edge_transition_fkey
        FOREIGN KEY (registry_id, transition_id) REFERENCES aegis.transition(registry_id, id) ON DELETE RESTRICT,
    CONSTRAINT compiled_edge_wind_edge_fkey
        FOREIGN KEY (wind_edge_id, wind_workflow_version_id)
        REFERENCES wind.workflow_edges(id, workflow_version_id) ON DELETE RESTRICT,
    CONSTRAINT compiled_edge_unique
        UNIQUE (compilation_id, transition_id),
    CONSTRAINT compiled_edge_wind_unique
        UNIQUE (compilation_id, wind_edge_id)
);

COMMENT ON TABLE aegis.compiled_edge IS
    'Immutable compilation lineage from a revision-scoped Aegis transition to a Wind workflow edge';

CREATE INDEX IF NOT EXISTS idx_compiled_edge_revision
    ON aegis.compiled_edge (registry_id, registry_revision_id);
CREATE INDEX IF NOT EXISTS idx_compiled_edge_wind_edge
    ON aegis.compiled_edge (wind_edge_id);

-- Cross-table bridge validation: enforce the check-only convention and ensure
-- transition outcomes belong to the source-state task selected for this exact
-- immutable revision. The composite FKs above handle row identity and scope;
-- this trigger handles the semantic relationship that SQL CHECK cannot express.
CREATE OR REPLACE FUNCTION aegis.validate_wind_bridge_mapping()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_tackle_task_id uuid;
    v_source_state_id uuid;
    v_mapped_task_id uuid;
    v_revision_model jsonb;
BEGIN
    SELECT t.tackle_task_id INTO v_tackle_task_id
    FROM wind.tasks t WHERE t.id = NEW.wind_task_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'wind task % does not exist', NEW.wind_task_id USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF TG_TABLE_NAME = 'wind_task_mapping' THEN
        IF NEW.is_check_only IS DISTINCT FROM (v_tackle_task_id IS NULL) THEN
            RAISE EXCEPTION 'is_check_only for Wind task % does not match tackle_task_id nullability', NEW.wind_task_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT model INTO v_revision_model
        FROM aegis.registry_revision
        WHERE id = NEW.registry_revision_id AND registry_id = NEW.registry_id;
        IF v_revision_model IS NULL OR NOT (v_revision_model->'states' @> jsonb_build_array(jsonb_build_object('id', NEW.state_id))) THEN
            RAISE EXCEPTION 'state % is not present in registry revision %', NEW.state_id, NEW.registry_revision_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT from_state_id INTO v_source_state_id
        FROM aegis.transition
        WHERE id = NEW.transition_id AND registry_id = NEW.registry_id;
        SELECT wind_task_id INTO v_mapped_task_id
        FROM aegis.wind_task_mapping
        WHERE registry_id = NEW.registry_id
          AND registry_revision_id = NEW.registry_revision_id
          AND state_id = v_source_state_id;
        IF v_mapped_task_id IS NULL OR v_mapped_task_id <> NEW.wind_task_id THEN
            RAISE EXCEPTION 'transition % outcome task % does not match source state % mapping',
                NEW.transition_id, NEW.wind_task_id, v_source_state_id USING ERRCODE = 'check_violation';
        END IF;
        SELECT model INTO v_revision_model
        FROM aegis.registry_revision
        WHERE id = NEW.registry_revision_id AND registry_id = NEW.registry_id;
        IF v_revision_model IS NULL OR NOT (v_revision_model->'transitions' @> jsonb_build_array(jsonb_build_object('id', NEW.transition_id))) THEN
            RAISE EXCEPTION 'transition % is not present in registry revision %', NEW.transition_id, NEW.registry_revision_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_task_mapping_validate ON aegis.wind_task_mapping;
CREATE TRIGGER trg_wind_task_mapping_validate
    BEFORE INSERT OR UPDATE ON aegis.wind_task_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.validate_wind_bridge_mapping();
DROP TRIGGER IF EXISTS trg_wind_outcome_mapping_validate ON aegis.wind_outcome_mapping;
CREATE TRIGGER trg_wind_outcome_mapping_validate
    BEFORE INSERT OR UPDATE ON aegis.wind_outcome_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.validate_wind_bridge_mapping();

-- A mapping is revision-scoped authoring metadata: create a new mapping for
-- a new revision rather than mutating the mapping attached to an old revision.
CREATE OR REPLACE FUNCTION aegis.forbid_wind_bridge_mapping_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'aegis.% is immutable: % blocked for row %', TG_TABLE_NAME, TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_task_mapping_no_update ON aegis.wind_task_mapping;
CREATE TRIGGER trg_wind_task_mapping_no_update
    BEFORE UPDATE ON aegis.wind_task_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_bridge_mapping_mutation();
DROP TRIGGER IF EXISTS trg_wind_task_mapping_no_delete ON aegis.wind_task_mapping;
CREATE TRIGGER trg_wind_task_mapping_no_delete
    BEFORE DELETE ON aegis.wind_task_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_bridge_mapping_mutation();
DROP TRIGGER IF EXISTS trg_wind_outcome_mapping_no_update ON aegis.wind_outcome_mapping;
CREATE TRIGGER trg_wind_outcome_mapping_no_update
    BEFORE UPDATE ON aegis.wind_outcome_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_bridge_mapping_mutation();
DROP TRIGGER IF EXISTS trg_wind_outcome_mapping_no_delete ON aegis.wind_outcome_mapping;
CREATE TRIGGER trg_wind_outcome_mapping_no_delete
    BEFORE DELETE ON aegis.wind_outcome_mapping
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_bridge_mapping_mutation();

-- Phase A compilation lineage is immutable. The compiler inserts a terminal
-- status; status changes are represented by a new compilation/evidence row.
CREATE OR REPLACE FUNCTION aegis.forbid_wind_compilation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'aegis.% is immutable: % blocked for row %', TG_TABLE_NAME, TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE OR REPLACE FUNCTION aegis.validate_wind_compilation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_revision aegis.registry_revision%ROWTYPE;
    v_version_number integer;
BEGIN
    SELECT * INTO v_revision
    FROM aegis.registry_revision
    WHERE id = NEW.registry_revision_id AND registry_id = NEW.registry_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'registry revision % is not owned by registry %', NEW.registry_revision_id, NEW.registry_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NEW.source_digest <> v_revision.source_digest OR NEW.model_digest <> v_revision.model_digest THEN
        RAISE EXCEPTION 'compilation digests do not match registry revision %', NEW.registry_revision_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT version_number INTO v_version_number
    FROM wind.workflow_versions
    WHERE id = NEW.wind_workflow_version_id AND workflow_id = NEW.wind_workflow_id;
    IF NEW.wind_workflow_version_number IS NULL THEN
        NEW.wind_workflow_version_number := v_version_number;
    ELSIF NEW.wind_workflow_version_number <> v_version_number THEN
        RAISE EXCEPTION 'workflow version number does not match Wind workflow version %', NEW.wind_workflow_version_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_compilation_validate ON aegis.wind_compilation;
CREATE TRIGGER trg_wind_compilation_validate
    BEFORE INSERT ON aegis.wind_compilation
    FOR EACH ROW EXECUTE FUNCTION aegis.validate_wind_compilation();

DROP TRIGGER IF EXISTS trg_wind_compilation_no_update ON aegis.wind_compilation;
CREATE TRIGGER trg_wind_compilation_no_update
    BEFORE UPDATE ON aegis.wind_compilation
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();
DROP TRIGGER IF EXISTS trg_wind_compilation_no_delete ON aegis.wind_compilation;
CREATE TRIGGER trg_wind_compilation_no_delete
    BEFORE DELETE ON aegis.wind_compilation
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();
DROP TRIGGER IF EXISTS trg_compiled_node_no_update ON aegis.compiled_node;
CREATE TRIGGER trg_compiled_node_no_update
    BEFORE UPDATE ON aegis.compiled_node
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();
DROP TRIGGER IF EXISTS trg_compiled_node_no_delete ON aegis.compiled_node;
CREATE TRIGGER trg_compiled_node_no_delete
    BEFORE DELETE ON aegis.compiled_node
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();
DROP TRIGGER IF EXISTS trg_compiled_edge_no_update ON aegis.compiled_edge;
CREATE TRIGGER trg_compiled_edge_no_update
    BEFORE UPDATE ON aegis.compiled_edge
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();
DROP TRIGGER IF EXISTS trg_compiled_edge_no_delete ON aegis.compiled_edge;
CREATE TRIGGER trg_compiled_edge_no_delete
    BEFORE DELETE ON aegis.compiled_edge
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_wind_compilation_mutation();

COMMIT;
