-- V148 — Aegis A: truthful, content-addressed verification evidence
--
-- Design-time registry rows remain mutable. Verification never targets the
-- mutable registry directly: it targets an immutable registry_revision.
-- model_check_result becomes append-only evidence and can never claim a
-- verified result without verified safety from the real checker.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- The source schema intended this rule but PostgreSQL partial uniqueness must
-- be a unique index, not a table constraint.
CREATE UNIQUE INDEX IF NOT EXISTS registry_active_name_unique
    ON aegis.registry (name)
    WHERE is_active = true;

CREATE TABLE IF NOT EXISTS aegis.registry_revision (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registry_id              uuid NOT NULL REFERENCES aegis.registry(id),
    revision_number          bigint NOT NULL,
    source                   text NOT NULL DEFAULT '',
    source_digest            text NOT NULL CHECK (source_digest ~ '^sha256:[0-9a-f]{64}$'),
    model                    jsonb NOT NULL,
    model_digest             text NOT NULL CHECK (model_digest ~ '^sha256:[0-9a-f]{64}$'),
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               text,
    supersedes_revision_id   uuid REFERENCES aegis.registry_revision(id),
    CONSTRAINT registry_revision_number_unique UNIQUE (registry_id, revision_number),
    CONSTRAINT registry_revision_id_digest_unique UNIQUE (registry_id, source_digest, model_digest)
);

CREATE INDEX IF NOT EXISTS idx_registry_revision_registry
    ON aegis.registry_revision (registry_id, revision_number DESC);
CREATE INDEX IF NOT EXISTS idx_registry_revision_source_digest
    ON aegis.registry_revision (source_digest);
CREATE INDEX IF NOT EXISTS idx_registry_revision_model_digest
    ON aegis.registry_revision (model_digest);

COMMENT ON TABLE aegis.registry_revision IS
    'Immutable content-addressed snapshot of a mutable Aegis registry authoring state';

CREATE OR REPLACE FUNCTION aegis.forbid_registry_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'aegis.registry_revision is immutable: % blocked for revision %', TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_registry_revision_no_update ON aegis.registry_revision;
CREATE TRIGGER trg_registry_revision_no_update
    BEFORE UPDATE ON aegis.registry_revision
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_registry_revision_mutation();
DROP TRIGGER IF EXISTS trg_registry_revision_no_delete ON aegis.registry_revision;
CREATE TRIGGER trg_registry_revision_no_delete
    BEFORE DELETE ON aegis.registry_revision
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_registry_revision_mutation();

-- Snapshot the complete structured registry in one database transaction. JSONB
-- canonicalizes object key order; explicit child ordering makes the digest
-- stable across repeated snapshots of unchanged authoring state.
CREATE OR REPLACE FUNCTION aegis.create_registry_revision(
    p_registry_id uuid,
    p_created_by text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
    v_registry aegis.registry%ROWTYPE;
    v_revision_number bigint;
    v_source text;
    v_model jsonb;
    v_source_digest text;
    v_model_digest text;
    v_revision_id uuid;
    v_previous uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(p_registry_id::text, 148));
    SELECT * INTO v_registry
    FROM aegis.registry
    WHERE id = p_registry_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'registry not found: %', p_registry_id USING ERRCODE = 'foreign_key_violation';
    END IF;

    SELECT COALESCE(max(revision_number), 0) + 1,
           (array_agg(id ORDER BY revision_number DESC))[1]
      INTO v_revision_number, v_previous
    FROM aegis.registry_revision
    WHERE registry_id = p_registry_id;

    v_source := COALESCE(v_registry.tla_plus_source, '');
    v_source_digest := 'sha256:' || encode(public.digest(convert_to(v_source, 'UTF8'), 'sha256'), 'hex');

    SELECT jsonb_build_object(
        'registry', to_jsonb(v_registry) - 'tla_plus_source',
        'constants', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.constant x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'variables', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.variable x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'states', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.state x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'transitions', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.transition x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'invariants', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.invariant x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'properties', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.property x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'temporal_properties', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.temporal_property x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'concept_mappings', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.concept_mapping x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'attribute_mappings', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.attribute_mapping x WHERE x.registry_id = p_registry_id), '[]'::jsonb),
        'relationship_mappings', COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at, x.id) FROM aegis.relationship_mapping x WHERE x.registry_id = p_registry_id), '[]'::jsonb)
    ) INTO v_model;    v_model_digest := 'sha256:' || encode(public.digest(convert_to(v_model::text, 'UTF8'), 'sha256'), 'hex');

    -- Idempotent snapshot: an unchanged authoring head maps to the same
    -- immutable revision, including when a migration/backfill is re-run.
    SELECT id INTO v_revision_id
    FROM aegis.registry_revision
    WHERE registry_id = p_registry_id
      AND source_digest = v_source_digest
      AND model_digest = v_model_digest
    ORDER BY revision_number DESC
    LIMIT 1;
    IF FOUND THEN
        RETURN v_revision_id;
    END IF;

    INSERT INTO aegis.registry_revision

        (registry_id, revision_number, source, source_digest, model, model_digest, created_by, supersedes_revision_id)
    VALUES
        (p_registry_id, v_revision_number, v_source, v_source_digest, v_model, v_model_digest, p_created_by, v_previous)
    RETURNING id INTO v_revision_id;

    RETURN v_revision_id;
