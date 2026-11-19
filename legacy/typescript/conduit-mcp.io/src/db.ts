import crypto from "crypto";
import { Pool, PoolClient } from "pg";

// ── Connection ──────────────────────────────────────────────────────

const PG_SCHEMA = process.env.CONDUIT_PG_SCHEMA || "conduit";
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

/** Transaction wrapper. */
async function withTransaction<T>(
  cb: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await pool.connect();
  await client.query(`SET search_path TO ${PG_SCHEMA},${TACKLE_SCHEMA}`);
  try {
    await client.query("BEGIN");
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
    CREATE TABLE IF NOT EXISTS plans (
      id            TEXT PRIMARY KEY,
      file_name     TEXT NOT NULL,
      title         TEXT NOT NULL DEFAULT '',
      project       TEXT NOT NULL DEFAULT '',
      goal          TEXT NOT NULL DEFAULT '',
      content       TEXT NOT NULL DEFAULT '',
      files_affected    TEXT NOT NULL DEFAULT '[]',
      acceptance_criteria TEXT NOT NULL DEFAULT '[]',
      dependencies  TEXT NOT NULL DEFAULT '[]',
      prompt_ref    TEXT NOT NULL DEFAULT '',
      notes         TEXT NOT NULL DEFAULT '',
      priority      INTEGER NOT NULL DEFAULT 0,
      deleted       INTEGER NOT NULL DEFAULT 0,
      created_at    TEXT NOT NULL,
      updated_at    TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(updated_at);

    -- Schema version tracking for formal migrations
    CREATE TABLE IF NOT EXISTS schema_version (
      version     INTEGER PRIMARY KEY,
      description TEXT NOT NULL,
      applied_at  TEXT NOT NULL
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
      created_at          TEXT NOT NULL,
      claimed_at          TEXT,
      closed_at           TEXT,
      token_budget        INTEGER,
      tokens_used         INTEGER,
      objective           TEXT,
      completion_criteria TEXT,
      owner               TEXT NOT NULL DEFAULT '',
      parent_ticket_id    TEXT REFERENCES ${VISION_SCHEMA}.tickets(id),
      spawn_reason        TEXT,
      last_activity       TEXT,      expires_at          TEXT,
          deadline            TEXT,
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
                      'PROPOSED','PLANNING',
                      'REVIEW','CRITIQUE','CRITIQUE_PASS','CRITIQUE_REJECT','PLAN_BLOCK','API_LIMIT',
                      'REQUEUED',
                      'CANCELLED','ABANDONED'
                    )),
      agent_role    TEXT NOT NULL,
      session_id    TEXT,
      artifact_path TEXT,
      summary       TEXT NOT NULL DEFAULT '',
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at    TEXT NOT NULL,
      ticket_id     TEXT REFERENCES ${VISION_SCHEMA}.tickets(id),
      tokens_used   INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
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
      workflow_id     TEXT,
      run_id          TEXT,
      workflow_start_time TEXT,
      workflow_close_time TEXT,
      workflow_run_time_ms REAL,
      workflow_result TEXT,
      created_at      TEXT NOT NULL,
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
      tripped_at             TEXT,
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
      updated_at             TEXT
    );

    INSERT INTO circuit_breaker (id, tripped) VALUES (1, 0)
    ON CONFLICT (id) DO NOTHING;

    ALTER TABLE circuit_breaker ADD COLUMN IF NOT EXISTS wake_requested_at TEXT;

    -- role_circuit_breaker lives in peb schema (governance engine)
    CREATE TABLE IF NOT EXISTS ${PEB_SCHEMA}.role_circuit_breaker (
      role            TEXT PRIMARY KEY,
      tripped         INTEGER DEFAULT 0,
      tripped_at      TEXT,
      retry_after     INTEGER DEFAULT 1800,
      error           TEXT,
      failure_count   INTEGER DEFAULT 0,
      updated_at      TEXT
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

    -- AFTER INSERT trigger: fire-and-forget event emission from vision → peb.
    -- No coupling to decision engine yet. This is eventual consistency only —
    -- governance is observable before it becomes authoritative.
    CREATE OR REPLACE FUNCTION vision.receipt_governance_trigger()
    RETURNS TRIGGER AS $TRIG$
    BEGIN
      INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, work_request_id, plan_id, agent_role, payload)
      VALUES (
        NEW.id,
        'receipt:' || NEW.type,
        NULL,
        NEW.plan_id,
        NEW.agent_role,
        jsonb_build_object(
          'session_id', NEW.session_id,
          'artifact_path', NEW.artifact_path,
          'summary', NEW.summary,
          'ticket_id', NEW.ticket_id,
          'tokens_used', NEW.tokens_used
        )
      )
      ON CONFLICT (receipt_id) DO NOTHING;
      RETURN NEW;
    END;
    $TRIG$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_receipt_governance ON ${VISION_SCHEMA}.receipts;
    CREATE TRIGGER trg_receipt_governance
    AFTER INSERT ON ${VISION_SCHEMA}.receipts
    FOR EACH ROW
    EXECUTE FUNCTION vision.receipt_governance_trigger();

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
      created_at   TEXT NOT NULL,
      updated_at   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.harnesses (
      id                   TEXT PRIMARY KEY,
      name                 TEXT NOT NULL,
      invocation_semantics TEXT NOT NULL DEFAULT '{}',
      created_at           TEXT NOT NULL,
      updated_at           TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.models (
      id               TEXT PRIMARY KEY,
      name             TEXT NOT NULL,
      harness_id       TEXT NOT NULL REFERENCES ${TACKLE_SCHEMA}.harnesses(id) ON DELETE CASCADE,
      provider_id      TEXT REFERENCES ${TACKLE_SCHEMA}.providers(id),
      model_identifier TEXT NOT NULL,
      created_at       TEXT NOT NULL,
      updated_at       TEXT NOT NULL
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
                        CHECK(invocation_mode IN ('CLI', 'HTTP', 'SDK', 'MCP')),
      command         TEXT,
      endpoint_url    TEXT,
      timeout_ms      INTEGER,
      valid_from      TEXT,
      valid_to        TEXT,
      is_active       INTEGER NOT NULL DEFAULT 1,
      metadata        TEXT NOT NULL DEFAULT '{}',
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL,
      UNIQUE(role, model_id)
    );

    DROP VIEW IF EXISTS ${PG_SCHEMA}.plans_by_status;
    DROP VIEW IF EXISTS ${PG_SCHEMA}.plan_status CASCADE;
    CREATE VIEW ${PG_SCHEMA}.plan_status AS
    SELECT 
      p.*,
      CASE
        -- REQUEUED: circuit breaker reset — checked FIRST so it can override even
        -- REVIEW_PASS (e.g. plan was completed, then manually requeued for retry).
        -- Uses "latest meaningful receipt" guard so stale REQUEUED receipts from
        -- a previous breaker trip don't override later IMPLEMENTATION/REVIEW_PASS.
        WHEN (
          SELECT r.type FROM ${VISION_SCHEMA}.receipts r
          WHERE r.plan_id = p.id
          AND r.type NOT IN ('PROPOSED', 'PLANNING')
          ORDER BY r.created_at DESC LIMIT 1
        ) = 'REQUEUED' THEN 'PLAN_CREATE'
        -- REVIEW_PASS — terminal success, unless overridden by later BLOCK/PLAN_BLOCK
        WHEN EXISTS (
          SELECT 1 FROM ${VISION_SCHEMA}.receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_PASS'
          AND NOT EXISTS (
            SELECT 1 FROM ${VISION_SCHEMA}.receipts r2
            WHERE r2.plan_id = p.id
            AND r2.type IN ('BLOCK', 'PLAN_BLOCK', 'CANCELLED', 'ABANDONED')
            AND r2.created_at > r.created_at
          )
        ) THEN 'REVIEW_PASS'
        -- REVIEW_REJECT — show latest non-BLOCK receipt or fallback to PLAN_CREATE
        WHEN EXISTS (
          SELECT 1 FROM ${VISION_SCHEMA}.receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_REJECT'
        ) THEN COALESCE(
          (SELECT r.type FROM ${VISION_SCHEMA}.receipts r 
           WHERE r.plan_id = p.id 
           AND r.type != 'BLOCK'
           ORDER BY r.created_at DESC LIMIT 1),
          'PLAN_CREATE'
        )
        ELSE COALESCE(
          (SELECT r.type FROM ${VISION_SCHEMA}.receipts r 
           WHERE r.plan_id = p.id 
           AND r.type NOT IN ('PROPOSED', 'PLANNING')
           ORDER BY r.created_at DESC LIMIT 1),
          (SELECT r.type FROM ${VISION_SCHEMA}.receipts r 
           WHERE r.plan_id = p.id 
           ORDER BY r.created_at DESC LIMIT 1),
          NULL
        )
      END AS derived_status
    FROM ${PG_SCHEMA}.plans p
    WHERE p.deleted = 0;

    CREATE VIEW ${PG_SCHEMA}.plans_by_status AS
    SELECT 
      ps.derived_status AS status,
      ps.*
    FROM ${PG_SCHEMA}.plan_status ps;
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
      await exec(`ALTER TABLE plans ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''`);
    },
  },
  {
    version: 4,
    description: "Add priority INTEGER column to plans for ordering importance",
    up: async (exec) => {
      await exec(`ALTER TABLE plans ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0`);
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
      await exec(`ALTER TABLE tickets ADD COLUMN IF NOT EXISTS deadline TEXT`);
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
      await exec(`
        CREATE OR REPLACE FUNCTION vision.receipt_governance_trigger()
        RETURNS TRIGGER AS $TRIG$
        BEGIN
          INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, work_request_id, plan_id, agent_role, payload)
          VALUES (
            NEW.id,
            'receipt:' || NEW.type,
            NULL,
            NEW.plan_id,
            NEW.agent_role,
            jsonb_build_object(
              'session_id', NEW.session_id,
              'artifact_path', NEW.artifact_path,
              'summary', NEW.summary,
              'ticket_id', NEW.ticket_id,
              'tokens_used', NEW.tokens_used
            )
          )
          ON CONFLICT (receipt_id) DO NOTHING;
          RETURN NEW;
        END;
        $TRIG$ LANGUAGE plpgsql
      `);
      await exec(`
        DROP TRIGGER IF EXISTS trg_receipt_governance ON ${VISION_SCHEMA}.receipts;
        CREATE TRIGGER trg_receipt_governance
        AFTER INSERT ON ${VISION_SCHEMA}.receipts
        FOR EACH ROW
        EXECUTE FUNCTION vision.receipt_governance_trigger()
      `);

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
          r.created_at::timestamptz
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
      // When a receipt is inserted and its plan_id matches a work_request's
      // wr_id, the work_request_uuid is copied into the governance event.
      await exec(`
        CREATE OR REPLACE FUNCTION vision.receipt_governance_trigger()
        RETURNS TRIGGER AS $TRIG$
        DECLARE
          v_wr_uuid TEXT;
        BEGIN
          -- Look up the work_request_uuid from vision.work_requests
          -- using NEW.plan_id as the wr_id lookup key
          SELECT wr.work_request_uuid INTO v_wr_uuid
          FROM ${VISION_SCHEMA}.work_requests wr
          WHERE wr.wr_id = NEW.plan_id
          LIMIT 1;

          INSERT INTO ${PEB_SCHEMA}.governance_events (
            receipt_id, event_type, work_request_id, plan_id, agent_role, payload
          ) VALUES (
            NEW.id,
            'receipt:' || NEW.type,
            v_wr_uuid,
            NEW.plan_id,
            NEW.agent_role,
            jsonb_build_object(
              'session_id', NEW.session_id,
              'artifact_path', NEW.artifact_path,
              'summary', NEW.summary,
              'ticket_id', NEW.ticket_id,
              'tokens_used', NEW.tokens_used
            )
          )
          ON CONFLICT (receipt_id) DO NOTHING;
          RETURN NEW;
        END;
        $TRIG$ LANGUAGE plpgsql
      `);

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

export async function upsertPlan(plan: UpsertPlanInput): Promise<void> {
  await qRun(
    `INSERT INTO plans (id, file_name, title, project, goal, content,
      files_affected, acceptance_criteria, dependencies, prompt_ref,
      notes, priority, deleted, created_at, updated_at)
    VALUES (@id, @file_name, @title, @project, @goal, @content,
      @files_affected, @acceptance_criteria, @dependencies, @prompt_ref,
      @notes, @priority, @deleted, @created_at, @updated_at)
    ON CONFLICT(id) DO UPDATE SET
      title = EXCLUDED.title,
      goal = EXCLUDED.goal,
      files_affected = EXCLUDED.files_affected,
      acceptance_criteria = EXCLUDED.acceptance_criteria,
      dependencies = EXCLUDED.dependencies,
      prompt_ref = EXCLUDED.prompt_ref,
      priority = EXCLUDED.priority,
      updated_at = EXCLUDED.updated_at`,
    { ...plan, deleted: plan.deleted ?? 0, notes: plan.notes ?? '', priority: plan.priority ?? 0 }
  );
}

export function checkpointWal(): void {
  // No-op: PG doesn't use WAL checkpointing from application layer
}

export async function getPlan(id: string): Promise<PlanRow | undefined> {
  return qOne(
    "SELECT * FROM plan_status WHERE id = @id",
    { id }
  );
}

export async function getPlansByStatus(status: string): Promise<PlanRow[]> {
  return qAll(
    "SELECT * FROM plan_status WHERE derived_status = @status",
    { status }
  );
}

export async function getAllPlans(): Promise<PlanRow[]> {
  return qAll("SELECT * FROM plan_status");
}

export async function getPlanById(id: string): Promise<PlanRow | undefined> {
  return qOne("SELECT * FROM plans WHERE id = @id", { id });
}

export async function softDeletePlan(planId: string): Promise<boolean> {
  const changes = await qRun(
    "UPDATE plans SET deleted = 1, updated_at = @now WHERE id = @planId AND deleted = 0",
    { planId, now: new Date().toISOString() }
  );
  return changes > 0;
}

export async function undeletePlan(planId: string): Promise<boolean> {
  const changes = await qRun(
    "UPDATE plans SET deleted = 0, updated_at = @now WHERE id = @planId AND deleted = 1",
    { planId, now: new Date().toISOString() }
  );
  return changes > 0;
}

export async function hardDeletePlan(planId: string): Promise<{
  deleted: boolean;
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
      client, "DELETE FROM plans WHERE id = @planId", { planId }
    );
    return {
      deleted: changes > 0,
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

export async function insertReceipt(r: ReceiptRow): Promise<void> {
  await qRun(
    `INSERT INTO ${VISION_SCHEMA}.receipts
      (id, plan_id, type, agent_role, session_id, ticket_id, artifact_path, summary, metadata_json, tokens_used, created_at)
    VALUES (@id, @plan_id, @type, @agent_role, @session_id, @ticket_id, @artifact_path, @summary, @metadata_json, @tokens_used, @created_at)`,
    { ...r, tokens_used: r.tokens_used ?? 0 }
  );
}

export async function getReceiptsForPlan(planId: string): Promise<ReceiptRow[]> {
  return qAll(
    `SELECT * FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId ORDER BY created_at ASC`,
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
    `SELECT type FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId ORDER BY created_at DESC LIMIT 1`,
    { planId }
  );
  return row?.type ?? null;
}

export async function getReceiptCount(): Promise<{ type: string; count: number }[]> {
  return qAll(
    `SELECT type, COUNT(*) as count FROM ${VISION_SCHEMA}.receipts GROUP BY type`
  );
}

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
  proposed: PlanRow[];
  planning: PlanRow[];
}

export async function getPlansGroupedByStatus(): Promise<PlansByStatus> {
  const all = await qAll("SELECT * FROM plan_status") as PlanRow[];

  const result: PlansByStatus = {
    pending: [], active: [], completed: [], blocked: [],
    archived: [], proposed: [], planning: [],
  };

  for (const plan of all) {
    switch (plan.derived_status) {
      case "PLAN_CREATE": result.pending.push(plan); break;
      case "IMPLEMENTATION": result.active.push(plan); break;
      case "REVIEW_PASS": result.completed.push(plan); break;
      case "BLOCK": result.blocked.push(plan); break;
      case "REVIEW_REJECT": result.active.push(plan); break;
      case "PROPOSED": result.proposed.push(plan); break;
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
  model: string | null;
  fallback_used: number;
  cost_usd: number | null;
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
    `SELECT type FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId
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
): Promise<number> {
  if (await _isPlanTerminal(planId)) {
    console.log(`Guard: plan ${planId} has terminal receipt(s) — skipping ticket creation`);
    return 0;
  }

  const nextRoles: string[] = [];
  if (terminalStatus === "completed") {
    if (ticketRole === "builder") nextRoles.push("reviewer");
    else if (ticketRole === "planner") nextRoles.push("builder", "critic");
    else if (ticketRole === "critic") nextRoles.push("builder");
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
  const now = new Date().toISOString();
  return qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'open', session_id = NULL,
      claimed_at = NULL, last_activity = @now
    WHERE session_id = @sessionId AND status = 'claimed'`,
    { sessionId, now }
  );
}

export async function resetAbandonedTickets(): Promise<number> {
  const now = new Date().toISOString();
  return qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'open', closed_at = NULL, last_activity = @now
    WHERE status = 'abandoned'`,
    { now }
  );
}

// ── Stale / expired detection ───────────────────────────────────────

const DEFAULT_STALE_SECONDS = 6 * 3600;

export async function detectStaleTickets(): Promise<number> {
  const threshold = new Date(Date.now() - DEFAULT_STALE_SECONDS * 1000).toISOString();
  return qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'stale'
    WHERE status = 'claimed' AND last_activity IS NOT NULL AND last_activity < @threshold`,
    { threshold }
  );
}

export async function detectExpiredTickets(): Promise<number> {
  const now = new Date().toISOString();
  return qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'expired'
    WHERE status IN ('open', 'claimed', 'stale') AND expires_at IS NOT NULL AND expires_at < @now`,
    { now }
  );
}

// ── Supersede / cancel ──────────────────────────────────────────────

export async function supersedeTicket(
  ticketId: string, reason: string, replace?: boolean,
): Promise<{
  superseded: boolean;
  oldTicket?: { plan_id: string; role: string; objective: string | null; owner: string };
  replacementId?: string;
}> {
  const now = new Date().toISOString();
  const old = await qOne(
    `SELECT plan_id, role, objective, owner FROM ${VISION_SCHEMA}.tickets
     WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
    { ticketId }
  );
  if (!old) return { superseded: false };

  await qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'superseded', closed_at = @now,
      last_activity = @now, closure_reason = @reason
    WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
    { ticketId, now, reason }
  );

  let replacementId: string | undefined;
  if (replace) {
    const expiresAt = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
    replacementId = `ticket-${old.plan_id}-${old.role}-${Date.now()}`;
    await qRun(
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
}

export async function cancelTicket(ticketId: string, reason: string): Promise<number> {
  const now = new Date().toISOString();

  // Look up the plan_id so we can cascade to work_requests/sessions
  const ticket = await qOne(
    `SELECT plan_id FROM ${VISION_SCHEMA}.tickets WHERE id = @ticketId`,
    { ticketId }
  );

  const cancelled = await qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'cancelled', closed_at = @now,
      last_activity = @now, closure_reason = @reason
    WHERE id = @ticketId AND status IN ('open', 'claimed', 'stale')`,
    { ticketId, now, reason }
  );

  // Cascade: if we cancelled the ticket, clean up work_requests and
  // sessions for the plan to prevent orphaned state.
  if (cancelled > 0 && ticket) {
    await _cancelWorkRequestsAndSessions(ticket.plan_id, now);
  }

  return cancelled;
}

/**
 * Cascade cleanup helper: cancels pending work_requests for a plan and
 * closes any running sessions linked to those work requests.
 *
 * Work requests reference sessions via dco_json.metadata.session_id.
 * When tickets are cancelled, the corresponding work requests and
 * harness sessions become orphans unless explicitly cleaned up here.
 */
async function _cancelWorkRequestsAndSessions(planId: string, now: string): Promise<void> {
  const wrCancelled = await qRun(
    `UPDATE ${VISION_SCHEMA}.work_requests SET status = 'cancelled', recorded_until_dt = NOW()
     WHERE context->>'plan_id' = @planId AND status = 'pending'`,
    { planId, now }
  );
  if (wrCancelled > 0) {
    console.log(
      `[${now}] cancelled ${wrCancelled} pending work_request(s) for plan ${planId}`,
    );
  }

  // Note: sessions stay in conduit — not being migrated
  const sessionsClosed = await qRun(
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
  const now = new Date().toISOString();

  // Cascade: cancel pending work_requests and close linked running sessions.
  // Without this, cancelled tickets leave orphaned work_requests (stuck in
  // 'pending') and sessions (stuck with is_running=1).
  await _cancelWorkRequestsAndSessions(planId, now);

  return qRun(
    `UPDATE ${VISION_SCHEMA}.tickets SET status = 'cancelled', closed_at = @now,
      last_activity = @now, closure_reason = @reason
    WHERE plan_id = @planId AND status IN ('open', 'claimed', 'stale', 'failed')`,
    { planId, now, reason }
  );
}

// ── Work Request CRUD (vision schema) ───────────────────────────────

export interface WorkRequestRow {
  id: number;        // BIGSERIAL internal PK
  wr_id: string;     // logical key (plan_id or work_request_uuid)
  work_request_uuid: string;  // cross-system immutable identifier
  dco_json: string;
  context: any;
  status: string;
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
}): Promise<{ ok: boolean; id: string; work_request_uuid: string }> {
  const now = new Date().toISOString();
  const ctx = wr.context ?? {};
  // Ensure plan_id is in context for backwards-compatible lookups
  if (!ctx.plan_id) ctx.plan_id = wr.id;
  const uuid = wr.work_request_uuid || crypto.randomUUID();
  if (!ctx.work_request_uuid) ctx.work_request_uuid = uuid;
  await qRun(
    `INSERT INTO ${VISION_SCHEMA}.work_requests (wr_id, work_request_uuid, dco_json, context, status, recorded_on_dt)
     VALUES (@wr_id, @work_request_uuid, @dco_json, @context::jsonb, @status, @now)
     ON CONFLICT (wr_id) DO UPDATE SET
       dco_json = EXCLUDED.dco_json,
       context = EXCLUDED.context,
       status = EXCLUDED.status`,
    {
      wr_id: wr.id,
      work_request_uuid: uuid,
      dco_json: wr.dco_json,
      context: JSON.stringify(ctx),
      status: wr.status ?? "pending",
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

export async function listReceiptsByPlan(planId: string, asOf?: string): Promise<any[]> {
  if (asOf) {
    return qAll(
      `SELECT * FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId AND created_at <= @asOf ORDER BY created_at ASC`,
      { planId, asOf }
    );
  }
  return qAll(
    `SELECT * FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId ORDER BY created_at ASC`,
    { planId }
  );
}

// ── Token reporting ─────────────────────────────────────────────────

export async function getTokenUsageByPlan(planId: string): Promise<{
  plan_id: string; total_tokens: number; receipts: number;
}> {
  const row = await qOne(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
    FROM ${VISION_SCHEMA}.receipts WHERE plan_id = @planId`,
    { planId }
  );
  return { plan_id: planId, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 };
}

export async function getTokenUsageByRole(role: string): Promise<{
  role: string; total_tokens: number; receipts: number;
}> {
  const row = await qOne(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
    FROM ${VISION_SCHEMA}.receipts WHERE agent_role = @role`,
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
  invocation_mode: "CLI" | "HTTP" | "SDK" | "MCP";
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
  { name: "Ollama", type: "ollama", endpoint_url: "http://localhost:11434" },
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
