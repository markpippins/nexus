-- V153 — Wind advisory provider-adapter dispatch registry and reservation states
--
-- Authorized by architect decision 44a25b17-5d66-4ec7-9611-86d9a4767e5c.
-- This migration adds only the strict adapter allow-list and the append-only
-- record-then-act reservation states. It does not grant lifecycle authority.
-- Provider credentials are environment-variable names only; secret material
-- never enters wind.*.

BEGIN;

CREATE TABLE IF NOT EXISTS wind.provider_contracts (
    adapter_id              text PRIMARY KEY,
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
    is_active               boolean NOT NULL DEFAULT true,
    registered_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT provider_contract_credential_ref_check
        CHECK (credential_env_ref IS NULL OR credential_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_contract_endpoint_ref_check
        CHECK (endpoint_env_ref IS NULL OR endpoint_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_contract_http_endpoint_check
        CHECK (invocation_mode <> 'HTTP' OR endpoint_env_ref IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_wind_provider_contracts_provider
    ON wind.provider_contracts (provider_id, provider_version);
CREATE INDEX IF NOT EXISTS idx_wind_provider_contracts_active
    ON wind.provider_contracts (is_active, adapter_id);

COMMENT ON TABLE wind.provider_contracts IS
    'Strict persisted allow-list of schema-verified advisory provider adapters; credential and endpoint columns contain environment variable names, never secret material';

-- The dispatch path records a reservation before acting. Since execution
-- evidence is append-only, the observed terminal result is a child attempt
-- rather than an UPDATE to the reservation row.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'wind.execution_attempts'::regclass
          AND conname = 'execution_attempt_status_check'
    ) THEN
        ALTER TABLE wind.execution_attempts DROP CONSTRAINT execution_attempt_status_check;
    END IF;
END;
$$;

ALTER TABLE wind.execution_attempts
    ADD CONSTRAINT execution_attempt_status_check
    CHECK (status IN ('IN_FLIGHT', 'ABORTED', 'SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID'));

COMMENT ON COLUMN wind.execution_attempts.status IS
    'IN_FLIGHT is a record-before-act reservation; terminal observed outcomes are append-only child attempts; ABORTED is truthful no-outcome termination';

-- Built-in adapters are still subject to this persisted registry. These rows
-- make the first bounded dispatch implementations explicit and reviewable.
-- The digest values are contract identities, not result digests.
INSERT INTO wind.provider_contracts
    (adapter_id, adapter_version, provider_id, provider_version, invocation_mode,
     input_schema_digest, output_schema_digest, credential_env_ref,
     endpoint_env_ref, schema_verification, is_active)
VALUES
    ('wind.echo.v1', '1.0.0', 'wind-local', '1', 'SDK',
     'sha256:1111111111111111111111111111111111111111111111111111111111111111',
     'sha256:2222222222222222222222222222222222222222222222222222222222222222',
     NULL, NULL, 'verified', true),
    ('wind.http-json.v1', '1.0.0', 'wind-http', '1', 'HTTP',
     'sha256:3333333333333333333333333333333333333333333333333333333333333333',
     'sha256:4444444444444444444444444444444444444444444444444444444444444444',
     NULL, 'WIND_HTTP_JSON_ENDPOINT', 'verified', true)
ON CONFLICT (adapter_id) DO UPDATE SET
    adapter_version = EXCLUDED.adapter_version,
    provider_id = EXCLUDED.provider_id,
    provider_version = EXCLUDED.provider_version,
    invocation_mode = EXCLUDED.invocation_mode,
    input_schema_digest = EXCLUDED.input_schema_digest,
    output_schema_digest = EXCLUDED.output_schema_digest,
    credential_env_ref = EXCLUDED.credential_env_ref,
    endpoint_env_ref = EXCLUDED.endpoint_env_ref,
    schema_verification = EXCLUDED.schema_verification,
    is_active = EXCLUDED.is_active;

COMMIT;