END;
$$;

COMMENT ON FUNCTION aegis.create_registry_revision(uuid, text) IS
    'Creates an immutable content-addressed snapshot of the current registry authoring state';

-- Extend the existing result table rather than create a parallel result store.
ALTER TABLE aegis.model_check_result
    ADD COLUMN IF NOT EXISTS registry_revision_id uuid,
    ADD COLUMN IF NOT EXISTS engine text,
    ADD COLUMN IF NOT EXISTS engine_version text,
    ADD COLUMN IF NOT EXISTS checker_config_digest text,
    ADD COLUMN IF NOT EXISTS source_digest text,
    ADD COLUMN IF NOT EXISTS model_digest text,
    ADD COLUMN IF NOT EXISTS input_snapshot_digest text,
    ADD COLUMN IF NOT EXISTS result_digest text,
    ADD COLUMN IF NOT EXISTS safety_status text,
    ADD COLUMN IF NOT EXISTS liveness_status text,
    ADD COLUMN IF NOT EXISTS authority_level text DEFAULT 'advisory',
    ADD COLUMN IF NOT EXISTS reason text;

ALTER TABLE aegis.model_check_result
    DROP CONSTRAINT IF EXISTS model_check_result_registry_revision_fkey,
    ADD CONSTRAINT model_check_result_registry_revision_fkey
        FOREIGN KEY (registry_revision_id) REFERENCES aegis.registry_revision(id),
    DROP CONSTRAINT IF EXISTS model_check_result_property_fkey,
    DROP CONSTRAINT IF EXISTS model_check_result_registry_property_fkey;

-- A result's optional property must belong to the same registry as the result.
CREATE UNIQUE INDEX IF NOT EXISTS property_registry_id_unique
    ON aegis.property (registry_id, id);
ALTER TABLE aegis.model_check_result
    ADD CONSTRAINT model_check_result_registry_property_fkey
        FOREIGN KEY (registry_id, property_id)
        REFERENCES aegis.property(registry_id, id);

-- Backfill any historical rows conservatively. Pre-Phase-A statuses are not
-- treated as formal proof: prior success/pass values become unknown unless a
-- new real checker run produces a verified result.
DO $$
DECLARE
    r record;
    v_revision_id uuid;
    v_source_digest text;
    v_model_digest text;
BEGIN
    FOR r IN SELECT DISTINCT registry_id FROM aegis.model_check_result LOOP
        SELECT aegis.create_registry_revision(r.registry_id, 'phase-a-backfill') INTO v_revision_id;
        SELECT source_digest, model_digest INTO v_source_digest, v_model_digest
        FROM aegis.registry_revision WHERE id = v_revision_id;
        UPDATE aegis.model_check_result m
        SET registry_revision_id = v_revision_id,
            engine = 'legacy',
            engine_version = 'pre-phase-a',
            checker_config_digest = 'sha256:' || encode(public.digest(convert_to('{}', 'UTF8'), 'sha256'), 'hex'),
            source_digest = v_source_digest,
            model_digest = v_model_digest,
            input_snapshot_digest = 'sha256:' || encode(public.digest(convert_to('{}', 'UTF8'), 'sha256'), 'hex'),
            result_digest = 'sha256:' || encode(public.digest(convert_to(m.id::text, 'UTF8'), 'sha256'), 'hex'),
            status = CASE WHEN lower(m.status) IN ('failure', 'failed', 'fail', 'violated') THEN 'violated' ELSE 'unknown' END,
            safety_status = CASE WHEN lower(m.status) IN ('failure', 'failed', 'fail', 'violated') THEN 'violated' ELSE 'unknown' END,
            liveness_status = 'unknown',
            authority_level = 'advisory',
            reason = 'Backfilled from pre-Phase-A result; no formal verification claim retained'
        WHERE m.registry_id = r.registry_id;
    END LOOP;
