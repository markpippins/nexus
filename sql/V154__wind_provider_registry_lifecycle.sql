-- V154 — Wind append-only provider registry lifecycle and credential rotation
--
-- Authorized by architect ruling c566c7a4-41aa-4958-85ee-3fd8b8f70d94.
-- Registry lifecycle is an immutable revision stream. The latest revision for
-- an adapter is the only dispatchable state; old rows remain audit history.
-- Credentials never enter Wind: only an environment reference and a digest of
-- the resolved value are retained in evidence/rotation metadata.

BEGIN;

CREATE TABLE IF NOT EXISTS wind.provider_contract_revisions (
    revision_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    adapter_id              text NOT NULL,
    revision_number         integer NOT NULL CHECK (revision_number > 0),
    adapter_version         text NOT NULL,
    provider_id             text NOT NULL,
    provider_version        text NOT NULL,
    invocation_mode         text NOT NULL CHECK (invocation_mode IN ('CLI', 'HTTP', 'SDK', 'MCP')),
    input_schema_digest     text NOT NULL CHECK (input_schema_digest ~ '^sha256:[0-9a-f]{64}$'),
    output_schema_digest    text NOT NULL CHECK (output_schema_digest ~ '^sha256:[0-9a-f]{64}$'),
    credential_env_ref      text,
    endpoint_env_ref        text,
    schema_verification     text NOT NULL DEFAULT 'verified'
                            CHECK (schema_verification = 'verified'),
    lifecycle_state         text NOT NULL CHECK (lifecycle_state IN ('ACTIVE', 'DEACTIVATED', 'RETIRED')),
    lifecycle_action        text NOT NULL CHECK (lifecycle_action IN ('REGISTERED', 'DEACTIVATED', 'RETIRED')),
    supersedes_revision_id  uuid,
    approval_record_ref     text NOT NULL,
    requested_by_role       text NOT NULL DEFAULT 'engineer',
    acknowledged_by_role    text,
    approved_by_role        text,
    confirmed_by_role       text,
    lifecycle_reason        text,
    registered_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT provider_revision_number_unique UNIQUE (adapter_id, revision_number),
    CONSTRAINT provider_revision_supersedes_fkey
        FOREIGN KEY (supersedes_revision_id) REFERENCES wind.provider_contract_revisions(revision_id),
    CONSTRAINT provider_revision_credential_ref_check
        CHECK (credential_env_ref IS NULL OR credential_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_revision_endpoint_ref_check
        CHECK (endpoint_env_ref IS NULL OR endpoint_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_revision_http_endpoint_check
        CHECK (invocation_mode <> 'HTTP' OR endpoint_env_ref IS NOT NULL),
    CONSTRAINT provider_revision_action_state_check
        CHECK ((lifecycle_action = 'REGISTERED' AND lifecycle_state = 'ACTIVE')
            OR (lifecycle_action = 'DEACTIVATED' AND lifecycle_state = 'DEACTIVATED')
            OR (lifecycle_action = 'RETIRED' AND lifecycle_state = 'RETIRED')),
    CONSTRAINT provider_revision_registration_control_check
        CHECK ((lifecycle_action = 'REGISTERED'
                AND requested_by_role = 'engineer'
                AND acknowledged_by_role IN ('architect', 'operator'))
            OR (lifecycle_action <> 'REGISTERED'
                AND approval_record_ref IS NOT NULL
                AND approved_by_role IS NOT NULL
                AND confirmed_by_role IS NOT NULL
                AND approved_by_role <> confirmed_by_role))
);

CREATE INDEX IF NOT EXISTS idx_wind_provider_revisions_adapter
    ON wind.provider_contract_revisions (adapter_id, revision_number DESC);
CREATE INDEX IF NOT EXISTS idx_wind_provider_revisions_state
    ON wind.provider_contract_revisions (lifecycle_state, adapter_id, revision_number DESC);

DO $$
BEGIN
    IF to_regclass('wind.v_active_provider_contracts') IS NULL THEN
        EXECUTE $view$
        CREATE VIEW wind.v_active_provider_contracts AS
        SELECT DISTINCT ON (adapter_id)
            revision_id, adapter_id, revision_number, adapter_version, provider_id,
            provider_version, invocation_mode, input_schema_digest,
            output_schema_digest, credential_env_ref, endpoint_env_ref,
            schema_verification, lifecycle_state, lifecycle_action, registered_at
        FROM wind.provider_contract_revisions
        ORDER BY adapter_id, revision_number DESC
        $view$;
    END IF;
END;
$$;

CREATE OR REPLACE VIEW wind.v_active_provider_contracts AS
SELECT DISTINCT ON (adapter_id)
    revision_id, adapter_id, revision_number, adapter_version, provider_id,
    provider_version, invocation_mode, input_schema_digest,
    output_schema_digest, credential_env_ref, endpoint_env_ref,
    schema_verification, lifecycle_state, lifecycle_action, registered_at
FROM wind.provider_contract_revisions
ORDER BY adapter_id, revision_number DESC;

COMMENT ON VIEW wind.v_active_provider_contracts IS
    'Latest immutable provider registry revision per adapter; dispatch is permitted only when lifecycle_state=ACTIVE';

-- Seed one immutable revision for each V153 contract. This is deliberately
-- INSERT-only and remains safe to reapply.
INSERT INTO wind.provider_contract_revisions
    (adapter_id, revision_number, adapter_version, provider_id, provider_version,
     invocation_mode, input_schema_digest, output_schema_digest, credential_env_ref,
     endpoint_env_ref, schema_verification, lifecycle_state, lifecycle_action,
     approval_record_ref, requested_by_role, acknowledged_by_role,
     approved_by_role, confirmed_by_role)
SELECT pc.adapter_id, 1, pc.adapter_version, pc.provider_id, pc.provider_version,
       pc.invocation_mode, pc.input_schema_digest, pc.output_schema_digest,
       pc.credential_env_ref, pc.endpoint_env_ref, pc.schema_verification,
       CASE WHEN pc.is_active THEN 'ACTIVE' ELSE 'DEACTIVATED' END,
       CASE WHEN pc.is_active THEN 'REGISTERED' ELSE 'DEACTIVATED' END,
       'migration:V154-seed', 'engineer',
       CASE WHEN pc.is_active THEN 'architect' ELSE NULL END,
       CASE WHEN pc.is_active THEN NULL ELSE 'architect' END,
       CASE WHEN pc.is_active THEN NULL ELSE 'operator' END
FROM wind.provider_contracts pc
WHERE NOT EXISTS (
    SELECT 1 FROM wind.provider_contract_revisions pr
    WHERE pr.adapter_id = pc.adapter_id AND pr.revision_number = 1
);

CREATE TABLE IF NOT EXISTS wind.provider_credential_rotations (
    rotation_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    adapter_id              text NOT NULL,
    revision_id             uuid NOT NULL REFERENCES wind.provider_contract_revisions(revision_id),
    credential_env_ref      text NOT NULL CHECK (credential_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    credential_fingerprint  text NOT NULL CHECK (credential_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    rotation_record_ref     text NOT NULL,
    rotated_at              timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_wind_credential_rotations_adapter
    ON wind.provider_credential_rotations (adapter_id, rotated_at DESC);

CREATE OR REPLACE FUNCTION wind.forbid_provider_registry_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'wind.% is append-only: % blocked for row %', TG_TABLE_NAME, TG_OP, OLD.revision_id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_provider_contract_revisions_immutable ON wind.provider_contract_revisions;
CREATE TRIGGER trg_wind_provider_contract_revisions_immutable
    BEFORE UPDATE OR DELETE ON wind.provider_contract_revisions
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_provider_registry_mutation();

CREATE OR REPLACE FUNCTION wind.forbid_provider_rotation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'wind.% is append-only: % blocked for row %', TG_TABLE_NAME, TG_OP, OLD.rotation_id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_provider_credential_rotations_immutable ON wind.provider_credential_rotations;
CREATE TRIGGER trg_wind_provider_credential_rotations_immutable
    BEFORE UPDATE OR DELETE ON wind.provider_credential_rotations
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_provider_rotation_mutation();

ALTER TABLE wind.execution_attempts
    ADD COLUMN IF NOT EXISTS credential_env_ref text,
    ADD COLUMN IF NOT EXISTS credential_fingerprint text;
ALTER TABLE wind.execution_receipts
    ADD COLUMN IF NOT EXISTS credential_env_ref text,
    ADD COLUMN IF NOT EXISTS credential_fingerprint text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'wind.execution_attempts'::regclass
          AND conname = 'execution_attempt_credential_fingerprint_check'
    ) THEN
        ALTER TABLE wind.execution_attempts
            ADD CONSTRAINT execution_attempt_credential_fingerprint_check
            CHECK (credential_fingerprint IS NULL OR credential_fingerprint ~ '^sha256:[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'wind.execution_receipts'::regclass
          AND conname = 'execution_receipt_credential_fingerprint_check'
    ) THEN
        ALTER TABLE wind.execution_receipts
            ADD CONSTRAINT execution_receipt_credential_fingerprint_check
            CHECK (credential_fingerprint IS NULL OR credential_fingerprint ~ '^sha256:[0-9a-f]{64}$');
    END IF;
END;
$$;

COMMIT;
