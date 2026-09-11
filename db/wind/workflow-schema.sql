-- ============================================================================
-- Wind Schema: Workflow Data Model
-- ============================================================================
-- Canonical home for workflow orchestration: offices, titles, tasks,
-- workflow graphs, runtime instances, tickets, and receipts.
--
-- References to existing schemas:
--   nebula.roles  — governance-enriched role definitions (will become a view
--                   over tackle.roles + nebula.role_capabilities)
--   tackle.*      — untouched; config bundles, providers, models, harnesses
-- ============================================================================

-- 0. SCHEMA
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS wind;

-- ============================================================================
-- 1. VIEWS INTO EXISTING SCHEMAS
-- ============================================================================
-- wind.v_roles: convenience view over nebula.roles.
-- FK constraints reference nebula.roles(id) directly (PostgreSQL cannot FK to
-- views). This view exists for query ergonomics only.

CREATE OR REPLACE VIEW wind.v_roles AS
SELECT id, name, display_name, description, owns_domains,
       can_greenlight, can_create_questions, can_create_agendas,
       can_resolve_questions, can_verify_work_requests,
       max_open_questions, requires_approval_from,
       cron_enabled, cron_expression, cron_description,
       escalates_to, escalation_triggers,
       level_filter_primary, level_filter_allowed, visibility_scope,
       created_at, updated_at
FROM nebula.roles;

-- ============================================================================
-- 2. ORGANIZATIONAL CONTAINER LAYER
-- ============================================================================

CREATE TABLE wind.offices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Titles: a named position within an office, bound to a governance role.
-- FK references nebula.roles directly (view is for queries only).
CREATE TABLE wind.titles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    office_id       UUID NOT NULL REFERENCES wind.offices(id),
    role_id         UUID NOT NULL REFERENCES nebula.roles(id),
    display_name    VARCHAR(100) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_office_title UNIQUE (office_id, display_name)
);

-- ============================================================================
-- 3. TASK & OUTCOME DEFINITIONS (DATA-FLOW CONTRACTS)
-- ============================================================================

CREATE TABLE wind.tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    office_id       UUID NOT NULL REFERENCES wind.offices(id),
    title_id        UUID NOT NULL REFERENCES wind.titles(id),
    name            VARCHAR(100) NOT NULL,
    description     TEXT,
    input_spec      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- NULL means a workflow-only/check task; non-NULL links to an agent task.
    tackle_task_id  UUID REFERENCES tackle.tasks(id) ON DELETE SET NULL,
    CONSTRAINT uq_office_task_name UNIQUE (office_id, name)
);

CREATE TABLE wind.task_outcomes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES wind.tasks(id) ON DELETE CASCADE,
    code            VARCHAR(50) NOT NULL,
    description     TEXT,
    output_spec     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_task_outcome_code UNIQUE (task_id, code),
    CONSTRAINT uq_task_outcome_composite UNIQUE (id, task_id)
);

-- ============================================================================
-- 4. IMMUTABLE WORKFLOW GRAPH & DETERMINISTIC GRAPH CONSTRAINTS
-- ============================================================================

CREATE TABLE wind.workflows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE wind.workflow_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES wind.workflows(id),
    version_number  INTEGER NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_workflow_version UNIQUE (workflow_id, version_number),
    CONSTRAINT uq_version_composite UNIQUE (id, workflow_id)
);

CREATE TABLE wind.workflow_nodes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_version_id UUID NOT NULL REFERENCES wind.workflow_versions(id) ON DELETE CASCADE,
    task_id             UUID NOT NULL REFERENCES wind.tasks(id),
    name                VARCHAR(100) NOT NULL,
    is_entrypoint       BOOLEAN NOT NULL DEFAULT false,
    is_terminal         BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_version_node_name UNIQUE (workflow_version_id, name),
    CONSTRAINT uq_node_task_composite UNIQUE (id, task_id),
    CONSTRAINT uq_node_version_composite UNIQUE (id, workflow_version_id)
);

