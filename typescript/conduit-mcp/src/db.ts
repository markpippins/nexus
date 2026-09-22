// ════════════════════════════════════════════════════════════════════════════
//  AUDIT NOTE — "No SQL in MCP Servers" (Assembly thread 02c7bb2b-...)
// ════════════════════════════════════════════════════════════════════════════
//
// Status (R1 record 6f5af6f8-da7c-43cd-ac3c-cb2a208cc1d8):
//   This file is INTENTIONALLY NOT split into a sibling `conduit-srv` this
//   session. See the Assembly `to-do` thread "No SQL in MCP Servers" for the
//   rationale. A real split is a multi-epoch architectural refactor and
//   requires an Architect decision before any implementation.
//
// Why this file is unusual:
//
//   1. SCOPE. ~2,500+ LoC including:
//      - initDb() / createSchema() / runMigrations()  — schema bootstrap
//      - translateSQL() SQLite→PostgreSQL dialect translator
//      - withTransaction() + helpers used by every receipt write
//      - 17 inline migrations (versions 1..21) covering the
//        conduit / vision / peb / tackle schema family
//      - Vision receipt-integrity invariants (migrations v20, v21)
//      - Runtime Kernel event-store foundation (migration v18)
//
//   2. CROSS-SYSTEM OWNERSHIP. This file bootstraps the global
//        conduit / vision / peb / tackle schemas used by nebula-srv,
//        tackle-mcp, execution-srv, peb-srv and several others on
//        startup. Moving initDb() into a new conduit-srv would reorder
//        Nexus initialization — nebula-srv and execution-srv currently
//        assume those schemas exist when they start, and would race
//        against the new conduit-srv.
//
//   3. DIALECT + RECEIPT INTEGRITY. The translateSQL() function and
//      the receipts / work_request_events invariants are deeply woven
//      into pipeline-manager semantics. A naive REST split risks
//      silently corrupting receipt sequence numbers, weakening
//      vision.is_terminal_receipt_type() integrity, or losing the
//      SQLite-dialect parity that executor_cloud.py relies on.
//
// Decision for this engineering epoch: AUDIT ONLY.
//
//   - knowledge-mcp → full split into knowledge-srv (port 3109) ✅
//   - terrain-mcp   → repointed to Spring Boot terrain (:8084/api/v1/) ✅
//   - conduit-mcp   → THIS FILE — header audit block only. NO SQL
//                       moved into a sibling server this session.
//                       Blocked on schema-bootstrap-ownership decision
//                       (Architect).
//
// When the Architect is ready to lift this, the work splits naturally
// into three sub-epochs:
//   (a) Move DDL/migration ownership into a dedicated bootstrap step
//       (separate from MCP server start).
//   (b) Extract all receipt/session/cost_logs reads & writes into
//       conduit-srv's REST surface.
//   (c) Replace inline `await q(...)` calls in `tools.ts` and
//       `runtime-kernel.ts` with `fetch` against (b). Verify
//       vision.is_terminal_receipt_type() and receipt sequence
//       invariants still pass after the cutover.
//
// Until then, this file will continue to own its SQL — there is no
// hidden risk because it is named-and-claimed in the audit thread.
// ════════════════════════════════════════════════════════════════════════════

import crypto from "node:crypto";
import { Pool, PoolClient, types } from "pg";
import { evaluateReleaseGate, ReleaseDecision } from "./release-gate";
import { RippleAssignment } from "./ripple-classifier";
import { CompareTarget } from "./compile-compare";

// ── Keep timestamps as ISO strings ─────────────────────────────────
// pg parses TIMESTAMPTZ into Date objects by default. Override to keep
// strings so all existing code (which writes/expects ISO 8601 strings)
// continues to work when we migrate TEXT columns to TIMESTAMPTZ.
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

// ── Connection ──────────────────────────────────────────────────────

const PG_SCHEMA = process.env.CONDUIT_PG_SCHEMA || "conduit";
// SECURITY: validate env-var-derived schema name before DDL interpolation.
// DDL (SET search_path, CREATE SCHEMA) doesn't support parameterized
// identifiers, so we validate against the safe-identifier regex at startup.
if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(PG_SCHEMA)) {
  throw new Error(
    `Invalid CONDUIT_PG_SCHEMA="${PG_SCHEMA}": must match /^[a-zA-Z_][a-zA-Z0-9_]*$/`
  );
}
const TACKLE_SCHEMA = "tackle";
const VISION_SCHEMA = "vision";
const PEB_SCHEMA = "peb";

let pool: Pool;

export async function initDb(_conduitDataDir?: string): Promise<Pool> {
  const dsn =
    process.env.CONDUIT_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus";

  pool = new Pool({
    connectionString: dsn,
    // Default search_path for all connections from this pool
    options: `-c search_path=${PG_SCHEMA},${VISION_SCHEMA},${PEB_SCHEMA},${TACKLE_SCHEMA}`,
    max: 10,
    idleTimeoutMillis: 30000,
  });

  // Acquire a dedicated client so all DDL runs with the same search_path
  const client = await pool.connect();
  try {
    await client.query(`SET search_path TO ${PG_SCHEMA},${VISION_SCHEMA},${PEB_SCHEMA},${TACKLE_SCHEMA}`);
    const exec = (sql: string, params?: any[]) => client.query(sql, params);
    await createSchema(exec);
    await runMigrations(exec);
  } finally {
    client.release();
  }
  return pool;
}

export function getDb(): Pool {
  if (!pool) throw new Error("DB not initialized. Call initDb() first.");
  return pool;
}

// ── SQL dialect translation ─────────────────────────────────────────
// Converts SQLite-specific SQL to PG-compatible SQL before execution.

function translateSQL(sql: string): string {
  let s = sql;

  // INSERT OR IGNORE → ON CONFLICT DO NOTHING (handles tables with PK 'id')
  s = s.replace(
    /\bINSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)/gi,
    (_m, table) => `INSERT INTO ${table}`
  );

  // INSERT OR REPLACE → ON CONFLICT(id) DO UPDATE
  s = s.replace(
    /\bINSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)/gi,
    (_m, table) => `INSERT INTO ${table}`
  );

  // datetime('now') → NOW()
  s = s.replace(/datetime\s*\(\s*'now'\s*\)/gi, "NOW()");

  // sqlite_master → information_schema
  s = s.replace(
    /SELECT\s+sql\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*@(\w+)/gi,
    (_m, name) =>
      `SELECT pg_get_tabledef(('${PG_SCHEMA}.' || $${name})::regclass) AS sql`
  );
  s = s.replace(
    /FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*@(\w+)/gi,
    (_m, name) =>
      `FROM information_schema.tables WHERE table_schema='${PG_SCHEMA}' AND table_name=$${name}`
  );

  // PRAGMA table_info → information_schema.columns
  s = s.replace(
    /FROM\s+pragma_table_info\s*\(\s*'(\w+)'\s*\)/gi,
    (_m, table) =>
      `FROM information_schema.columns WHERE table_schema='${PG_SCHEMA}' AND table_name='${table}'`
  );
  s = s.replace(
    /PRAGMA\s+foreign_key_check/gi,
    `SELECT 1 WHERE FALSE`
  );

  // json_array_length → jsonb_array_length (for plans_processed etc.)
  s = s.replace(
    /\bjson_array_length\s*\(\s*@(\w+)\s*\)/gi,
    (_m, param) => `jsonb_array_length($${param}::jsonb)`
  );

  // Add ON CONFLICT clause to INSERT statements that don't have one yet
  // (INSERT OR IGNORE was stripped above; now add PG ON CONFLICT)
  const insertMatch = s.match(/INSERT\s+INTO\s+(\w+)/i);
  if (insertMatch && /INSERT\s+OR\s+/i.test(sql) && !/ON\s+CONFLICT/i.test(s)) {
    const table = insertMatch[1].toLowerCase();
    // Tables with multi-column unique constraints: use no target
    if (table === "receipts" || table === "tickets") {
      s = s.trimEnd() + " ON CONFLICT DO NOTHING";
    } else {
      // For ON CONFLICT on 'role' column (pipeline_cursor)
      if (table === "pipeline_cursor") {
        // Handled inline with explicit ON CONFLICT (role) in the SQL
      } else if (table === "config_bundle") {
        // Handled inline with explicit ON CONFLICT (role, model_id) in the SQL
      } else {
        s = s.trimEnd() + " ON CONFLICT (id) DO NOTHING";
      }
    }
  }

  // INSERT OR REPLACE → INSERT ... ON CONFLICT (id) DO UPDATE SET ... = EXCLUDED... (per-table)
  if (/INSERT\s+OR\s+REPLACE/i.test(sql) && !/ON\s+CONFLICT/i.test(s)) {
    // For sessions table: full upsert — replace all columns on conflict
    if (insertMatch && insertMatch[1].toLowerCase() === "sessions") {
      s = s.trimEnd() + ` ON CONFLICT (id) DO UPDATE SET
        agent_role = EXCLUDED.agent_role,
        start_iso = EXCLUDED.start_iso,
        pid = EXCLUDED.pid,
        plans_processed = EXCLUDED.plans_processed,
        plan_count = EXCLUDED.plan_count,
        model = EXCLUDED.model,
        fallback_used = EXCLUDED.fallback_used,
        is_running = EXCLUDED.is_running,
        last_activity = EXCLUDED.last_activity,
        workflow_id = EXCLUDED.workflow_id,
        run_id = EXCLUDED.run_id,
        workflow_start_time = EXCLUDED.workflow_start_time,
        workflow_close_time = EXCLUDED.workflow_close_time,
        workflow_run_time_ms = EXCLUDED.workflow_run_time_ms,
        workflow_result = EXCLUDED.workflow_result,
        created_at = EXCLUDED.created_at,
        tags = EXCLUDED.tags`;
    } else {
      // Generic fallback: DO NOTHING is safer than guessing columns
      s = s.trimEnd() + " ON CONFLICT (id) DO NOTHING";
    }
  }

  return s;
}

// ── Parameter conversion ────────────────────────────────────────────
// Converts @param → $1, $2 positional parameters.

function convertParams(
  sql: string,
  params: Record<string, any> = {}
): { text: string; values: any[] } {
  let text = sql;
  const values: any[] = [];
  let idx = 1;
  const seen = new Map<string, number>();

  text = text.replace(/@([a-zA-Z0-9_]+)/g, (_match, name) => {
    if (!seen.has(name)) {
      seen.set(name, idx);
      values.push(params[name] !== undefined ? params[name] : null);
      idx++;
    }
    return `$${seen.get(name)}`;
  });

  return { text, values };
}

// ── Query helpers ───────────────────────────────────────────────────

interface QueryResult {
  rows: any[];
  changes: number;
}

async function _rawQuery(
  client: PoolClient | Pool,
  sql: string,
  params: Record<string, any> = {}
): Promise<QueryResult> {
  const translated = translateSQL(sql);
  const { text, values } = convertParams(translated, params);
  const result = await client.query(text, values);
  return { rows: result.rows, changes: result.rowCount ?? 0 };
}

// Public helpers — use the pool singleton by default
async function q(sql: string, params: Record<string, any> = {}): Promise<QueryResult> {
  return _rawQuery(pool, sql, params);
}
export async function qOne(sql: string, params: Record<string, any> = {}): Promise<any | undefined> {
  const r = await q(sql, params); return r.rows[0];
}
export async function qRun(sql: string, params: Record<string, any> = {}): Promise<number> {
  const r = await q(sql, params); return r.changes;
}
export async function qAll(sql: string, params: Record<string, any> = {}): Promise<any[]> {
  const r = await q(sql, params); return r.rows;
}

// ── Kernel transition event recording ───────────────────────────────
// ADR-016: Every state change must INSERT INTO kernel.transition_event
// so the trg_authorize_transition trigger enforces policy rules.

const KERNEL_SCHEMA = "kernel";

/**
 * Record a state transition in kernel.transition_event.
 * The trg_authorize_transition trigger fires BEFORE INSERT and enforces:
 * - actor required
 * - aggregate_type required
 * - aggregate_id required
 * - no future timestamps
 * - all enabled policy_rule predicates for this event_type
 *
 * If the trigger rejects, this INSERT raises an exception which rolls
 * back the caller's transaction — the state change is blocked.
 */
export async function recordTransition(opts: {
  aggregateType: string;    // e.g. "work_request", "ticket", "implementation_plan"
  aggregateId: string;      // e.g. work_request UUID, ticket ID, plan number
  eventType: string;        // kernel.event_type enum value
  actor: string;            // who initiated the transition
  authority?: string;       // role authority (required by authority.required policy)
  payload?: Record<string, any>;  // from_status, to_status, reason, etc.
  receipt?: string;         // receipt hash if applicable
  causationId?: string;     // UUID of the event that caused this transition
  correlationId?: string;   // UUID for grouping related transitions
  client?: PoolClient;      // ADR-016: pass when inside withTransaction() for atomicity
}): Promise<void> {
  const eventId = crypto.randomUUID();
  const {
    aggregateType, aggregateId, eventType, actor,
    authority, payload = {}, receipt, causationId, correlationId,
    client,
  } = opts;

  // ADR-016: Use the transactional client when provided, pool otherwise.
  // Inside withTransaction(), the INSERT participates in the same TX as the
  // caller's UPDATE — if the trigger rejects, both are rolled back atomically.
  const queryFn = client
    ? (sql: string, params: Record<string, any>) => _rawQuery(client, sql, params)
    : q;

  await queryFn(
    `INSERT INTO ${KERNEL_SCHEMA}.transition_event
       (event_id, event_type, aggregate_type, aggregate_id, actor,
        authority, payload, receipt, causation_id, correlation_id)
     VALUES (@eventId::uuid, @eventType, @aggregateType, @aggregateId,
             @actor, @authority, @payload::jsonb, @receipt,
             @causationId::uuid, @correlationId::uuid)`,
    {
      eventId, eventType, aggregateType, aggregateId, actor,
      authority: authority || actor,
      payload: JSON.stringify(payload),
      receipt: receipt || null,
      causationId: causationId || null,
      correlationId: correlationId || null,
    },
  );
}

/**
 * Get the current status of a work_request before a transition.
 * Used to capture from_status for the kernel transition event.
 */
export async function getWorkRequestStatus(wrId: string): Promise<string | undefined> {
  const uuid = await resolveWrUuid(wrId);
  const r = await qOne(
    `SELECT status FROM ${VISION_SCHEMA}.work_requests WHERE work_request_uuid = @uuid`,
    { uuid },
  );
  return r?.status;
}

/**
 * Get the current status of a ticket before a transition.
 */
export async function getTicketStatus(ticketId: string): Promise<string | undefined> {
  const r = await qOne(
    `SELECT status FROM ${VISION_SCHEMA}.tickets WHERE id = @ticketId`,
    { ticketId },
  );
  return r?.status;
}

// Transaction-aware helpers — used inside withTransaction()
async function tQuery(client: PoolClient, sql: string, params: Record<string, any> = {}): Promise<QueryResult> {
  return _rawQuery(client, sql, params);
}
async function tRun(client: PoolClient, sql: string, params: Record<string, any> = {}): Promise<number> {
  const r = await _rawQuery(client, sql, params); return r.changes;
}
async function tAll(client: PoolClient, sql: string, params: Record<string, any> = {}): Promise<any[]> {
  const r = await _rawQuery(client, sql, params); return r.rows;
}
async function tOne(client: PoolClient, sql: string, params: Record<string, any> = {}): Promise<any | undefined> {
  const r = await _rawQuery(client, sql, params); return r.rows[0];
}