END;
$$;

ALTER TABLE aegis.model_check_result
    DROP CONSTRAINT IF EXISTS model_check_result_status_check,
    ADD CONSTRAINT model_check_result_status_check CHECK
        (status IN ('verified', 'violated', 'unknown', 'stale', 'invalid', 'unavailable')),
    DROP CONSTRAINT IF EXISTS model_check_result_safety_status_check,
    ADD CONSTRAINT model_check_result_safety_status_check CHECK
        (safety_status IN ('verified', 'violated', 'unknown', 'stale', 'invalid', 'unavailable')),
    DROP CONSTRAINT IF EXISTS model_check_result_liveness_status_check,
    ADD CONSTRAINT model_check_result_liveness_status_check CHECK
        (liveness_status IN ('verified', 'violated', 'unknown', 'stale', 'invalid', 'unavailable')),
    DROP CONSTRAINT IF EXISTS model_check_result_authority_level_check,
    ADD CONSTRAINT model_check_result_authority_level_check CHECK (authority_level = 'advisory'),
    DROP CONSTRAINT IF EXISTS model_check_result_verified_safety_check,
    ADD CONSTRAINT model_check_result_verified_safety_check CHECK
        (status <> 'verified' OR safety_status = 'verified'),
    DROP CONSTRAINT IF EXISTS model_check_result_liveness_gate_check,
    ADD CONSTRAINT model_check_result_liveness_gate_check CHECK (liveness_status <> 'verified'),
    DROP CONSTRAINT IF EXISTS model_check_result_source_digest_check,
    ADD CONSTRAINT model_check_result_source_digest_check CHECK (source_digest IS NULL OR source_digest ~ '^sha256:[0-9a-f]{64}$'),
    DROP CONSTRAINT IF EXISTS model_check_result_model_digest_check,
    ADD CONSTRAINT model_check_result_model_digest_check CHECK (model_digest IS NULL OR model_digest ~ '^sha256:[0-9a-f]{64}$'),
    DROP CONSTRAINT IF EXISTS model_check_result_checker_config_digest_check,
    ADD CONSTRAINT model_check_result_checker_config_digest_check CHECK (checker_config_digest IS NULL OR checker_config_digest ~ '^sha256:[0-9a-f]{64}$'),
    DROP CONSTRAINT IF EXISTS model_check_result_input_snapshot_digest_check,
    ADD CONSTRAINT model_check_result_input_snapshot_digest_check CHECK (input_snapshot_digest IS NULL OR input_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'),
    DROP CONSTRAINT IF EXISTS model_check_result_result_digest_check,
    ADD CONSTRAINT model_check_result_result_digest_check CHECK (result_digest IS NULL OR result_digest ~ '^sha256:[0-9a-f]{64}$'),
    ALTER COLUMN registry_revision_id SET NOT NULL,
    ALTER COLUMN engine SET NOT NULL,
    ALTER COLUMN engine_version SET NOT NULL,
    ALTER COLUMN checker_config_digest SET NOT NULL,
    ALTER COLUMN source_digest SET NOT NULL,
    ALTER COLUMN model_digest SET NOT NULL,
    ALTER COLUMN input_snapshot_digest SET NOT NULL,
    ALTER COLUMN result_digest SET NOT NULL,
    ALTER COLUMN safety_status SET NOT NULL,
    ALTER COLUMN liveness_status SET NOT NULL,
    ALTER COLUMN authority_level SET NOT NULL,
    ALTER COLUMN reason SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_check_revision
    ON aegis.model_check_result (registry_revision_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_check_result_digest
    ON aegis.model_check_result (result_digest);

COMMENT ON TABLE aegis.model_check_result IS
    'Append-only advisory verification evidence; never a lifecycle or PEB authority record';

CREATE OR REPLACE FUNCTION aegis.forbid_model_check_result_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'aegis.model_check_result is append-only: % blocked for result %', TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_check_result_no_update ON aegis.model_check_result;
CREATE TRIGGER trg_model_check_result_no_update
    BEFORE UPDATE ON aegis.model_check_result
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_model_check_result_mutation();
DROP TRIGGER IF EXISTS trg_model_check_result_no_delete ON aegis.model_check_result;
CREATE TRIGGER trg_model_check_result_no_delete
    BEFORE DELETE ON aegis.model_check_result
    FOR EACH ROW EXECUTE FUNCTION aegis.forbid_model_check_result_mutation();

COMMIT;