CREATE TABLE wind.workflow_edges (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_version_id UUID NOT NULL,
    from_node_id        UUID NOT NULL,
    from_task_id        UUID NOT NULL,
    outcome_id          UUID NOT NULL,
    to_node_id          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    -- Strict graph determinism: one edge per (version, node, outcome)
    CONSTRAINT uq_deterministic_edge UNIQUE (workflow_version_id, from_node_id, outcome_id),

    -- Both nodes belong to the same workflow version
    CONSTRAINT fk_edge_version_from FOREIGN KEY (from_node_id, workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id) ON DELETE CASCADE,
    CONSTRAINT fk_edge_version_to FOREIGN KEY (to_node_id, workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id) ON DELETE CASCADE,

    -- Outcome belongs to the source node's task
    CONSTRAINT fk_edge_from_node_task FOREIGN KEY (from_node_id, from_task_id)
        REFERENCES wind.workflow_nodes(id, task_id),
    CONSTRAINT fk_edge_outcome_task FOREIGN KEY (outcome_id, from_task_id)
        REFERENCES wind.task_outcomes(id, task_id)
);

-- ============================================================================
-- 5. RUNTIME EXECUTION, TICKETING, & RECEIPT BRACKETING
-- ============================================================================

CREATE TABLE wind.workflow_instances (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_version_id UUID NOT NULL REFERENCES wind.workflow_versions(id),
    status              VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_instance_status CHECK (status IN ('ACTIVE', 'COMPLETED', 'FAILED', 'PAUSED')),
    CONSTRAINT uq_instance_version_composite UNIQUE (id, workflow_version_id)
);

CREATE TABLE wind.tickets (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_instance_id    UUID NOT NULL,
    workflow_version_id     UUID NOT NULL,
    node_id                 UUID NOT NULL,
    node_task_id            UUID NOT NULL,
    assigned_title_id       UUID NOT NULL REFERENCES wind.titles(id),
    status                  VARCHAR(30) NOT NULL DEFAULT 'PENDING',

    -- Polymorphic input artifact binding
    input_artifact_type     VARCHAR(100) NOT NULL,
    input_artifact_id       UUID NOT NULL,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_ticket_status CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),

    -- Ticket instance & version must match the instance's declared version
    CONSTRAINT fk_ticket_instance_version FOREIGN KEY (workflow_instance_id, workflow_version_id)
        REFERENCES wind.workflow_instances(id, workflow_version_id) ON DELETE CASCADE,

    -- Ticket node must belong to that exact same workflow version
    CONSTRAINT fk_ticket_node_version FOREIGN KEY (node_id, workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id),

    -- Ticket node must bind to the task declared at that node
    CONSTRAINT fk_ticket_node_task FOREIGN KEY (node_id, node_task_id)
        REFERENCES wind.workflow_nodes(id, task_id),

    CONSTRAINT uq_ticket_task_composite UNIQUE (id, node_task_id)
);

CREATE TABLE wind.receipts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id               UUID NOT NULL UNIQUE,
    ticket_task_id          UUID NOT NULL,
    outcome_id              UUID NOT NULL,
    work_request_id         UUID NOT NULL,

    -- Polymorphic output artifact binding
    output_artifact_type    VARCHAR(100),
    output_artifact_id      UUID,

    completed_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Receipt must point to a valid ticket and capture its assigned task
    CONSTRAINT fk_receipt_ticket_task FOREIGN KEY (ticket_id, ticket_task_id)
        REFERENCES wind.tickets(id, node_task_id) ON DELETE CASCADE,

    -- Outcome recorded must belong to the task assigned to that ticket's node
    CONSTRAINT fk_receipt_outcome_task FOREIGN KEY (outcome_id, ticket_task_id)
        REFERENCES wind.task_outcomes(id, task_id)
);

-- ============================================================================
-- 6. EXECUTION REQUEST / ATTEMPT / RECEIPT EVIDENCE SEAM
-- ============================================================================
-- Phase B evidence projection. These rows are immutable and advisory-only;
-- they do not invoke providers, activate workflows, mutate tickets, or admit
-- lifecycle transitions. Corrections are represented by new rows.