/** Transaction wrapper. */
async function withTransaction<T>(
  cb: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    // SET LOCAL (not plain SET): scoped to this transaction, so the session
    // reverts to the pool default search_path (conduit,vision,peb,tackle) when
    // the client is released. A plain SET here leaked a vision-less path into
    // pooled connections, crashing the watcher's unqualified `FROM tickets`.
    await client.query(`SET LOCAL search_path TO ${PG_SCHEMA},${TACKLE_SCHEMA}`);
    const result = await cb(client);
    await client.query("COMMIT");
    return result;
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

// ── Schema ───────────────────────────────────────────────────────────

/** Run all DDL through a single connection to ensure consistent search_path. */
async function createSchema(
  exec: (sql: string, params?: any[]) => Promise<any> = (sql, params) => pool.query(sql, params)
): Promise<void> {
  // Ensure all schemas exist before creating any tables
  await exec(`CREATE SCHEMA IF NOT EXISTS ${PG_SCHEMA}`);
  await exec(`CREATE SCHEMA IF NOT EXISTS ${VISION_SCHEMA}`);
  await exec(`CREATE SCHEMA IF NOT EXISTS ${PEB_SCHEMA}`);
  await exec(`CREATE SCHEMA IF NOT EXISTS ${TACKLE_SCHEMA}`);

  await exec(`
    -- conduit.plans removed 2026-08-07: empty legacy table, no dependents
    -- (runtime reads nebula.plans, the legacy-compat view over
    -- nebula.implementation_plans_history). Dropped from DDL so it stays gone.
    -- Schema version tracking for formal migrations
    CREATE TABLE IF NOT EXISTS schema_version (
      version     INTEGER PRIMARY KEY,
      description TEXT NOT NULL,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  // config_bundle.provider_id / harness_id are in the DDL below, not in
  // migrations. They replace role_config and role_models entirely.

  await exec(`
    -- tickets live in vision schema (FK references stay in conduit for plans)
    CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.tickets (
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
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      claimed_at TIMESTAMPTZ,
      closed_at TIMESTAMPTZ,
      token_budget        INTEGER,
      tokens_used         INTEGER,
      cost_budget_usd     REAL,
      cost_used_usd       REAL DEFAULT 0,
      objective           TEXT,
      completion_criteria TEXT,
      owner               TEXT NOT NULL DEFAULT '',
      parent_ticket_id    TEXT REFERENCES ${VISION_SCHEMA}.tickets(id),
      spawn_reason        TEXT,
      last_activity       TEXT,      expires_at TIMESTAMPTZ,
          deadline            TIMESTAMPTZ,
          confidence          REAL,
          closure_reason      TEXT,
      replacement_of      TEXT REFERENCES ${VISION_SCHEMA}.tickets(id)
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_tickets_open
      ON ${VISION_SCHEMA}.tickets(plan_id, role) WHERE status = 'open';

    -- receipts live in vision schema
    CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.receipts (
      id            TEXT PRIMARY KEY,
      plan_id       TEXT NOT NULL,
      type          TEXT NOT NULL CHECK(type IN (
                      'PLAN_CREATE','IMPLEMENTATION','REVIEW_PASS','REVIEW_REJECT','BLOCK',
                      'PLANNING','HOLD',
                      'REVIEW','CRITIQUE','CRITIQUE_PASS','CRITIQUE_REJECT','PLAN_BLOCK','API_LIMIT',
                      'REQUEUED',
                      'CANCELLED','ABANDONED'
                    )),
      agent_role    TEXT NOT NULL,
      session_id    TEXT,
      artifact_path TEXT,
      summary       TEXT NOT NULL DEFAULT '',
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      ticket_id     TEXT REFERENCES ${VISION_SCHEMA}.tickets(id),
      tokens_used   INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
      id              TEXT PRIMARY KEY,
      agent_role      TEXT NOT NULL,
      start_iso TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      end_iso TIMESTAMPTZ,
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
      workflow_id     TEXT,
      run_id          TEXT,
      workflow_start_time TIMESTAMPTZ,
      workflow_close_time TIMESTAMPTZ,
      workflow_run_time_ms REAL,
      workflow_result TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      tags            TEXT NOT NULL DEFAULT '[]'
    );

    -- session_logs live in tackle schema (model fitness / agent op-logs)
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.session_logs (
      id          BIGSERIAL PRIMARY KEY,
      session_id  TEXT NOT NULL,
      timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      level       TEXT NOT NULL DEFAULT 'INFO',
      line        TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tackle_session_logs_session_id ON ${TACKLE_SCHEMA}.session_logs(session_id);

    CREATE TABLE IF NOT EXISTS circuit_breaker (
      id                     INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
      tripped                INTEGER DEFAULT 0,
      tripped_at TIMESTAMPTZ,
      retry_after            INTEGER DEFAULT 1800,
      error                  TEXT,
      detail                 TEXT,
      source                 TEXT,
      fallback_model         TEXT,
      paused                 INTEGER DEFAULT 0,
      max_retries_per_model  INTEGER DEFAULT 3,
      retry_delay_seconds    INTEGER DEFAULT 120,
      max_fallbacks          INTEGER DEFAULT 3,
      push_back_to_pending   INTEGER DEFAULT 1,
      updated_at             TIMESTAMPTZ
    );

    INSERT INTO circuit_breaker (id, tripped) VALUES (1, 0)
    ON CONFLICT (id) DO NOTHING;

    ALTER TABLE circuit_breaker ADD COLUMN IF NOT EXISTS wake_requested_at TIMESTAMPTZ;

    -- ════════════════════════════════════════════════════════════════
    -- Token cost tracking tables (plan 1018)
    -- ════════════════════════════════════════════════════════════════

    CREATE TABLE IF NOT EXISTS model_pricing (
      model_name             TEXT PRIMARY KEY,
      provider               TEXT NOT NULL,
      input_price_per_token  DOUBLE PRECISION NOT NULL,
      output_price_per_token DOUBLE PRECISION NOT NULL,
      cache_hit_price        DOUBLE PRECISION,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS agent_budgets (
      agent_role      TEXT PRIMARY KEY,
      ceiling_usd     DOUBLE PRECISION,
      ceiling_tokens  INTEGER,
      current_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
      current_tokens  INTEGER NOT NULL DEFAULT 0,
      reset_period    TEXT NOT NULL DEFAULT 'monthly'
                      CHECK(reset_period IN ('daily','weekly','monthly')),
      reset_at TIMESTAMPTZ,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS cost_logs (
      id                BIGSERIAL PRIMARY KEY,
      session_id        TEXT NOT NULL,
      ticket_id         TEXT,
      model             TEXT NOT NULL,
      input_tokens      INTEGER NOT NULL DEFAULT 0,
      output_tokens     INTEGER NOT NULL DEFAULT 0,
      estimated_cost_usd REAL,
      actual_cost_usd   REAL,
      recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      tags              TEXT NOT NULL DEFAULT '[]'
    );

    CREATE INDEX IF NOT EXISTS idx_cost_logs_session ON cost_logs(session_id);
    CREATE INDEX IF NOT EXISTS idx_cost_logs_ticket  ON cost_logs(ticket_id);

    -- role_circuit_breaker lives in peb schema (governance engine)
    CREATE TABLE IF NOT EXISTS ${PEB_SCHEMA}.role_circuit_breaker (
      role            TEXT PRIMARY KEY,
      tripped         INTEGER DEFAULT 0,
      tripped_at TIMESTAMPTZ,
      retry_after     INTEGER DEFAULT 1800,
      error           TEXT,
      failure_count   INTEGER DEFAULT 0,
      updated_at      TIMESTAMPTZ
    );

    -- governance_events: observability spine from vision → peb
    -- receipt_id is UNIQUE for idempotency: exactly one governance event per receipt.
    -- This is an event sink, not a decision engine. No blocking, no synchronous
    -- governance. Enforcement happens in the bridge layer (Phase B).
    CREATE TABLE IF NOT EXISTS ${PEB_SCHEMA}.governance_events (
      id              BIGSERIAL PRIMARY KEY,
      receipt_id      TEXT NOT NULL UNIQUE,
      event_type      TEXT NOT NULL,
      work_request_id TEXT,
      plan_id         TEXT NOT NULL,
      agent_role      TEXT NOT NULL,
      payload         JSONB NOT NULL DEFAULT '{}',
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      replayed_at     TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_peb_governance_events_plan_id ON ${PEB_SCHEMA}.governance_events(plan_id);
    CREATE INDEX IF NOT EXISTS idx_peb_governance_events_event_type ON ${PEB_SCHEMA}.governance_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_peb_governance_events_created_at ON ${PEB_SCHEMA}.governance_events(created_at);

    -- NOTE (D-T19-2c, V099): the vision → peb governance trigger is RETIRED.
    -- The canonical governance projection now lives on execution.receipts
    -- (execution.receipt_governance_trigger, created by nexus/sql/V099).
    -- vision.receipts is frozen (D-T19-2d) and no longer projects governance.

    -- work_requests: canonical store for decomposition objects
    -- Note: INDEX creation is handled in migrations (v12/v13) to avoid
    -- conflict with legacy DBs where this relation exists as a view.
    CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.work_requests (
      id              BIGSERIAL PRIMARY KEY,
      wr_id           TEXT UNIQUE,
      dco_json        TEXT NOT NULL DEFAULT '{}',
      context         JSONB NOT NULL DEFAULT '{}',
      status          TEXT NOT NULL DEFAULT 'pending',
      step_outputs    TEXT NOT NULL DEFAULT '{}',
      recorded_on_dt      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      recorded_until_dt   TIMESTAMPTZ
    );

    -- AI config tables (moved to tackle schema, renamed without ai_ prefix)
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.providers (
      id           TEXT PRIMARY KEY,
      name         TEXT NOT NULL,
      type         TEXT NOT NULL CHECK(type IN (
                     'openai','anthropic','google','ollama',
                     'opencode','codex','spring_ai','lm_server','custom'
                   )),
      endpoint_url TEXT,
      api_key      TEXT,
      config_json  TEXT NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.harnesses (
      id                   TEXT PRIMARY KEY,
      name                 TEXT NOT NULL,
      invocation_semantics TEXT NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.models (
      id               TEXT PRIMARY KEY,
      name             TEXT NOT NULL,
      harness_id       TEXT NOT NULL REFERENCES ${TACKLE_SCHEMA}.harnesses(id) ON DELETE CASCADE,
      provider_id      TEXT REFERENCES ${TACKLE_SCHEMA}.providers(id),
      model_identifier TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.config_bundle (
      id              TEXT PRIMARY KEY,
      name            TEXT NOT NULL,
      role            TEXT NOT NULL,
      model_id        TEXT NOT NULL REFERENCES ${TACKLE_SCHEMA}.models(id),
      provider_id     TEXT REFERENCES ${TACKLE_SCHEMA}.providers(id),
      harness_id      TEXT REFERENCES ${TACKLE_SCHEMA}.harnesses(id),
      priority        INTEGER NOT NULL DEFAULT 0,
      invocation_mode TEXT NOT NULL DEFAULT 'CLI'
                        CHECK(invocation_mode IN ('CLI', 'HTTP', 'SDK', 'MCP', 'INTERACTIVE')),
      command         TEXT,
      endpoint_url    TEXT,
      timeout_ms      INTEGER,
      valid_from TIMESTAMPTZ,
      valid_to TIMESTAMPTZ,
      is_active       INTEGER NOT NULL DEFAULT 1,
      metadata        TEXT NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(role, model_id)
    );

    DROP VIEW IF EXISTS ${PG_SCHEMA}.plans_by_status CASCADE;
    DROP VIEW IF EXISTS ${PG_SCHEMA}.plan_status CASCADE;
    -- plan_status and plans_by_status are now owned by nebula-srv (migration 040).
    -- conduit-mcp no longer creates these views; they live in nebula schema.
    -- All runtime queries use nebula.plan_status explicitly.
  `);

  console.log(`Schema initialized in PG ${PG_SCHEMA} schema.`);
}

// ── Schema versioning (formal migration system) ─────────────────────

/** A single migration step, ordered by version number. */
export interface Migration {
  version: number;
  description: string;
  /**
   * Apply the migration. Receives the same `exec` function used during
   * schema creation, so DDL runs on the dedicated connection.
   */
  up: (exec: (sql: string, params?: any[]) => Promise<any>) => Promise<void>;
}

/**
 * Ordered list of schema migrations.
 *
 * - Version 1 is the baseline: all tables and columns from the DDL in
 *   createSchema(). On fresh databases or legacy databases that already
 *   have all columns (from the old ad-hoc ALTER TABLE loop), this is a
 *   no-op that simply records the baseline version.
 * - Future migrations add or alter schema objects and should be appended
 *   here with incrementing version numbers.
 * - Each migration runs exactly once, in version order. After successful
 *   execution, a row is inserted into schema_version to mark it applied.
 */
const migrations: Migration[] = [
  {
    version: 1,
    description: "Baseline — all core tables (plans, tickets, receipts, sessions, circuit_breaker, tackle AI config)",
    up: async () => {
      // No-op: the DDL in createSchema() is the source of truth for v1.
    },
  },
  {
    version: 2,
    description: "Add index on sessions.created_at for ordering performance",
    up: async (exec) => {
      await exec(`CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at)`);
    },
  },
  {
    version: 3,
    description: "Add notes TEXT column to plans for free-form annotation",
    up: async (exec) => {
      // Guard: conduit.plans no longer exists on fresh schemas (removed
      // 2026-08-07). Only legacy DBs that still carry the table get the column.
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
            AND table_name = 'plans'
          ) THEN
            ALTER TABLE plans ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT '';
          END IF;
        END $$;
      `);
    },
  },
  {
    version: 4,
    description: "Add priority INTEGER column to plans for ordering importance",
    up: async (exec) => {
      // Guard: conduit.plans no longer exists on fresh schemas (removed
      // 2026-08-07). Only legacy DBs that still carry the table get the column.
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
            AND table_name = 'plans'
          ) THEN
            ALTER TABLE plans ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;
          END IF;
        END $$;
      `);
    },
  },
  {
    version: 5,
    description: "Add tags TEXT column to sessions for free-form categorization labels",
    up: async (exec) => {
      await exec(`ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT '[]'`);
    },
  },
  {
    version: 6,
    description: "Add deadline TEXT column to tickets for target completion date",
    up: async (exec) => {
      await exec(`ALTER TABLE tickets ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ`);
    },
  },
  {
    version: 7,
    description: "(obsoleted) role_config CHECK constraint — table replaced by config_bundle",
    up: async () => {
      // No-op: role_config table was removed in favor of config_bundle.
    },
  },
  {
    version: 8,
    description: "(obsoleted) role_config CHECK rover extension — table replaced by config_bundle",
    up: async () => {
      // No-op: role_config table was removed in favor of config_bundle.
    },
  },
  {
    version: 9,
    description: "Add step_outputs JSONB column to work_requests for DB-only artifact storage",
    up: async (exec) => {
      // Guard: only apply if conduit.work_requests still exists (legacy DB upgrade path)
      await exec(`
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'conduit' AND table_name = 'work_requests') THEN
            ALTER TABLE conduit.work_requests ADD COLUMN IF NOT EXISTS step_outputs TEXT NOT NULL DEFAULT '{}';
          END IF;
        END $$;
      `);
    },
  },
  {
    version: 10,
    description: "Add session_logs table for DB-backed log streaming",
    up: async (exec) => {
      // session_logs now lives in tackle schema — created by createSchema.
      // This migration is a no-op guard for legacy DBs where the table
      // was originally created in conduit. On fresh DBs, createSchema
      // handles it.
      await exec(`
        CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.session_logs (
          id          BIGSERIAL PRIMARY KEY,
          session_id  TEXT NOT NULL,
          timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          level       TEXT NOT NULL DEFAULT 'INFO',
          line        TEXT NOT NULL
        )
      `);
      await exec(`CREATE INDEX IF NOT EXISTS idx_tackle_session_logs_session_id ON ${TACKLE_SCHEMA}.session_logs(session_id)`);
    },
  },
  {
    version: 11,
    description: "Add peb.governance_events table and receipt→governance trigger for observability spine",
    up: async (exec) => {
      // On legacy DBs where createSchema() has already run, the DDL in createSchema
      // handles fresh tables. This migration is a no-op guard — in the unlikely event
      // createSchema missed the table (legacy DB with search_path edge case), the
      // IF NOT EXISTS protects us.
      await exec(`
        CREATE TABLE IF NOT EXISTS ${PEB_SCHEMA}.governance_events (
          id              BIGSERIAL PRIMARY KEY,
          receipt_id      TEXT NOT NULL UNIQUE,
          event_type      TEXT NOT NULL,
          work_request_id TEXT,
          plan_id         TEXT NOT NULL,
          agent_role      TEXT NOT NULL,
          payload         JSONB NOT NULL DEFAULT '{}',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          replayed_at     TIMESTAMPTZ
        )
      `);
      await exec(`CREATE INDEX IF NOT EXISTS idx_peb_governance_events_plan_id ON ${PEB_SCHEMA}.governance_events(plan_id)`);
      await exec(`CREATE INDEX IF NOT EXISTS idx_peb_governance_events_event_type ON ${PEB_SCHEMA}.governance_events(event_type)`);
      await exec(`CREATE INDEX IF NOT EXISTS idx_peb_governance_events_created_at ON ${PEB_SCHEMA}.governance_events(created_at)`);

      // Trigger function and trigger
      // NOTE (D-T19-2c, V099): the vision → peb receipt_governance_trigger is
      // RETIRED. Governance projection now lives on execution.receipts
      // (execution.receipt_governance_trigger, created by nexus/sql/V099).
      // vision.receipts is frozen (D-T19-2d) and no longer projects governance.

      // Backfill: emit governance events for all existing receipts that don't have one yet
      await exec(`
        INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, plan_id, agent_role, payload, created_at)
        SELECT
          r.id,
          'receipt:' || r.type,
          r.plan_id,
          r.agent_role,
          jsonb_build_object(
            'session_id', r.session_id,
            'artifact_path', r.artifact_path,
            'summary', r.summary,
            'ticket_id', r.ticket_id,
            'tokens_used', r.tokens_used
          ),
          r.created_at
        FROM ${VISION_SCHEMA}.receipts r
        WHERE NOT EXISTS (
          SELECT 1 FROM ${PEB_SCHEMA}.governance_events g WHERE g.receipt_id = r.id
        )
      `);
    },
  },
  {
    version: 12,
    description: "Add dco_json and wr_id columns to vision.work_requests for LOSM bridge compatibility",
    up: async (exec) => {
      // The work_requests table was created manually during the conduit→vision
      // schema split with a BIGSERIAL id. This migration adds the columns
      // that the typed bridge requires.
      // The existing vision.work_requests was created as a VIEW during the
      // manual schema split (pointing at the old conduit.work_requests which
      // was later dropped). Replace it with a proper BASE TABLE.
      // NOTE: on fresh DBs createSchema already creates this as a BASE TABLE,
      // so the DROP is relkind-guarded (only a leftover VIEW is dropped) and
      // the CREATE is IF NOT EXISTS. The INDEX still must be created here —
      // createSchema deliberately defers it to v12/v13 (see comment above).
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = '${VISION_SCHEMA}' AND c.relname = 'work_requests'
            AND c.relkind = 'v'
          ) THEN
            EXECUTE 'DROP VIEW ${VISION_SCHEMA}.work_requests CASCADE';
          END IF;
        END $$;
      `);
      await exec(`
        CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.work_requests (
          id              BIGSERIAL PRIMARY KEY,
          wr_id           TEXT UNIQUE,
          dco_json        TEXT NOT NULL DEFAULT '{}',
          context         JSONB NOT NULL DEFAULT '{}',
          status          TEXT NOT NULL DEFAULT 'pending',
          step_outputs    TEXT NOT NULL DEFAULT '{}',
          recorded_on_dt      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          recorded_until_dt   TIMESTAMPTZ
        )
      `);
      await exec(`CREATE INDEX IF NOT EXISTS idx_vision_work_requests_status ON ${VISION_SCHEMA}.work_requests(status)`);
    },
  },
  {
    version: 13,
    description: "Replace vision.work_requests view with proper BASE TABLE (v12 was a no-op on legacy DBs)",
    up: async (exec) => {
      // Check if work_requests is still a view (v12 may have been recorded as
      // applied without doing the replacement due to the earlier check)
      const checkResult = await exec(`
        SELECT table_type FROM information_schema.tables
        WHERE table_schema = '${VISION_SCHEMA}' AND table_name = 'work_requests'
      `);
      const tableType = checkResult?.rows?.[0]?.table_type;
      if (tableType === 'BASE TABLE') {
        console.log("[migrations] v13: vision.work_requests is already a BASE TABLE — skipping");
        return;
      }
      // Drop the view and create a proper table
      await exec(`DROP VIEW IF EXISTS ${VISION_SCHEMA}.work_requests CASCADE`);
      await exec(`
        CREATE TABLE ${VISION_SCHEMA}.work_requests (
          id              BIGSERIAL PRIMARY KEY,
          wr_id           TEXT UNIQUE,
          dco_json        TEXT NOT NULL DEFAULT '{}',
          context         JSONB NOT NULL DEFAULT '{}',
          status          TEXT NOT NULL DEFAULT 'pending',
          step_outputs    TEXT NOT NULL DEFAULT '{}',
          recorded_on_dt      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          recorded_until_dt   TIMESTAMPTZ
        )
      `);
      await exec(`CREATE INDEX IF NOT EXISTS idx_vision_work_requests_status ON ${VISION_SCHEMA}.work_requests(status)`);
      console.log("[migrations] v13: Replaced vision.work_requests view with BASE TABLE");
    },
  },
  {
    version: 14,
    description: "Cross-system identity contract: add work_request_uuid to vision.work_requests, propagate to governance events",
    up: async (exec) => {
      // Step 1: Add work_request_uuid column (nullable initially for backfill)
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.work_requests
        ADD COLUMN IF NOT EXISTS work_request_uuid TEXT
      `);

      // Step 2: Backfill existing rows with generated UUIDs
      await exec(`
        UPDATE ${VISION_SCHEMA}.work_requests
        SET work_request_uuid = gen_random_uuid()::text
        WHERE work_request_uuid IS NULL
      `);

      // Step 3: Create unique index (UNIQUE constraint can't be added
      // with IF NOT EXISTS, so we use a unique index instead)
      await exec(`
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_work_requests_uuid
        ON ${VISION_SCHEMA}.work_requests(work_request_uuid)
      `);

      // Step 4: Add NOT NULL constraint to work_request_uuid
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.work_requests
        ALTER COLUMN work_request_uuid SET NOT NULL
      `);

      // Step 5: Update the governance trigger to propagate work_request_uuid.
      // NOTE (D-T19-2c, V099): RETIRED. The vision.receipt_governance_trigger
      // update is removed — governance now projects from execution.receipts
      // (execution.receipt_governance_trigger, nexus/sql/V099). The
      // work_request_id stitch lives in that trigger, not on vision.receipts.

      // Step 6: Backfill existing governance events with work_request_uuid
      // for receipts that have a matching work request
      await exec(`
        UPDATE ${PEB_SCHEMA}.governance_events g
        SET work_request_id = wr.work_request_uuid
        FROM ${VISION_SCHEMA}.work_requests wr
        WHERE g.plan_id = wr.wr_id
        AND g.work_request_id IS NULL
      `);

      console.log("[migrations] v14: Cross-system identity contract applied");
      console.log("  - Added work_request_uuid to vision.work_requests");
      console.log("  - Updated governance trigger to propagate UUID");
    },
  },
  {
    version: 15,
    description: "Add sequence column to vision.receipts for deterministic WRP bridge ordering (Conduit→Nebula #0174)",
    up: async (exec) => {
      // Step 1: Add sequence column (nullable initially for backfill)
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.receipts
        ADD COLUMN IF NOT EXISTS sequence INTEGER
      `);

      // Step 2: Backfill existing receipts with per-plan monotonic sequence numbers.
      // Uses a window function ordered by (created_at, id) — the canonical ordering
      // key minus the explicit sequence field itself.
      await exec(`
        WITH numbered AS (
          SELECT id, plan_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY plan_id
                   ORDER BY created_at ASC, id ASC
                 ) - 1 AS seq
          FROM ${VISION_SCHEMA}.receipts
          WHERE sequence IS NULL
        )
        UPDATE ${VISION_SCHEMA}.receipts r
        SET sequence = n.seq
        FROM numbered n
        WHERE r.id = n.id
      `);

      // Step 3: Add NOT NULL constraint.  By this point every row has a sequence.
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.receipts
        ALTER COLUMN sequence SET NOT NULL
      `);

      // Step 4: Add per-plan uniqueness constraint so no two receipts share a sequence.
      await exec(`
        CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_plan_sequence
        ON ${VISION_SCHEMA}.receipts(plan_id, sequence)
      `);

      // Step 5: Add CHECK constraint guaranteeing sequence >= 0 and no gaps are
      // enforced at application level (insert-time sequence assignment).  The
      // UNIQUE index + NOT NULL guarantee that each plan has at most one receipt
      // per sequence value; MAX(sequence) = COUNT(*)-1 is a runtime invariant.
      // Guarded for idempotency — fresh-DB replay (schema tests) and live DB
      // (already-applied v15) must both pass.  ADD CONSTRAINT has no
      // IF NOT EXISTS in PG, so pre-check pg_constraint (same pattern as v25).
      await exec(`
        DO $MIGRATE$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = '${VISION_SCHEMA}.receipts'::regclass
              AND conname = 'chk_receipts_sequence_non_negative'
          ) THEN
            ALTER TABLE ${VISION_SCHEMA}.receipts
            ADD CONSTRAINT chk_receipts_sequence_non_negative
            CHECK (sequence >= 0);
          END IF;
        END;
        $MIGRATE$
      `);

      // Step 6: Create a trigger function that auto-assigns sequence on INSERT
      // if the caller does not provide one.  Uses MAX+1 per plan_id.
      await exec(`
        CREATE OR REPLACE FUNCTION vision.receipts_assign_sequence()
        RETURNS TRIGGER AS $BODY$
        BEGIN
          IF NEW.sequence IS NULL THEN
            SELECT COALESCE(MAX(r.sequence), -1) + 1
            INTO NEW.sequence
            FROM ${VISION_SCHEMA}.receipts r
            WHERE r.plan_id = NEW.plan_id;
          END IF;
          RETURN NEW;
        END;
        $BODY$ LANGUAGE plpgsql
      `);

      await exec(`
        DROP TRIGGER IF EXISTS trg_receipts_assign_sequence ON ${VISION_SCHEMA}.receipts;
        CREATE TRIGGER trg_receipts_assign_sequence
        BEFORE INSERT ON ${VISION_SCHEMA}.receipts
        FOR EACH ROW
        EXECUTE FUNCTION vision.receipts_assign_sequence()
      `);

      console.log("[migrations] v15: Added sequence column to vision.receipts");
      console.log("  - Backfilled per-plan monotonic sequence numbers");
      console.log("  - Added UNIQUE(plan_id, sequence) index");
      console.log("  - Added BEFORE INSERT trigger for auto-assignment");
    },
  },
  {
    version: 16,
    description: "Add last_heartbeat_at TEXT column to sessions for agent-liveness tracking",
    up: async (exec) => {
      await exec(`ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ`);
    },
  },
  {
    version: 17,
    description: "Add work_request_events table for Runtime Kernel event-sourced state machine",
    up: async (exec) => {
      // v18 (event-sourcing foundation) DROPs this v17-shaped table and
      // rebuilds it with a different shape (work_request_id instead of
      // wr_id). On fresh-replay after v18 has run, the table already exists
      // in v18 shape — v17's CREATE TABLE IF NOT EXISTS would skip, but the
      // wr_id index/backfill below would fail ("column wr_id does not exist").
      // Guard: skip the v17 body entirely when the table exists without a
      // wr_id column (v18 shape). Absent table or v17-shape table → run.
      const shapeCheck = await exec(`
        SELECT EXISTS (
          SELECT 1 FROM information_schema.tables t
          WHERE t.table_schema = '${PG_SCHEMA}'
          AND t.table_name = 'work_request_events'
          AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_schema = t.table_schema
            AND c.table_name = t.table_name
            AND c.column_name = 'wr_id'
          )
        ) AS is_v18_shape
      `);
      const isV18Shape = shapeCheck?.rows?.[0]?.is_v18_shape === true;
      if (isV18Shape) {
        console.log("[migrations] v17: Skipped — work_request_events already in v18 shape");
        return;
      }

      // The event log is the source of truth for WorkRequest lifecycle.
      // State = fold(events), never direct mutation.
      await exec(`
        CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.work_request_events (
          id          BIGSERIAL PRIMARY KEY,
          wr_id       TEXT NOT NULL REFERENCES ${VISION_SCHEMA}.work_requests(wr_id),
          event_type  TEXT NOT NULL,
          payload     JSONB NOT NULL DEFAULT '{}',
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_wre_wr_id ON ${PG_SCHEMA}.work_request_events(wr_id);
        CREATE INDEX IF NOT EXISTS idx_wre_event_type ON ${PG_SCHEMA}.work_request_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_wre_created_at ON ${PG_SCHEMA}.work_request_events(created_at);
      `);
      // Backfill event log for existing pending work requests.
      // Every existing WR with status 'pending' gets a synthetic WR_SUBMITTED event
      // so the fold produces VALIDATED state, making them eligible for the decision loop.
      // Skip identity-less rows (wr_id NULL/'' — e.g. the resolution seed
      // `wr-mongo-wiring`): they have no compile-unit identity, so they can't
      // carry an event-log entry (wr_id is the FK key). Backfilling them would
      // violate the NOT NULL constraint and brick fresh-schema bootstrap.
      await exec(`
        INSERT INTO ${PG_SCHEMA}.work_request_events (wr_id, event_type, payload, created_at)
        SELECT wr_id, 'WR_SUBMITTED',
               jsonb_build_object('backfill', true, 'original_status', status),
               recorded_on_dt
        FROM ${VISION_SCHEMA}.work_requests
        WHERE status = 'pending'
          AND wr_id IS NOT NULL
          AND wr_id <> ''
          AND wr_id NOT IN (
            SELECT wr_id FROM ${PG_SCHEMA}.work_request_events WHERE event_type = 'WR_SUBMITTED'
          )
      `);
      // Also ensure the work_requests cache status reflects the initial folded state
      await exec(`
        UPDATE ${VISION_SCHEMA}.work_requests wr
        SET status = 'validated'
        WHERE wr.status = 'pending'
          AND wr.wr_id IN (
            SELECT wre.wr_id FROM ${PG_SCHEMA}.work_request_events wre
            WHERE wre.event_type = 'WR_SUBMITTED'
              AND wr.wr_id = wre.wr_id
          )
      `);
    },
  },
  {
    version: 18,
    description: "Event-Sourcing Foundation: WorkRequest Event Store, State Projection, and Replay Engine (plan 1052)",
    up: async (exec) => {
      await exec(`DROP TABLE IF EXISTS ${PG_SCHEMA}.work_request_events CASCADE`);

      await exec(`
        CREATE TABLE ${PG_SCHEMA}.work_request_events (
          event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          work_request_id   UUID NOT NULL,
          event_type        TEXT NOT NULL
            CHECK(event_type IN (
              'WORKREQUEST.CREATED','VISION.IR_PRODUCED',
              'STATE.TRANSITION_PROPOSED','STATE.TRANSITION_APPROVED','STATE.TRANSITION_COMMITTED',
              'EXECUTION.STARTED','EXECUTION.COMPLETED','EXECUTION.FAILED',
              'SYSTEM.CRON_TRIGGERED'
            )),
          event_version     INTEGER NOT NULL DEFAULT 1,
          correlation_id    UUID,
          causation_id      UUID,
          occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
          actor_type        TEXT NOT NULL DEFAULT 'system',
          actor_id          TEXT NOT NULL DEFAULT '',
          sequence_number   BIGSERIAL NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_wre_wr_seq
          ON ${PG_SCHEMA}.work_request_events(work_request_id, sequence_number);
        CREATE INDEX IF NOT EXISTS idx_wre_wr_occurred
          ON ${PG_SCHEMA}.work_request_events(work_request_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_wre_event_type
          ON ${PG_SCHEMA}.work_request_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_wre_correlation
          ON ${PG_SCHEMA}.work_request_events(correlation_id);
        CREATE INDEX IF NOT EXISTS idx_wre_payload
          ON ${PG_SCHEMA}.work_request_events USING GIN(payload);
      `);

      await exec(`
        CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.work_request_state (
          work_request_id   UUID PRIMARY KEY,
          current_state     TEXT NOT NULL DEFAULT 'PROPOSED'
            CHECK(current_state IN (
              'PROPOSED','PLANNING','PENDING','IMPLEMENTING','REVIEW','COMPLETED','FAILED','CANCELLED'
            )),
          vision_stage      TEXT
            CHECK(vision_stage IN ('PLAN_IR','SPEC_IR','EXECUTION_IR','VALIDATION_IR')),
          vision_ir_version INTEGER NOT NULL DEFAULT 0,
          last_event_id     UUID,
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_wrs_current_state
          ON ${PG_SCHEMA}.work_request_state(current_state);
      `);

      await exec(`
        CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.vision_ir_artifacts (
          artifact_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          work_request_id   UUID NOT NULL,
          event_id          UUID NOT NULL,
          ir_stage          TEXT NOT NULL
            CHECK(ir_stage IN ('PLAN_IR','SPEC_IR','EXECUTION_IR','VALIDATION_IR')),
          ir_version        INTEGER NOT NULL DEFAULT 1,
          artifact_type     TEXT NOT NULL,
          content           JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_vision_ir_wr_stage_ver
          ON ${VISION_SCHEMA}.vision_ir_artifacts(work_request_id, ir_stage, ir_version);
        CREATE INDEX IF NOT EXISTS idx_vision_ir_stage
          ON ${VISION_SCHEMA}.vision_ir_artifacts(ir_stage);
        CREATE INDEX IF NOT EXISTS idx_vision_ir_content
          ON ${VISION_SCHEMA}.vision_ir_artifacts USING GIN(content);
      `);

      await exec(`
        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.enforce_state_transition()
        RETURNS TRIGGER AS $TRIG$
        BEGIN
          -- Only STATE.TRANSITION_COMMITTED may carry a new_state payload key.
          -- Any other event type that includes new_state is a state-mutation
          -- attempt disguised as a non-state event — reject unconditionally.
          IF NEW.event_type != 'STATE.TRANSITION_COMMITTED'
             AND NEW.payload ? 'new_state'
          THEN
            RAISE EXCEPTION
              'STATE_MUTATION_FORBIDDEN: event type % must not carry payload.new_state; '
              'only STATE.TRANSITION_COMMITTED may mutate state',
              NEW.event_type;
          END IF;
          RETURN NEW;
        END;
        $TRIG$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_enforce_state_transition ON ${PG_SCHEMA}.work_request_events;
        CREATE TRIGGER trg_enforce_state_transition
        BEFORE INSERT ON ${PG_SCHEMA}.work_request_events
        FOR EACH ROW
        EXECUTE FUNCTION ${PG_SCHEMA}.enforce_state_transition();
      `);

      await exec(`
        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.update_work_request_state()
        RETURNS TRIGGER AS $TRIG$
        BEGIN
          IF NEW.event_type = 'STATE.TRANSITION_COMMITTED' THEN
            INSERT INTO ${PG_SCHEMA}.work_request_state
              (work_request_id, current_state, last_event_id, updated_at)
            VALUES (
              NEW.work_request_id,
              COALESCE(NEW.payload->>'new_state', 'PROPOSED'),
              NEW.event_id,
              NEW.occurred_at
            )
            ON CONFLICT (work_request_id) DO UPDATE SET
              current_state = EXCLUDED.current_state,
              last_event_id = EXCLUDED.last_event_id,
              updated_at = EXCLUDED.updated_at;
          END IF;

          IF NEW.event_type = 'VISION.IR_PRODUCED' THEN
            INSERT INTO ${PG_SCHEMA}.work_request_state
              (work_request_id, vision_stage, vision_ir_version, last_event_id, updated_at)
            VALUES (
              NEW.work_request_id,
              NEW.payload->>'ir_stage',
              COALESCE((NEW.payload->>'ir_version')::integer, 1),
              NEW.event_id,
              NEW.occurred_at
            )
            ON CONFLICT (work_request_id) DO UPDATE SET
              vision_stage = EXCLUDED.vision_stage,
              vision_ir_version = EXCLUDED.vision_ir_version,
              last_event_id = EXCLUDED.last_event_id,
              updated_at = EXCLUDED.updated_at;
          END IF;

          IF NEW.event_type = 'WORKREQUEST.CREATED' THEN
            INSERT INTO ${PG_SCHEMA}.work_request_state
              (work_request_id, current_state, last_event_id, updated_at)
            VALUES (NEW.work_request_id, 'PROPOSED', NEW.event_id, NEW.occurred_at)
            ON CONFLICT (work_request_id) DO UPDATE SET
              last_event_id = EXCLUDED.last_event_id,
              updated_at = EXCLUDED.updated_at;
          END IF;

          RETURN NEW;
        END;
        $TRIG$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_update_wr_state ON ${PG_SCHEMA}.work_request_events;
        CREATE TRIGGER trg_update_wr_state
        AFTER INSERT ON ${PG_SCHEMA}.work_request_events
        FOR EACH ROW
        EXECUTE FUNCTION ${PG_SCHEMA}.update_work_request_state();
      `);

      await exec(`
        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.replay_work_request_events(p_wr_id UUID)
        RETURNS TABLE (
          event_id UUID,
          event_type TEXT,
          event_version INTEGER,
          sequence_number BIGINT,
          occurred_at TIMESTAMPTZ,
          payload JSONB,
          actor_type TEXT,
          actor_id TEXT
        ) AS $FUNC$
        BEGIN
          RETURN QUERY
          SELECT e.event_id, e.event_type, e.event_version, e.sequence_number,
                 e.occurred_at, e.payload, e.actor_type, e.actor_id
          FROM ${PG_SCHEMA}.work_request_events e
          WHERE e.work_request_id = p_wr_id
          ORDER BY e.sequence_number ASC;
        END;
        $FUNC$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.replay_from_checkpoint(p_wr_id UUID, p_checkpoint BIGINT)
        RETURNS TABLE (
          event_id UUID,
          event_type TEXT,
          event_version INTEGER,
          sequence_number BIGINT,
          occurred_at TIMESTAMPTZ,
          payload JSONB,
          actor_type TEXT,
          actor_id TEXT
        ) AS $FUNC$
        BEGIN
          RETURN QUERY
          SELECT e.event_id, e.event_type, e.event_version, e.sequence_number,
                 e.occurred_at, e.payload, e.actor_type, e.actor_id
          FROM ${PG_SCHEMA}.work_request_events e
          WHERE e.work_request_id = p_wr_id
            AND e.sequence_number > p_checkpoint
          ORDER BY e.sequence_number ASC;
        END;
        $FUNC$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.rebuild_work_request_state(p_wr_id UUID)
        RETURNS TEXT AS $FUNC$
        DECLARE
          v_event RECORD;
          v_state TEXT := 'PROPOSED';
          v_stage TEXT := NULL;
          v_ir_ver INTEGER := 0;
          v_last_event UUID := NULL;
          v_last_at TIMESTAMPTZ := NOW();
        BEGIN
          FOR v_event IN
            SELECT * FROM ${PG_SCHEMA}.work_request_events
            WHERE work_request_id = p_wr_id
            ORDER BY sequence_number ASC
          LOOP
            IF v_event.event_type = 'WORKREQUEST.CREATED' THEN
              v_state := 'PROPOSED';
            END IF;
            IF v_event.event_type = 'STATE.TRANSITION_COMMITTED' THEN
              v_state := COALESCE(v_event.payload->>'new_state', v_state);
            END IF;
            IF v_event.event_type = 'VISION.IR_PRODUCED' THEN
              v_stage := v_event.payload->>'ir_stage';
              v_ir_ver := COALESCE((v_event.payload->>'ir_version')::integer, v_ir_ver);
            END IF;
            v_last_event := v_event.event_id;
            v_last_at := v_event.occurred_at;
          END LOOP;

          INSERT INTO ${PG_SCHEMA}.work_request_state
            (work_request_id, current_state, vision_stage, vision_ir_version, last_event_id, updated_at)
          VALUES (p_wr_id, v_state, v_stage, v_ir_ver, v_last_event, v_last_at)
          ON CONFLICT (work_request_id) DO UPDATE SET
            current_state = EXCLUDED.current_state,
            vision_stage = EXCLUDED.vision_stage,
            vision_ir_version = EXCLUDED.vision_ir_version,
            last_event_id = EXCLUDED.last_event_id,
            updated_at = EXCLUDED.updated_at;

          RETURN v_state;
        END;
        $FUNC$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.rebuild_all_state_projections()
        RETURNS INTEGER AS $FUNC$
        DECLARE
          v_count INTEGER := 0;
          v_wr_id UUID;
        BEGIN
          TRUNCATE ${PG_SCHEMA}.work_request_state;
          FOR v_wr_id IN
            SELECT DISTINCT work_request_id FROM ${PG_SCHEMA}.work_request_events
          LOOP
            PERFORM ${PG_SCHEMA}.rebuild_work_request_state(v_wr_id);
            v_count := v_count + 1;
          END LOOP;
          RETURN v_count;
        END;
        $FUNC$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION ${PG_SCHEMA}.check_projection_drift(p_wr_id UUID)
        RETURNS TABLE (
          expected_state TEXT,
          expected_vision_stage TEXT,
          expected_vision_ir_version INTEGER,
          expected_last_event_id UUID,
          live_state TEXT,
          live_vision_stage TEXT,
          live_vision_ir_version INTEGER,
          live_last_event_id UUID,
          has_drift BOOLEAN
        ) AS $FUNC$
        DECLARE
          v_event RECORD;
          v_state TEXT := 'PROPOSED';
          v_stage TEXT := NULL;
          v_ir_ver INTEGER := 0;
          v_last_event UUID := NULL;
          v_live RECORD;
        BEGIN
          FOR v_event IN
            SELECT * FROM ${PG_SCHEMA}.work_request_events
            WHERE work_request_id = p_wr_id
            ORDER BY sequence_number ASC
          LOOP
            IF v_event.event_type = 'WORKREQUEST.CREATED' THEN
              v_state := 'PROPOSED';
            END IF;
            IF v_event.event_type = 'STATE.TRANSITION_COMMITTED' THEN
              v_state := COALESCE(v_event.payload->>'new_state', v_state);
            END IF;
            IF v_event.event_type = 'VISION.IR_PRODUCED' THEN
              v_stage := v_event.payload->>'ir_stage';
              v_ir_ver := COALESCE((v_event.payload->>'ir_version')::integer, v_ir_ver);
            END IF;
            v_last_event := v_event.event_id;
          END LOOP;

          SELECT * INTO v_live
          FROM ${PG_SCHEMA}.work_request_state
          WHERE work_request_id = p_wr_id;

          IF v_live IS NULL THEN
            expected_state := v_state;
            expected_vision_stage := v_stage;
            expected_vision_ir_version := v_ir_ver;
            expected_last_event_id := v_last_event;
            live_state := NULL;
            live_vision_stage := NULL;
            live_vision_ir_version := NULL;
            live_last_event_id := NULL;
            has_drift := TRUE;
          ELSE
            expected_state := v_state;
            expected_vision_stage := v_stage;
            expected_vision_ir_version := v_ir_ver;
            expected_last_event_id := v_last_event;
            live_state := v_live.current_state;
            live_vision_stage := v_live.vision_stage;
            live_vision_ir_version := v_live.vision_ir_version;
            live_last_event_id := v_live.last_event_id;
            has_drift := (
              v_state != v_live.current_state
              OR v_stage IS DISTINCT FROM v_live.vision_stage
              OR v_ir_ver != v_live.vision_ir_version
              OR v_last_event IS DISTINCT FROM v_live.last_event_id
            );
          END IF;

          RETURN NEXT;
        END;
        $FUNC$ LANGUAGE plpgsql;
      `);

      await exec(`
        CREATE OR REPLACE FUNCTION ${VISION_SCHEMA}.auto_update_vision_ir_artifact()
        RETURNS TRIGGER AS $TRIG$
        BEGIN
          IF NEW.ir_version IS NULL OR NEW.ir_version = 0 THEN
            SELECT COALESCE(MAX(a.ir_version), 0) + 1
            INTO NEW.ir_version
            FROM ${VISION_SCHEMA}.vision_ir_artifacts a
            WHERE a.work_request_id = NEW.work_request_id
              AND a.ir_stage = NEW.ir_stage;
          END IF;
          RETURN NEW;
        END;
        $TRIG$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_auto_ir_version ON ${VISION_SCHEMA}.vision_ir_artifacts;
        CREATE TRIGGER trg_auto_ir_version
        BEFORE INSERT ON ${VISION_SCHEMA}.vision_ir_artifacts
        FOR EACH ROW
        EXECUTE FUNCTION ${VISION_SCHEMA}.auto_update_vision_ir_artifact();
      `);
    },
  },
  {
    version: 19,
    description: "Broaden work_request_events CHECK constraint to accept runtime kernel WR_* event types alongside existing Vision event types",
    up: async (exec) => {
      await exec(`
        ALTER TABLE ${PG_SCHEMA}.work_request_events
        DROP CONSTRAINT IF EXISTS work_request_events_event_type_check;
      `);
      await exec(`
        ALTER TABLE ${PG_SCHEMA}.work_request_events
        ADD CONSTRAINT work_request_events_event_type_check
        CHECK(event_type IN (
          'WORKREQUEST.CREATED','VISION.IR_PRODUCED',
          'STATE.TRANSITION_PROPOSED','STATE.TRANSITION_APPROVED','STATE.TRANSITION_COMMITTED',
          'EXECUTION.STARTED','EXECUTION.COMPLETED','EXECUTION.FAILED',
          'SYSTEM.CRON_TRIGGERED',
          'WR_SUBMITTED','WR_VALIDATED','WR_QUEUED','WR_CLAIMED','WR_ACKED','WR_SETTLED',
          'WR_REJECTED',          'WR_FAILED','WR_NOOP','WR_DEFERRED'
        ));
      `);
    },
  },
  {
    version: 21,
    description: "Add vision.is_terminal_receipt_type() helper — canonical source-of-truth for terminal receipt types, consulted by vision.check_receipt_integrity() (Plan 0175 follow-up)",
    up: async (exec) => {
      // Helper used by vision.check_receipt_integrity() (migrations v20+) and
      // any future receipt-flow invariant. Single source of truth so the
      // verifier stays in lockstep with db.ts:_isPlanTerminal, which already
      // treats REVIEW_PASS, BLOCK, PLAN_BLOCK, CANCELLED, ABANDONED as
      // close-out receipts.
      //
      // Independent of v20 because:
      // 1. Fresh-DB bootstrap ordering — v20 kinds #1/#3 invoke this helper,
      //    so it must exist before any code path that selects from the
      //    check function runs against it.
      // 2. Drift prevention — adding new terminal types means one place
      //    (this function) instead of editing kinds #1+3 in v20 separately.
      await exec(`
        CREATE OR REPLACE FUNCTION vision.is_terminal_receipt_type(p_type text)
        RETURNS boolean AS $FUNC$
        BEGIN
          RETURN p_type IN ('REVIEW_PASS','BLOCK','PLAN_BLOCK','CANCELLED','ABANDONED');
        END;
        $FUNC$ LANGUAGE plpgsql IMMUTABLE
      `);
    },
  },
  {
    version: 20,
    description: "Add vision.check_receipt_integrity() — orphan-detection invariant for tickets-vs-receipts-vs-plans consistency (Plan 0175 follow-up)",
    up: async (exec) => {
      // The function detects three INDEPENDENT classes of orphan state that
      // can accumulate silently during partial failures (e.g. the 2026-07-03
      // power outage that left 14 ghost plans visible in /state). The kinds
      // are partitioned so each row fires at most ONE anomaly, keeping verifier
      // output signal-rich (no double-counting across kinds).
      //
      //   1. STUCK_OPEN_TICKET_NO_TERMINAL_RECEIPT — a non-terminal ticket on
      //      a soft-deleted plan with NO terminal CANCELLED/ABANDONED/BLOCK/
      //      PLAN_BLOCK receipt on record. Builder keeps polling for a plan
      //      that should already be closed.
      //
      //   2. ORPHAN_RECEIPT_NO_PLAN — a receipt whose plan_id has no live
      //      nebula.plans row (deleted=0) AND no conduit_plan_id linkage
      //      on a nebula.requirements row. Unmoored audit history.
      //
      //   3. DELETED_PLAN_HAS_OPEN_TICKETS_AFTER_TERMINAL_RECEIPT — a soft-
      //      deleted plan still has non-terminal tickets *despite* a terminal
      //      receipt being on record. Indicates delete_plan MCP cascade did
      //      not cancel the ticket (clean-up signal).
      //
      // Gating note for kind #1 + #3: scoped strictly to `p.deleted = 1` so
      // active plans with legitimately-running open tickets are NOT flagged
      // as anomalies (their close-out receipt is correctly absent during
      // normal lifecycle).
      //
      // Read-only invariant — never mutates state. Safe to call repeatedly.
      await exec(`
        CREATE OR REPLACE FUNCTION vision.check_receipt_integrity()
        RETURNS TABLE(
          kind text,
          plan_id text,
          ticket_id text,
          receipt_id text,
          detail text
        ) AS $FUNC$
        BEGIN
          /* ── 1. Stuck ticket on deleted plan with NO terminal receipt ── */
          RETURN QUERY
          SELECT
            'STUCK_OPEN_TICKET_NO_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) on deleted plan %s has no terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND NOT EXISTS (
              SELECT 1 FROM ${VISION_SCHEMA}.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 2. Receipt whose plan row is gone AND no requirements link ── */
          RETURN QUERY
          SELECT
            'ORPHAN_RECEIPT_NO_PLAN'::text AS kind,
            r.plan_id::text AS plan_id,
            NULL::text AS ticket_id,
            r.id::text AS receipt_id,
            format(
              'receipt %s type=%s plan=%s has no live nebula.plans row and no conduit_plan_id linkage',
              r.id, r.type, r.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.receipts r
          WHERE NOT EXISTS (
            SELECT 1 FROM nebula.plans p
            WHERE p.id = r.plan_id AND p.deleted = 0
          )
          AND NOT EXISTS (
            SELECT 1 FROM nebula.requirements req
            WHERE req.conduit_plan_id = r.plan_id
          );

          /* ── 3. Deleted plan still has open tickets DESPITE terminal receipt ── */
          RETURN QUERY
          SELECT
            'DELETED_PLAN_HAS_OPEN_TICKETS_AFTER_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) left open on deleted plan %s despite terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND EXISTS (
              SELECT 1 FROM ${VISION_SCHEMA}.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 4. Ticket references a plan that has NO nebula.plans row at all ── */
          /* Closes the symmetric orphan-ticket gap (Kind #2 covers orphan RECEIPTS; */
          /* this covers orphan TICKETS). Triggered by partial hard_delete_plan cascade */
          /* where the plan row is gone but a ticket somehow survived. */
          RETURN QUERY
          SELECT
            'ORPHAN_TICKET_NO_PLAN'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) references plan %s which has no row in nebula.plans',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          LEFT JOIN nebula.plans p ON p.id = t.plan_id
          WHERE p.id IS NULL
            AND t.status IN ('open','claimed','stale','failed');
        END;
        $FUNC$ LANGUAGE plpgsql STABLE
      `);

      // Quick smoke-query — confirm the function is callable and returns
      // the expected shape (zero rows on a clean DB).
      // Guarded: on DBs where v22 already applied, the parameterized
      // check_receipt_integrity(p_threshold_seconds int DEFAULT 1800) also
      // exists, making the zero-arg call ambiguous ("function is not unique").
      // The smoke check is a verification nicety — skip it when overloads
      // coexist rather than failing the migration.
      await exec(`
        DO $SMOKE$
        BEGIN
          BEGIN
            PERFORM count(*) FROM vision.check_receipt_integrity();
          EXCEPTION WHEN ambiguous_function OR undefined_function THEN
            RAISE NOTICE 'v20 smoke query skipped: check_receipt_integrity overloads coexist (v22 parameterized version present)';
          END;
        END;
        $SMOKE$
      `);
    },
  },
  {
    version: 23,
    description: "Add title TEXT column to conduit.work_requests and vision.work_requests for denormalized WR titles, avoiding costly joins to nebula.plans (Architect gap #2)",
    up: async (exec) => {
      // Add title to conduit.work_requests (Python-managed pipeline table)
      await exec(`
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'conduit' AND table_name = 'work_requests') THEN
            ALTER TABLE conduit.work_requests ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';
          END IF;
        END $$;
      `);

      // Add title to vision.work_requests (TypeScript-managed runtime kernel table)
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.work_requests ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''
      `);

      // Backfill titles for existing rows where title is empty
      // First pass: join on wr_id = plan_id (direct mapping)
      // Second pass: join on context->>'plan_id' = plan_id (UUID-based vision WRs)
      await exec(`
        UPDATE ${VISION_SCHEMA}.work_requests wr
        SET title = COALESCE(p.title, '')
        FROM nebula.plans p
        WHERE wr.wr_id = p.id
          AND (wr.title IS NULL OR wr.title = '')
          AND p.title IS NOT NULL AND p.title != ''
      `);

      // Second pass: join on context->>'plan_id' for UUID-based vision work requests
      await exec(`
        UPDATE ${VISION_SCHEMA}.work_requests wr
        SET title = COALESCE(p.title, '')
        FROM nebula.plans p
        WHERE wr.context->>'plan_id' = p.id
          AND (wr.title IS NULL OR wr.title = '')
          AND p.title IS NOT NULL AND p.title != ''
      `);

      // Backfill conduit.work_requests titles the same way
      await exec(`
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'conduit' AND table_name = 'work_requests') THEN
            UPDATE conduit.work_requests wr
            SET title = COALESCE(p.title, '')
            FROM nebula.plans p
            WHERE wr.plan_id = p.id
              AND (wr.title IS NULL OR wr.title = '')
              AND p.title IS NOT NULL AND p.title != '';
          END IF;
        END $$;
      `);

      console.log("[migrations] v23: Added title column to work_requests (conduit + vision)");
    },
  },
  {
    version: 24,
    description: "Expose status column in nebula.plans view (Architect gap #1) — add implementation_plan status (draft/pending/approved/work_requested/completed/archived) so consumers can query plan lifecycle stage without joining to nebula.implementation_plans",
    up: async (exec) => {
      // Add status column to nebula.plans view.
      // IMPORTANT: Must match existing column order exactly (created_at,
      // updated_at, deleted) and append status at the END. CREATE OR REPLACE
      // VIEW cannot change column names — any reordering is interpreted as a
      // rename and rejected.
      await exec(`
        CREATE OR REPLACE VIEW nebula.plans AS
        SELECT
          plan_number AS id,
          ''::text AS file_name,
          title,
          'wrp'::text AS project,
          COALESCE(goal, ''::text) AS goal,
          COALESCE(content, ''::text) AS content,
          COALESCE(array_to_string(files_affected, ','::text), ''::text) AS files_affected,
          COALESCE(acceptance_criteria::text, '[]'::text) AS acceptance_criteria,
          COALESCE(array_to_string(dependencies, ','::text), ''::text) AS dependencies,
          ''::text AS prompt_ref,
          ''::text AS notes,
          0 AS priority,
          created_at::text AS created_at,
          updated_at::text AS updated_at,
          CASE
            WHEN status = 'archived' THEN 1
            ELSE 0
          END AS deleted,
          status
        FROM nebula.implementation_plans
      `);

      // plan_status / plans_by_status views are now owned by nebula-srv
      // (migration 040, 2026-07-25), which drops the legacy conduit views.
      // On DBs that still carry conduit.plan_status (pre-040), rebuild the
      // conduit.plans_by_status mirror to avoid the duplicate-column-name
      // ambiguity introduced by adding 'status' to nebula.plans above.
      // On current/fresh DBs the conduit views no longer exist — no-op.
      // The nebula.plans_by_status mirror is no longer created here.
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.views
            WHERE table_schema = 'conduit' AND table_name = 'plan_status'
          ) THEN
            DROP VIEW IF EXISTS conduit.plans_by_status CASCADE;
            CREATE VIEW conduit.plans_by_status AS
            SELECT
              ps.id,
              ps.file_name,
              ps.title,
              ps.project,
              ps.goal,
              ps.content,
              ps.files_affected,
              ps.acceptance_criteria,
              ps.dependencies,
              ps.prompt_ref,
              ps.notes,
              ps.priority,
              ps.created_at,
              ps.updated_at,
              ps.deleted,
              ps.derived_status AS status
            FROM conduit.plan_status ps;
          END IF;
        END $$;
      `);

      // NOTE: temporal.plans / temporal.plan_status mirror views were removed
      // (2026-08-07) — the temporal schema was eliminated long ago and the
      // guarded CREATE was dead code that never fired on any DB.
      console.log("[migrations] v24: Exposed status column in nebula.plans view");
    },
  },
  {
    version: 22,
    description: "Add 5th kind STUCK_PENDING_PLAN_AGE to vision.check_receipt_integrity() and parameterize the function with p_threshold_seconds (default 1800s) — surfaces plans stuck in pending with only a PLAN_CREATE receipt + open builder ticket past threshold (Plan 0175 follow-up)",
    up: async (exec) => {
      // v22 supersedes v20's function body with a 5th kind AND changes the
      // function signature from no-arg to (p_threshold_seconds int DEFAULT 1800).
      // The migration system treats each version as immutable history, so we
      // replace the function rather than edit v20 in place. The end state of
      // the function after v20+v22 is: kinds 1-4 from v20 + kind 5 from v22,
      // with a parameterized threshold.
      //
      //   5. STUCK_PENDING_PLAN_AGE — a plan that has ONLY PLAN_CREATE
      //      receipts (no progress receipts of any kind), with an open
      //      builder ticket, and the PLAN_CREATE receipt is older than
      //      p_threshold_seconds (default 1800s = 30 min). Gates on
      //      `deleted = 0` so already-cleaned plans do not re-fire after
      //      the cleanup script soft-deletes them.
      //
      // Signature change handling: CREATE OR REPLACE with a different
      // signature does NOT drop the old no-arg function. We must DROP both
      // possible old signatures (no-arg + int) before CREATE OR REPLACE,
      // otherwise a fresh-DB bootstrap (or any DB that previously ran v20)
      // will end up with two functions of different signatures and any
      // no-arg caller will fail with "function is not unique".
      //
      // Function signature now takes a threshold parameter so callers
      // (e.g. the cleanup script) can pass STUCK_PENDING_THRESHOLD_SECONDS
      // without SQL and Node drifting out of sync. The default keeps
      // existing no-arg callers (the verifier) compatible.
      //
      // Read-only invariant — never mutates state. Safe to call repeatedly.
      // The cleanup script `migrations/cleanup-stuck-pending-plans.js`
      // consumes this kind to take the actual cleanup action.

      // Pre-step: drop any old no-arg + int signatures so CREATE OR REPLACE
      // below is unambiguous. Both IF EXISTS so this migration is safe to
      // re-apply on a DB that already has the parameterized version.
      await exec(`DROP FUNCTION IF EXISTS vision.check_receipt_integrity()`);
      await exec(`DROP FUNCTION IF EXISTS vision.check_receipt_integrity(int)`);

      await exec(`
        CREATE OR REPLACE FUNCTION vision.check_receipt_integrity(
          p_threshold_seconds int DEFAULT 1800
        )
        RETURNS TABLE(
          kind text,
          plan_id text,
          ticket_id text,
          receipt_id text,
          detail text
        ) AS $FUNC$
        BEGIN
          /* ── 1. Stuck ticket on deleted plan with NO terminal receipt ── */
          RETURN QUERY
          SELECT
            'STUCK_OPEN_TICKET_NO_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) on deleted plan %s has no terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND NOT EXISTS (
              SELECT 1 FROM ${VISION_SCHEMA}.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 2. Receipt whose plan row is gone AND no requirements link ── */
          RETURN QUERY
          SELECT
            'ORPHAN_RECEIPT_NO_PLAN'::text AS kind,
            r.plan_id::text AS plan_id,
            NULL::text AS ticket_id,
            r.id::text AS receipt_id,
            format(
              'receipt %s type=%s plan=%s has no live nebula.plans row and no conduit_plan_id linkage',
              r.id, r.type, r.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.receipts r
          WHERE NOT EXISTS (
            SELECT 1 FROM nebula.plans p
            WHERE p.id = r.plan_id AND p.deleted = 0
          )
          AND NOT EXISTS (
            SELECT 1 FROM nebula.requirements req
            WHERE req.conduit_plan_id = r.plan_id
          );

          /* ── 3. Deleted plan still has open tickets DESPITE terminal receipt ── */
          RETURN QUERY
          SELECT
            'DELETED_PLAN_HAS_OPEN_TICKETS_AFTER_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) left open on deleted plan %s despite terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND EXISTS (
              SELECT 1 FROM ${VISION_SCHEMA}.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 4. Ticket references a plan that has NO nebula.plans row at all ── */
          RETURN QUERY
          SELECT
            'ORPHAN_TICKET_NO_PLAN'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) references plan %s which has no row in nebula.plans',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM ${VISION_SCHEMA}.tickets t
          LEFT JOIN nebula.plans p ON p.id = t.plan_id
          WHERE p.id IS NULL
            AND t.status IN ('open','claimed','stale','failed');

          /* ── 5. Stuck-pending plan: ONLY PLAN_CREATE receipt(s), open builder ticket, age > threshold ── */
          /* Threshold comes from p_threshold_seconds parameter (default 1800s = 30 min). */
          /* Gated on deleted=0 so already-cleaned plans do not re-fire after the cleanup script. */
          RETURN QUERY
          SELECT
            'STUCK_PENDING_PLAN_AGE'::text AS kind,
            p.id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'plan %s stuck pending: only PLAN_CREATE receipt(s) for %ss (threshold=%ss), open builder ticket %s',
              p.id,
              EXTRACT(EPOCH FROM NOW() - MIN(r.created_at))::int,
              p_threshold_seconds,
              t.id
            ) AS detail
          FROM nebula.plans p
          JOIN ${VISION_SCHEMA}.tickets t ON t.plan_id = p.id
          JOIN ${VISION_SCHEMA}.receipts r ON r.plan_id = p.id
          WHERE p.deleted = 0
            AND t.role = 'builder'
            AND t.status IN ('open','claimed','stale','failed')
            AND r.type = 'PLAN_CREATE'
            AND NOT EXISTS (
              SELECT 1 FROM ${VISION_SCHEMA}.receipts r2
              WHERE r2.plan_id = p.id
                AND r2.type != 'PLAN_CREATE'
            )
          GROUP BY p.id, t.id
          HAVING EXTRACT(EPOCH FROM NOW() - MIN(r.created_at))::int > p_threshold_seconds;
        END;
        $FUNC$ LANGUAGE plpgsql STABLE
      `);

      // Smoke-query: confirm Kind #5 is reachable with default threshold
      await exec(`
        SELECT count(*) FILTER (WHERE kind = 'STUCK_PENDING_PLAN_AGE') AS kind5_rows
        FROM vision.check_receipt_integrity()
      `);
    },
  },
  {
    version: 25,
    description: "Add FK constraints to tackle.config_bundle, agent_scheduler, sessions, and role_memory referencing tackle.roles(name) — idempotent, safe for fresh DB and existing DB",
    up: async (exec) => {
      // Seed default tackle roles (idempotent — mirrors tackle-mcp/src/db.ts seedDefaultRoles)
      // Needed for fresh-DB standalone operation (conduit-mcp creates tackle schema tables)
      const now = new Date().toISOString();
      const defaultRoles = [
        { name: "engineer", desc: "Primary implementation agent — writes code, runs commands, integrates systems" },
        { name: "architect", desc: "System design authority — owns architecture decisions, cross-system contracts, and design lineage" },
        { name: "planner", desc: "Work decomposition authority — creates and manages implementation plans, promotes proposals" },
        { name: "builder", desc: "Implementation executor — picks up pending plans and implements them against acceptance criteria" },
        { name: "reviewer", desc: "Quality gate — reviews changes, issues approval/rejection receipts" },
        { name: "critic", desc: "Adversarial evaluator — surfaces risks, contradictions, and blind spots" },
        { name: "analyst", desc: "Gap and triage analyst — identifies missing coverage, classifies incidents" },
        { name: "inspector", desc: "Compliance auditor — verifies invariants, issues violation reports" },
        { name: "auditor", desc: "Audit and compliance reviewer — verifies records, constraints, and drift; issues inspection findings" },
        { name: "epistemologist", desc: "Epistemic governance — tracks knowledge stratification, role boundaries, and cross-role divergence" },
        { name: "operator", desc: "Pipeline and platform operator — monitors pipeline state, investigates stuck plans and drift, keeps operational surfaces healthy" },
        { name: "sysadmin", desc: "Infrastructure health governance — systemd-timer cycles, service health, incident reporting; runs standalone" },
        { name: "test", desc: "Internal test harness role — used for test invoke sessions and ad-hoc agent runs" },
        { name: "tester", desc: "Walkthrough role (b80f0fdb) — full-surface demonstration of the role-creation runbook" },
      ];
      for (const r of defaultRoles) {
        await exec(
          `INSERT INTO tackle.roles (name, description, created_at, updated_at)
           VALUES ($1, $2, $3, $3)
           ON CONFLICT (name) DO NOTHING`,
          [r.name, r.desc, now]
        );
      }

      // Add FK constraints idempotently (mirrors tackle-mcp/src/db.ts lines 294-330)
      await exec(`
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_config_bundle_role') THEN
            ALTER TABLE tackle.config_bundle
              ADD CONSTRAINT fk_config_bundle_role
              FOREIGN KEY (role) REFERENCES tackle.roles(name);
          END IF;

          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_scheduler_role') THEN
            ALTER TABLE tackle.agent_scheduler
              ADD CONSTRAINT fk_agent_scheduler_role
              FOREIGN KEY (role) REFERENCES tackle.roles(name);
          END IF;

          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_role_memory_role') THEN
            ALTER TABLE tackle.role_memory
              ADD CONSTRAINT fk_role_memory_role
              FOREIGN KEY (role) REFERENCES tackle.roles(name);
          END IF;

          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sessions_agent_role') THEN
            ALTER TABLE tackle.sessions
              ADD CONSTRAINT fk_sessions_agent_role
              FOREIGN KEY (agent_role) REFERENCES tackle.roles(name);
          END IF;
        END $$;
      `);

      console.log("[migrations] v25: FK constraints added to tackle schema (config_bundle, agent_scheduler, sessions, role_memory → roles.name)");
    },
  },
  {
    version: 26,
    description: "Add missing PRIMARY KEY constraints to vision.receipts, vision.vision_ir_artifacts, and peb.role_circuit_breaker (idempotent — safe for fresh and legacy DBs)",
    up: async (exec) => {
      await exec(`
        DO $$
        BEGIN
          -- vision.receipts.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipts_pkey'
            AND conrelid = '${VISION_SCHEMA}.receipts'::regclass) THEN
            ALTER TABLE ${VISION_SCHEMA}.receipts ADD PRIMARY KEY (id);
          END IF;
          -- vision.vision_ir_artifacts.artifact_id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vision_ir_artifacts_pkey'
            AND conrelid = '${VISION_SCHEMA}.vision_ir_artifacts'::regclass) THEN
            ALTER TABLE ${VISION_SCHEMA}.vision_ir_artifacts ADD PRIMARY KEY (artifact_id);
          END IF;
          -- peb.role_circuit_breaker.role PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'role_circuit_breaker_pkey'
            AND conrelid = '${PEB_SCHEMA}.role_circuit_breaker'::regclass) THEN
            ALTER TABLE ${PEB_SCHEMA}.role_circuit_breaker ADD PRIMARY KEY (role);
          END IF;
        END $$;
      `);
      console.log("[migrations] v26: Added PKs to vision.receipts, vision.vision_ir_artifacts, peb.role_circuit_breaker");
    },
  },
  {
    version: 27,
    description: "Migrate TEXT timestamp columns to TIMESTAMPTZ — conduit core tables and tackle shared tables (providers, harnesses, models, config_bundle)",
    up: async (exec) => {
      const tables: [string, string[]][] = [
        ["conduit.plans", ["created_at", "updated_at"]],
        ["conduit.schema_version", ["applied_at"]],
        ["conduit.sessions", ["created_at", "start_iso", "end_iso", "last_heartbeat_at", "last_activity", "workflow_start_time", "workflow_close_time"]],
        ["conduit.circuit_breaker", ["tripped_at", "updated_at", "wake_requested_at"]],
        ["conduit.work_requests", ["created_at", "updated_at"]],
        ["tackle.providers", ["created_at", "updated_at"]],
        ["tackle.harnesses", ["created_at", "updated_at"]],
        ["tackle.models", ["created_at", "updated_at"]],
        ["tackle.config_bundle", ["created_at", "updated_at", "valid_from", "valid_to"]],
      ];
      for (const [tbl, cols] of tables) {
        const [sch, tname] = tbl.split('.');
        for (const col of cols) {
          await exec(`
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '${sch}'
                AND table_name = '${tname}'
                AND column_name = '${col}'
                AND data_type = 'text'
              ) THEN
                ALTER TABLE ${tbl} ALTER COLUMN ${col} TYPE TIMESTAMPTZ USING CASE WHEN ${col} = '' THEN NULL WHEN ${col} ~ '[+-]\\d{2}:\\d{2}Z$' THEN REPLACE(${col}, 'Z', '')::timestamptz ELSE ${col}::timestamptz END;
              END IF;
            END $$;
          `);
        }
      }
      console.log("[migrations] v27: Migrated TEXT→TIMESTAMPTZ for conduit core + tackle shared tables");
    },
  },
  {
    version: 28,
    description: "Migrate TEXT timestamp columns to TIMESTAMPTZ — conduit utility tables (cost_logs, model_pricing, agent_budgets, pipeline_cursor, role_circuit_breaker)",
    up: async (exec) => {
      // role_circuit_breaker.created_at has DEFAULT ''::text — drop it before ALTER
      await exec(`ALTER TABLE conduit.role_circuit_breaker ALTER COLUMN created_at DROP DEFAULT`);

      const tables: [string, string[]][] = [
        ["conduit.cost_logs", ["recorded_at"]],
        ["conduit.model_pricing", ["updated_at"]],
        ["conduit.agent_budgets", ["reset_at", "updated_at"]],
        ["conduit.pipeline_cursor", ["updated_at"]],
        ["conduit.role_circuit_breaker", ["created_at", "tripped_at", "updated_at"]],
      ];
      for (const [tbl, cols] of tables) {
        const [sch, tname] = tbl.split('.');
        for (const col of cols) {
          await exec(`
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '${sch}'
                AND table_name = '${tname}'
                AND column_name = '${col}'
                AND data_type = 'text'
              ) THEN
                ALTER TABLE ${tbl} ALTER COLUMN ${col} TYPE TIMESTAMPTZ USING CASE WHEN ${col} = '' THEN NULL WHEN ${col} ~ '[+-]\\d{2}:\\d{2}Z$' THEN REPLACE(${col}, 'Z', '')::timestamptz ELSE ${col}::timestamptz END;
              END IF;
            END $$;
          `);
        }
      }

      // Restore proper NOW() default
      await exec(`ALTER TABLE conduit.role_circuit_breaker ALTER COLUMN created_at SET DEFAULT NOW()`);

      console.log("[migrations] v28: Migrated TEXT→TIMESTAMPTZ for conduit utility tables");
    },
  },
  {
    version: 29,
    description: "Migrate TEXT timestamp columns to TIMESTAMPTZ — conduit kernel/log tables (kernel_delta_log, kernel_snapshot, lineage_log, bridge_checkpoint)",
    up: async (exec) => {
      // bridge_checkpoint.last_recorded_on_dt has DEFAULT ''::text — drop it before ALTER
      await exec(`ALTER TABLE conduit.bridge_checkpoint ALTER COLUMN last_recorded_on_dt DROP DEFAULT`);

      // kernel tables have to_char(now(),...)::text defaults — drop before ALTER
      await exec(`ALTER TABLE conduit.kernel_delta_log ALTER COLUMN created_at DROP DEFAULT`);
      await exec(`ALTER TABLE conduit.kernel_snapshot ALTER COLUMN created_at DROP DEFAULT`);
      await exec(`ALTER TABLE conduit.lineage_log ALTER COLUMN created_at DROP DEFAULT`);

      const tables: [string, string[]][] = [
        ["conduit.kernel_delta_log", ["created_at"]],
        ["conduit.kernel_snapshot", ["created_at"]],
        ["conduit.lineage_log", ["created_at"]],
        ["conduit.bridge_checkpoint", ["last_recorded_on_dt"]],
      ];
      for (const [tbl, cols] of tables) {
        const [sch, tname] = tbl.split('.');
        for (const col of cols) {
          await exec(`
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '${sch}'
                AND table_name = '${tname}'
                AND column_name = '${col}'
                AND data_type = 'text'
              ) THEN
                ALTER TABLE ${tbl} ALTER COLUMN ${col} TYPE TIMESTAMPTZ USING CASE WHEN ${col} = '' THEN NULL WHEN ${col} ~ '[+-]\\d{2}:\\d{2}Z$' THEN REPLACE(${col}, 'Z', '')::timestamptz ELSE ${col}::timestamptz END;
              END IF;
            END $$;
          `);
        }
      }
      // Restore proper NOW() defaults on kernel tables
      await exec(`ALTER TABLE conduit.kernel_delta_log ALTER COLUMN created_at SET DEFAULT NOW()`);
      await exec(`ALTER TABLE conduit.kernel_snapshot ALTER COLUMN created_at SET DEFAULT NOW()`);
      await exec(`ALTER TABLE conduit.lineage_log ALTER COLUMN created_at SET DEFAULT NOW()`);

      console.log("[migrations] v29: Migrated TEXT→TIMESTAMPTZ for conduit kernel/log tables");
    },
  },
  {
    version: 30,
    description: "Migrate TEXT timestamp columns to TIMESTAMPTZ — vision and peb tables (receipts, tickets, role_circuit_breaker)",
        up: async (exec) => {
      // conduit.plan_status VIEW depends on vision.receipts.created_at —
      // must drop it before altering the column type, then recreate after.
      // NOTE: since nebula-srv migration 040 (2026-07-25), the plan_status /
      // plans_by_status views are owned by nebula and the conduit ones are
      // dropped. Only legacy DBs that still carry conduit.plan_status get the
      // drop/recreate cycle; fresh DBs must NOT recreate views in the shared
      // conduit schema (they live in nebula now).
      const legacyView = await exec(`
        SELECT 1 FROM information_schema.views
        WHERE table_schema = 'conduit' AND table_name = 'plan_status'
        LIMIT 1
      `);
      const hasLegacyConduitView = (legacyView?.rows?.length ?? 0) > 0;
      if (hasLegacyConduitView) {
        await exec(`DROP VIEW IF EXISTS conduit.plan_status CASCADE`);
      }

      const tables: [string, string[]][] = [
        ["vision.receipts", ["created_at"]],
        ["vision.tickets", ["created_at", "claimed_at", "closed_at", "expires_at"]],
        ["peb.role_circuit_breaker", ["tripped_at", "updated_at"]],
      ];
      for (const [tbl, cols] of tables) {
        const [sch, tname] = tbl.split('.');
        for (const col of cols) {
          await exec(`
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '${sch}'
                AND table_name = '${tname}'
                AND column_name = '${col}'
                AND data_type = 'text'
              ) THEN
                ALTER TABLE ${tbl} ALTER COLUMN ${col} TYPE TIMESTAMPTZ USING CASE WHEN ${col} = '' THEN NULL WHEN ${col} ~ '[+-]\\d{2}:\\d{2}Z$' THEN REPLACE(${col}, 'Z', '')::timestamptz ELSE ${col}::timestamptz END;
              END IF;
            END $$;
          `);
        }
      }

      // Recreate the plan_status and plans_by_status views (legacy path only —
      // fresh DBs get them from nebula-srv migration 040)
      if (hasLegacyConduitView) {
        await exec(`
          CREATE VIEW conduit.plan_status AS
          SELECT
            p.*,
            CASE
              WHEN EXISTS (
                SELECT 1 FROM vision.receipts r WHERE r.plan_id = p.id AND r.type = 'HOLD'
                AND NOT EXISTS (
                  SELECT 1 FROM vision.receipts r2
                  WHERE r2.plan_id = p.id
                  AND r2.type IN ('CANCELLED', 'ABANDONED')
                  AND r2.created_at > r.created_at
                )
              ) THEN 'HOLD'
              WHEN (
                SELECT r.type FROM vision.receipts r
                WHERE r.plan_id = p.id
                AND r.type NOT IN ('PLANNING', 'HOLD')
                ORDER BY r.created_at DESC LIMIT 1
              ) = 'REQUEUED' THEN 'PLAN_CREATE'
              WHEN EXISTS (
                SELECT 1 FROM vision.receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_PASS'
                AND NOT EXISTS (
                  SELECT 1 FROM vision.receipts r2
                  WHERE r2.plan_id = p.id
                  AND r2.type IN ('BLOCK', 'PLAN_BLOCK', 'CANCELLED', 'ABANDONED')
                  AND r2.created_at > r.created_at
                )
              ) THEN 'REVIEW_PASS'
              WHEN EXISTS (
                SELECT 1 FROM vision.receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_REJECT'
              ) THEN COALESCE(
                (SELECT r.type FROM vision.receipts r
                 WHERE r.plan_id = p.id
                 AND r.type != 'BLOCK'
                 ORDER BY r.created_at DESC LIMIT 1),
                'PLAN_CREATE'
              )
              ELSE COALESCE(
                (SELECT r.type FROM vision.receipts r
                 WHERE r.plan_id = p.id
                 AND r.type NOT IN ('PLANNING', 'HOLD')
                 ORDER BY r.created_at DESC LIMIT 1),
                (SELECT r.type FROM vision.receipts r
                 WHERE r.plan_id = p.id
                 ORDER BY r.created_at DESC LIMIT 1),
                NULL
              )
            END AS derived_status
          FROM nebula.plans p
          WHERE p.deleted = 0
        `);

        await exec(`
          CREATE VIEW conduit.plans_by_status AS
          SELECT
            ps.id, ps.file_name, ps.title, ps.project, ps.goal, ps.content,
            ps.files_affected, ps.acceptance_criteria, ps.dependencies,
            ps.prompt_ref, ps.notes, ps.priority, ps.deleted,
            ps.created_at, ps.updated_at, ps.derived_status AS status
          FROM conduit.plan_status ps
        `);
      }

      console.log("[migrations] v30: Migrated TEXT→TIMESTAMPTZ for vision + peb tables");
    },
  },
  {
    version: 31,
    description: "Migrate vision.tickets.deadline TEXT → TIMESTAMPTZ (missed by original audit — naming pattern didn't match _at/_iso/_dt)",
    up: async (exec) => {
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'vision'
            AND table_name = 'tickets'
            AND column_name = 'deadline'
            AND data_type = 'text'
          ) THEN
            ALTER TABLE vision.tickets ALTER COLUMN deadline TYPE TIMESTAMPTZ USING NULLIF(deadline, '')::timestamptz;
          END IF;
        END $$;
      `);
      console.log("[migrations] v31: Migrated vision.tickets.deadline TEXT→TIMESTAMPTZ");
    },
  },
  // ── Execution Authority Schema (ADR-006) ──────────────────────────
  {
    version: 32,
    description: "Create execution schema and four durable nouns: requests, leases, attempts, receipts (ADR-006 Execution Authority Protocol)",
    up: async (exec) => {
      await exec(`
        BEGIN;

        CREATE SCHEMA IF NOT EXISTS execution;

        -- execution.requests — WorkRequest (immutable intent)
        CREATE TABLE IF NOT EXISTS execution.requests (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            business_key    TEXT UNIQUE NOT NULL,
            title           TEXT NOT NULL DEFAULT '',
            intent_type     TEXT NOT NULL DEFAULT 'task',
            objective       TEXT NOT NULL DEFAULT '',
            inputs          JSONB NOT NULL DEFAULT '{}'::jsonb,
            deterministic   BOOLEAN NOT NULL DEFAULT TRUE,
            max_retries     INTEGER,
            timeout_policy  TEXT,
            resource_hints  TEXT[] DEFAULT '{}',
            op_trace        JSONB NOT NULL DEFAULT '{}'::jsonb,
            status          TEXT NOT NULL DEFAULT 'DRAFT'
                            CHECK (status IN (
                                'DRAFT','COMPILED','VALIDATED',
                                'ADMITTED','READY',
                                'COMPLETED','FAILED','CANCELLED'
                            )),
            source_plan_id  TEXT,
            source_wr_id    TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_execution_requests_status
            ON execution.requests (status);
        CREATE INDEX IF NOT EXISTS idx_execution_requests_source_plan
            ON execution.requests (source_plan_id)
            WHERE source_plan_id IS NOT NULL;

        -- execution.leases — temporal permission to execute
        CREATE TABLE IF NOT EXISTS execution.leases (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id      UUID NOT NULL REFERENCES execution.requests(id),
            executor_id     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'ACTIVE'
                            CHECK (status IN ('ACTIVE','EXPIRED','RELEASED')),
            ttl_seconds     INTEGER NOT NULL DEFAULT 300,
            acquired_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMPTZ NOT NULL,
            released_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_leases_active_per_request
            ON execution.leases (request_id)
            WHERE status = 'ACTIVE';
        CREATE INDEX IF NOT EXISTS idx_execution_leases_request
            ON execution.leases (request_id);
        CREATE INDEX IF NOT EXISTS idx_execution_leases_executor
            ON execution.leases (executor_id);

        -- execution.attempts — one run of the work
        CREATE TABLE IF NOT EXISTS execution.attempts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lease_id        UUID NOT NULL REFERENCES execution.leases(id),
            request_id      UUID NOT NULL REFERENCES execution.requests(id),
            executor_id     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'CREATED'
                            CHECK (status IN ('CREATED','RUNNING','SUCCEEDED','FAILED','TIMED_OUT')),
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            result          JSONB NOT NULL DEFAULT '{}'::jsonb,
            error           TEXT,
            exit_code       INTEGER,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_execution_attempts_request
            ON execution.attempts (request_id);
        CREATE INDEX IF NOT EXISTS idx_execution_attempts_lease
            ON execution.attempts (lease_id);
        CREATE INDEX IF NOT EXISTS idx_execution_attempts_status
            ON execution.attempts (status);

        -- execution.receipts — immutable evidence (ADR-006 noun #4)
        CREATE TABLE IF NOT EXISTS execution.receipts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            attempt_id          UUID NOT NULL REFERENCES execution.attempts(id),
            request_id          UUID NOT NULL REFERENCES execution.requests(id),
            type                TEXT NOT NULL,
            agent_role          TEXT NOT NULL DEFAULT '',
            summary             TEXT NOT NULL DEFAULT '',
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            lineage_source      TEXT,
            lineage_original_id TEXT,
            issued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_execution_receipts_request
            ON execution.receipts (request_id);
        CREATE INDEX IF NOT EXISTS idx_execution_receipts_attempt
            ON execution.receipts (attempt_id);
        CREATE INDEX IF NOT EXISTS idx_execution_receipts_type
            ON execution.receipts (type);

        -- updated_at trigger
        CREATE OR REPLACE FUNCTION execution.set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_execution_requests_updated_at ON execution.requests;
        CREATE TRIGGER trg_execution_requests_updated_at
            BEFORE UPDATE ON execution.requests
            FOR EACH ROW
            EXECUTE FUNCTION execution.set_updated_at();

        COMMIT;
      `);
      console.log("[migrations] v32: Created execution schema (requests, leases, attempts, receipts) — ADR-006");
    },
  },
  {
    version: 33,
    description: "Migrate vision.receipts → execution.receipts with lineage tracking (ADR-006 receipt migration)",
    up: async (exec) => {
      // Create legacy execution.requests for each plan with receipts
      await exec(`
        INSERT INTO execution.requests (
            business_key, title, intent_type, objective, status,
            source_plan_id, created_at, updated_at
        )
        SELECT
            'legacy-plan-' || p.id AS business_key,
            COALESCE(p.title, 'Legacy plan ' || p.id) AS title,
            'legacy' AS intent_type,
            COALESCE(p.goal, '') AS objective,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM vision.receipts r2
                    WHERE r2.plan_id = p.id AND r2.type = 'REVIEW_PASS'
                ) THEN 'COMPLETED'
                WHEN EXISTS (
                    SELECT 1 FROM vision.receipts r3
                    WHERE r3.plan_id = p.id AND r3.type IN ('CANCELLED','ABANDONED')
                ) THEN 'CANCELLED'
                ELSE 'READY'
            END AS status,
            p.id AS source_plan_id,
            MIN(r.created_at) AS created_at,
            MAX(r.created_at) AS updated_at
        FROM nebula.plans p
        -- C5 (Lilac plan 8261639): read via the unified projection, not the
        -- legacy table — same rows during dual-read, survives the C6 re-point.
        JOIN nebula.receipts_unified r ON r.plan_id = p.id
        WHERE NOT EXISTS (
            SELECT 1 FROM execution.requests er
            WHERE er.source_plan_id = p.id
        )
        GROUP BY p.id, p.title, p.goal
      `);

      // Create synthetic leases for legacy attempts
      await exec(`
        INSERT INTO execution.leases (
            request_id, executor_id, status, ttl_seconds,
            acquired_at, expires_at, released_at, created_at
        )
        SELECT
            er.id AS request_id,
            'legacy' AS executor_id,
            'RELEASED' AS status,
            0 AS ttl_seconds,
            er.created_at AS acquired_at,
            er.created_at AS expires_at,
            er.updated_at AS released_at,
            er.created_at AS created_at
        FROM execution.requests er
        WHERE er.source_plan_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM execution.leases el
            WHERE el.request_id = er.id
          )
      `);

      // Create legacy execution.attempts
      await exec(`
        INSERT INTO execution.attempts (
            lease_id, request_id, executor_id, status,
            started_at, completed_at, created_at
        )
        SELECT
            el.id AS lease_id,
            er.id AS request_id,
            'legacy' AS executor_id,
            CASE
                WHEN er.status = 'COMPLETED' THEN 'SUCCEEDED'
                WHEN er.status = 'CANCELLED' THEN 'FAILED'
                ELSE 'RUNNING'
            END AS status,
            er.created_at AS started_at,
            er.updated_at AS completed_at,
            er.created_at AS created_at
        FROM execution.requests er
        JOIN execution.leases el ON el.request_id = er.id
        WHERE er.source_plan_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM execution.attempts ea
            WHERE ea.request_id = er.id
          )
      `);

      // Migrate vision.receipts → execution.receipts
      await exec(`
        INSERT INTO execution.receipts (
            attempt_id, request_id, type, agent_role,
            summary, metadata, lineage_source, lineage_original_id,
            issued_at
        )
        SELECT
            ea.id AS attempt_id,
            er.id AS request_id,
            vr.type AS type,
            vr.agent_role AS agent_role,
            COALESCE(vr.summary, '') AS summary,
            COALESCE(vr.metadata_json::jsonb, '{}'::jsonb) AS metadata,
            'vision.receipts' AS lineage_source,
            vr.id AS lineage_original_id,
            vr.created_at AS issued_at
        FROM vision.receipts vr
        JOIN execution.requests er ON er.source_plan_id = vr.plan_id
        JOIN execution.attempts ea ON ea.request_id = er.id
        WHERE NOT EXISTS (
            SELECT 1 FROM execution.receipts er2
            WHERE er2.lineage_original_id = vr.id
        )
      `);

      // Log counts
      const result = await exec(`
        SELECT
          (SELECT count(*) FROM vision.receipts) AS vision_count,
          (SELECT count(*) FROM execution.receipts) AS execution_count,
          (SELECT count(*) FROM execution.requests WHERE source_plan_id IS NOT NULL) AS request_count
      `);
      const row = result?.rows?.[0];
      console.log(`[migrations] v33: Migrated vision.receipts → execution.receipts (${row?.execution_count || 0} of ${row?.vision_count || 0}, ${row?.request_count || 0} legacy requests)`);
    },
  },
  // ── v34: Corrected receipt migration (nebula.plans, not conduit.plans) ──
  {
    version: 34,
    description: "Re-run receipt migration using nebula.plans (conduit.plans was empty)",
    up: async (exec) => {
      // Check if v33 already migrated data
      const check = await exec(`SELECT count(*) AS cnt FROM execution.receipts WHERE lineage_source = 'vision.receipts'`);
      const existingCount = check?.rows?.[0]?.cnt ?? 0;
      if (existingCount > 0) {
        console.log(`[migrations] v34: Skipping — ${existingCount} receipts already migrated by v33`);
        return;
      }

      console.log('[migrations] v34: Re-running receipt migration with nebula.plans...');

      // Create legacy requests from nebula.plans (not conduit.plans)
      await exec(`
        INSERT INTO execution.requests (
            business_key, title, intent_type, objective, status,
            source_plan_id, created_at, updated_at
        )
        SELECT
            'legacy-plan-' || p.id AS business_key,
            COALESCE(p.title, 'Legacy plan ' || p.id) AS title,
            'legacy' AS intent_type,
            COALESCE(p.goal, '') AS objective,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM vision.receipts r2
                    WHERE r2.plan_id = p.id AND r2.type = 'REVIEW_PASS'
                ) THEN 'COMPLETED'
                WHEN EXISTS (
                    SELECT 1 FROM vision.receipts r3
                    WHERE r3.plan_id = p.id AND r3.type IN ('CANCELLED','ABANDONED')
                ) THEN 'CANCELLED'
                ELSE 'READY'
            END AS status,
            p.id AS source_plan_id,
            MIN(r.created_at) AS created_at,
            MAX(r.created_at) AS updated_at
        FROM nebula.plans p
        -- C5 (Lilac plan 8261639): read via the unified projection, not the
        -- legacy table — same rows during dual-read, survives the C6 re-point.
        JOIN nebula.receipts_unified r ON r.plan_id = p.id
        WHERE NOT EXISTS (
            SELECT 1 FROM execution.requests er
            WHERE er.source_plan_id = p.id
        )
        GROUP BY p.id, p.title, p.goal
      `);

      // Create synthetic leases for legacy requests
      await exec(`
        INSERT INTO execution.leases (
            request_id, executor_id, status, ttl_seconds,
            acquired_at, expires_at, released_at, created_at
        )
        SELECT
            er.id, 'legacy', 'RELEASED', 0,
            er.created_at, er.created_at, er.updated_at, er.created_at
        FROM execution.requests er
        WHERE er.source_plan_id IS NOT NULL
          AND er.intent_type = 'legacy'
          AND NOT EXISTS (
            SELECT 1 FROM execution.leases el WHERE el.request_id = er.id
          )
      `);

      // Create legacy attempts
      await exec(`
        INSERT INTO execution.attempts (
            lease_id, request_id, executor_id, status,
            started_at, completed_at, created_at
        )
        SELECT
            el.id, er.id, 'legacy',
            CASE
                WHEN er.status = 'COMPLETED' THEN 'SUCCEEDED'
                WHEN er.status = 'CANCELLED' THEN 'FAILED'
                ELSE 'RUNNING'
            END,
            er.created_at, er.updated_at, er.created_at
        FROM execution.requests er
        JOIN execution.leases el ON el.request_id = er.id
        WHERE er.source_plan_id IS NOT NULL
          AND er.intent_type = 'legacy'
          AND NOT EXISTS (
            SELECT 1 FROM execution.attempts ea WHERE ea.request_id = er.id
          )
      `);

      // Migrate vision.receipts → execution.receipts
      await exec(`
        INSERT INTO execution.receipts (
            attempt_id, request_id, type, agent_role,
            summary, metadata, lineage_source, lineage_original_id,
            issued_at
        )
        SELECT
            ea.id, er.id, vr.type, vr.agent_role,
            COALESCE(vr.summary, ''),
            COALESCE(vr.metadata_json::jsonb, '{}'::jsonb),
            'vision.receipts', vr.id, vr.created_at
        FROM vision.receipts vr
        JOIN execution.requests er ON er.source_plan_id = vr.plan_id
        JOIN execution.attempts ea ON ea.request_id = er.id
        WHERE NOT EXISTS (
            SELECT 1 FROM execution.receipts er2
            WHERE er2.lineage_original_id = vr.id
        )
      `);

      // Log counts
      const result = await exec(`
        SELECT
          (SELECT count(*) FROM vision.receipts) AS vision_count,
          (SELECT count(*) FROM execution.receipts WHERE lineage_source = 'vision.receipts') AS execution_count,
          (SELECT count(*) FROM execution.requests WHERE source_plan_id IS NOT NULL AND intent_type = 'legacy') AS request_count
      `);
      const row = result?.rows?.[0];
      console.log(`[migrations] v34: Migrated vision.receipts → execution.receipts (${row?.execution_count || 0} of ${row?.vision_count || 0}, ${row?.request_count || 0} legacy requests)`);
    },
  },
  // ── v35: Add missing PRIMARY KEY to vision.tickets ──
  {
    version: 35,
    description: "Add missing PRIMARY KEY to vision.tickets (lost when table was created externally without PK)",
    up: async (exec) => {
      // Check if PK already exists
      const check = await exec(`
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'vision.tickets'::regclass
        AND contype = 'p'
        LIMIT 1
      `);
      if (check?.rows?.length > 0) {
        console.log('[migrations] v35: Skipping — vision.tickets PRIMARY KEY already exists');
        return;
      }

      console.log('[migrations] v35: Adding PRIMARY KEY to vision.tickets...');
      await exec(`ALTER TABLE vision.tickets ADD PRIMARY KEY (id)`);
      console.log('[migrations] v35: PRIMARY KEY added to vision.tickets');
    },
  },
  {
    version: 36,
    description: "config_bundle: add INTERACTIVE invocation mode for Freebuff-hosted roles (leased-builder) + harn-freebuff harness",
    up: async (exec) => {
      // 1. Extend the invocation_mode CHECK to allow INTERACTIVE.
      //    Freebuff-hosted roles (interactive channel) are never launched —
      //    Freebuff itself is the execution host. INTERACTIVE marks them
      //    so launch paths (harness-srv /run, agent_scheduler) refuse to spawn.
      await exec(`
        ALTER TABLE tackle.config_bundle
          DROP CONSTRAINT IF EXISTS config_bundle_invocation_mode_check
      `);
      await exec(`
        ALTER TABLE tackle.config_bundle
          ADD CONSTRAINT config_bundle_invocation_mode_check
          CHECK (invocation_mode IN ('CLI', 'HTTP', 'SDK', 'MCP', 'INTERACTIVE'))
      `);

      // 2. Seed the Freebuff-hosted harness — semantics declare NO binary:
      //    execution.mode = hosted, host = freebuff. Anything that reads
      //    invocation_semantics can see this is not launchable.
      await exec(`
        INSERT INTO tackle.harnesses (id, name, invocation_semantics, created_at, updated_at)
        VALUES (
          'harn-freebuff',
          'Freebuff (interactive host)',
          '{"binary":null,"capabilities":{"agent":true,"model":true,"working_directory":false,"system_prompt":true},"execution":{"mode":"hosted","host":"freebuff"},"semantics":{},"role_mapping":{"strategy":"none"}}',
          NOW(), NOW()
        )
        ON CONFLICT (id) DO NOTHING
      `);

      // 3. Seed leased-builder's interactive config bundle.
      //    invocation_mode=INTERACTIVE + harness_id=harn-freebuff means:
      //    model resolution still works (role_lease accounting), but no
      //    launch path may spawn this role.
      //    The role itself must exist first: fk_config_bundle_role
      //    (v25) references tackle.roles(name), and 'leased-builder'
      //    is not in v25's default role list. On a fresh DB this insert
      //    otherwise violates the FK (found by the CI hermetic bootstrap).
      await exec(`
        INSERT INTO tackle.roles (name, description, created_at, updated_at)
        VALUES (
          'leased-builder',
          'Interactive-hosted builder role — leased via role lease; never binary-launched',
          NOW(), NOW()
        )
        ON CONFLICT (name) DO NOTHING
      `);
      await exec(`
        INSERT INTO tackle.config_bundle
          (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
        VALUES (
          'cb-leased-builder-interactive',
          'Leased-Builder (Freebuff interactive)',
          'leased-builder',
          'mod-glm-5-2',
          NULL,
          'harn-freebuff',
          0,
          'INTERACTIVE',
          1,
          '{"channel":"interactive","host":"freebuff"}',
          NOW(), NOW()
        )
        ON CONFLICT (role, model_id) DO NOTHING
      `);
    },
  },
  {
    version: 37,
    description: "Add entity_key (CCNF content identity) to work_requests (vision + conduit) — roadmap Q4: every WR gets content identity at creation",
    up: async (exec) => {
      // entity_key = SHA256 over sorted {domain, intent, actor, scope} per
      // go/wrp/ccnf-ref (DeriveIdentity). Pure-Python mirror lives in
      // nexus_core/wrp/identity.py; cross-language parity guarded by wr-conf-010.
      // Column is nullable: legacy rows predate compile-time emission; new WRs
      // get it from birth via vision_bridge.create_work_request.
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.work_requests
          ADD COLUMN IF NOT EXISTS entity_key TEXT
      `);
      // The legacy ${PG_SCHEMA}.work_requests ("conduit" in production) is a
      // pre-split table that does NOT exist on a fresh schema (the test
      // harness runs initDb against an empty test schema). Guard with an
      // existence check so this migration stays replay-safe — same pattern as
      // the v6 step_outputs guard.
      await exec(`
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = '${PG_SCHEMA}' AND table_name = 'work_requests'
          ) THEN
            ALTER TABLE ${PG_SCHEMA}.work_requests
              ADD COLUMN IF NOT EXISTS entity_key TEXT;
          END IF;
        END $$;
      `);
      console.log("[migrations] v37: added entity_key to work_requests (vision + conduit)");
    },
  },
  {
    version: 38,
    description: "Create vision.wr_compile_verdicts (R-A-2026-08-15-010 option c) — WR-scoped compile verdict store keyed by entityKey",
    up: async (exec) => {
      // D2/D5 (amended): WR_COMPILE_PASS/FAIL verdicts live in a dedicated,
      // entityKey-keyed store — NOT vision.receipts (frozen, D-T19-2(d)) and
      // NOT execution.receipts (request/attempt-scoped, ADR-006). The verdict
      // exists pre-plan and pre-attempt; its identity is the compile unit
      // (entityKey, D3). Insert-only: deterministic verdict_id + idempotent
      // INSERT ... ON CONFLICT DO NOTHING. Immutability guard rejects
      // UPDATE/DELETE (mirrors execution.receipts' immutable-evidence intent).
      await exec(`
        CREATE TABLE IF NOT EXISTS ${VISION_SCHEMA}.wr_compile_verdicts (
            verdict_id   TEXT PRIMARY KEY,          -- SHA256(type, entity_key, rule_version, description)
            entity_key   TEXT NOT NULL,             -- compile-unit identity (D3)
            wr_id        TEXT,                      -- WR row link when present (nullable: pure-compile mode)
            plan_id      TEXT,                      -- re-parented at release via entityKey (D2 semantic)
            verdict_type TEXT NOT NULL CHECK (verdict_type IN ('WR_COMPILE_PASS','WR_COMPILE_FAIL')),
            rule_version TEXT NOT NULL,
            description  TEXT NOT NULL,
            detected_at  TIMESTAMPTZ,               -- compile event timestamp (deterministic)
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS idx_wr_compile_verdicts_entity_key
            ON ${VISION_SCHEMA}.wr_compile_verdicts (entity_key, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wr_compile_verdicts_wr_id
            ON ${VISION_SCHEMA}.wr_compile_verdicts (wr_id);
        CREATE INDEX IF NOT EXISTS idx_wr_compile_verdicts_plan
            ON ${VISION_SCHEMA}.wr_compile_verdicts (plan_id);

        -- Immutability guard — no UPDATE/DELETE on verdict rows.
        CREATE OR REPLACE FUNCTION ${VISION_SCHEMA}.wr_compile_verdicts_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'vision.wr_compile_verdicts is immutable: % not allowed on verdict rows', TG_OP;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON ${VISION_SCHEMA}.wr_compile_verdicts;
        CREATE TRIGGER trg_wr_compile_verdicts_immutable
            BEFORE UPDATE OR DELETE ON ${VISION_SCHEMA}.wr_compile_verdicts
            FOR EACH ROW
            EXECUTE FUNCTION ${VISION_SCHEMA}.wr_compile_verdicts_immutable();
      `);
      console.log("[migrations] v38: created vision.wr_compile_verdicts (WR-scoped compile verdicts)");
    },
  },
  {
    version: 39,
    description: "Add route to vision.wr_compile_verdicts (CP-9 review a5f096e9: D5 gate must hold reserved routes)",
    up: async (exec) => {
      // CP-9 review (a5f096e9): the bootstrap gate could not distinguish
      // PASS+reserved from PASS+conduit because the verdict store had no
      // route column — a PASS on an R3/R4 (reserved) route would auto-emit a
      // builder ticket, which R-A-003 forbids. Persist classification.route
      // so the D5 gate can block on FAIL OR route='reserved'. Nullable:
      // pre-v39 verdicts and route-less issue_compile_verdict calls are
      // legacy (no route = not reserved, legacy behavior unchanged).
      await exec(`
        ALTER TABLE ${VISION_SCHEMA}.wr_compile_verdicts
            ADD COLUMN IF NOT EXISTS route TEXT;   -- classification.route: conduit | conduit-review | reserved
      `);
      console.log("[migrations] v39: added route column to vision.wr_compile_verdicts");
    },
  },
];

