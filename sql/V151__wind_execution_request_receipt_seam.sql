-- V151 — Wind execution-request / attempt / receipt seam (Phase B)
--
-- This is an evidence projection only. It deliberately does not create an
-- admission path, mutate Resolution/PEB, or execute a provider. Every row is
-- an immutable, revision-pinned record. Corrections are new attempts/receipts.

BEGIN;

CREATE TABLE IF NOT EXISTS wind.execution_requests (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_version_id         uuid,
    node_id                     uuid,
    artifact_type               text NOT NULL,
    artifact_ref                text NOT NULL,
    artifact_revision           text NOT NULL,
    artifact_fingerprint        text NOT NULL CHECK (artifact_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    read_set_digest             text NOT NULL CHECK (read_set_digest ~ '^sha256:[0-9a-f]{64}$'),
    evaluator_contract_digest   text NOT NULL CHECK (evaluator_contract_digest ~ '^sha256:[0-9a-f]{64}$'),
    causation_id                uuid,
    correlation_id              uuid NOT NULL,
    idempotency_key             text NOT NULL UNIQUE,
    provider_contract           jsonb NOT NULL DEFAULT '{}'::jsonb,
    invocation_contract         jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_policy              jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_digest              text NOT NULL CHECK (request_digest ~ '^sha256:[0-9a-f]{64}$'),
    authority_level             text NOT NULL DEFAULT 'advisory',
    requested_at                timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT execution_request_authority_check
        CHECK (authority_level = 'advisory'),
    CONSTRAINT execution_request_version_fkey
        FOREIGN KEY (workflow_version_id) REFERENCES wind.workflow_versions(id),
    CONSTRAINT execution_request_node_fkey
        FOREIGN KEY (node_id) REFERENCES wind.workflow_nodes(id),
    CONSTRAINT execution_request_node_version_fkey
        FOREIGN KEY (node_id, workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id),
    CONSTRAINT execution_request_node_pair_check
        CHECK ((node_id IS NULL) = (workflow_version_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_wind_execution_requests_correlation
    ON wind.execution_requests (correlation_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_wind_execution_requests_artifact
    ON wind.execution_requests (artifact_type, artifact_ref, artifact_revision);

CREATE TABLE IF NOT EXISTS wind.execution_attempts (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id                  uuid NOT NULL,
    parent_attempt_id           uuid,
    attempt_number              integer NOT NULL CHECK (attempt_number > 0),
    attempt_idempotency_key     text NOT NULL,
    executor_id                 text NOT NULL,
    provider_invocation_ref    text,
    status                      text NOT NULL,
    result                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    error                       text,
    result_digest               text NOT NULL CHECK (result_digest ~ '^sha256:[0-9a-f]{64}$'),
    started_at                  timestamptz,
    completed_at                timestamptz,
    recorded_at                 timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT execution_attempt_request_fkey
        FOREIGN KEY (request_id) REFERENCES wind.execution_requests(id) ON DELETE RESTRICT,
    CONSTRAINT execution_attempt_parent_fkey
        FOREIGN KEY (parent_attempt_id, request_id)
        REFERENCES wind.execution_attempts(id, request_id) ON DELETE RESTRICT,
    CONSTRAINT execution_attempt_status_check
        CHECK (status IN ('SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID')),
    CONSTRAINT execution_attempt_request_number_unique
        UNIQUE (request_id, attempt_number),
    CONSTRAINT execution_attempt_request_key_unique
        UNIQUE (request_id, attempt_idempotency_key),
    CONSTRAINT execution_attempt_id_request_unique
        UNIQUE (id, request_id),
    CONSTRAINT execution_attempt_time_check
        CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_wind_execution_attempts_request
    ON wind.execution_attempts (request_id, attempt_number DESC);
CREATE INDEX IF NOT EXISTS idx_wind_execution_attempts_status
    ON wind.execution_attempts (status, recorded_at DESC);

CREATE TABLE IF NOT EXISTS wind.execution_receipts (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id                  uuid NOT NULL,
    attempt_id                  uuid NOT NULL,
    outcome_status              text NOT NULL,
    result_digest               text NOT NULL CHECK (result_digest ~ '^sha256:[0-9a-f]{64}$'),
    evidence_refs               jsonb NOT NULL DEFAULT '[]'::jsonb,
    lineage                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    authority_level             text NOT NULL DEFAULT 'advisory',
    issued_at                   timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT execution_receipt_request_fkey
        FOREIGN KEY (request_id) REFERENCES wind.execution_requests(id) ON DELETE RESTRICT,
    CONSTRAINT execution_receipt_attempt_request_fkey
        FOREIGN KEY (attempt_id, request_id)
        REFERENCES wind.execution_attempts(id, request_id) ON DELETE RESTRICT,
    CONSTRAINT execution_receipt_status_check
        CHECK (outcome_status IN ('SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID')),
    CONSTRAINT execution_receipt_authority_check
        CHECK (authority_level = 'advisory'),
    CONSTRAINT execution_receipt_attempt_unique
        UNIQUE (attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_wind_execution_receipts_request
    ON wind.execution_receipts (request_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_wind_execution_receipts_status
    ON wind.execution_receipts (outcome_status, issued_at DESC);

-- Complete the contract when upgrading a database that received an earlier
-- draft of this migration. Existing production rows must already be truthful
-- before the stronger NOT NULL constraint is installed.
ALTER TABLE wind.execution_attempts
    ALTER COLUMN result_digest SET NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'wind.execution_requests'::regclass
          AND conname = 'execution_request_node_pair_check'
    ) THEN
        ALTER TABLE wind.execution_requests
            ADD CONSTRAINT execution_request_node_pair_check
            CHECK ((node_id IS NULL) = (workflow_version_id IS NULL));
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION wind.forbid_execution_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'wind.% is append-only: % blocked for evidence row %',
        TG_TABLE_NAME, TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_wind_execution_requests_immutable ON wind.execution_requests;
CREATE TRIGGER trg_wind_execution_requests_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_requests
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();

DROP TRIGGER IF EXISTS trg_wind_execution_attempts_immutable ON wind.execution_attempts;
CREATE TRIGGER trg_wind_execution_attempts_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_attempts
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();

DROP TRIGGER IF EXISTS trg_wind_execution_receipts_immutable ON wind.execution_receipts;
CREATE TRIGGER trg_wind_execution_receipts_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_receipts
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();

COMMENT ON TABLE wind.execution_requests IS
    'Immutable advisory execution request projection; artifact and evaluator identities are revision-pinned and no admission authority is implied';
COMMENT ON TABLE wind.execution_attempts IS
    'Immutable truthful outcome of one provider attempt; retry/correction creates another attempt row';
COMMENT ON TABLE wind.execution_receipts IS
    'Immutable advisory receipt joining one request and one attempt; it records evidence, never admission or lifecycle mutation';

COMMIT;