CREATE TABLE wind.execution_requests (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_version_id         UUID,
    node_id                     UUID,
    artifact_type               TEXT NOT NULL,
    artifact_ref                TEXT NOT NULL,
    artifact_revision           TEXT NOT NULL,
    artifact_fingerprint        TEXT NOT NULL,
    read_set_digest             TEXT NOT NULL,
    evaluator_contract_digest   TEXT NOT NULL,
    causation_id                UUID,
    correlation_id              UUID NOT NULL,
    idempotency_key             TEXT NOT NULL UNIQUE,
    provider_contract           JSONB NOT NULL DEFAULT '{}'::jsonb,
    invocation_contract         JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_policy              JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_digest              TEXT NOT NULL,
    authority_level             TEXT NOT NULL DEFAULT 'advisory',
    requested_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT execution_request_authority_check CHECK (authority_level = 'advisory'),
    CONSTRAINT execution_request_version_fkey FOREIGN KEY (workflow_version_id) REFERENCES wind.workflow_versions(id),
    CONSTRAINT execution_request_node_fkey FOREIGN KEY (node_id) REFERENCES wind.workflow_nodes(id),
    CONSTRAINT execution_request_node_version_fkey FOREIGN KEY (node_id, workflow_version_id)
        REFERENCES wind.workflow_nodes(id, workflow_version_id)
);