/**
 * Run pending migrations. Called from initDb() after createSchema(),
 * using the same dedicated connection so search_path is consistent.
 *
 * Reads the current version from schema_version, then applies any
 * migrations with version > current version in ascending order.
 * On a fresh database (currentVersion === 0), the baseline v1
 * migration runs as a no-op and records itself.
 */
async function runMigrations(
  exec: (sql: string, params?: any[]) => Promise<any>,
): Promise<void> {
  // Read current version (0 if no rows yet)
  const result = await exec(
    `SELECT COALESCE(MAX(version), 0) AS current_version FROM schema_version`
  );
  const currentVersion = result?.rows?.[0]?.current_version ?? 0;

  // Run pending migrations in order.
  // On a fresh DB (currentVersion === 0), v1 baseline is applied as a no-op.
  // On a legacy DB (currentVersion >= 1), already-applied versions are skipped.
  for (const m of migrations) {
    if (m.version <= currentVersion) continue;
    console.log(`[migrations] Applying v${m.version}: ${m.description}`);
    await m.up(exec);
    const now = new Date().toISOString();
    await exec(
      `INSERT INTO schema_version (version, description, applied_at) VALUES ($1, $2, $3)`,
      [m.version, m.description, now]
    );
    console.log(`[migrations] v${m.version} applied`);
  }
}

