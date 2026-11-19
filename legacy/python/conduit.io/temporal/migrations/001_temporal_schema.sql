-- 001_temporal_schema.sql
-- Creates the application tables needed by the Temporal Conduit worker
-- in the 'temporal' schema within the nexus database.
--
-- Strategy:
--   - MCP-owned read-only tables → cross-schema VIEWs pointing to conduit.*
--   - Tables the Temporal worker writes to → real TABLEs in temporal.*
--   - Manager-owned tables (work_requests, pipeline_cursor) → real TABLEs
--
-- Applied: psql -h strontium -U pguser -d nexus -f 001_temporal_schema.sql

-- ── Read-only cross-schema views (MCP owns the data in conduit.*) ──

CREATE OR REPLACE VIEW temporal.plans AS SELECT * FROM conduit.plans;
CREATE OR REPLACE VIEW temporal.ai_providers AS SELECT * FROM vector.providers;
CREATE OR REPLACE VIEW temporal.ai_harnesses AS SELECT * FROM vector.harnesses;
CREATE OR REPLACE VIEW temporal.ai_models AS SELECT * FROM vector.models;
CREATE OR REPLACE VIEW temporal.ai_role_config AS SELECT * FROM vector.role_config;
CREATE OR REPLACE VIEW temporal.ai_role_models AS SELECT * FROM vector.role_models;
CREATE OR REPLACE VIEW temporal.plan_status AS SELECT * FROM conduit.plan_status;

-- ── Writeable tables (Temporal worker owns these) ───────────────────

-- sessions: agent execution sessions
CREATE TABLE IF NOT EXISTS temporal.sessions (
    id              TEXT PRIMARY KEY,
    agent_role      TEXT NOT NULL,
    start_iso       TEXT NOT NULL,
    end_iso         TEXT,
    exit_code       INTEGER,
    retries_used    INTEGER DEFAULT 0,
    plans_processed TEXT NOT NULL DEFAULT '[]',
    plan_count      INTEGER DEFAULT 0,
    pid             INTEGER,
    is_running      INTEGER DEFAULT 1,
    last_activity   TEXT,
    model           TEXT,
    fallback_used   INTEGER DEFAULT 0,
    cost_usd        REAL,
    total_work_seconds REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

-- work_requests: DCO persistence (manager-owned)
CREATE TABLE IF NOT EXISTS temporal.work_requests (
    id          TEXT PRIMARY KEY,
    plan_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    dco_json    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- pipeline_cursor: per-role cursor tracking (manager-owned)
CREATE TABLE IF NOT EXISTS temporal.pipeline_cursor (
    role                  TEXT PRIMARY KEY,
    last_processed_plan_id TEXT,
    last_work_request_id  TEXT,
    updated_at            TEXT NOT NULL
);

-- tickets: work authorization tokens
CREATE TABLE IF NOT EXISTS temporal.tickets (
    id                  TEXT PRIMARY KEY,
    plan_id             TEXT NOT NULL,
    role                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN (
                            'open','claimed','completed','failed',
                            'abandoned','superseded','cancelled',
                            'stale','expired'
                        )),
    session_id          TEXT,
    created_by_receipt  TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    claimed_at          TEXT,
    closed_at           TEXT,
    token_budget        INTEGER,
    tokens_used         INTEGER,
    objective           TEXT,
    completion_criteria TEXT,
    owner               TEXT NOT NULL DEFAULT '',
    parent_ticket_id    TEXT,
    spawn_reason        TEXT,
    last_activity       TEXT,
    expires_at          TEXT,
    confidence          REAL,
    closure_reason      TEXT,
    replacement_of      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_temporal_tickets_open
    ON temporal.tickets(plan_id, role) WHERE status = 'open';

-- receipts: audit trail entries
CREATE TABLE IF NOT EXISTS temporal.receipts (
    id            TEXT PRIMARY KEY,
    plan_id       TEXT NOT NULL,
    type          TEXT NOT NULL CHECK(type IN (
                    'PLAN_CREATE','IMPLEMENTATION','REVIEW_PASS','REVIEW_REJECT','BLOCK',
                    'PROPOSED','PLANNING','REQUEUED',
                    'REVIEW','CRITIQUE','CRITIQUE_PASS','CRITIQUE_REJECT','PLAN_BLOCK','API_LIMIT'
                  )),
    agent_role    TEXT NOT NULL,
    session_id    TEXT,
    artifact_path TEXT,
    summary       TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    ticket_id     TEXT,
    tokens_used   INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_temporal_receipts_unique
    ON temporal.receipts(plan_id, type, COALESCE(session_id, ''));

-- circuit_breaker: safety cut-off
CREATE TABLE IF NOT EXISTS temporal.circuit_breaker (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
    tripped         INTEGER DEFAULT 0,
    tripped_at      TEXT,
    retry_after     INTEGER DEFAULT 1800,
    error           TEXT,
    detail          TEXT,
    source          TEXT,
    fallback_model  TEXT,
    paused          INTEGER DEFAULT 0,
    updated_at      TEXT,
    max_retries_per_model INTEGER DEFAULT 3,
    retry_delay_seconds   INTEGER DEFAULT 120,
    max_fallbacks         INTEGER DEFAULT 3,
    push_back_to_pending  INTEGER DEFAULT 1
);
INSERT INTO temporal.circuit_breaker (id, tripped) VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;