CREATE TABLE wind.execution_attempts (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id                  UUID NOT NULL,
    parent_attempt_id           UUID,
    attempt_number              INTEGER NOT NULL CHECK (attempt_number > 0),
    attempt_idempotency_key     TEXT NOT NULL,
    executor_id                 TEXT NOT NULL,
    provider_invocation_ref    TEXT,
    status                      TEXT NOT NULL CHECK (status IN ('IN_FLIGHT', 'ABORTED', 'SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID')),
    result                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    error                       TEXT,
    result_digest               TEXT,
    started_at                  TIMESTAMPTZ,
    completed_at                TIMESTAMPTZ,
    recorded_at                 TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT execution_attempt_request_fkey FOREIGN KEY (request_id) REFERENCES wind.execution_requests(id) ON DELETE RESTRICT,
    CONSTRAINT execution_attempt_parent_fkey FOREIGN KEY (parent_attempt_id, request_id)
        REFERENCES wind.execution_attempts(id, request_id) ON DELETE RESTRICT,
    CONSTRAINT execution_attempt_request_number_unique UNIQUE (request_id, attempt_number),
    CONSTRAINT execution_attempt_request_key_unique UNIQUE (request_id, attempt_idempotency_key),
    CONSTRAINT execution_attempt_id_request_unique UNIQUE (id, request_id),
    CONSTRAINT execution_attempt_time_check CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE wind.execution_receipts (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id                  UUID NOT NULL,
    attempt_id                  UUID NOT NULL,
    outcome_status              TEXT NOT NULL CHECK (outcome_status IN ('SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID')),
    result_digest               TEXT NOT NULL,
    evidence_refs               JSONB NOT NULL DEFAULT '[]'::jsonb,
    lineage                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    authority_level             TEXT NOT NULL DEFAULT 'advisory',
    issued_at                   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT execution_receipt_request_fkey FOREIGN KEY (request_id) REFERENCES wind.execution_requests(id) ON DELETE RESTRICT,
    CONSTRAINT execution_receipt_attempt_request_fkey FOREIGN KEY (attempt_id, request_id)
        REFERENCES wind.execution_attempts(id, request_id) ON DELETE RESTRICT,
    CONSTRAINT execution_receipt_authority_check CHECK (authority_level = 'advisory'),
    CONSTRAINT execution_receipt_attempt_unique UNIQUE (attempt_id)
);

CREATE INDEX idx_wind_execution_requests_correlation ON wind.execution_requests (correlation_id, requested_at DESC);
CREATE INDEX idx_wind_execution_requests_artifact ON wind.execution_requests (artifact_type, artifact_ref, artifact_revision);
CREATE INDEX idx_wind_execution_attempts_request ON wind.execution_attempts (request_id, attempt_number DESC);
CREATE INDEX idx_wind_execution_attempts_status ON wind.execution_attempts (status, recorded_at DESC);
CREATE INDEX idx_wind_execution_receipts_request ON wind.execution_receipts (request_id, issued_at DESC);
CREATE INDEX idx_wind_execution_receipts_status ON wind.execution_receipts (outcome_status, issued_at DESC);

CREATE TABLE wind.provider_contracts (
    adapter_id              TEXT PRIMARY KEY,
    adapter_version         TEXT NOT NULL,
    provider_id             TEXT NOT NULL,
    provider_version        TEXT NOT NULL,
    invocation_mode         TEXT NOT NULL CHECK (invocation_mode IN ('CLI', 'HTTP', 'SDK', 'MCP')),
    input_schema_digest     TEXT NOT NULL,
    output_schema_digest    TEXT NOT NULL,
    credential_env_ref      TEXT,
    endpoint_env_ref        TEXT,
    schema_verification     TEXT NOT NULL DEFAULT 'verified' CHECK (schema_verification = 'verified'),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT provider_contract_credential_ref_check
        CHECK (credential_env_ref IS NULL OR credential_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_contract_endpoint_ref_check
        CHECK (endpoint_env_ref IS NULL OR endpoint_env_ref ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CONSTRAINT provider_contract_http_endpoint_check
        CHECK (invocation_mode <> 'HTTP' OR endpoint_env_ref IS NOT NULL)
);

-- Provider contract rows are an explicit dispatch allow-list. References are
-- environment-variable names only; secret material never belongs in wind.*.
CREATE INDEX idx_wind_provider_contracts_active
    ON wind.provider_contracts (is_active, adapter_id);

CREATE OR REPLACE FUNCTION wind.forbid_execution_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'wind.% is append-only: % blocked for evidence row %', TG_TABLE_NAME, TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_wind_execution_requests_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_requests
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();
CREATE TRIGGER trg_wind_execution_attempts_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_attempts
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();
CREATE TRIGGER trg_wind_execution_receipts_immutable
    BEFORE UPDATE OR DELETE ON wind.execution_receipts
    FOR EACH ROW EXECUTE FUNCTION wind.forbid_execution_evidence_mutation();

-- ============================================================================
-- 7. GENERIC GRAPH INTEGRITY VALIDATOR
-- ============================================================================

CREATE OR REPLACE VIEW wind.v_workflow_graph_validation AS
WITH node_outcomes AS (
    SELECT
        wn.workflow_version_id,
        wn.id                AS node_id,
        wn.name              AS node_name,
        t.id                 AS task_id,
        t.name               AS task_name,
        o.id                 AS outcome_id,
        o.code               AS outcome_code,
        o.output_spec
    FROM wind.workflow_nodes wn
    JOIN wind.tasks t ON wn.task_id = t.id
    JOIN wind.task_outcomes o ON o.task_id = t.id
    WHERE wn.is_terminal = FALSE
),
edge_analysis AS (
    SELECT
        no.workflow_version_id,
        no.node_id,
        no.node_name,
        no.outcome_code,
        we.id                AS edge_id,
        we.to_node_id,
        downstream_task.input_spec  AS downstream_input_spec,
        no.output_spec             AS upstream_output_spec
    FROM node_outcomes no
    LEFT JOIN wind.workflow_edges we
        ON  no.workflow_version_id = we.workflow_version_id
        AND no.node_id             = we.from_node_id
        AND no.outcome_id          = we.outcome_id
    LEFT JOIN wind.workflow_nodes downstream_node ON we.to_node_id = downstream_node.id
    LEFT JOIN wind.tasks downstream_task          ON downstream_node.task_id = downstream_task.id
)
-- 1. UNHANDLED OUTCOMES (exhaustiveness check)
SELECT
    workflow_version_id,
    'UNHANDLED_OUTCOME'    AS issue_type,
    node_id,
    FORMAT('Node "%s" leaves outcome "%s" unhandled. No edge defined.', node_name, outcome_code) AS details
FROM edge_analysis
WHERE edge_id IS NULL

UNION ALL

-- 2. UNREACHABLE NODES (orphaned graph vertices)
SELECT
    wn.workflow_version_id,
    'UNREACHABLE_NODE'     AS issue_type,
    wn.id                  AS node_id,
    FORMAT('Node "%s" is neither an entrypoint nor reached by any edge.', wn.name) AS details
FROM wind.workflow_nodes wn
WHERE wn.is_entrypoint = FALSE
  AND wn.id NOT IN (SELECT to_node_id FROM wind.workflow_edges)

UNION ALL

-- 3. DATA CONTRACT MISMATCHES (data-flow check)
SELECT
    workflow_version_id,
    'DATA_CONTRACT_MISMATCH' AS issue_type,
    node_id,
    FORMAT('Node "%s" outcome "%s" output_spec does not satisfy downstream task input_spec.', node_name, outcome_code) AS details
FROM edge_analysis
WHERE edge_id IS NOT NULL
  AND NOT (upstream_output_spec @> downstream_input_spec);