// ── Plan CRUD ──────────────────────────────────────────────────────
// All write operations go directly to nebula.blueprints_history (canonical
// blueprint surface, V171). Read operations go through the compat views
// nebula.plans / nebula.plan_status (over blueprints_history), which map
// blueprint rows to the legacy PlanRow shape for backward compat.
//
// WRITE MAPPING (blueprints_history columns):
//   title            -> title
//   status           -> blueprint_status   (draft/pending/approved/work_requested/completed/archived)
//   plan_number      -> plan_number        (the 431-receipt key — carried verbatim, never renumbered)
//   goal/content/    -> payload JSONB:
//     files_affected/    goal, content, files_affected[], acceptance_criteria[],
//     acceptance_criteria dependencies[], tags[], spec_ref, requirement_ref, project
//     dependencies/      (+ prompt_ref/notes/priority folded into payload for fidelity)
//     prompt_ref/notes
//     priority/project

export interface PlanRow {
  id: string;
  file_name: string;
  title: string;
  project: string;
  goal: string;
  content: string;
  files_affected: string;
  acceptance_criteria: string;
  dependencies: string;
  prompt_ref: string;
  notes: string;
  priority: number;
  created_at: string;
  updated_at: string;
  derived_status: string;
  deleted: number;
}

export type UpsertPlanInput = Omit<PlanRow, "derived_status" | "deleted"> & {
  deleted?: number;
};

