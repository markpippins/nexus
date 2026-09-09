-- V150 — Wind immutability for Aegis-compiled artifacts
--
-- Wind remains the runtime owner. Before a workflow version is referenced by
-- an Aegis compilation it may be edited through its normal design-time APIs.
-- Once referenced, its version, nodes, and edges are immutable so the graph
-- digest and Aegis lineage cannot silently drift from the runtime artifact.

BEGIN;

CREATE OR REPLACE FUNCTION wind.forbid_compiled_artifact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_version_id uuid;
    v_compilation_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'workflow_versions' THEN
        v_version_id := OLD.id;
    ELSIF TG_TABLE_NAME = 'workflow_nodes' THEN
        v_version_id := OLD.workflow_version_id;
    ELSIF TG_TABLE_NAME = 'workflow_edges' THEN
        v_version_id := OLD.workflow_version_id;
    END IF;

    SELECT id INTO v_compilation_id
      FROM aegis.wind_compilation
     WHERE wind_workflow_version_id = v_version_id
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'wind.% is immutable after Aegis compilation %, operation % blocked for version %',
            TG_TABLE_NAME, v_compilation_id, TG_OP, v_version_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_workflow_versions_compiled_immutable ON wind.workflow_versions;
CREATE TRIGGER trg_workflow_versions_compiled_immutable
    BEFORE UPDATE OR DELETE ON wind.workflow_versions
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_compiled_artifact_mutation();

DROP TRIGGER IF EXISTS trg_workflow_nodes_compiled_immutable ON wind.workflow_nodes;
CREATE TRIGGER trg_workflow_nodes_compiled_immutable
    BEFORE UPDATE OR DELETE ON wind.workflow_nodes
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_compiled_artifact_mutation();

DROP TRIGGER IF EXISTS trg_workflow_edges_compiled_immutable ON wind.workflow_edges;
CREATE TRIGGER trg_workflow_edges_compiled_immutable
    BEFORE UPDATE OR DELETE ON wind.workflow_edges
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_compiled_artifact_mutation();

COMMENT ON FUNCTION wind.forbid_compiled_artifact_mutation() IS
    'Blocks mutation of workflow versions, nodes, and edges referenced by immutable Aegis compilation lineage';

COMMIT;
