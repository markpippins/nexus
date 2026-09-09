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
-- 6. GENERIC GRAPH INTEGRITY VALIDATOR
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