// ── Helpers ────────────────────────────────────────────────────────

/** Parse a value that may be a JSON array string or comma-separated. */
function parseTextArray(val: string | undefined | null): string[] {
  if (!val) return [];
  try {
    const parsed = JSON.parse(val);
    if (Array.isArray(parsed)) return parsed.map(String);
  } catch {
    // not JSON — treat as comma-separated
  }
  return val.split(",").map(s => s.trim()).filter(s => s.length > 0);
}

// ── Write ──────────────────────────────────────────────────────────

export async function upsertPlan(plan: UpsertPlanInput): Promise<void> {
  const planNumber = plan.id; // old id was the plan number
  const uuid = crypto.randomUUID();

  // Parse array fields — callers may pass JSON or comma-separated
  const filesAffected = parseTextArray(plan.files_affected);
  const deps = parseTextArray(plan.dependencies);

  // Fold flat fields into the blueprint payload JSONB (V171). The compat
  // view nebula.implementation_plans reads these exact keys:
  //   goal, content, files_affected[], acceptance_criteria[], dependencies[],
  //   tags[], spec_ref, requirement_ref, project
  // prompt_ref/notes/priority ride along for fidelity (not surfaced by the view).
  const payload = {
    goal: plan.goal ?? "",
    content: plan.content ?? "",
    files_affected: filesAffected,
    acceptance_criteria: parseTextArray(plan.acceptance_criteria),
    dependencies: deps,
    tags: [],
    spec_ref: null,
    requirement_ref: null,
    project: plan.project ?? null,
    ...(plan.prompt_ref ? { prompt_ref: plan.prompt_ref } : {}),
    ...(plan.notes ? { notes: plan.notes } : {}),
    ...(plan.priority ? { priority: plan.priority } : {}),
  };

  // Map deleted flag → blueprint_status (both values valid in the CHECK)
  const blueprintStatus = plan.deleted === 1 ? "archived" : "pending";

  // Generate a deterministic-looking plan_number if caller didn't provide one
  // (the old upsert used `id` which was already the plan number). plan_number
  // is the 431-receipt key: carried verbatim, never renumbered, never regenerated.
  const effectivePlanNumber = planNumber || plan.title?.slice(0, 8).toUpperCase() || uuid.slice(0, 8);

  await qRun(
    `INSERT INTO nebula.blueprints_history
       (id, plan_number, title, payload, blueprint_status, created_at, updated_at)
     VALUES
       (@uuid::uuid, @planNumber, @title, @payload::jsonb, @blueprintStatus,
        @createdAt::timestamptz, @updatedAt::timestamptz)
     ON CONFLICT (plan_number) DO UPDATE SET
       title           = EXCLUDED.title,
       payload         = blueprints_history.payload || EXCLUDED.payload,
       blueprint_status = CASE WHEN EXCLUDED.blueprint_status = 'archived' THEN 'archived'
                               ELSE blueprints_history.blueprint_status END,
       updated_at      = EXCLUDED.updated_at`,
    {
      uuid,
      planNumber: effectivePlanNumber,
      title: plan.title ?? "",
      payload: JSON.stringify(payload),
      blueprintStatus,
      createdAt: plan.created_at || new Date().toISOString(),
      updatedAt: plan.updated_at || new Date().toISOString(),
    },
  );
}

export function checkpointWal(): void {
  // No-op: PG doesn't use WAL checkpointing from application layer
}

// ── Read ───────────────────────────────────────────────────────────
// All reads go through the compat view nebula.plans or conduit.plan_status.
// The compat view maps implementation_plans columns to the old PlanRow shape,
// so callers receive familiar field names (id=plan_number, deleted=1 for archived).

export async function getPlan(id: string): Promise<PlanRow | undefined> {
  return qOne(
    "SELECT * FROM nebula.plan_status WHERE id = @id",
    { id }
  );
}

export async function getPlansByStatus(status: string): Promise<PlanRow[]> {
  return qAll(
    "SELECT * FROM nebula.plan_status WHERE derived_status = @status",
    { status }
  );
}

export async function getAllPlans(): Promise<PlanRow[]> {
  return qAll("SELECT * FROM nebula.plan_status");
}

export async function getPlanById(id: string): Promise<PlanRow | undefined> {
  return qOne("SELECT * FROM nebula.plans WHERE id = @id", { id });
}

// ── Soft delete / undelete ─────────────────────────────────────────
// Maps to status field on implementation_plans.

export async function softDeletePlan(planId: string): Promise<boolean> {
  const changes = await qRun(
    `UPDATE nebula.blueprints_history
        SET blueprint_status = 'archived', updated_at = @now::timestamptz
      WHERE plan_number = @planId AND blueprint_status != 'archived'`,
    { planId, now: new Date().toISOString() },
  );
  return changes > 0;
}

export async function undeletePlan(planId: string): Promise<boolean> {
  const changes = await qRun(
    `UPDATE nebula.blueprints_history
        SET blueprint_status = 'pending', updated_at = @now::timestamptz
      WHERE plan_number = @planId AND blueprint_status = 'archived'`,
    { planId, now: new Date().toISOString() },
  );
  return changes > 0;
}

// ── Expire (soft-delete) ────────────────────────────────────────────

export async function hardDeletePlan(planId: string): Promise<{
  expired: boolean;
  ticketsDeleted: number;
  receiptsDeleted: number;
}> {
  return withTransaction(async (client) => {
    const receiptsDeleted = await tRun(
      client, `DELETE FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId`, { planId }
    );
    const ticketsDeleted = await tRun(
      client, `DELETE FROM ${VISION_SCHEMA}.tickets WHERE plan_id = @planId`, { planId }
    );
    const changes = await tRun(
      client,
      "UPDATE nebula.blueprints_history SET valid_until = now() WHERE plan_number = @planId AND valid_until > now()",
      { planId },
    );
    return {
      expired: changes > 0,
      ticketsDeleted,
      receiptsDeleted,
    };
  });
}

// ── Receipt CRUD ───────────────────────────────────────────────────

export interface ReceiptRow {
  id: string;
  plan_id: string;
  type: string;
  agent_role: string;
  session_id: string;
  ticket_id: string | null;
  artifact_path: string | null;
  summary: string;
  metadata_json: string;
  tokens_used: number;
  created_at: string;
}

/**
 * D-T19-2(b): resolve (or get-or-create) the execution.requests id for a plan.
 * Tiered: 1) reuse existing (source_plan_id); 2) get-or-create a
 * legacy-provisioned request for a real plan (nebula.plans row); 3) null for
 * test/synthetic plans. Idempotent via ON CONFLICT (business_key).
 */
export async function resolveRequestForPlan(planId: string): Promise<string | null> {
  const existing = await qOne(
    `SELECT id::text AS id FROM execution.requests WHERE source_plan_id = @planId LIMIT 1`,
    { planId },
  );
  if (existing?.id) return existing.id;

  const plan = await qOne(
    `SELECT id, title, goal FROM nebula.plans WHERE id = @planId LIMIT 1`,
    { planId },
  );
  if (!plan) return null;

  const businessKey = `legacy-plan-${planId}`;
  const created = await qOne(
    `INSERT INTO execution.requests
       (business_key, title, objective, intent_type, source_plan_id, source_wr_id, status)
     VALUES (@businessKey, @title, @objective, 'legacy', @planId, NULL, 'READY')
     ON CONFLICT (business_key) DO UPDATE SET business_key = EXCLUDED.business_key
     RETURNING id::text AS id`,
    {
      businessKey,
      title: plan.title || `Legacy plan ${planId}`,
      objective: plan.goal || "",
      planId,
    },
  );
  return created?.id ?? null;
}

export async function insertReceipt(r: ReceiptRow): Promise<void> {
  const requestId = await resolveRequestForPlan(r.plan_id);
  if (requestId) {
    // Canonical: execution.receipts, request-scoped (attempt_id NULL).
    // The legacy "rec-*" id is preserved in lineage_original_id.
    let baseMeta: Record<string, unknown> = {};
    try {
      baseMeta = r.metadata_json ? JSON.parse(r.metadata_json) : {};
    } catch {
      baseMeta = {};
    }
    const execMeta = {
      ...baseMeta,
      session_id: r.session_id ?? "",
      artifact_path: r.artifact_path ?? null,
      ticket_id: r.ticket_id ?? null,
      tokens_used: r.tokens_used ?? 0,
    };
    await qRun(
      `INSERT INTO execution.receipts
         (request_id, attempt_id, type, agent_role, summary, metadata,
          lineage_source, lineage_original_id, issued_at)
       VALUES (@requestId::uuid, NULL, @type, @agent_role, @summary, @metadata::jsonb,
               'conduit', @id, @created_at)
       ON CONFLICT (lineage_original_id) WHERE lineage_source = 'conduit' DO NOTHING`,
      {
        requestId,
        type: r.type,
        agent_role: r.agent_role,
        summary: r.summary ?? "",
        metadata: JSON.stringify(execMeta),
        id: r.id,
        created_at: r.created_at,
      },
    );
    return;
  }
  // Test/synthetic plan: preserve legacy write surface (frozen in D-T19-2(d)).
  await qRun(
    `INSERT INTO ${VISION_SCHEMA}.receipts
      (id, plan_id, type, agent_role, session_id, ticket_id, artifact_path, summary, metadata_json, tokens_used, created_at)
    VALUES (@id, @plan_id, @type, @agent_role, @session_id, @ticket_id, @artifact_path, @summary, @metadata_json, @tokens_used, @created_at)`,
    { ...r, tokens_used: r.tokens_used ?? 0 }
  );
}

export async function getReceiptsForPlan(planId: string): Promise<ReceiptRow[]> {
  return qAll(
    `SELECT * FROM nebula.receipts_unified WHERE plan_id = @planId ORDER BY created_at ASC`,
    { planId }
  );
}

export async function getPlanReceipts(planId: string): Promise<Array<{
  id: string;
  type: string;
  agent_role: string;
  session_id: string;
  artifact_path: string | null;
  summary: string;
  metadata: any;
  created_at: string;
}>> {
  const rows = await getReceiptsForPlan(planId);
  return rows.map((r) => ({
    id: r.id,
    type: r.type,
    agent_role: r.agent_role,
    session_id: r.session_id,
    artifact_path: r.artifact_path,
    summary: r.summary,
    metadata: (() => {
      try { return JSON.parse(r.metadata_json); } catch { return {}; }
    })(),
    created_at: r.created_at,
  }));
}

export async function getLatestReceiptType(planId: string): Promise<string | null> {
  const row = await qOne(
    `SELECT type FROM nebula.receipts_unified WHERE plan_id = @planId ORDER BY created_at DESC LIMIT 1`,
    { planId }
  );
  return row?.type ?? null;
}

// ── WR compile verdicts (R-A-2026-08-15-010 option c) ────────────────────
// A pre-release WR_COMPILE_PASS/FAIL verdict, keyed by the compile unit's
// entityKey (D3). Lives in vision.wr_compile_verdicts — NOT vision.receipts
// (frozen legacy surface, D-T19-2(d)) and NOT execution.receipts
// (request/attempt-scoped, ADR-006). Insert-only and idempotent.

export type CompileVerdictType = "WR_COMPILE_PASS" | "WR_COMPILE_FAIL";

export interface CompileVerdictRow {
  verdict_id: string;
  entity_key: string;
  wr_id: string | null;
  plan_id: string | null;
  verdict_type: CompileVerdictType;
  rule_version: string;
  description: string;
  detected_at: string | null;
  created_at: string;
  /** classification.route (conduit | conduit-review | reserved). */
  route: string | null;
}

export interface CompileVerdictInput {
  verdict_id?: string; // omitted → derived deterministically below
  entity_key: string;
  wr_id?: string | null;
  plan_id?: string | null;
  verdict_type: CompileVerdictType;
  rule_version: string;
  description: string;
  detected_at?: string | null;
  /** classification.route — persisted so the D5 gate can hold reserved. */
  route?: string | null;
}

/**
 * Deterministic verdict id — SHA256(type, entity_key, rule_version,
 * description). Re-issuing the same verdict for the same compile unit yields
 * the same id, so INSERT ... ON CONFLICT (verdict_id) DO NOTHING is naturally
 * idempotent (the same pattern as peb.cir_violations, V106).
 */
export function computeCompileVerdictId(
  verdictType: string,
  entityKey: string,
  ruleVersion: string,
  description: string,
): string {
  return crypto
    .createHash("sha256")
    .update(verdictType)
    .update("\u0000")
    .update(entityKey)
    .update("\u0000")
    .update(ruleVersion)
    .update("\u0000")
    .update(description)
    .digest("hex");
}

/** Insert a compile verdict. Idempotent on verdict_id (no duplicate rows). */
export async function insertCompileVerdict(v: CompileVerdictInput): Promise<void> {
  const verdictId =
    v.verdict_id ??
    computeCompileVerdictId(v.verdict_type, v.entity_key, v.rule_version, v.description);
  await qRun(
    `INSERT INTO ${VISION_SCHEMA}.wr_compile_verdicts
       (verdict_id, entity_key, wr_id, plan_id, verdict_type, rule_version,
        description, detected_at, route, created_at)
     VALUES (@verdictId, @entityKey, @wrId, @planId, @verdictType, @ruleVersion,
             @description, @detectedAt, @route, now())
     ON CONFLICT (verdict_id) DO NOTHING`,
    {
      verdictId,
      entityKey: v.entity_key,
      wrId: v.wr_id ?? null,
      planId: v.plan_id ?? null,
      verdictType: v.verdict_type,
      ruleVersion: v.rule_version,
      description: v.description,
      detectedAt: v.detected_at ?? null,
      route: v.route ?? null,
    },
  );
}

/** Newest verdict for a compile unit (the D5 bootstrap gate query). */
export async function getNewestCompileVerdict(
  entityKey: string,
): Promise<CompileVerdictRow | undefined> {
  return qOne(
    `SELECT * FROM ${VISION_SCHEMA}.wr_compile_verdicts
     WHERE entity_key = @entityKey
     ORDER BY created_at DESC
     LIMIT 1`,
    { entityKey },
  );
}

/**
 * Resolve the compile-unit entityKey(s) linked to a plan, for the D5 gate.
 *
 * A plan links to its compile unit through the released WorkRequest whose
 * `context.plan_id` equals the plan number (the release-emission boundary
 * writes this link at CP-9; a WR carries its D3 entityKey from birth).
 *
 * Returns [] when no entityKey is resolvable — a legacy/unclassified plan,
 * for which the gate is unchanged (no verdict = legacy behavior).
 */
export async function resolveEntityKeysForPlan(planId: string): Promise<string[]> {
  const rows = await qAll(
    `SELECT DISTINCT wr.entity_key AS entity_key
     FROM ${VISION_SCHEMA}.work_requests wr
     WHERE wr.entity_key IS NOT NULL
       AND wr.context->>'plan_id' = @planId`,
    { planId },
  );
  return (rows ?? []).map((r: any) => r.entity_key).filter(Boolean);
}

/**
 * Newest verdict for a plan (the D5 bootstrap gate).
 *
 * Matches verdicts by the plan's resolved compile-unit entityKey(s)
 * (canonical D5 key) OR by a release-time re-parented plan_id; newest wins.
 * No verdict → undefined (legacy unchanged).
 */
export async function getNewestCompileVerdictForPlan(
  planId: string,
): Promise<CompileVerdictRow | undefined> {
  const entityKeys = await resolveEntityKeysForPlan(planId);
  if (entityKeys.length === 0) {
    return qOne(
      `SELECT * FROM ${VISION_SCHEMA}.wr_compile_verdicts
       WHERE plan_id = @planId
       ORDER BY created_at DESC
       LIMIT 1`,
      { planId },
    );
  }
  const placeholders = entityKeys.map((_, i) => `@ek${i}`).join(", ");
  const params: Record<string, any> = { planId };
  entityKeys.forEach((ek, i) => {
    params[`ek${i}`] = ek;
  });
  return qOne(
    `SELECT * FROM ${VISION_SCHEMA}.wr_compile_verdicts
     WHERE plan_id = @planId OR entity_key IN (${placeholders})
     ORDER BY created_at DESC
     LIMIT 1`,
    params,
  );
}

/**
 * D5 bootstrap gate predicate (CP-9 review a5f096e9).
 *
 * A compile verdict blocks auto-bootstrap when it is a FAIL, or a PASS on a
 * reserved (R3/R4) route — R-A-003: reserved is never auto-armed (explicit
 * Architect/human release only). ``undefined`` (no verdict) → false = legacy
 * behavior unchanged. This is the single source of truth shared by the
 * watcher's bootstrap pass and the tests.
 */
export function verdictBlocksBootstrap(
  verdict: Pick<CompileVerdictRow, "verdict_type" | "route"> | undefined,
): boolean {
  if (!verdict) return false;
  return verdict.verdict_type === "WR_COMPILE_FAIL" || verdict.route === "reserved";
}

/**
 * D5 downstream analytics — compile pass/fail measurement surface (CP-9
 * delta sub-item 1). Aggregates vision.wr_compile_verdicts by verdict_type
 * and rule_version so pass/fail rates are measurable once the gate is live.
 */
export interface CompileVerdictStats {
  total: number;
  byVerdictType: Array<{ verdict_type: string; count: number }>;
  byRuleVersion: Array<{ rule_version: string; count: number }>;
}

export async function getCompileVerdictStats(): Promise<CompileVerdictStats> {
  const byType = await qAll(
    `SELECT verdict_type, count(*)::int AS count
     FROM ${VISION_SCHEMA}.wr_compile_verdicts
     GROUP BY verdict_type
     ORDER BY verdict_type`,
  );
  const byVersion = await qAll(
    `SELECT rule_version, count(*)::int AS count
     FROM ${VISION_SCHEMA}.wr_compile_verdicts
     GROUP BY rule_version
     ORDER BY rule_version`,
  );
  const total = (byType ?? []).reduce((sum: number, r: any) => sum + r.count, 0);
  return {
    total,
    byVerdictType: (byType ?? []).map((r: any) => ({
      verdict_type: r.verdict_type,
      count: r.count,
    })),
    byRuleVersion: (byVersion ?? []).map((r: any) => ({
      rule_version: r.rule_version,
      count: r.count,
    })),
  };
}

export async function getReceiptCount(): Promise<{ type: string; count: number }[]> {
  return qAll(
    `SELECT type, COUNT(*) as count FROM nebula.receipts_unified GROUP BY type`
  );
}

/**
 * CP-9 end-to-end: run the release gate and persist the verdict.
 *
 * Fuses compare → classify into the deterministic release decision
 * (evaluateReleaseGate), then persists the pre-row verdict to
 * vision.wr_compile_verdicts (idempotent on the deterministic verdict_id).
 * Returns the decision + the persisted verdict_id for the caller/bootstrapper.
 */
export async function runCompileGate(opts: {
  wr: CompareTarget;
  plan: CompareTarget;
  assignment: RippleAssignment;
  entityKey: string;
  ruleVersion?: string;
  wrId?: string | null;
  planId?: string | null;
}): Promise<ReleaseDecision & { verdict_id: string }> {
  const decision = evaluateReleaseGate(opts.wr, opts.plan, opts.assignment);
  const ruleVersion = opts.ruleVersion ?? "1";
  const verdictId = computeCompileVerdictId(
    decision.verdict,
    opts.entityKey,
    ruleVersion,
    decision.reason,
  );
  await insertCompileVerdict({
    verdict_id: verdictId,
    entity_key: opts.entityKey,
    wr_id: opts.wrId ?? null,
    plan_id: opts.planId ?? null,
    verdict_type: decision.verdict,
    rule_version: ruleVersion,
    description: decision.reason,
    route: decision.classification.route,
  });
  return { ...decision, verdict_id: verdictId };
}

/**
 * Delete receipts by plan and type.
 *
 * Sole legitimate caller: the unblock_plan tool (tools.ts ~L1905). Allowed
 * type set: the unblock receipt family {BLOCK, PLAN_BLOCK, CANCELLED,
 * ABANDONED}. This is a scoped, typed, operator-action-backed purge (the
 * unblock workflow) — NOT a general-purpose receipt deleter. Receipt-
 * immutability doctrine is untouched. The REST surface
 * (DELETE /api/receipts/{plan_id}) rejects any type outside this family
 * (architect ruling fcec95a2 #2). Do not widen without an architect decision.
 */
export async function deleteReceiptsByPlanAndType(
  planId: string,
  types: string[],
): Promise<number> {
  if (types.length === 0) return 0;
  const placeholders = types.map((_, i) => `@type${i}`).join(",");
  const params: Record<string, any> = { planId };
  types.forEach((t, i) => (params[`type${i}`] = t));
  
  const changes = await qRun(
    `DELETE FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId AND type IN (${placeholders})`,
    params
  );
  return changes;
}

// ── Grouped Plan Status ─────────────────────────────────────────────

import { PlanCard } from "./types";

export interface PlansByStatus {
  pending: PlanRow[];
  active: PlanRow[];
  completed: PlanRow[];
  blocked: PlanRow[];
  archived: PlanRow[];
  planning: PlanRow[];
  hold: PlanRow[];
}

export async function getPlansGroupedByStatus(): Promise<PlansByStatus> {
  const all = await qAll("SELECT * FROM nebula.plan_status") as PlanRow[];

  const result: PlansByStatus = {
    pending: [], active: [], completed: [], blocked: [],
    archived: [], planning: [], hold: [],
  };

  for (const plan of all) {
    switch (plan.derived_status) {
      case "PLAN_CREATE": result.pending.push(plan); break;
      case "IMPLEMENTATION": result.active.push(plan); break;
      case "REVIEW_PASS": result.completed.push(plan); break;
      case "BLOCK": result.blocked.push(plan); break;
      case "REVIEW_REJECT": result.active.push(plan); break;
      case "HOLD": result.hold.push(plan); break;
      case "PLANNING": result.planning.push(plan); break;
      case "REVIEW": result.active.push(plan); break;
      case "CRITIQUE": result.active.push(plan); break;
      case "CRITIQUE_PASS": result.pending.push(plan); break;
      case "CRITIQUE_REJECT": result.planning.push(plan); break;
      case "PLAN_BLOCK": result.blocked.push(plan); break;
    }
  }

  return result;
}

export function planRowToPlanCard(row: PlanRow): PlanCard {
  return {
    fileName: row.file_name,
    planNumber: row.id,
    baseName: row.file_name.replace(".md", ""),
    title: row.title,
    project: row.project,
    createdAt: row.created_at,
    movedAt: undefined,
    completedAt: row.derived_status === "REVIEW_PASS" ? row.updated_at : undefined,
    blockReason: undefined,
    goal: row.goal || undefined,
    filesAffected: safeParseJson(row.files_affected) || [],
    acceptanceCriteria: safeParseJson(row.acceptance_criteria) || [],
    dependencies: safeParseJson(row.dependencies) || [],
    promptRef: row.prompt_ref || undefined,
    derivedStatus: row.derived_status || undefined,
    priority: row.priority,
  };
}

function safeParseJson(s: string): any {
  try { return JSON.parse(s); } catch { return undefined; }
}

// ── Session CRUD ────────────────────────────────────────────────────

export interface SessionRow {
  id: string;
  agent_role: string;
  start_iso: string;
  end_iso: string | null;
  exit_code: number | null;
  retries_used: number;
  plans_processed: string;
  plan_count: number;
  pid: number | null;
  is_running: number;
  last_activity: string | null;
  last_heartbeat_at: string | null;
  model: string | null;
  fallback_used: number;
  cost_usd: number | null;
  total_work_seconds: number;
  workflow_id: string | null;
  run_id: string | null;
  workflow_start_time: string | null;
  workflow_close_time: string | null;
  workflow_run_time_ms: number | null;
  workflow_result: string | null;
  created_at: string;
  tags: string;
}

export interface SessionStartInput {
  id: string;
  agent_role: string;
  start_iso: string;
  pid?: number;
  plans_processed?: string[];
  plan_count?: number;
  model?: string;
  fallback_used?: number;
  workflow_id?: string;
  run_id?: string;
  workflow_start_time?: string;
  tags?: string[];
}

export async function startSession(s: SessionStartInput): Promise<void> {
  await qRun(
    `INSERT OR REPLACE INTO sessions
      (id, agent_role, start_iso, pid, plans_processed, plan_count,
       model, fallback_used, is_running, last_activity,
       workflow_id, run_id, workflow_start_time,
       created_at, tags)
    VALUES (@id, @agent_role, @start_iso, @pid, @plans_processed, @plan_count,
            @model, @fallback_used, 1, @start_iso,
            @workflow_id, @run_id, @workflow_start_time,
            @start_iso, @tags)`,
    {
      id: s.id,
      agent_role: s.agent_role,
      start_iso: s.start_iso,
      pid: s.pid ?? null,
      plans_processed: JSON.stringify(s.plans_processed ?? []),
      plan_count: s.plan_count ?? 0,
      model: s.model ?? null,
      fallback_used: s.fallback_used ?? 0,
      workflow_id: s.workflow_id ?? null,
      run_id: s.run_id ?? null,
      workflow_start_time: s.workflow_start_time ?? null,
      tags: JSON.stringify(s.tags ?? []),
    }
  );
}

export async function endSession(
  id: string, exitCode: number, endIso: string,
  plansProcessed?: string[],
  workflowCloseTime?: string,
  workflowRunTimeMs?: number,
  workflowResult?: string,
): Promise<void> {
  await qRun(
    `UPDATE sessions SET
      end_iso = @end_iso,
      exit_code = @exit_code,
      is_running = 0,
      last_activity = @end_iso,
      plans_processed = COALESCE(@plans_processed, plans_processed),
      plan_count = CASE WHEN @plans_processed IS NOT NULL
                    THEN jsonb_array_length(@plans_processed::jsonb) ELSE plan_count END,
      workflow_close_time = COALESCE(@workflow_close_time, workflow_close_time),
      workflow_run_time_ms = COALESCE(@workflow_run_time_ms, workflow_run_time_ms),
      workflow_result = COALESCE(@workflow_result, workflow_result)
    WHERE id = @id`,
    {
      id,
      end_iso: endIso,
      exit_code: exitCode,
      plans_processed: plansProcessed ? JSON.stringify(plansProcessed) : null,
      workflow_close_time: workflowCloseTime ?? null,
      workflow_run_time_ms: workflowRunTimeMs ?? null,
      workflow_result: workflowResult ?? null,
    }
  );
}

export async function updateSessionPid(id: string, pid: number): Promise<void> {
  await qRun("UPDATE sessions SET pid = @pid WHERE id = @id", { id, pid });
}

export async function updateSessionActivity(id: string, activityIso: string): Promise<void> {
  await qRun(
    "UPDATE sessions SET last_activity = @activity WHERE id = @id",
    { id, activity: activityIso }
  );
}

export async function getRunningSessions(): Promise<SessionRow[]> {
  return qAll(
    "SELECT * FROM sessions WHERE is_running != 0 ORDER BY start_iso DESC"
  );
}

export async function getSession(id: string): Promise<SessionRow | undefined> {
  return qOne("SELECT * FROM sessions WHERE id = @id", { id });
}

export async function getAllSessions(): Promise<SessionRow[]> {
  return qAll("SELECT * FROM sessions ORDER BY start_iso DESC");
}

export async function updateSessionCost(id: string, costUsd: number): Promise<void> {
  await qRun(
    "UPDATE sessions SET cost_usd = @cost WHERE id = @id",
    { id, cost: costUsd }
  );
}

export async function updateSessionHeartbeat(id: string): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    "UPDATE sessions SET last_activity = @now, last_heartbeat_at = @now WHERE id = @id",
    { id, now }
  );
}

export async function getStaleSessions(staleThresholdSeconds: number): Promise<SessionRow[]> {
  return qAll(
    `SELECT * FROM sessions
     WHERE is_running = 1
     AND last_heartbeat_at IS NOT NULL
     AND (EXTRACT(EPOCH FROM NOW()) - EXTRACT(EPOCH FROM last_heartbeat_at)) > @threshold`,
    { threshold: staleThresholdSeconds }
  );
}

// ── Session Logs ─────────────────────────────────────────────────────

export interface SessionLogRow {
  id: string;
  session_id: string;
  timestamp: string;
  level: string;
  line: string;
}

export async function appendSessionLog(
  sessionId: string,
  line: string,
  level: string = "INFO",
): Promise<void> {
  await qRun(
    `INSERT INTO ${TACKLE_SCHEMA}.session_logs (session_id, level, line) VALUES (@session_id, @level, @line)`,
    { session_id: sessionId, level, line },
  );
}

export async function getSessionLogs(sessionId: string): Promise<SessionLogRow[]> {
  return qAll(
    `SELECT * FROM ${TACKLE_SCHEMA}.session_logs WHERE session_id = @session_id ORDER BY id ASC`,
    { session_id: sessionId },
  );
}

// ── Circuit breaker ─────────────────────────────────────────────────

export interface BreakerRow {
  id: number;
  tripped: number;
  tripped_at: string | null;
  retry_after: number;
  error: string | null;
  detail: string | null;
  source: string | null;
  fallback_model: string | null;
  paused: number;
  updated_at: string | null;
  max_retries_per_model: number | null;
  retry_delay_seconds: number | null;
  max_fallbacks: number | null;
  push_back_to_pending: number | null;
}

export async function tripBreaker(input: {
  error: string; detail?: string; source?: string; retryAfter?: number; fallbackModel?: string;
}): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `UPDATE circuit_breaker SET
      tripped = 1, tripped_at = @tripped_at, retry_after = @retry_after,
      error = @error, detail = @detail, source = @source,
      fallback_model = @fallback_model, updated_at = @updated_at
    WHERE id = 1`,
    {
      tripped_at: now, retry_after: input.retryAfter ?? 1800,
      error: input.error, detail: input.detail ?? null,
      source: input.source ?? null, fallback_model: input.fallbackModel ?? null,
      updated_at: now,
    }
  );
}

export async function clearBreaker(): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `UPDATE circuit_breaker SET tripped = 0, tripped_at = NULL,
      error = NULL, detail = NULL, source = NULL, updated_at = @updated_at
    WHERE id = 1`,
    { updated_at: now }
  );
}

export async function isConduitPaused(): Promise<boolean> {
  const row = await qOne("SELECT paused FROM circuit_breaker WHERE id = 1");
  return row?.paused === 1;
}

export async function setConduitPaused(paused: boolean): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    "UPDATE circuit_breaker SET paused = @paused, updated_at = @updated_at WHERE id = 1",
    { paused: paused ? 1 : 0, updated_at: now }
  );
}

export async function getBreaker(): Promise<BreakerRow> {
  const row = await qOne("SELECT * FROM circuit_breaker WHERE id = 1");
  if (!row) {
    return { id: 1, tripped: 0, tripped_at: null, retry_after: 1800, error: null,
      detail: null, source: null, fallback_model: null, paused: 0, updated_at: null,
      max_retries_per_model: 3, retry_delay_seconds: 120, max_fallbacks: 3,
      push_back_to_pending: 1 };
  }
  return row;
}

export async function isBreakerTripped(): Promise<boolean> {
  const row = await qOne("SELECT tripped FROM circuit_breaker WHERE id = 1");
  return row?.tripped === 1;
}

// ── Scheduler Wake ──────────────────────────────────────────────────

export async function requestSchedulerWake(): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `UPDATE circuit_breaker SET wake_requested_at = @wake_requested_at, updated_at = @updated_at WHERE id = 1`,
    { wake_requested_at: now, updated_at: now }
  );
}

/** Returns true and clears wake_requested_at if a wake was requested since the
 *  given timestamp. Used by the scheduler to shorten idle backoff on config change. */
export async function consumeSchedulerWake(since: string): Promise<boolean> {
  const row = await qOne(
    `SELECT wake_requested_at FROM circuit_breaker WHERE id = 1
     AND wake_requested_at IS NOT NULL AND wake_requested_at > @since`,
    { since }
  );
  if (row?.wake_requested_at) {
    await qRun(
      `UPDATE circuit_breaker SET wake_requested_at = NULL, updated_at = @updated_at WHERE id = 1`,
      { updated_at: new Date().toISOString() }
    );
    return true;
  }
  return false;
}

// ── Role Circuit Breaker ────────────────────────────────────────────

export async function isRoleBreakerTripped(role: string): Promise<boolean> {
  const row = await qOne(
    `SELECT tripped FROM ${PEB_SCHEMA}.role_circuit_breaker WHERE role = @role`,
    { role }
  );
  return row?.tripped === 1;
}

export async function tripRoleBreaker(
  role: string,
  error: string,
  retryAfter: number = 1800,
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO ${PEB_SCHEMA}.role_circuit_breaker (role, tripped, tripped_at, retry_after, error, failure_count, updated_at)
     VALUES (@role, 1, @tripped_at, @retry_after, @error, 1, @updated_at)
     ON CONFLICT (role) DO UPDATE SET
       tripped = 1, tripped_at = @tripped_at, retry_after = @retry_after,
       error = @error, failure_count = ${PEB_SCHEMA}.role_circuit_breaker.failure_count + 1,
       updated_at = @updated_at`,
    { role, tripped_at: now, retry_after: retryAfter, error, updated_at: now }
  );
}

export async function resetRoleBreaker(role: string): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `DELETE FROM ${PEB_SCHEMA}.role_circuit_breaker WHERE role = @role`,
    { role }
  );
  // Wake the Python scheduler so it re-polls immediately instead of
  // waiting out the idle backoff (SCHEDULER_IDLE_BACKOFF, 60s).
  await requestSchedulerWake();
}

// ── Tickets ─────────────────────────────────────────────────────────

export interface TicketRow {
  id: string; plan_id: string; role: string;
  status: "open" | "claimed" | "completed" | "failed" | "abandoned" |
          "superseded" | "cancelled" | "stale" | "expired";
  session_id: string | null; created_by_receipt: string; created_at: string;
  claimed_at: string | null; closed_at: string | null;
  token_budget: number | null; tokens_used: number | null;
  objective: string | null; completion_criteria: string | null;
  owner: string; parent_ticket_id: string | null;
  spawn_reason: string | null; last_activity: string | null;
  expires_at: string | null; confidence: number | null;
  closure_reason: string | null; replacement_of: string | null;
}

async function _isPlanTerminal(planId: string): Promise<boolean> {
  const row = await qOne(
    `SELECT type FROM nebula.receipts_unified WHERE plan_id = @planId
     ORDER BY created_at DESC LIMIT 1`,
    { planId }
  );
  if (!row) return false;
  return ["REVIEW_PASS", "BLOCK", "PLAN_BLOCK", "CANCELLED", "ABANDONED"].includes(row.type);
}

export async function createNextTickets(
  planId: string, ticketRole: string, terminalStatus: string,
  parentTicketId: string = "", objective: string = "",
  completionCriteria: string = "", owner: string = "",
  critiquePosition: "admission" | "artifact" | "" = "",
): Promise<number> {
  if (await _isPlanTerminal(planId)) {
    console.log(`Guard: plan ${planId} has terminal receipt(s) — skipping ticket creation`);
    return 0;
  }

  const nextRoles: string[] = [];
  if (terminalStatus === "completed") {
    if (ticketRole === "builder") nextRoles.push("reviewer");
    else if (ticketRole === "planner") nextRoles.push("builder", "critic");
    // Plan 0016 — the ARTIFACT critique edge: a critic completing after an
    // IMPLEMENTATION-triggered critique hands to the reviewer (the artifact has
    // been implemented; a negative critique influenced the reviewer, but the
    // next actor is the reviewer). An ADMISSION critique (after PLAN_CREATE)
    // hands back to the builder to implement. The position is resolved from the
    // last non-CRITIQUE receipt (critiquePosition arg) — NEVER ticket.objective.
    else if (ticketRole === "critic") {
      if (critiquePosition === "artifact") nextRoles.push("reviewer");
      else nextRoles.push("builder"); // admission (or unknown) → builder
    }
  } else if (terminalStatus === "failed") {
    if (ticketRole === "reviewer") nextRoles.push("builder");
    else if (ticketRole === "planner") nextRoles.push("planner");
  }
  if (nextRoles.length === 0) return 0;

  const now = new Date().toISOString();
  const expiresAt = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
  let count = 0;
  for (const role of nextRoles) {
    const spawnReason = `${ticketRole} ${terminalStatus} → ${role}`;
    const changes = await qRun(
      `INSERT OR IGNORE INTO ${VISION_SCHEMA}.tickets
        (id, plan_id, role, status, created_at,
         objective, completion_criteria, owner,
         parent_ticket_id, spawn_reason,
         last_activity, expires_at)
      VALUES (@id, @plan_id, @role, 'open', @created_at,
              @objective, @completion_criteria, @owner,
              @parent_ticket_id, @spawn_reason,
              @last_activity, @expires_at)`,
      {
        id: `ticket-${planId}-${role}-${Date.now()}`,
        plan_id: planId, role, created_at: now,
        objective: objective || "", completion_criteria: completionCriteria || "",
        owner: owner || role, parent_ticket_id: parentTicketId || null,
        spawn_reason: spawnReason, last_activity: now, expires_at: expiresAt,
      }
    );
    if (changes > 0) count++;
  }
  return count;
}

/**
 * Pure mapping: which role's ticket a receipt type completes, and with what
 * terminal status. PLAN_CREATE creates the builder ticket (not a completion);
 * BLOCK/HOLD/etc. complete no ticket. Exported + pure so the wiring contract
 * is unit-testable without a DB (plan 0016 AC2 spirit — routing/advancement
 * depends only on the receipt type and the non-CRITIQUE receipt position,
 * never ticket.objective).
 */
export function receiptToCompletingRole(
  receiptType: string,
): { role: string; status: "completed" | "failed" } | null {
  switch (receiptType) {
    case "IMPLEMENTATION": return { role: "builder", status: "completed" };
    case "CRITIQUE_PASS": return { role: "critic", status: "completed" };
    case "CRITIQUE_REJECT": return { role: "critic", status: "completed" };
    case "REVIEW_PASS": return { role: "reviewer", status: "completed" };
    case "REVIEW_REJECT": return { role: "reviewer", status: "failed" };
    default: return null;
  }
}

/**
 * WIRING FIX (plan 0016 follow-up; timers-readiness): advance the ticket
 * lifecycle when a receipt lands.
 *
 * Previously createNextTickets had NO callers — tickets were created on
 * PLAN_CREATE (createTicketIfMissing) but never completed or fanned out, so
 * plans could not advance past a single ticket when timers re-enabled
 * automatic dispatch. This function closes that gap: on the receipts that
 * represent a role finishing its work, it
 *   1. marks the completing role's open ticket completed/failed, and
 *   2. spawns the next role's ticket via createNextTickets (which for a critic
 *      uses the position — admission vs artifact — resolved from the last
 *      non-CRITIQUE receipt; NEVER ticket.objective).
 *
 * Returns { completed, spawned, critiquePosition } for observability.
 */
export async function advanceTicketsOnReceipt(
  planId: string,
  receiptType: string,
  _agentRole: string,
): Promise<{ completed: number; spawned: number; critiquePosition?: "admission" | "artifact" }> {
  const m = receiptToCompletingRole(receiptType);
  if (!m) return { completed: 0, spawned: 0 };

  // Find the open/claimed/stale ticket for the completing role.
  const ticket = await qOne(
    `SELECT id, objective, completion_criteria, owner
     FROM ${VISION_SCHEMA}.tickets
     WHERE plan_id = @planId AND role = @role AND status IN ('open','claimed','stale')
     ORDER BY created_at ASC LIMIT 1`,
    { planId, role: m.role },
  );
  if (!ticket) return { completed: 0, spawned: 0 };

  // Mark it completed/failed.
  const now = new Date().toISOString();
  await qRun(
    `UPDATE ${VISION_SCHEMA}.tickets
     SET status = @status, closed_at = @now::timestamptz, last_activity = @now,
         closure_reason = @reason
     WHERE id = @ticketId AND status IN ('open','claimed','stale')`,
    { ticketId: ticket.id, status: m.status, now, reason: `receipt:${receiptType}` },
  );

  // Resolve critique position locally from the receipt chain (db.ts reads PG
  // directly — no HTTP, no import from receipts.ts to avoid a layering cycle).
  let critiquePosition: "admission" | "artifact" | "" = "";
  if (m.role === "critic") {
    const position = await lastNonCritiqueReceiptType(planId);
    if (position === "PLAN_CREATE") critiquePosition = "admission";
    else if (position === "IMPLEMENTATION") critiquePosition = "artifact";
  }

  // Spawn the next role's ticket (position-aware for critic completion).
  const spawned = await createNextTickets(
    planId, m.role, m.status, ticket.id,
    ticket.objective || "", ticket.completion_criteria || "", ticket.owner || m.role,
    critiquePosition,
  );

  return {
    completed: 1,
    spawned,
    critiquePosition: critiquePosition === "" ? undefined : critiquePosition,
  };
}

/** The last receipt in plan history that is NOT in the critique family. */
async function lastNonCritiqueReceiptType(planId: string): Promise<string | null> {
  const rows = await getReceiptsForPlan(planId); // created_at ASC
  const CRITIQUE_FAMILY = new Set(["CRITIQUE", "CRITIQUE_PASS", "CRITIQUE_REJECT"]);
  for (let i = rows.length - 1; i >= 0; i--) {
    if (!CRITIQUE_FAMILY.has(rows[i].type)) return rows[i].type;
  }
  return null;
}

export async function createTicketIfMissing(
  planId: string, role: string, createdByReceipt: string,
  createdAt: string, objective: string = "",
  completionCriteria: string = "", owner: string = "",
  parentTicketId: string | null = null, spawnReason: string = "",
  replacementOf: string | null = null,
): Promise<string | null> {
  const expiresAt = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
  const ticketId = `ticket-${planId}-${role}-${createdByReceipt}`;
  const changes = await qRun(
    `INSERT OR IGNORE INTO ${VISION_SCHEMA}.tickets
      (id, plan_id, role, status, created_by_receipt, created_at,
       objective, completion_criteria, owner,
       parent_ticket_id, spawn_reason,
       last_activity, expires_at, replacement_of)
    VALUES (@id, @plan_id, @role, 'open', @created_by_receipt, @created_at,
            @objective, @completion_criteria, @owner,
            @parent_ticket_id, @spawn_reason,
            @last_activity, @expires_at, @replacement_of)`,
    {
      id: ticketId, plan_id: planId, role,
      created_by_receipt: createdByReceipt, created_at: createdAt,
      objective: objective || "", completion_criteria: completionCriteria || "",
      owner: owner || role, parent_ticket_id: parentTicketId,
      spawn_reason: spawnReason || "", last_activity: createdAt,
      expires_at: expiresAt, replacement_of: replacementOf,
    }
  );
  if (changes > 0) return ticketId;

  const existing = await qOne(
    `SELECT id FROM ${VISION_SCHEMA}.tickets WHERE plan_id = @planId AND role = @role AND status = 'open'`,
    { planId, role }
  );
  return existing?.id ?? null;
}

export async function releaseSessionTickets(sessionId: string): Promise<number> {
  // ADR-016: Wrap UPDATE + recordTransition in single transaction for atomicity.
  // If the trigger rejects a transition, the UPDATE is rolled back too.
  return withTransaction(async (client) => {
    const now = new Date().toISOString();
    const affected = await tAll(
      client,
      `SELECT id FROM ${VISION_SCHEMA}.tickets WHERE session_id = @sessionId AND status = 'claimed'`,
      { sessionId },
    );
    const count = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'open', session_id = NULL,
        claimed_at = NULL, last_activity = @now
      WHERE session_id = @sessionId AND status = 'claimed'`,
      { sessionId, now }
    );
    for (const t of affected) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: t.id,
        eventType: "transition.committed",
        actor: "conduit-mcp",
        authority: "system",
        payload: { from_status: "claimed", to_status: "open", reason: "session_released", session_id: sessionId },
      });
    }
    return count;
  });
}

export async function resetAbandonedTickets(): Promise<number> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();
    const affected = await tAll(
      client,
      `SELECT id FROM ${VISION_SCHEMA}.tickets WHERE status = 'abandoned'`,
      {},
    );
    const count = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'open', closed_at = NULL, last_activity = @now
      WHERE status = 'abandoned'`,
      { now }
    );
    for (const t of affected) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: t.id,
        eventType: "transition.committed",
        actor: "conduit-mcp",
        authority: "system",
        payload: { from_status: "abandoned", to_status: "open", reason: "abandoned_reset" },
      });
    }
    return count;
  });
}

// ── Manual ticket claims (walk-through D3, 2026-09-22) ─────────────
// No tool could claim a ticket: manual builders/reviewers worked silently
// unclaimed, with no reservation and no double-work guard. claimTicket
// completes the session-claim model the rest of this file already
// implements (releaseSessionTickets, detectStaleTickets, and
// advanceTicketsOnReceipt's 'claimed' handling). Claims follow the house
// convention: status='claimed' + session_id + claimed_at + last_activity,
// transition-recorded (ADR-016). When tickets migrate to the canonical
// store (Lilac wave, plan 8261639), these functions move with the store.

/** Local claim errors (db-layer; createError lives in errors.ts for the
 * MCP layer and importing it here would risk a layering cycle). */
function claimError(code: string, message: string, extra?: Record<string, unknown>): Error {
  const e = new Error(message) as Error & { code: string; details?: Record<string, unknown> };
  e.code = code;
  if (extra) e.details = extra;
  return e;
}

/** Pure claim decision — exported for hermetic tests.
 *
 *  none      — ticket absent or in a non-claimable status (completed/failed/expired/…)
 *  claim     — open or stale ticket, unclaimed → claim it
 *  refresh   — same session re-claiming its own live claim (idempotent heartbeat)
 *  conflict  — fresh claim held by a DIFFERENT session (refuse unless forced)
 *  takeover  — claim held by another session but stale (older than staleMinutes)
 */
export function decideClaimAction(input: {
  ticketStatus: string | null;
  claimedSessionId: string | null;
  lastActivity: string | null; // freshness anchor of the live claim
  requestedSessionId: string;
  nowMs: number;
  staleMinutes: number;
}): { action: "claim" | "refresh" | "conflict" | "takeover" | "none"; holder?: string; holderSince?: string } {
  if (!input.ticketStatus) return { action: "none" };
  if (input.ticketStatus !== "claimed") {
    if (input.ticketStatus === "open" || input.ticketStatus === "stale") return { action: "claim" };
    return { action: "none" };
  }
  if (input.claimedSessionId === input.requestedSessionId) return { action: "refresh" };
  const anchor = input.lastActivity ? Date.parse(input.lastActivity) : NaN;
  const ageMs = Number.isNaN(anchor) ? 0 : input.nowMs - anchor;
  if (ageMs < input.staleMinutes * 60_000) {
    return {
      action: "conflict",
      holder: input.claimedSessionId || "(unknown session)",
      holderSince: input.lastActivity || undefined,
    };
  }
  return {
    action: "takeover",
    holder: input.claimedSessionId || "(unknown session)",
    holderSince: input.lastActivity || undefined,
  };
}

export async function claimTicket(
  planId: string,
  role: string,
  sessionId: string,
  opts: { staleMinutes?: number; force?: boolean } = {},
): Promise<{ ticketId: string; action: string; tookOverFrom?: string }> {
  const staleMinutes = opts.staleMinutes ?? 30;
  return withTransaction(async (client) => {
    const ticket = await tOne(
      client,
      `SELECT id, status, session_id, claimed_at, last_activity
       FROM ${VISION_SCHEMA}.tickets
       WHERE plan_id = @planId AND role = @role AND status IN ('open','claimed','stale')
       ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END, created_at ASC
       LIMIT 1`,
      { planId, role },
    );
    if (!ticket) {
      throw claimError(
        "NO_CLAIMABLE_TICKET",
        `No open/claimed/stale ${role} ticket for plan ${planId}`,
      );
    }
    const decision = decideClaimAction({
      ticketStatus: ticket.status,
      claimedSessionId: ticket.session_id || null,
      lastActivity: ticket.last_activity || ticket.claimed_at || null,
      requestedSessionId: sessionId,
      nowMs: Date.now(),
      staleMinutes,
    });
    if (decision.action === "conflict" && !opts.force) {
      throw claimError(
        "TICKET_CLAIM_CONFLICT",
        `Ticket ${ticket.id} is claimed by ${decision.holder} since ${decision.holderSince} (fresh within ${staleMinutes}m). Re-claim from that session, wait for staleness, or pass force=true to take over.`,
        { holder: decision.holder, holderSince: decision.holderSince },
      );
    }
    if (decision.action === "none") {
      throw claimError("NO_CLAIMABLE_TICKET", `Ticket ${ticket.id} is ${ticket.status} — nothing to claim`);
    }
    const now = new Date().toISOString();
    await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets
       SET status = 'claimed', session_id = @sessionId, claimed_at = @now, last_activity = @now
       WHERE id = @ticketId`,
      { sessionId, now, ticketId: ticket.id },
    );
    await recordTransition({
      client,
      aggregateType: "ticket",
      aggregateId: ticket.id,
      eventType: "transition.committed",
      actor: "conduit-mcp",
      authority: "system",
      payload: {
        from_status: ticket.status,
        to_status: "claimed",
        reason: decision.action === "takeover" ? "claim_takeover" : decision.action === "refresh" ? "claim_refresh" : "manual_claim",
        session_id: sessionId,
        ...(decision.action === "takeover" ? { took_over_from: decision.holder } : {}),
      },
    });
    return {
      ticketId: ticket.id,
      action: decision.action,
      ...(decision.action === "takeover" ? { tookOverFrom: decision.holder } : {}),
    };
  });
}

/** Release a manual claim — only the claiming session may release its own. */
export async function releaseTicket(
  planId: string,
  role: string,
  sessionId: string,
): Promise<{ released: boolean; ticketId?: string }> {
  return withTransaction(async (client) => {
    const ticket = await tOne(
      client,
      `SELECT id, status, session_id FROM ${VISION_SCHEMA}.tickets
       WHERE plan_id = @planId AND role = @role AND status = 'claimed'
       ORDER BY created_at ASC LIMIT 1`,
      { planId, role },
    );
    if (!ticket) return { released: false };
    if (ticket.session_id && ticket.session_id !== sessionId) {
      throw claimError(
        "TICKET_CLAIM_CONFLICT",
        `Ticket ${ticket.id} is claimed by another session (${ticket.session_id}) — only the holder can release`,
      );
    }
    const now = new Date().toISOString();
    await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'open', session_id = NULL,
        claimed_at = NULL, last_activity = @now
       WHERE id = @ticketId`,
      { now, ticketId: ticket.id },
    );
    await recordTransition({
      client,
      aggregateType: "ticket",
      aggregateId: ticket.id,
      eventType: "transition.committed",
      actor: "conduit-mcp",
      authority: "system",
      payload: { from_status: "claimed", to_status: "open", reason: "manual_release", session_id: sessionId },
    });
    return { released: true, ticketId: ticket.id };
  });
}

// ── Stale / expired detection ───────────────────────────────────────

const DEFAULT_STALE_SECONDS = 6 * 3600;

export async function detectStaleTickets(): Promise<number> {
  return withTransaction(async (client) => {
    const threshold = new Date(Date.now() - DEFAULT_STALE_SECONDS * 1000).toISOString();
    const affected = await tAll(
      client,
      `SELECT id FROM ${VISION_SCHEMA}.tickets
      WHERE status = 'claimed' AND last_activity IS NOT NULL AND last_activity < @threshold`,
      { threshold },
    );
    const count = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'stale'
      WHERE status = 'claimed' AND last_activity IS NOT NULL AND last_activity < @threshold`,
      { threshold }
    );
    for (const t of affected) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: t.id,
        eventType: "transition.requested",
        actor: "conduit-mcp",
        authority: "system",
        payload: { from_status: "claimed", to_status: "stale", reason: "stale_detection" },
      });
    }
    return count;
  });
}

export async function detectExpiredTickets(): Promise<number> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();
    const affected = await tAll(
      client,
      `SELECT id, status FROM ${VISION_SCHEMA}.tickets
      WHERE status IN ('open', 'claimed', 'stale') AND expires_at IS NOT NULL AND expires_at < @now`,
      { now },
    );
    const count = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'expired'
      WHERE status IN ('open', 'claimed', 'stale') AND expires_at IS NOT NULL AND expires_at < @now`,
      { now }
    );
    for (const t of affected) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: t.id,
        eventType: "transition.rejected",
        actor: "conduit-mcp",
        authority: "system",
        payload: { from_status: t.status, to_status: "expired", reason: "expiry_detection" },
      });
    }
    return count;
  });
}

// ── Supersede / cancel ──────────────────────────────────────────────

export async function supersedeTicket(
  ticketId: string, reason: string, replace?: boolean,
): Promise<{
  superseded: boolean;
  oldTicket?: { plan_id: string; role: string; objective: string | null; owner: string };
  replacementId?: string;
}> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();
    const old = await tOne(
      client,
      `SELECT plan_id, role, objective, owner FROM ${VISION_SCHEMA}.tickets
       WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
      { ticketId }
    );
    if (!old) return { superseded: false };

    const fromStatus = (await tOne(
      client,
      `SELECT status FROM ${VISION_SCHEMA}.tickets WHERE id = @ticketId`,
      { ticketId },
    ))?.status || "unknown";

    await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'superseded', closed_at = @now::timestamptz,
        last_activity = @now, closure_reason = @reason
      WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
      { ticketId, now, reason }
    );

    await recordTransition({
      client,
      aggregateType: "ticket",
      aggregateId: ticketId,
      eventType: "transition.rejected",
      actor: "conduit-mcp",
      authority: "builder",
      payload: { from_status: fromStatus, to_status: "superseded", reason },
    });

    let replacementId: string | undefined;
    if (replace) {
      const expiresAt = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
      replacementId = `ticket-${old.plan_id}-${old.role}-${Date.now()}`;
      await tRun(
        client,
        `INSERT INTO ${VISION_SCHEMA}.tickets
          (id, plan_id, role, status, created_at,
           objective, owner,
           spawn_reason, last_activity, expires_at, replacement_of)
        VALUES (@id, @plan_id, @role, 'open', @created_at,
                @objective, @owner,
                @spawn_reason, @last_activity, @expires_at, @replacement_of)`,
        {
          id: replacementId, plan_id: old.plan_id, role: old.role,
          created_at: now, objective: old.objective || "",
          owner: old.owner || old.role, spawn_reason: "replacement after supersede",
          last_activity: now, expires_at: expiresAt, replacement_of: ticketId,
        }
      );
    }

    return { superseded: true, oldTicket: old, replacementId };
  });
}

export async function cancelTicket(ticketId: string, reason: string): Promise<number> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();

    const ticket = await tOne(
      client,
      `SELECT plan_id, status FROM ${VISION_SCHEMA}.tickets WHERE id = @ticketId`,
      { ticketId }
    );
    const fromStatus = ticket?.status || "unknown";

    const cancelled = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'cancelled', closed_at = @now::timestamptz,
        last_activity = @now, closure_reason = @reason
      WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
      { ticketId, now, reason }
    );

    if (cancelled > 0) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: ticketId,
        eventType: "transition.rejected",
        actor: "conduit-mcp",
        authority: "builder",
        payload: { from_status: fromStatus, to_status: "cancelled", reason },
      });
    }

    if (cancelled > 0 && ticket) {
      await _cancelWorkRequestsAndSessions(ticket.plan_id, now, client);
    }

    return cancelled;
  });
}

/**
 * Cascade cleanup helper: cancels pending work_requests for a plan and
 * closes any running sessions linked to those work requests.
 *
 * Work requests reference sessions via dco_json.metadata.session_id.
 * When tickets are cancelled, the corresponding work requests and
 * harness sessions become orphans unless explicitly cleaned up here.
 *
 * ADR-016: Accepts optional client to participate in caller's transaction.
 */
async function _cancelWorkRequestsAndSessions(
  planId: string, now: string, client?: PoolClient,
): Promise<void> {
  const queryFn = client
    ? (sql: string, params: Record<string, any>) => _rawQuery(client, sql, params)
    : q;
  const runFn = client
    ? (sql: string, params: Record<string, any>) => queryFn(sql, params).then(r => r.changes)
    : qRun;
  const allFn = client
    ? (sql: string, params: Record<string, any>) => queryFn(sql, params).then(r => r.rows)
    : qAll;

  const affectedWRs = await allFn(
    `SELECT work_request_uuid, status FROM ${VISION_SCHEMA}.work_requests
     WHERE context->>'plan_id' = @planId AND status = 'pending'`,
    { planId },
  );

  const wrCancelled = await runFn(
    `UPDATE ${VISION_SCHEMA}.work_requests SET status = 'cancelled', recorded_until_dt = NOW()
     WHERE context->>'plan_id' = @planId AND status = 'pending'`,
    { planId, now }
  );
  if (wrCancelled > 0) {
    console.log(
      `[${now}] cancelled ${wrCancelled} pending work_request(s) for plan ${planId}`,
    );
    for (const wr of affectedWRs) {
      await recordTransition({
        client,
        aggregateType: "work_request",
        aggregateId: wr.work_request_uuid,
        eventType: "work_request.failed",
        actor: "conduit-mcp",
        authority: "builder",
        payload: { from_status: wr.status, to_status: "cancelled", reason: "ticket_cancelled_cascade", plan_id: planId },
      });
    }
  }

  const sessionsClosed = await runFn(
    `UPDATE ${PG_SCHEMA}.sessions SET is_running = 0, end_iso = @now
     WHERE is_running = 1 AND id IN (
       SELECT context->>'session_id'
       FROM ${VISION_SCHEMA}.work_requests
       WHERE context->>'plan_id' = @planId
         AND context->>'session_id' IS NOT NULL
     )`,
    { planId, now }
  );
  if (sessionsClosed > 0) {
    console.log(
      `[${now}] closed ${sessionsClosed} running session(s) for plan ${planId}`,
    );
  }
}

export async function cancelTicketsByPlan(planId: string, reason: string): Promise<number> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();

    await _cancelWorkRequestsAndSessions(planId, now, client);

    const affected = await tAll(
      client,
      `SELECT id, status FROM ${VISION_SCHEMA}.tickets
      WHERE plan_id = @planId AND status IN ('open', 'claimed', 'stale', 'failed')`,
      { planId },
    );

    const count = await tRun(
      client,
      `UPDATE ${VISION_SCHEMA}.tickets SET status = 'cancelled', closed_at = @now::timestamptz,
        last_activity = @now, closure_reason = @reason
      WHERE plan_id = @planId AND status IN ('open', 'claimed', 'stale', 'failed')`,
      { planId, now, reason }
    );

    for (const t of affected) {
      await recordTransition({
        client,
        aggregateType: "ticket",
        aggregateId: t.id,
        eventType: "transition.rejected",
        actor: "conduit-mcp",
        authority: "builder",
        payload: { from_status: t.status, to_status: "cancelled", reason, plan_id: planId },
      });
    }

    return count;
  });
}

// ── Work Request CRUD (vision schema) ───────────────────────────────

export interface WorkRequestRow {
  id: number;        // BIGSERIAL internal PK
  wr_id: string;     // logical key (plan_id or work_request_uuid)
  work_request_uuid: string;  // cross-system immutable identifier
  dco_json: string;
  context: any;
  status: string;
  title: string;         // denormalized title to avoid costly joins
  step_outputs: string;
  recorded_on_dt: string;
  recorded_until_dt: string | null;
}

export async function createWorkRequest(wr: {
  id: string;
  work_request_uuid?: string;
  dco_json: string;
  context?: any;
  status?: string;
  title?: string;
  entity_key?: string | null;
}): Promise<{ ok: boolean; id: string; work_request_uuid: string }> {
  const now = new Date().toISOString();
  const ctx = wr.context ?? {};
  // Ensure plan_id is in context for backwards-compatible lookups
  if (!ctx.plan_id) ctx.plan_id = wr.id;
  const uuid = wr.work_request_uuid || crypto.randomUUID();
  if (!ctx.work_request_uuid) ctx.work_request_uuid = uuid;
  await qRun(
    `INSERT INTO ${VISION_SCHEMA}.work_requests (wr_id, work_request_uuid, dco_json, context, status, title, recorded_on_dt, entity_key)
     VALUES (@wr_id, @work_request_uuid, @dco_json, @context::jsonb, @status, @title, @now, @entity_key)
     ON CONFLICT (wr_id) DO UPDATE SET
       dco_json = EXCLUDED.dco_json,
       context = EXCLUDED.context,
       status = EXCLUDED.status,
       title = COALESCE(EXCLUDED.title, ${VISION_SCHEMA}.work_requests.title),
       entity_key = COALESCE(EXCLUDED.entity_key, ${VISION_SCHEMA}.work_requests.entity_key)`,
    {
      wr_id: wr.id,
      work_request_uuid: uuid,
      dco_json: wr.dco_json,
      context: JSON.stringify(ctx),
      status: wr.status ?? "pending",
      title: wr.title ?? "",
      entity_key: wr.entity_key ?? null,
      now,
    }
  );
  return { ok: true, id: wr.id, work_request_uuid: uuid };
}

export async function getWorkRequest(id: string): Promise<WorkRequestRow | undefined> {
  return qOne(
    `SELECT * FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = @wr_id`,
    { wr_id: id }
  );
}

export async function listWorkRequests(filters: {
  planId?: string;
  status?: string;
  limit?: number;
}): Promise<WorkRequestRow[]> {
  const conditions: string[] = [];
  const params: any = {};
  if (filters.planId) {
    conditions.push("context->>'plan_id' = @planId");
    params.planId = filters.planId;
  }
  if (filters.status) {
    conditions.push("status = @status");
    params.status = filters.status;
  }
  const where = conditions.length > 0 ? "WHERE " + conditions.join(" AND ") : "";
  const limit = filters.limit ?? 50;
  return qAll(
    `SELECT * FROM ${VISION_SCHEMA}.work_requests ${where} ORDER BY recorded_on_dt DESC LIMIT ${limit}`,
    params
  );
}

// ── Runtime Kernel Event Log ───────────────────────────────────────

export async function resolveWrUuid(wrIdOrUuid: string): Promise<string> {
  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuidRegex.test(wrIdOrUuid)) return wrIdOrUuid;
  const row = await qOne(
    `SELECT work_request_uuid FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = @wrId`,
    { wrId: wrIdOrUuid },
  );
  if (!row) return wrIdOrUuid;
  return row.work_request_uuid;
}

export interface WorkRequestEventRow {
  event_id: string;
  work_request_id: string;
  event_type: string;
  event_version: number;
  correlation_id: string | null;
  causation_id: string | null;
  occurred_at: string;
  payload: any;
  actor_type: string;
  actor_id: string;
  sequence_number: number;
}

export async function appendEvent(
  wrId: string,
  eventType: string,
  payload: Record<string, unknown> = {},
): Promise<void> {
  return withTransaction(async (client) => {
    const uuid = await resolveWrUuid(wrId);

    const currentRow = await tOne(
      client,
      `SELECT status FROM ${VISION_SCHEMA}.work_requests WHERE work_request_uuid = @uuid`,
      { uuid },
    );
    const fromStatus = currentRow?.status || "unknown";

    const eventId = crypto.randomUUID();

    // T19: populate correlation_id (the WR's identity) and causation_id
    // (the immediately-preceding event) so the WR's events form a queryable
    // causal chain under one correlation identity. correlation_id = the
    // work_request UUID; causation_id = the prior event's event_id (NULL for
    // the root WR_SUBMITTED event).
    const prevRow = await tOne(
      client,
      `SELECT event_id FROM ${PG_SCHEMA}.work_request_events
        WHERE work_request_id = @uuid
        ORDER BY sequence_number DESC
        LIMIT 1`,
      { uuid },
    );
    const prevEventId: string | null = prevRow?.event_id || null;

    await tQuery(
      client,
      `INSERT INTO ${PG_SCHEMA}.work_request_events
         (event_id, work_request_id, event_type, payload, actor_type, actor_id, correlation_id, causation_id)
       VALUES (@eventId::uuid, @uuid::uuid, @eventType, @payload::jsonb, 'system', '', @uuid::uuid, @prevEventId::uuid)`,
      { eventId, uuid, eventType, payload: JSON.stringify(payload), prevEventId },
    );

    const statusMap: Record<string, string> = {
      WR_SUBMITTED: "validated",
      WR_VALIDATED: "queued",
      WR_QUEUED: "claimed",
      WR_CLAIMED: "acked",
      WR_ACKED: "settled",
      WR_SETTLED: "settled",
      WR_REJECTED: "rejected",
      WR_FAILED: "failed",
      WR_NOOP: "noop",
      WR_DEFERRED: "deferred",
      "WORKREQUEST.CREATED": "proposed",
      "STATE.TRANSITION_COMMITTED": (payload.new_state as string)?.toLowerCase() || "pending",
      "EXECUTION.STARTED": "implementing",
      "EXECUTION.COMPLETED": "completed",
      "EXECUTION.FAILED": "failed",
    };
    const newStatus = statusMap[eventType] || "pending";
    await tQuery(
      client,
      `UPDATE ${VISION_SCHEMA}.work_requests SET status = @newStatus WHERE work_request_uuid = @uuid`,
      { newStatus, uuid },
    );

    const kernelEventTypeMap: Record<string, string> = {
      WR_SUBMITTED: "work_request.created",
      WR_VALIDATED: "work_request.dispatched",
      WR_QUEUED: "transition.committed",
      WR_CLAIMED: "work_request.dispatched",
      WR_ACKED: "transition.committed",
      WR_SETTLED: "work_request.completed",
      WR_REJECTED: "work_request.failed",
      WR_FAILED: "work_request.failed",
      WR_NOOP: "transition.committed",
      WR_DEFERRED: "transition.requested",
      "WORKREQUEST.CREATED": "work_request.created",
      "STATE.TRANSITION_COMMITTED": "transition.committed",
      "EXECUTION.STARTED": "work_request.dispatched",
      "EXECUTION.COMPLETED": "work_request.completed",
      "EXECUTION.FAILED": "work_request.failed",
    };
    await recordTransition({
      client,
      aggregateType: "work_request",
      aggregateId: uuid,
      eventType: kernelEventTypeMap[eventType] || "transition.committed",
      actor: "conduit-mcp",
      authority: "builder",
      payload: { from_status: fromStatus, to_status: newStatus, conduit_event_type: eventType, event_id: eventId },
      causationId: eventId,
      correlationId: uuid,
    });
  });
}

export async function appendLedgerEvent(
  workRequestId: string,
  eventType: string,
  payload: Record<string, unknown> = {},
  opts: {
    correlationId?: string;
    causationId?: string;
    actorType?: string;
    actorId?: string;
    eventVersion?: number;
  } = {},
): Promise<string> {
  const eventId = crypto.randomUUID();
  await q(
    `INSERT INTO ${PG_SCHEMA}.work_request_events
       (event_id, work_request_id, event_type, event_version,
        correlation_id, causation_id, payload, actor_type, actor_id)
     VALUES (@eventId::uuid, @workRequestId::uuid, @eventType, @eventVersion,
        @correlationId::uuid, @causationId::uuid, @payload::jsonb, @actorType, @actorId)`,
    {
      eventId,
      workRequestId,
      eventType,
      eventVersion: opts.eventVersion ?? 1,
      correlationId: opts.correlationId || null,
      causationId: opts.causationId || null,
      payload: JSON.stringify(payload),
      actorType: opts.actorType ?? "system",
      actorId: opts.actorId ?? "",
    },
  );
  return eventId;
}

export async function getEvents(wrId: string): Promise<WorkRequestEventRow[]> {
  const uuid = await resolveWrUuid(wrId);
  return qAll(
    `SELECT * FROM ${PG_SCHEMA}.work_request_events
     WHERE work_request_id = @uuid::uuid
     ORDER BY sequence_number ASC`,
    { uuid },
  );
}

// ── CIR-SDM violation persistence (T23 Step 8) ─────────────────────
// Governed-decision record: a blocking violation detected at WR-transition
// admission is recorded in peb.cir_violations (INSERT-only, idempotent on the
// deterministic violation_id) — never a mutation of canonical WR rows.

export interface CirViolationRow {
  violation_id: string;
  cer_id: string | null;
  event_id: string;
  rule_id: string;
  rule_version: string;
  severity: string;
  description: string;
  detected_at: number | null;
  blocking: boolean;
}

export async function insertCirViolation(v: CirViolationRow): Promise<number> {
  return qRun(
    `INSERT INTO ${PEB_SCHEMA}.cir_violations
       (violation_id, cer_id, event_id, rule_id, rule_version, severity, description, detected_at, blocking)
     VALUES (@violation_id, @cer_id, @event_id, @rule_id, @rule_version, @severity, @description, @detected_at, @blocking)
     ON CONFLICT (violation_id) DO NOTHING`,
    {
      violation_id: v.violation_id,
      cer_id: v.cer_id,
      event_id: v.event_id,
      rule_id: v.rule_id,
      rule_version: v.rule_version,
      severity: v.severity,
      description: v.description,
      detected_at: v.detected_at
        ? new Date(v.detected_at * 1000).toISOString()
        : null,
      blocking: v.blocking,
    },
  );
}

export async function getAllEvents(filters?: {
  eventType?: string;
  limit?: number;
}): Promise<WorkRequestEventRow[]> {
  const conditions: string[] = [];
  const params: Record<string, any> = {};
  if (filters?.eventType) {
    conditions.push("event_type = @eventType");
    params.eventType = filters.eventType;
  }
  const where = conditions.length > 0 ? "WHERE " + conditions.join(" AND ") : "";
  const limit = filters?.limit ?? 200;
  return qAll(
    `SELECT * FROM ${PG_SCHEMA}.work_request_events ${where} ORDER BY occurred_at DESC, sequence_number DESC LIMIT ${limit}`,
    params,
  );
}

export async function replayEvents(workRequestId: string): Promise<WorkRequestEventRow[]> {
  return qAll(
    `SELECT * FROM ${PG_SCHEMA}.replay_work_request_events(@workRequestId::uuid)`,
    { workRequestId },
  );
}

export async function replayFromCheckpoint(
  workRequestId: string,
  checkpoint: number,
): Promise<WorkRequestEventRow[]> {
  return qAll(
    `SELECT * FROM ${PG_SCHEMA}.replay_from_checkpoint(@workRequestId::uuid, @checkpoint)`,
    { workRequestId, checkpoint },
  );
}

export async function rebuildState(workRequestId: string): Promise<string> {
  const row = await qOne(
    `SELECT ${PG_SCHEMA}.rebuild_work_request_state(@workRequestId::uuid) AS state`,
    { workRequestId },
  );
  return row?.state ?? "PROPOSED";
}

export async function rebuildAllStateProjections(): Promise<number> {
  const row = await qOne(
    `SELECT ${PG_SCHEMA}.rebuild_all_state_projections() AS count`,
  );
  return row?.count ?? 0;
}

export async function getWorkRequestStateRow(
  workRequestId: string,
): Promise<any | undefined> {
  return qOne(
    `SELECT * FROM ${PG_SCHEMA}.work_request_state WHERE work_request_id = @workRequestId::uuid`,
    { workRequestId },
  );
}

export interface ProjectionDriftResult {
  expected_state: string;
  expected_vision_stage: string | null;
  expected_vision_ir_version: number;
  expected_last_event_id: string | null;
  live_state: string | null;
  live_vision_stage: string | null;
  live_vision_ir_version: number | null;
  live_last_event_id: string | null;
  has_drift: boolean;
}

export async function checkProjectionDrift(
  workRequestId: string,
): Promise<ProjectionDriftResult | undefined> {
  return qOne(
    `SELECT * FROM ${PG_SCHEMA}.check_projection_drift(@workRequestId::uuid)`,
    { workRequestId },
  );
}

export async function insertVisionIRArtifact(
  input: {
    workRequestId: string;
    eventId: string;
    irStage: string;
    irVersion?: number;
    artifactType: string;
    content: any;
  },
): Promise<string> {
  const artifactId = crypto.randomUUID();
  await q(
    `INSERT INTO ${VISION_SCHEMA}.vision_ir_artifacts
       (artifact_id, work_request_id, event_id, ir_stage, ir_version, artifact_type, content)
     VALUES (@artifactId::uuid, @workRequestId::uuid, @eventId::uuid, @irStage, @irVersion, @artifactType, @content::jsonb)`,
    {
      artifactId,
      workRequestId: input.workRequestId,
      eventId: input.eventId,
      irStage: input.irStage,
      irVersion: input.irVersion ?? 0,
      artifactType: input.artifactType,
      content: JSON.stringify(input.content),
    },
  );
  return artifactId;
}

export async function getVisionIRArtifacts(
  workRequestId: string,
  irStage?: string,
): Promise<any[]> {
  if (irStage) {
    return qAll(
      `SELECT * FROM ${VISION_SCHEMA}.vision_ir_artifacts
       WHERE work_request_id = @workRequestId::uuid AND ir_stage = @irStage
       ORDER BY ir_version ASC`,
      { workRequestId, irStage },
    );
  }
  return qAll(
    `SELECT * FROM ${VISION_SCHEMA}.vision_ir_artifacts
     WHERE work_request_id = @workRequestId::uuid
     ORDER BY ir_stage, ir_version ASC`,
    { workRequestId },
  );
}

export async function selectNextRunnable(): Promise<WorkRequestRow | undefined> {
  for (const status of ["validated", "queued", "claimed", "acked"]) {
    const row = await qOne(
      `SELECT * FROM ${VISION_SCHEMA}.work_requests
       WHERE status = @status
       ORDER BY recorded_on_dt ASC
       LIMIT 1`,
      { status },
    );
    if (row) return row;
  }
  return undefined;
}

export async function listWorkRequestStates(filters?: {
  status?: string;
  limit?: number;
}): Promise<any[]> {
  const conditions: string[] = [];
  const params: Record<string, any> = {};
  if (filters?.status) {
    conditions.push("wr.status = @status");
    params.status = filters.status;
  }
  const where = conditions.length > 0 ? "WHERE " + conditions.join(" AND ") : "";
  const limit = filters?.limit ?? 50;
  // Guard: work_request_uuid is TEXT and legacy rows may hold non-uuid ids
  // (e.g. 'wr-test-p11-local'); casting those to uuid throws and kills the
  // whole unfiltered listing. Only cast well-formed uuids.
  return qAll(
    `SELECT wr.*,
            (SELECT count(*) FROM ${PG_SCHEMA}.work_request_events e
             WHERE wr.work_request_uuid ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               AND e.work_request_id = wr.work_request_uuid::uuid) AS event_count
     FROM ${VISION_SCHEMA}.work_requests wr
     ${where}
     ORDER BY wr.recorded_on_dt DESC
     LIMIT ${limit}`,
    params,
  );
}

export async function listReceiptsByPlan(planId: string, asOf?: string): Promise<any[]> {
  if (asOf) {
    return qAll(
      `SELECT * FROM nebula.receipts_unified WHERE plan_id = @planId AND created_at <= @asOf ORDER BY created_at ASC`,
      { planId, asOf }
    );
  }
  return qAll(
    `SELECT * FROM nebula.receipts_unified WHERE plan_id = @planId ORDER BY created_at ASC`,
    { planId }
  );
}

// ── Token reporting ─────────────────────────────────────────────────

export async function getTokenUsageByPlan(planId: string): Promise<{
  plan_id: string; total_tokens: number; receipts: number;
}> {
  const row = await qOne(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
    FROM nebula.receipts_unified WHERE plan_id = @planId`,
    { planId }
  );
  return { plan_id: planId, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 };
}

export async function getTokenUsageByRole(role: string): Promise<{
  role: string; total_tokens: number; receipts: number;
}> {
  const row = await qOne(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
    FROM nebula.receipts_unified WHERE agent_role = @role`,
    { role }
  );
  return { role, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 };
}

export async function getTokenUsageByTicket(ticketId: string): Promise<{
  ticket_id: string; tokens_used: number;
}> {
  const row = await qOne(
    `SELECT COALESCE(tokens_used, 0) as tokens_used FROM ${VISION_SCHEMA}.tickets WHERE id = @ticketId`,
    { ticketId }
  );
  return { ticket_id: ticketId, tokens_used: row?.tokens_used ?? 0 };
}



export async function getTicketLineage(planId: string): Promise<Array<{
  id: string; role: string; status: string; tokens_used: number | null;
  parent_ticket_id: string | null; spawn_reason: string | null;
  replacement_of: string | null; closure_reason: string | null;
  created_at: string; closed_at: string | null;
}>> {
  return qAll(
    `SELECT id, role, status, tokens_used,
       parent_ticket_id, spawn_reason, replacement_of, closure_reason,
       created_at, closed_at
    FROM ${VISION_SCHEMA}.tickets WHERE plan_id = @planId ORDER BY created_at ASC`,
    { planId }
  );
}

// ── AI Config ───────────────────────────────────────────────────────

export interface AIProviderRow {
  id: string; name: string;
  type: "openai" | "anthropic" | "google" | "ollama" | "opencode" | "codex" | "spring_ai" | "lm_server" | "custom";
  endpoint_url: string | null; api_key: string | null;
  config_json: string; created_at: string; updated_at: string;
}

export interface AIHarnessRow {
  id: string; name: string; invocation_semantics: string;
  created_at: string; updated_at: string;
}

export interface AIModelRow {
  id: string; name: string; harness_id: string;
  provider_id: string | null; model_identifier: string;
  created_at: string; updated_at: string;
}

export interface AIRoleConfigRow {
  id: string; role: "planner" | "builder" | "reviewer" | "critic" | "analyst" | "architect" | "inspector" | "engineer" | "rover";
  provider_id: string; harness_id: string; model_id: string;
  extra_params: string; created_at: string; updated_at: string;
}

export interface AIConfigSnapshot {
  providers: AIProviderRow[]; harnesses: AIHarnessRow[];
  models: AIModelRow[]; roles: AIRoleConfigRow[];
}

export async function getAIProviders(): Promise<AIProviderRow[]> {
  return qAll("SELECT * FROM providers ORDER BY name");
}

export async function getAIProvider(id: string): Promise<AIProviderRow | undefined> {
  return qOne("SELECT * FROM providers WHERE id = @id", { id });
}

export async function upsertAIProvider(
  p: Omit<AIProviderRow, "created_at" | "updated_at"> & { created_at?: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO providers (id, name, type, endpoint_url, api_key, config_json, created_at, updated_at)
    VALUES (@id, @name, @type, @endpoint_url, @api_key, @config_json, @created_at, @updated_at)
    ON CONFLICT(id) DO UPDATE SET
      name = EXCLUDED.name, type = EXCLUDED.type,
      endpoint_url = EXCLUDED.endpoint_url, api_key = EXCLUDED.api_key,
      config_json = EXCLUDED.config_json, updated_at = EXCLUDED.updated_at`,
    {
      ...p, endpoint_url: p.endpoint_url ?? null, api_key: p.api_key ?? null,
      config_json: p.config_json ?? "{}",
      created_at: p.created_at ?? now, updated_at: now,
    }
  );
}

export async function deleteAIProvider(id: string): Promise<boolean> {
  const changes = await qRun("DELETE FROM providers WHERE id = @id", { id });
  return changes > 0;
}

export async function getAIHarnesses(): Promise<AIHarnessRow[]> {
  return qAll("SELECT * FROM harnesses ORDER BY name");
}

export async function getAIHarness(id: string): Promise<AIHarnessRow | undefined> {
  return qOne("SELECT * FROM harnesses WHERE id = @id", { id });
}

export async function upsertAIHarness(
  h: Omit<AIHarnessRow, "created_at" | "updated_at"> & { created_at?: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
    VALUES (@id, @name, @invocation_semantics, @created_at, @updated_at)
    ON CONFLICT(id) DO UPDATE SET
      name = EXCLUDED.name, invocation_semantics = EXCLUDED.invocation_semantics,
      updated_at = EXCLUDED.updated_at`,
    { ...h, invocation_semantics: h.invocation_semantics ?? "{}",
      created_at: h.created_at ?? now, updated_at: now }
  );
}

export async function deleteAIHarness(id: string): Promise<boolean> {
  const changes = await qRun("DELETE FROM harnesses WHERE id = @id", { id });
  return changes > 0;
}

export async function getAIModels(): Promise<AIModelRow[]> {
  return qAll("SELECT * FROM models ORDER BY name");
}

export async function getAIModel(id: string): Promise<AIModelRow | undefined> {
  return qOne("SELECT * FROM models WHERE id = @id", { id });
}

export async function upsertAIModel(
  m: Omit<AIModelRow, "created_at" | "updated_at"> & { created_at?: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO models (id, name, harness_id, provider_id, model_identifier, created_at, updated_at)
    VALUES (@id, @name, @harness_id, @provider_id, @model_identifier, @created_at, @updated_at)
    ON CONFLICT(id) DO UPDATE SET
      name = EXCLUDED.name, harness_id = EXCLUDED.harness_id,
      provider_id = EXCLUDED.provider_id, model_identifier = EXCLUDED.model_identifier,
      updated_at = EXCLUDED.updated_at`,
    { ...m, provider_id: m.provider_id ?? null,
      created_at: m.created_at ?? now, updated_at: now }
  );
}

export async function deleteAIModel(id: string): Promise<boolean> {
  const changes = await qRun("DELETE FROM models WHERE id = @id", { id });
  return changes > 0;
}

export async function getAIRoleConfigs(): Promise<AIRoleConfigRow[]> {
  return qAll(
    `SELECT DISTINCT ON (cb.role) cb.id, cb.role, cb.model_id,
            COALESCE(cb.provider_id, m.provider_id) AS provider_id,
            COALESCE(cb.harness_id, m.harness_id) AS harness_id,
            '{}'::TEXT AS extra_params, cb.created_at, cb.updated_at
     FROM config_bundle cb
     JOIN models m ON cb.model_id = m.id
     WHERE cb.is_active = 1
     ORDER BY cb.role, cb.priority ASC`
  );
}

export async function getAIRoleConfig(role: string): Promise<AIRoleConfigRow | undefined> {
  return qOne(
    `SELECT cb.id, cb.role, cb.model_id,
            COALESCE(cb.provider_id, m.provider_id) AS provider_id,
            COALESCE(cb.harness_id, m.harness_id) AS harness_id,
            '{}'::TEXT AS extra_params, cb.created_at, cb.updated_at
     FROM config_bundle cb
     JOIN models m ON cb.model_id = m.id
     WHERE cb.role = @role AND cb.is_active = 1
     ORDER BY cb.priority ASC LIMIT 1`,
    { role }
  );
}

export async function upsertAIRoleConfig(
  rc: Omit<AIRoleConfigRow, "created_at" | "updated_at"> & { created_at?: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO config_bundle (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
     VALUES (@id, @name, @role, @model_id, @provider_id, @harness_id, 0, 'CLI', 1, '{}', @created_at, @updated_at)
     ON CONFLICT (role, model_id) DO UPDATE SET
       id = EXCLUDED.id, provider_id = EXCLUDED.provider_id,
       harness_id = EXCLUDED.harness_id, priority = 0,
       is_active = 1, updated_at = EXCLUDED.updated_at`,
    { ...rc, name: `Primary: ${rc.model_id} for ${rc.role}`,
      extra_params: rc.extra_params ?? "{}",
      created_at: rc.created_at ?? now, updated_at: now }
  );
}

export interface AIRoleModelRow {
  id: string; role: string; model_id: string; priority: number;
  provider_id: string | null; harness_id: string | null;
}

export interface ConfigBundleRow {
  id: string;
  name: string;
  role: string;
  model_id: string;
  provider_id: string | null;
  harness_id: string | null;
  priority: number;
  invocation_mode: "CLI" | "HTTP" | "SDK" | "MCP" | "INTERACTIVE";
  command: string | null;
  endpoint_url: string | null;
  timeout_ms: number | null;
  valid_from: string | null;
  valid_to: string | null;
  is_active: number;
  metadata: string;
  created_at: string;
  updated_at: string;
}

export async function getRoleModels(role: string): Promise<AIRoleModelRow[]> {
  return qAll(
    `SELECT id, role, model_id, priority, provider_id, harness_id
     FROM config_bundle WHERE role = @role AND is_active = 1
     ORDER BY priority ASC`,
    { role }
  );
}

export async function getAllRoleModels(): Promise<AIRoleModelRow[]> {
  return qAll(
    `SELECT id, role, model_id, priority, provider_id, harness_id
     FROM config_bundle WHERE is_active = 1
     ORDER BY role, priority ASC`
  );
}

export async function upsertRoleModels(
  role: string, priorities: { model_id: string; priority: number; provider_id?: string | null; harness_id?: string | null }[],
): Promise<void> {
  if (priorities.length === 0) return;

  await withTransaction(async (client) => {
    await tRun(client, "DELETE FROM config_bundle WHERE role = @role", { role });
    for (const p of priorities) {
      await tRun(client,
        `INSERT INTO config_bundle (id, name, role, model_id, priority, provider_id, harness_id, invocation_mode, is_active, metadata, created_at, updated_at)
         VALUES (@id, @name, @role, @model_id, @priority, @provider_id, @harness_id, 'CLI', 1, '{}', @now, @now)`,
        { id: `cb-${role}-${p.model_id}`, name: `Bundle: ${p.model_id}`,
          role, model_id: p.model_id, priority: p.priority,
          provider_id: p.provider_id ?? null, harness_id: p.harness_id ?? null, now: new Date().toISOString() }
      );
    }
    // Reset role circuit breaker so scheduler can re-dispatch immediately
    await tRun(client, `DELETE FROM ${PEB_SCHEMA}.role_circuit_breaker WHERE role = @role`, { role });
    // Signal scheduler to wake from idle backoff
    await tRun(client,
      `UPDATE circuit_breaker SET wake_requested_at = @wake_at, updated_at = @wake_at WHERE id = 1`,
      { wake_at: new Date().toISOString() }
    );
  });
}

/** Import a full AI config snapshot: clear existing data and bulk-insert.
 *  Runs inside a transaction so partial imports are rolled back on error.
 *  Accepts legacy `roles` and `role_models` arrays, converting them to
 *  config_bundle entries for backward compatibility. */
export async function importAIConfig(
  data: AIConfigSnapshot & { role_models?: { role: string; model_id: string; priority: number; provider_id?: string | null; harness_id?: string | null }[] },
): Promise<{ providers: number; harnesses: number; models: number; roles: number; bundles: number }> {
  let pCount = 0, hCount = 0, mCount = 0, bCount = 0;
  const now = new Date().toISOString();

  await withTransaction(async (client) => {
    // Clear existing data in dependency order
    await tRun(client, "DELETE FROM config_bundle");
    await tRun(client, "DELETE FROM models");
    await tRun(client, "DELETE FROM harnesses");
    await tRun(client, "DELETE FROM providers");

    // Insert providers
    for (const p of data.providers || []) {
      await tRun(client,
        `INSERT INTO providers (id, name, type, endpoint_url, api_key, config_json, created_at, updated_at)
         VALUES (@id, @name, @type, @endpoint_url, @api_key, @config_json, @created_at, @updated_at)`,
        { id: p.id, name: p.name, type: p.type, endpoint_url: p.endpoint_url ?? null,
          api_key: p.api_key ?? null, config_json: p.config_json ?? "{}",
          created_at: p.created_at || now, updated_at: now }
      );
      pCount++;
    }

    // Insert harnesses
    for (const h of data.harnesses || []) {
      await tRun(client,
        `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
         VALUES (@id, @name, @invocation_semantics, @created_at, @updated_at)`,
        { id: h.id, name: h.name, invocation_semantics: h.invocation_semantics ?? "{}",
          created_at: h.created_at || now, updated_at: now }
      );
      hCount++;
    }

    // Insert models
    for (const m of data.models || []) {
      await tRun(client,
        `INSERT INTO models (id, name, harness_id, provider_id, model_identifier, created_at, updated_at)
         VALUES (@id, @name, @harness_id, @provider_id, @model_identifier, @created_at, @updated_at)`,
        { id: m.id, name: m.name, harness_id: m.harness_id, provider_id: m.provider_id ?? null,
          model_identifier: m.model_identifier, created_at: m.created_at || now, updated_at: now }
      );
      mCount++;
    }

    // Convert legacy roles to config_bundle entries (priority=0 primary)
    for (const r of data.roles || []) {
      await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @provider_id, @harness_id, 0, 'CLI', 1, '{}', @created_at, @updated_at)
         ON CONFLICT (role, model_id) DO NOTHING`,
        { id: `cb-${r.role}-${r.model_id}`, name: `Primary: ${r.model_id} for ${r.role}`,
          role: r.role, model_id: r.model_id, provider_id: r.provider_id ?? null,
          harness_id: r.harness_id ?? null, created_at: r.created_at || now, updated_at: now }
      );
    }

    // Insert role_models as config_bundle entries
    for (const rm of data.role_models || []) {
      await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, 'CLI', 1, '{}', @created_at, @updated_at)
         ON CONFLICT (role, model_id) DO NOTHING`,
        { id: `cb-${rm.role}-${rm.model_id}`, name: `Bundle: ${rm.model_id}`,
          role: rm.role, model_id: rm.model_id, priority: rm.priority ?? 0,
          provider_id: rm.provider_id ?? null, harness_id: rm.harness_id ?? null,
          created_at: now, updated_at: now }
      );
      bCount++;
    }
  });

  console.log(`[import-ai-config] Imported ${pCount} providers, ${hCount} harnesses, ${mCount} models, ${bCount} bundles.`);
  return { providers: pCount, harnesses: hCount, models: mCount, roles: (data.roles || []).length, bundles: bCount };
}

export async function getAIConfigSnapshot(): Promise<AIConfigSnapshot & { role_models: AIRoleModelRow[] }> {
  return {
    providers: await getAIProviders(),
    harnesses: await getAIHarnesses(),
    models: await getAIModels(),
    roles: await getAIRoleConfigs(),
    role_models: await getAllRoleModels(),
  };
}

export interface ConfigValidationWarning {
  role: string;
  field: string;
  message: string;
  severity: "error" | "warning";
}

export async function validateAIConfig(): Promise<ConfigValidationWarning[]> {
  const cfg = await getAIConfigSnapshot();
  const warnings: ConfigValidationWarning[] = [];

  const harnessMap = new Map(cfg.harnesses.map(h => [h.id, h]));
  const modelMap = new Map(cfg.models.map(m => [m.id, m]));
  const providerMap = new Map(cfg.providers.map(p => [p.id, p]));
  const roleModelsMap = new Map<string, AIRoleModelRow[]>();
  for (const rm of cfg.role_models) {
    const list = roleModelsMap.get(rm.role) || [];
    list.push(rm);
    roleModelsMap.set(rm.role, list);
  }

  for (const rc of cfg.roles) {
    // Check primary model exists
    if (!modelMap.has(rc.model_id)) {
      warnings.push({
        role: rc.role, field: "model_id",
        message: `Primary model '${rc.model_id}' not found in models table.`,
        severity: "error",
      });
    } else {
      const model = modelMap.get(rc.model_id)!;
      // Check primary model's harness
      const harness = harnessMap.get(model.harness_id);
      if (!harness) {
        warnings.push({
          role: rc.role, field: "harness_id",
          message: `Primary model '${rc.model_id}' references harness '${model.harness_id}' which does not exist.`,
          severity: "error",
        });
      } else {
        const sem = parseJsonSafe(harness.invocation_semantics, {});
        const binary = sem?.binary ?? "";
        if (!binary) {
          warnings.push({
            role: rc.role, field: "invocation_semantics.binary",
            message: `Harness '${harness.name}' for primary model '${model.name}' has no 'binary' in invocation_semantics. Model will be skipped in the execution chain.`,
            severity: "error",
          });
        }
      }

      // Check primary model's provider
      if (model.provider_id && !providerMap.has(model.provider_id)) {
        warnings.push({
          role: rc.role, field: "provider_id",
          message: `Primary model '${rc.model_id}' references provider '${model.provider_id}' which does not exist.`,
          severity: "warning",
        });
      }
    }

    // Check fallback models (role_models)
    const rms = roleModelsMap.get(rc.role) || [];
    for (const rm of rms) {
      if (!modelMap.has(rm.model_id)) {
        warnings.push({
          role: rc.role, field: "model_id",
          message: `Fallback model '${rm.model_id}' not found in models table.`,
          severity: "error",
        });
        continue;
      }
      const model = modelMap.get(rm.model_id)!;
      const harnessId = rm.harness_id || model.harness_id;
      const harness = harnessMap.get(harnessId);
      if (!harness) {
        warnings.push({
          role: rc.role, field: "harness_id",
          message: `Fallback model '${rm.model_id}' references harness '${harnessId}' which does not exist.`,
          severity: "error",
        });
      } else {
        const sem = parseJsonSafe(harness.invocation_semantics, {});
        const binary = sem?.binary ?? "";
        if (!binary) {
          warnings.push({
            role: rc.role, field: "invocation_semantics.binary",
            message: `Harness '${harness.name}' for fallback model '${model.name}' has no 'binary' in invocation_semantics. This model will be skipped in the execution chain.`,
            severity: "warning",
          });
        }
      }

      const providerId = rm.provider_id || model.provider_id;
      if (providerId && !providerMap.has(providerId)) {
        warnings.push({
          role: rc.role, field: "provider_id",
          message: `Fallback model '${rm.model_id}' references provider '${providerId}' which does not exist.`,
          severity: "warning",
        });
      }
    }

    // Warn if role has no fallback models
    if (rms.length === 0) {
      warnings.push({
        role: rc.role, field: "model_priorities",
        message: `Role '${rc.role}' has no fallback models configured. If the primary model fails, execution will halt.`,
        severity: "warning",
      });
    }
  }

  return warnings;
}

function parseJsonSafe(text: string, fallback: any): any {
  try { return JSON.parse(text); } catch { return fallback; }
}

// ── Governance Events ────────────────────────────────────────────────

/**
 * Replay governance events for receipts that don't have one yet.
 * Idempotent — safe to call multiple times.
 * Returns the count of newly emitted events.
 */
export async function replayGovernanceEvents(): Promise<{ replayed: number }> {
  const result = await qRun(`
    INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, plan_id, agent_role, payload, created_at)
    SELECT
      r.id,
      'receipt:' || r.type,
      r.plan_id,
      r.agent_role,
      jsonb_build_object(
        'session_id', r.session_id,
        'artifact_path', r.artifact_path,
        'summary', r.summary,
        'ticket_id', r.ticket_id,
        'tokens_used', r.tokens_used
      ),
      r.created_at::timestamptz
    FROM ${VISION_SCHEMA}.receipts r
    WHERE NOT EXISTS (
      SELECT 1 FROM ${PEB_SCHEMA}.governance_events g WHERE g.receipt_id = r.id
    )
    ON CONFLICT (receipt_id) DO NOTHING
  `);
  const replayed = result ?? 0;

  // Mark replayed events with replayed_at = NOW()
  if (replayed > 0) {
    await qRun(`
      UPDATE ${PEB_SCHEMA}.governance_events
      SET replayed_at = NOW()
      WHERE replayed_at IS NULL
      AND receipt_id IN (
        SELECT r.id FROM ${VISION_SCHEMA}.receipts r
        WHERE r.created_at::timestamptz < NOW() - INTERVAL '1 second'
      )
    `);
  }

  return { replayed };
}

/**
 * List recent governance events, optionally filtered by plan_id or event_type.
 */
export async function listGovernanceEvents(filters: {
  planId?: string;
  eventType?: string;
  asOf?: string;
  limit?: number;
}): Promise<any[]> {
  const conditions: string[] = [];
  const params: any = {};
  if (filters.planId) {
    conditions.push("plan_id = @planId");
    params.planId = filters.planId;
  }
  if (filters.eventType) {
    conditions.push("event_type = @eventType");
    params.eventType = filters.eventType;
  }
  if (filters.asOf) {
    conditions.push("created_at <= @asOf");
    params.asOf = filters.asOf;
  }
  const where = conditions.length > 0 ? "WHERE " + conditions.join(" AND ") : "";
  const limit = filters.limit ?? 50;
  return qAll(
    `SELECT * FROM ${PEB_SCHEMA}.governance_events ${where} ORDER BY created_at DESC LIMIT ${limit}`,
    params
  );
}

// ── Seed defaults ───────────────────────────────────────────────────

const DEFAULT_PROVIDERS = [
  { name: "OpenAI", type: "openai", endpoint_url: "https://api.openai.com/v1" },
  { name: "Anthropic", type: "anthropic", endpoint_url: "https://api.anthropic.com/v1" },
  { name: "Ollama", type: "ollama", endpoint_url: "http://192.168.1.202:11434" },
  { name: "OpenCode", type: "opencode", endpoint_url: "http://localhost:3100" },
  { name: "Codex", type: "codex", endpoint_url: "" },
];

const DEFAULT_MODELS: Array<{ id: string; name: string; harnessId: string; providerId: string; modelId: string }> = [
  { id: "mod-gpt4o", name: "GPT-4o", harnessId: "harn-opencode", providerId: "prov-openai", modelId: "gpt-4o" },
  { id: "mod-claude-sonnet", name: "Claude Sonnet 4", harnessId: "harn-opencode", providerId: "prov-anthropic", modelId: "claude-sonnet-4-20250514" },
  { id: "mod-llama3", name: "Llama 3 (local)", harnessId: "harn-ollama-sdk", providerId: "prov-ollama", modelId: "llama3" },
  { id: "mod-big-pickle", name: "Big Pickle", harnessId: "harn-opencode", providerId: "prov-opencode", modelId: "big-pickle" },
  { id: "mod-codex-gpt4o", name: "GPT-4o (via Codex)", harnessId: "harn-codex-cli", providerId: "prov-codex", modelId: "gpt-4o" },
];

const ALL_ROLES = ["planner", "builder", "reviewer", "critic", "analyst", "architect", "inspector", "engineer", "rover"] as const;

export async function seedDefaultAIConfig(force?: boolean): Promise<{
  seeded: boolean; providers: number; harnesses: number; models: number; roles: number; message: string;
}> {
  if (!force) {
    const existing = await qOne("SELECT COUNT(*) as c FROM providers");
    if (existing?.c > 0) {
      return { seeded: false, providers: 0, harnesses: 0, models: 0, roles: 0,
        message: "Config already exists — not overwriting." };
    }
  }

  let pCount = 0, hCount = 0, mCount = 0, rCount = 0;
  const now = new Date().toISOString();

  await withTransaction(async (client) => {
    // Providers
    for (const p of DEFAULT_PROVIDERS) {
      const id = `prov-${p.type}`;
      const changes = await tRun(client,
        `INSERT OR IGNORE INTO providers (id, name, type, endpoint_url, api_key, config_json, created_at, updated_at)
         VALUES (@id, @name, @type, @endpoint_url, '', '{}', @now, @now)`,
        { id, name: p.name, type: p.type, endpoint_url: p.endpoint_url, now }
      );
      if (changes > 0) pCount++;
    }

    // Harnesses
    const opencodeSemantics = JSON.stringify({
      binary: "opencode", capabilities: { model: true, agent: true, working_directory: true, system_prompt: false },
      execution: { mode: "interactive", subcommand: "run" },
      semantics: { model: { type: "flag", flag: "--model" }, agent: { type: "flag", flag: "--agent" }, working_directory: { type: "flag", flag: "--dir" } },
      role_mapping: { strategy: "agent" },
    });
    let changes = await tRun(client,
      "INSERT OR IGNORE INTO harnesses (id, name, invocation_semantics, created_at, updated_at) VALUES (@id, @name, @invocation_semantics, @now, @now)",
      { id: "harn-opencode", name: "Opencode CLI", invocation_semantics: opencodeSemantics, now }
    );
    if (changes > 0) hCount++;

    const ollamaSemantics = JSON.stringify({
      binary: "ollama", capabilities: { model: true, agent: false, working_directory: false, system_prompt: true },
      execution: { mode: "daemon" },
      semantics: { model: { type: "positional_after_subcommand", subcommand: "run" }, system_prompt: { type: "flag", flag: "--system" } },
      role_mapping: { strategy: "none" },
    });
    changes = await tRun(client,
      "INSERT OR IGNORE INTO harnesses (id, name, invocation_semantics, created_at, updated_at) VALUES (@id, @name, @invocation_semantics, @now, @now)",
      { id: "harn-ollama-sdk", name: "Ollama SDK", invocation_semantics: ollamaSemantics, now }
    );
    if (changes > 0) hCount++;

    const codexSemantics = JSON.stringify({
      binary: "codex", capabilities: { model: false, agent: false, working_directory: true, system_prompt: true },
      execution: { mode: "oneshot", subcommand: "exec" },
      semantics: { working_directory: { type: "flag", flag: "--cd" } },
      role_mapping: { strategy: "prompt_file" },
    });
    changes = await tRun(client,
      "INSERT OR IGNORE INTO harnesses (id, name, invocation_semantics, created_at, updated_at) VALUES (@id, @name, @invocation_semantics, @now, @now)",
      { id: "harn-codex-cli", name: "Codex CLI", invocation_semantics: codexSemantics, now }
    );
    if (changes > 0) hCount++;

    // Models
    for (const md of DEFAULT_MODELS) {
      const changes = await tRun(client,
        `INSERT OR IGNORE INTO models (id, name, harness_id, provider_id, model_identifier, created_at, updated_at)
         VALUES (@id, @name, @harness_id, @provider_id, @model_identifier, @now, @now)`,
        { id: md.id, name: md.name, harness_id: md.harnessId, provider_id: md.providerId, model_identifier: md.modelId, now }
      );
      if (changes > 0) mCount++;
    }

    // Role configs (stored as config_bundle with priority=0 for primary)
    for (const role of ALL_ROLES) {
      const changes = await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, priority, provider_id, harness_id, invocation_mode, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, 0, @provider_id, @harness_id, 'CLI', 1, '{}', @now, @now)
         ON CONFLICT (role, model_id) DO NOTHING`,
        { id: `cb-${role}-mod-gpt4o`, name: `Default: GPT-4o for ${role}`, role,
          model_id: "mod-gpt4o", provider_id: "prov-openai", harness_id: "harn-opencode", now }
      );
      if (changes > 0) rCount++;
    }

    console.log(`[seed-defaults] ${force ? "Force re-" : "S"}eeded ${pCount} providers, ${hCount} harnesses, ${mCount} models, ${rCount} role configs.`);
  });

  return {
    seeded: true, providers: pCount, harnesses: hCount, models: mCount, roles: rCount,
    message: `${force ? "Force re-s" : "S"}eeded ${pCount} providers, ${hCount} harnesses, ${mCount} models, ${rCount} role configs.`,
  };
}
