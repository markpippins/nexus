import { Pool, PoolClient, types } from "pg";
import { seedMemoryProcedures, loadCanonicalRoleMemoryShape } from "tackle-seeds";

// ── Keep timestamps as ISO strings ─────────────────────────────────
// pg parses TIMESTAMPTZ into Date objects by default. Override to keep
// strings so all existing code (which writes/expects ISO 8601 strings)
// continues to work when we migrate TEXT columns to TIMESTAMPTZ.
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

// ── Connection ──────────────────────────────────────────────────────

const TACKLE_SCHEMA = "tackle";

let pool: Pool;

export async function initDb(): Promise<Pool> {
  const dsn =
    process.env.TACKLE_PG_DSN ||
    process.env.CONDUIT_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus";

  pool = new Pool({
    connectionString: dsn,
    options: `-c search_path=${TACKLE_SCHEMA}`,
    max: 10,
    idleTimeoutMillis: 30000,
  });

  const client = await pool.connect();
  try {
    await client.query(`SET search_path TO ${TACKLE_SCHEMA}`);
    const exec = (sql: string, params?: any[]) => client.query(sql, params);
    await createSchema(exec);
    await runMigrations(exec);
    console.log(`Tackle schema initialized in PG schema ${TACKLE_SCHEMA}.`);
  } finally {
    client.release();
  }
  return pool;
}

export function getDb(): Pool {
  if (!pool) throw new Error("DB not initialized. Call initDb() first.");
  return pool;
}

// ── Query helpers ───────────────────────────────────────────────────

interface QueryResult {
  rows: any[];
  changes: number;
}

async function q(sql: string, params: Record<string, any> = {}): Promise<QueryResult> {
  const { text, values } = convertParams(sql, params);
  const result = await pool.query(text, values);
  return { rows: result.rows, changes: result.rowCount ?? 0 };
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

async function tRun(client: PoolClient, sql: string, params: Record<string, any> = {}): Promise<number> {
  const { text, values } = convertParams(sql, params);
  const r = await client.query(text, values);
  return r.rowCount ?? 0;
}

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

async function withTransaction<T>(
  cb: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await pool.connect();
  await client.query(`SET search_path TO ${TACKLE_SCHEMA}`);
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

async function createSchema(
  exec: (sql: string, params?: any[]) => Promise<any>
): Promise<void> {
  await exec(`CREATE SCHEMA IF NOT EXISTS ${TACKLE_SCHEMA}`);

  await exec(`
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.schema_version (
      version     INTEGER PRIMARY KEY,
      description TEXT NOT NULL,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

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

    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.roles (
      id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name        TEXT NOT NULL UNIQUE,
      description TEXT NOT NULL DEFAULT '',
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
      verified         BOOLEAN NOT NULL DEFAULT false,
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
  `);

  // ── Sessions (for test invoke flow) ──────────────────────────────
  await exec(`
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.sessions (
      id          TEXT PRIMARY KEY,
      agent_role  TEXT NOT NULL DEFAULT 'test',
      start_iso TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      end_iso TIMESTAMPTZ,
      exit_code   INTEGER,
      pid         INTEGER,
      is_running  INTEGER NOT NULL DEFAULT 1,
      error_info  TEXT,
      model       TEXT,
      plans_processed TEXT NOT NULL DEFAULT '[]',
      plan_count  INTEGER NOT NULL DEFAULT 0,
      cost_usd    REAL DEFAULT 0,
      workflow_id TEXT,
      run_id      TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  // ── Circuit breaker (for failure recovery config) ───────────────
  await exec(`
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.circuit_breaker (
      id                       INTEGER PRIMARY KEY,
      tripped                  INTEGER NOT NULL DEFAULT 0,
      tripped_at TIMESTAMPTZ,
      error                    TEXT,
      detail                   TEXT,
      source                   TEXT,
      retry_after              INTEGER DEFAULT 1800,
      paused                   INTEGER NOT NULL DEFAULT 0,
      wake_requested_at TIMESTAMPTZ,
      max_retries_per_model    INTEGER NOT NULL DEFAULT 3,
      retry_delay_seconds      INTEGER NOT NULL DEFAULT 120,
      max_fallbacks            INTEGER NOT NULL DEFAULT 3,
      push_back_to_pending     INTEGER NOT NULL DEFAULT 1,
      updated_at               TIMESTAMPTZ
    );
  `);

  // ── Agent scheduler (cron-driven agent runs) ───────────────────────
  await exec(`
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.agent_scheduler (
      id               SERIAL PRIMARY KEY,
      role             TEXT NOT NULL,
      model_id         TEXT,
      harness          TEXT NOT NULL DEFAULT 'opencode'
                        CHECK(harness IN ('opencode', 'conduit')),
      agent_config     TEXT NOT NULL DEFAULT '{}',
      schedule_type    TEXT NOT NULL DEFAULT 'interval'
                        CHECK(schedule_type IN ('interval', 'cron', 'manual')),
      schedule_value   INTEGER NOT NULL DEFAULT 3600,
      project_dir      TEXT NOT NULL DEFAULT '/home/codex/dev',
      enabled          INTEGER NOT NULL DEFAULT 1,
      last_run_at TIMESTAMPTZ,
      last_run_status  TEXT,
      metadata         TEXT NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  // ── Memory procedure registry ─────────────────────────────────────
  // Shape consumed from the canonical fragment (sql/canonical/) via
  // tackle-seeds — DO NOT restate the DDL inline (parity test enforces).
  await exec(loadCanonicalRoleMemoryShape(TACKLE_SCHEMA));

  // role_leases — session-level role leases (RoleLeases / plan 1286):
  // a bounded window + budget under which a role on a given channel may
  // consume work. Mirrors execution.leases (per-request) but scoped to a
  // role/session. One ACTIVE lease per role at a time.
  await exec(`
    CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.role_leases (
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      role            TEXT NOT NULL,
      channel         TEXT NOT NULL DEFAULT 'interactive'
                      CHECK (channel IN ('interactive','opencode','ollama','unknown')),
      model           TEXT,
      window_start    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      window_end      TIMESTAMPTZ NOT NULL,
      budget_units    INTEGER,
      consumed_units  INTEGER NOT NULL DEFAULT 0,
      status          TEXT NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE','EXPIRED','RELEASED')),
      acquired_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      expires_at      TIMESTAMPTZ NOT NULL,
      released_at     TIMESTAMPTZ,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await exec(`
    CREATE UNIQUE INDEX IF NOT EXISTS idx_role_leases_active_per_role
      ON ${TACKLE_SCHEMA}.role_leases (role)
      WHERE status = 'ACTIVE'
  `);
  await exec(`
    CREATE INDEX IF NOT EXISTS idx_role_leases_status
      ON ${TACKLE_SCHEMA}.role_leases (status, expires_at)
  `);

  // ── Add FK constraints to roles(name) (idempotent for existing tables) ──
  await exec(`
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_config_bundle_role'
      ) THEN
        ALTER TABLE ${TACKLE_SCHEMA}.config_bundle
          ADD CONSTRAINT fk_config_bundle_role
          FOREIGN KEY (role) REFERENCES ${TACKLE_SCHEMA}.roles(name);
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_scheduler_role'
      ) THEN
        ALTER TABLE ${TACKLE_SCHEMA}.agent_scheduler
          ADD CONSTRAINT fk_agent_scheduler_role
          FOREIGN KEY (role) REFERENCES ${TACKLE_SCHEMA}.roles(name);
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_role_memory_role'
      ) THEN
        ALTER TABLE ${TACKLE_SCHEMA}.role_memory
          ADD CONSTRAINT fk_role_memory_role
          FOREIGN KEY (role) REFERENCES ${TACKLE_SCHEMA}.roles(name);
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_sessions_agent_role'
      ) THEN
        ALTER TABLE ${TACKLE_SCHEMA}.sessions
          ADD CONSTRAINT fk_sessions_agent_role
          FOREIGN KEY (agent_role) REFERENCES ${TACKLE_SCHEMA}.roles(name);
      END IF;
    END $$;
  `);

  console.log(`Tackle schema DDL applied in PG schema ${TACKLE_SCHEMA}.`);
}

// ── Schema versioning (formal migration system) ─────────────────────

/** A single migration step, ordered by version number. */
interface Migration {
  version: number;
  description: string;
  up: (exec: (sql: string, params?: any[]) => Promise<any>) => Promise<void>;
}

/**
 * Ordered list of schema migrations for the tackle schema.
 *
 * - Version 1 is the baseline: all tables and DDL in createSchema() are the
 *   source of truth. On fresh databases this is a no-op that records v1.
 * - Version 2 adds missing PRIMARY KEY and UNIQUE constraints to tables
 *   that were created by older migrations without them (the 2026-07-11
 *   outage root cause).
 * - Future migrations should be appended here with incrementing version
 *   numbers. Each runs exactly once, in order.
 */
const migrations: Migration[] = [
  {
    version: 1,
    description: "Baseline — all core tables (providers, roles, harnesses, models, config_bundle, sessions, circuit_breaker, agent_scheduler, memory, role_memory)",
    up: async () => {
      // No-op: the DDL in createSchema() is the source of truth for v1.
    },
  },
  {
    version: 2,
    description: "Add missing PRIMARY KEY and UNIQUE constraints for tables created by older migrations without them (2026-07-11 outage fix)",
    up: async (exec) => {
      await exec(`
        DO $$
        BEGIN
          -- providers.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'providers_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.providers'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.providers ADD PRIMARY KEY (id);
          END IF;
          -- roles.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roles_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.roles'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.roles ADD PRIMARY KEY (id);
          END IF;
          -- roles.name UNIQUE
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roles_name_key'
            AND conrelid = '${TACKLE_SCHEMA}.roles'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.roles ADD CONSTRAINT roles_name_key UNIQUE (name);
          END IF;
          -- harnesses.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'harnesses_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.harnesses'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.harnesses ADD PRIMARY KEY (id);
          END IF;
          -- models.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'models_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.models'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.models ADD PRIMARY KEY (id);
          END IF;
          -- config_bundle.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_bundle_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.config_bundle'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.config_bundle ADD PRIMARY KEY (id);
          END IF;
          -- config_bundle (role, model_id) UNIQUE
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_bundle_role_model_id_key'
            AND conrelid = '${TACKLE_SCHEMA}.config_bundle'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.config_bundle
              ADD CONSTRAINT config_bundle_role_model_id_key UNIQUE (role, model_id);
          END IF;
          -- circuit_breaker.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'circuit_breaker_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.circuit_breaker'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.circuit_breaker ADD PRIMARY KEY (id);
          END IF;
          -- memory.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memory_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.memory'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.memory ADD PRIMARY KEY (id);
          END IF;
          -- memory.slug UNIQUE
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memory_slug_key'
            AND conrelid = '${TACKLE_SCHEMA}.memory'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.memory ADD CONSTRAINT memory_slug_key UNIQUE (slug);
          END IF;
          -- role_memory.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'role_memory_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.role_memory'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.role_memory ADD PRIMARY KEY (id);
          END IF;
        END $$;
      `);
    },
  },
  {
    version: 3,
    description: "Add missing PRIMARY KEY constraints to tackle.sessions and tackle.agent_scheduler (idempotent)",
    up: async (exec) => {
      await exec(`
        DO $$
        BEGIN
          -- tackle.sessions.id PK
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sessions_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.sessions'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.sessions ADD PRIMARY KEY (id);
          END IF;
          -- tackle.agent_scheduler.id PK (SERIAL column, should have PK but older migration may have missed it)
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_scheduler_pkey'
            AND conrelid = '${TACKLE_SCHEMA}.agent_scheduler'::regclass) THEN
            ALTER TABLE ${TACKLE_SCHEMA}.agent_scheduler ADD PRIMARY KEY (id);
          END IF;
        END $$;
      `);
      console.log("[tackle-migrations] v3: Added PKs to tackle.sessions, tackle.agent_scheduler");
    },
  },
  {
    version: 4,
    description: "Add performance indexes on sessions(created_at, agent_role) and agent_scheduler(enabled, last_run_at)",
    up: async (exec) => {
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_sessions_created_at
          ON ${TACKLE_SCHEMA}.sessions (created_at DESC)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_sessions_agent_role
          ON ${TACKLE_SCHEMA}.sessions (agent_role)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_agent_scheduler_due
          ON ${TACKLE_SCHEMA}.agent_scheduler (enabled, last_run_at)
      `);
      console.log("[tackle-migrations] v4: Added indexes on sessions, agent_scheduler");
    },
  },
  {
    version: 5,
    description: "Seed default roles and memory procedures (moved from createSchema to run after constraint-fix migrations)",
    up: async (exec) => {
      // Seed default circuit breaker row (idempotent)
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.circuit_breaker (id, tripped, updated_at)
         VALUES (1, 0, $1)
         ON CONFLICT (id) DO NOTHING`,
        [new Date().toISOString()]
      );

      // Seed default roles (idempotent, parameterized)
      await seedDefaultRoles(exec);

      // Seed memory procedures (idempotent — all values are hardcoded constants)
      await exec(seedMemoryProcedures());

      console.log("[tackle-migrations] v5: Seeded circuit breaker, default roles, and memory procedures");
    },
  },
  {
    version: 6,
    description: "Migrate TEXT timestamp columns to TIMESTAMPTZ — tackle-owned tables (roles, sessions, circuit_breaker, agent_scheduler, schema_version). Shared tables (providers, harnesses, models, config_bundle) are handled by conduit-mcp v27.",
    up: async (exec) => {
      // agent_scheduler.created_at and updated_at have DEFAULT ''::text — drop before ALTER
      await exec(`ALTER TABLE tackle.agent_scheduler ALTER COLUMN created_at DROP DEFAULT`);
      await exec(`ALTER TABLE tackle.agent_scheduler ALTER COLUMN updated_at DROP DEFAULT`);

      const tables: [string, string[]][] = [
        ["tackle.roles", ["created_at", "updated_at"]],
        ["tackle.sessions", ["created_at", "start_iso", "end_iso"]],
        ["tackle.circuit_breaker", ["tripped_at", "updated_at", "wake_requested_at"]],
        ["tackle.agent_scheduler", ["created_at", "updated_at", "last_run_at"]],
        ["tackle.schema_version", ["applied_at"]],
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

      // Restore proper NOW() defaults
      await exec(`ALTER TABLE tackle.agent_scheduler ALTER COLUMN created_at SET DEFAULT NOW()`);
      await exec(`ALTER TABLE tackle.agent_scheduler ALTER COLUMN updated_at SET DEFAULT NOW()`);

      console.log("[tackle-migrations] v6: Migrated TEXT→TIMESTAMPTZ for tackle-owned tables");
    },
  },
];

/**
 * Run pending migrations. Called from initDb() after createSchema(),
 * using the same dedicated connection so search_path is consistent.
 *
 * Reads the current version from schema_version, then applies any
 * migrations with version > current version in ascending order.
 */
/** Advisory lock key to prevent concurrent migration runs from
 *  multiple instances starting simultaneously. */
const MIGRATION_LOCK_KEY = 873492874;

async function runMigrations(
  exec: (sql: string, params?: any[]) => Promise<any>,
): Promise<void> {
  // Acquire an advisory lock to prevent concurrent migrations from
  // multiple instances starting simultaneously.
  await exec(`SELECT pg_advisory_lock(${MIGRATION_LOCK_KEY})`);
  try {
    const result = await exec(
      `SELECT COALESCE(MAX(version), 0) AS current_version FROM ${TACKLE_SCHEMA}.schema_version`
    );
    const currentVersion = (result as any)?.rows?.[0]?.current_version ?? 0;

    for (const m of migrations) {
      if (m.version <= currentVersion) continue;
      console.log(`[tackle-migrations] Applying v${m.version}: ${m.description}`);
      await m.up(exec);
      const now = new Date().toISOString();
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.schema_version (version, description, applied_at) VALUES ($1, $2, $3)`,
        [m.version, m.description, now]
      );
      console.log(`[tackle-migrations] v${m.version} applied`);
    }
  } finally {
    await exec(`SELECT pg_advisory_unlock(${MIGRATION_LOCK_KEY})`);
  }
}

// ── Default roles seed ─────────────────────────────────────────────

const DEFAULT_ROLES: { name: string; description: string }[] = [
  { name: "engineer", description: "Primary implementation agent — writes code, runs commands, integrates systems" },
  { name: "engineer-ii", description: "Primary implementation agent — writes code, runs commands, integrates systems" },
  { name: "devops", description: "Infrastructure operations and systems administration — system scripts, container setup/maintenance, migrations, sysadmin tasks; expansion of engineer with sysadmin concerns" },
  { name: "topologist", description: "Interactive representative of the terrain subsystem — verifies local service docs match actual configuration; validates specs/plans/work requests against live capabilities; offers running alternatives for unavailable services" },
  { name: "architect", description: "System design authority — owns architecture decisions, cross-system contracts, and design lineage" },
  { name: "planner", description: "Work decomposition authority — creates and manages implementation plans, promotes proposals" },
  { name: "builder", description: "Implementation executor — picks up pending plans and implements them against acceptance criteria" },
  { name: "reviewer", description: "Quality gate — reviews changes, issues approval/rejection receipts" },
  { name: "critic", description: "Adversarial evaluator — surfaces risks, contradictions, and blind spots" },
  { name: "analyst", description: "Gap and triage analyst — identifies missing coverage, classifies incidents" },
  { name: "analyst-ii", description: "Analysis-only role — sits and does analysis (findings/recommendations); zero decision authority; never an escalation target; findings route to the analyst chair to close" },
  { name: "inspector", description: "Compliance auditor — verifies invariants, issues violation reports" },
  { name: "auditor", description: "Audit and compliance reviewer — verifies records, constraints, and drift; issues inspection findings" },
  { name: "epistemologist", description: "Epistemic governance — tracks knowledge stratification, role boundaries, and cross-role divergence" },
  { name: "operator", description: "Pipeline and platform operator — monitors pipeline state, investigates stuck plans and drift, keeps operational surfaces healthy" },
  { name: "sysadmin", description: "Infrastructure health governance — systemd-timer cycles, service health, incident reporting; runs standalone" },
  { name: "supervisor", description: "Role-system administrator — registers and configures roles, regenerates doctrine and OpenCode projections, and verifies role-surface coverage; no WorkRequest execution authority" },
  { name: "test", description: "Internal test harness role — used for test invoke sessions and ad-hoc agent runs" },
  { name: "tester", description: "Walkthrough role (b80f0fdb) — full-surface demonstration of the role-creation runbook" },
  { name: "leased-builder", description: "Interactive-channel implementation executor — bounded role lease (RoleLeases, plan 1286): consumes from the READY pool under a window+budget lease, mirroring builder with a mandatory time limit" },
];

async function seedDefaultRoles(
  exec: (sql: string, params?: any[]) => Promise<any>,
): Promise<void> {
  const now = new Date().toISOString();
  for (const r of DEFAULT_ROLES) {
    await exec(
      `INSERT INTO ${TACKLE_SCHEMA}.roles (name, description, created_at, updated_at)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (name) DO NOTHING`,
      [r.name, r.description, now, now]
    );
  }
}

// ── AI Config CRUD ─────────────────────────────────────────────────

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
  id: string; role: string;
  provider_id: string; harness_id: string; model_id: string;
  extra_params: string; created_at: string; updated_at: string;
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

export interface AIConfigSnapshot {
  providers: AIProviderRow[]; harnesses: AIHarnessRow[];
  models: AIModelRow[]; roles: AIRoleConfigRow[];
  bundles: ConfigBundleRow[];
}

export interface ConfigValidationWarning {
  role: string;
  field: string;
  message: string;
  severity: "error" | "warning";
}

// ── Providers ─────────────────────────────────────────────────────

export async function getAIProviders(): Promise<AIProviderRow[]> {
  return qAll("SELECT * FROM providers ORDER BY name");
}

export async function getAIProvider(id: string): Promise<AIProviderRow | undefined> {
  return qOne("SELECT * FROM providers WHERE id = @id", { id });
}

export async function upsertAIProvider(
  p: Partial<AIProviderRow> & { id: string; name: string; type: string },
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

// ── Harnesses ─────────────────────────────────────────────────────

export async function getAIHarnesses(): Promise<AIHarnessRow[]> {
  return qAll("SELECT * FROM harnesses ORDER BY name");
}

export async function getAIHarness(id: string): Promise<AIHarnessRow | undefined> {
  return qOne("SELECT * FROM harnesses WHERE id = @id", { id });
}

export async function upsertAIHarness(
  h: Partial<AIHarnessRow> & { id: string; name: string },
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

// ── Models ────────────────────────────────────────────────────────

export async function getAIModels(): Promise<AIModelRow[]> {
  return qAll("SELECT * FROM models ORDER BY name");
}

export async function getAIModel(id: string): Promise<AIModelRow | undefined> {
  return qOne("SELECT * FROM models WHERE id = @id", { id });
}

export async function upsertAIModel(
  m: Partial<AIModelRow> & { id: string; name: string; harness_id: string; model_identifier: string },
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

// ── Role Configs (via config_bundle) ─────────────────────────────
//
// The `role_config` table was removed. Role assignments are now stored in
// `config_bundle`, where the lowest-priority (highest preference) active
// bundle per role acts as the "primary" role config.

export async function getAIRoleConfigs(): Promise<AIRoleConfigRow[]> {
  return qAll(
    `SELECT DISTINCT ON (cb.role)
            cb.id, cb.role, cb.model_id,
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
  rc: Partial<AIRoleConfigRow> & { id: string; role: string; provider_id: string; harness_id: string; model_id: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO config_bundle (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
     VALUES (@id, @name, @role, @model_id, @provider_id, @harness_id, 0, 'CLI', 1, '{}', @created_at, @updated_at)
     ON CONFLICT (id) DO UPDATE SET
       name = EXCLUDED.name, role = EXCLUDED.role, model_id = EXCLUDED.model_id,
       provider_id = EXCLUDED.provider_id, harness_id = EXCLUDED.harness_id,
       priority = 0, is_active = 1, updated_at = EXCLUDED.updated_at`,
    { ...rc, name: `Primary: ${rc.model_id} for ${rc.role}`,
      extra_params: rc.extra_params ?? "{}",
      created_at: rc.created_at ?? now, updated_at: now }
  );
}

export async function deleteAIRoleConfig(role: string): Promise<boolean> {
  const changes = await qRun("DELETE FROM config_bundle WHERE role = @role", { role });
  return changes > 0;
}

// ── Config Bundles (replaces role_config + role_models) ──────────

export async function getConfigBundles(role: string): Promise<ConfigBundleRow[]> {
  return qAll(
    "SELECT * FROM config_bundle WHERE role = @role ORDER BY priority ASC",
    { role }
  );
}

export async function getAllConfigBundles(): Promise<ConfigBundleRow[]> {
  return qAll("SELECT * FROM config_bundle ORDER BY role, priority ASC");
}

export async function getConfigBundle(id: string): Promise<ConfigBundleRow | undefined> {
  return qOne("SELECT * FROM config_bundle WHERE id = @id", { id });
}

export async function upsertConfigBundle(
  b: Partial<ConfigBundleRow> & { id: string; name: string; role: string; model_id: string },
): Promise<void> {
  const now = new Date().toISOString();
  await qRun(
    `INSERT INTO config_bundle (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, command, endpoint_url, timeout_ms, valid_from, valid_to, is_active, metadata, created_at, updated_at)
     VALUES (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, @invocation_mode, @command, @endpoint_url, @timeout_ms, @valid_from, @valid_to, @is_active, @metadata, @created_at, @updated_at)
     ON CONFLICT (id) DO UPDATE SET
       name = EXCLUDED.name, role = EXCLUDED.role,
       model_id = EXCLUDED.model_id, provider_id = EXCLUDED.provider_id,
       harness_id = EXCLUDED.harness_id, priority = EXCLUDED.priority,
       invocation_mode = EXCLUDED.invocation_mode, command = EXCLUDED.command,
       endpoint_url = EXCLUDED.endpoint_url, timeout_ms = EXCLUDED.timeout_ms,
       is_active = EXCLUDED.is_active, metadata = EXCLUDED.metadata,
       updated_at = EXCLUDED.updated_at`,
    {
      ...b,
      provider_id: b.provider_id ?? null,
      harness_id: b.harness_id ?? null,
      priority: b.priority ?? 0,
      invocation_mode: b.invocation_mode ?? "CLI",
      command: b.command ?? null,
      endpoint_url: b.endpoint_url ?? null,
      timeout_ms: b.timeout_ms ?? null,
      valid_from: b.valid_from ?? null,
      valid_to: b.valid_to ?? null,
      is_active: b.is_active ?? 1,
      metadata: b.metadata ?? "{}",
      created_at: b.created_at ?? now,
      updated_at: now,
    }
  );
}

export async function deleteConfigBundle(id: string): Promise<boolean> {
  const changes = await qRun("DELETE FROM config_bundle WHERE id = @id", { id });
  return changes > 0;
}

export async function upsertConfigBundles(
  role: string, bundles: {
    model_id: string; priority: number;
    provider_id?: string | null; harness_id?: string | null;
    name?: string; invocation_mode?: "CLI" | "HTTP" | "SDK" | "MCP" | "INTERACTIVE";
    command?: string | null; endpoint_url?: string | null; timeout_ms?: number | null;
  }[],
): Promise<void> {
  if (bundles.length === 0) return;
  const now = new Date().toISOString();

  console.log(`[upsertConfigBundles] Starting for role: ${role}, bundles: ${bundles.length}`);

  await withTransaction(async (client) => {
    const deleteResult = await tRun(client, "DELETE FROM config_bundle WHERE role = @role", { role });
    console.log(`[upsertConfigBundles] Deleted rows for role ${role}:`, deleteResult);
    
    for (const b of bundles) {
      const id = `cb-${role}-${b.model_id}`;
      console.log(`[upsertConfigBundles] Inserting bundle: ${id}`);
      await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode,
            command, endpoint_url, timeout_ms, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, @invocation_mode,
            @command, @endpoint_url, @timeout_ms, 1, '{}', @now, @now)`,
        {
          id,
          name: b.name ?? `Bundle: ${b.model_id}`,
          role,
          model_id: b.model_id,
          priority: b.priority,
          provider_id: b.provider_id ?? null,
          harness_id: b.harness_id ?? null,
          invocation_mode: b.invocation_mode ?? "CLI",
          command: b.command ?? null,
          endpoint_url: b.endpoint_url ?? null,
          timeout_ms: b.timeout_ms ?? null,
          now,
        }
      );
    }
    console.log(`[upsertConfigBundles] Completed for role: ${role}`);
  });
}

// ── Session CRUD ────────────────────────────────────────────────────

export interface SessionRow {
  id: string;
  agent_role: string;
  start_iso: string;
  end_iso: string | null;
  exit_code: number | null;
  pid: number | null;
  is_running: number;
  error_info: string | null;
  model: string | null;
  plans_processed: string;
  plan_count: number;
  cost_usd: number | null;
  workflow_id: string | null;
  run_id: string | null;
  created_at: string;
}

export async function startSession(session: {
  id: string; agent_role: string; start_iso: string;
  plans_processed?: string[]; plan_count?: number;
  model?: string;
}): Promise<void> {
  await qRun(
    `INSERT INTO sessions (id, agent_role, start_iso, plans_processed, plan_count, model, created_at)
     VALUES (@id, @agent_role, @start_iso, @plans_processed, @plan_count, @model, @created_at)`,
    { id: session.id, agent_role: session.agent_role, start_iso: session.start_iso,
      plans_processed: JSON.stringify(session.plans_processed || []),
      plan_count: session.plan_count ?? 0, model: session.model ?? null,
      created_at: session.start_iso }
  );
}

export async function getSession(id: string): Promise<SessionRow | undefined> {
  return qOne("SELECT * FROM sessions WHERE id = @id", { id });
}

export async function endSession(id: string, exitCode: number, endIso: string): Promise<void> {
  await qRun(
    `UPDATE sessions SET end_iso = @end_iso, exit_code = @exit_code, is_running = 0 WHERE id = @id`,
    { end_iso: endIso, exit_code: exitCode, id }
  );
}

export async function updateSessionPid(id: string, pid: number): Promise<void> {
  await qRun("UPDATE sessions SET pid = @pid WHERE id = @id", { pid, id });
}

export async function releaseSessionTickets(sessionId: string): Promise<number> {
  // Tackle-mcp has no tickets; stub for interface compatibility
  return 0;
}

export async function getAllSessions(): Promise<SessionRow[]> {
  return qAll("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 100");
}

// ── Circuit Breaker / Failure Recovery ─────────────────────────────

export interface CircuitBreakerRow {
  id: number;
  tripped: number;
  tripped_at: string | null;
  error: string | null;
  detail: string | null;
  source: string | null;
  retry_after: number;
  paused: number;
  wake_requested_at: string | null;
  max_retries_per_model: number;
  retry_delay_seconds: number;
  max_fallbacks: number;
  push_back_to_pending: number;
  updated_at: string | null;
}

export async function getBreaker(): Promise<CircuitBreakerRow> {
  const row = await qOne("SELECT * FROM circuit_breaker WHERE id = 1");
  return row || {
    id: 1, tripped: 0, tripped_at: null, error: null, detail: null, source: null,
    retry_after: 1800, paused: 0, wake_requested_at: null,
    max_retries_per_model: 3, retry_delay_seconds: 120, max_fallbacks: 3,
    push_back_to_pending: 1, updated_at: null,
  };
}

export async function saveFailureRecoveryConfig(config: {
  max_retries_per_model?: number;
  retry_delay_seconds?: number;
  max_fallbacks?: number;
  push_back_to_pending?: boolean;
  circuit_breaker_retry_after?: number;
}): Promise<void> {
  const pool = getDb();
  const now = new Date().toISOString();
  await pool.query(
    `UPDATE ${TACKLE_SCHEMA}.circuit_breaker SET
      max_retries_per_model = $1,
      retry_delay_seconds = $2,
      max_fallbacks = $3,
      push_back_to_pending = $4,
      retry_after = $5,
      updated_at = $6
    WHERE id = 1`,
    [
      typeof config.max_retries_per_model === 'number' ? config.max_retries_per_model : 3,
      typeof config.retry_delay_seconds === 'number' ? config.retry_delay_seconds : 120,
      typeof config.max_fallbacks === 'number' ? config.max_fallbacks : 3,
      config.push_back_to_pending !== false ? 1 : 0,
      typeof config.circuit_breaker_retry_after === 'number' ? config.circuit_breaker_retry_after : 1800,
      now,
    ]
  );
}

// ── Agent Scheduler ──────────────────────────────────────────────────

// ── Roles Registry CRUD ─────────────────────────────────────────────

export interface RoleRow {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export async function getRoles(): Promise<RoleRow[]> {
  return qAll("SELECT * FROM roles ORDER BY name");
}

export async function getRole(idOrName: string): Promise<RoleRow | undefined> {
  // Check if input looks like a UUID before trying UUID query
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuidPattern.test(idOrName)) {
    const byId = await qOne("SELECT * FROM roles WHERE id = @id", { id: idOrName });
    if (byId) return byId;
  }
  return qOne("SELECT * FROM roles WHERE name = @name", { name: idOrName });
}

export async function upsertRole(
  r: Partial<RoleRow> & { name: string; description?: string },
): Promise<RoleRow> {
  const now = new Date().toISOString();
  // Use the DEFAULT gen_random_uuid() when no id is provided
  const hasId = !!r.id;
  return qOne(
    hasId
      ? `INSERT INTO roles (id, name, description, created_at, updated_at)
         VALUES (@id, @name, @description, @now, @now)
         ON CONFLICT (name) DO UPDATE SET
           description = EXCLUDED.description,
           updated_at = EXCLUDED.updated_at
         RETURNING *`
      : `INSERT INTO roles (name, description, created_at, updated_at)
         VALUES (@name, @description, @now, @now)
         ON CONFLICT (name) DO UPDATE SET
           description = EXCLUDED.description,
           updated_at = EXCLUDED.updated_at
         RETURNING *`,
    { id: r.id ?? undefined, name: r.name, description: r.description ?? "", now }
  );
}

export async function deleteRole(idOrName: string): Promise<boolean> {
  // Check if input looks like a UUID before trying UUID query
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let changes = 0;
  if (uuidPattern.test(idOrName)) {
    changes = await qRun("DELETE FROM roles WHERE id = @id", { id: idOrName });
  }
  if (changes === 0) {
    changes = await qRun("DELETE FROM roles WHERE name = @name", { name: idOrName });
  }
  return changes > 0;
}

export interface AgentSchedulerRow {
  id: number;
  role: string;
  model_id: string | null;
  harness: string;
  agent_config: string;
  schedule_type: string;
  schedule_value: number;
  project_dir: string;
  task_slug: string | null;
  enabled: number;
  last_run_at: string | null;
  last_run_status: string | null;
  metadata: string;
  created_at: string;
  updated_at: string;
}

export async function listSchedulerEntries(): Promise<AgentSchedulerRow[]> {
  return qAll("SELECT * FROM agent_scheduler ORDER BY role, id ASC");
}

export async function getSchedulerEntry(id: number): Promise<AgentSchedulerRow | undefined> {
  return qOne("SELECT * FROM agent_scheduler WHERE id = @id", { id });
}

// schedule_value is INTEGER seconds; the UI/agents may send durations like
// "15m" or "1h" — parse them so they store the intended seconds instead of
// silently falling back to the default (cron strings are unparseable as
// seconds and keep the default; the runner only re-fires interval entries).
function toScheduleSeconds(v: unknown, dflt: number): number {
  if (v === undefined || v === null || v === "") return dflt;
  if (typeof v === "number") return Number.isFinite(v) && v >= 1 ? v : dflt;
  const s = String(v).trim();
  const m = /^(\d+)(ms|s|m|h|d)?$/.exec(s);
  if (m) {
    const n = parseInt(m[1], 10);
    const mult: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400, ms: 0.001 };
    const secs = n * (mult[m[2] || "s"]);
    if (secs >= 1 && Number.isFinite(secs)) return Math.round(secs);
  }
  const n2 = Number(v);
  return Number.isFinite(n2) && n2 >= 1 ? n2 : dflt;
}

export async function createSchedulerEntry(data: {
  role: string; model_id?: string; harness?: string;
  agent_config?: string; schedule_type?: string; schedule_value?: number | string;
  project_dir?: string; task_slug?: string | null; enabled?: number;
}): Promise<AgentSchedulerRow> {
  const now = new Date().toISOString();
  const row = await qOne(`
    INSERT INTO agent_scheduler (role, model_id, harness, agent_config, schedule_type, schedule_value, project_dir, task_slug, enabled, metadata, created_at, updated_at)
    VALUES (@role, @model_id, @harness, @agent_config, @schedule_type, @schedule_value, @project_dir, @task_slug, @enabled, '{}', @now, @now)
    RETURNING *
  `, {
    role: data.role,
    model_id: data.model_id ?? null,
    harness: data.harness ?? "opencode",
    agent_config: data.agent_config ?? "{}",
    schedule_type: data.schedule_type ?? "interval",
    schedule_value: toScheduleSeconds(data.schedule_value, 3600),
    project_dir: data.project_dir ?? "/home/codex/dev",
    task_slug: data.task_slug ?? null,
    enabled: data.enabled ?? 1,
    now,
  });
  return row;
}

export async function updateSchedulerEntry(id: number, data: Partial<{
  role: string; model_id: string | null; harness: string;
  agent_config: string; schedule_type: string; schedule_value: number | string;
  project_dir: string; task_slug: string | null; enabled: number; last_run_at: string;
  last_run_status: string; metadata: string;
}>): Promise<AgentSchedulerRow | undefined> {
  const now = new Date().toISOString();
  const sets: string[] = ["updated_at = @now"];
  const params: Record<string, any> = { id, now };
  const fields = ["role", "model_id", "harness", "agent_config", "schedule_type",
    "schedule_value", "project_dir", "task_slug", "enabled", "last_run_at", "last_run_status", "metadata"];
  for (const f of fields) {
    if ((data as any)[f] !== undefined) {
      sets.push(`${f} = @${f}`);
      params[f] = f === "schedule_value"
        ? toScheduleSeconds((data as any)[f], 3600)
        : (data as any)[f];
    }
  }
  return qOne(
    `UPDATE agent_scheduler SET ${sets.join(", ")} WHERE id = @id RETURNING *`,
    params
  );
}

export async function deleteSchedulerEntry(id: number): Promise<boolean> {
  const changes = await qRun("DELETE FROM agent_scheduler WHERE id = @id", { id });
  return changes > 0;
}

/**
 * Due scheduler entry, enriched with the prompt payload an agent needs to
 * start the run: the role's DEFAULT persona (latest `opencode-persona`)
 * as base_prompt_body, the attached task's template body as
 * task_prompt_body, and assembled_prompt = base + appended task prompt.
 * When no task is attached, assembled_prompt === base_prompt_body.
 */
export interface DueSchedulerEntry extends AgentSchedulerRow {
  base_prompt_body: string | null;
  task_prompt_body: string | null;
  assembled_prompt: string | null;
}

// Latest version of a (role, slug) prompt template body.
async function resolvePromptBody(role: string, slug: string): Promise<string | null> {
  const row = await qOne(
    `SELECT DISTINCT ON (role, slug) body_md
     FROM prompts
     WHERE role = @role AND slug = @slug
     ORDER BY role, slug, version DESC
     LIMIT 1`,
    { role, slug }
  );
  return row?.body_md ?? null;
}

async function resolveSchedulerPrompt(
  entry: AgentSchedulerRow
): Promise<Pick<DueSchedulerEntry, "base_prompt_body" | "task_prompt_body" | "assembled_prompt">> {
  // Default system prompt for the role: latest `opencode-persona` template.
  const base = await resolvePromptBody(entry.role, "opencode-persona");

  // Attached task (if any): resolve its bound template and append its body.
  let taskBody: string | null = null;
  if (entry.task_slug) {
    const task = await qOne(
      `SELECT p.role AS prompt_role, p.slug AS prompt_slug
       FROM tasks t
       LEFT JOIN prompts p ON p.id = t.prompt_id
       WHERE t.task_slug = @slug
       ORDER BY t.active DESC, t.updated_at DESC
       LIMIT 1`,
      { slug: entry.task_slug }
    );
    if (task?.prompt_role && task?.prompt_slug) {
      taskBody = await resolvePromptBody(task.prompt_role, task.prompt_slug);
    }
  }

  let assembled: string | null = base;
  if (taskBody) {
    // Exact contract: base persona + separator + appended task prompt.
    // No trim() — the seeded persona bodies carry meaningful leading
    // whitespace/control chars that must survive verbatim.
    assembled = base
      ? `${base}\n\n---\n\n## Attached Task: ${entry.task_slug}\n\n${taskBody}`
      : `## Attached Task: ${entry.task_slug}\n\n${taskBody}`;
  }
  return { base_prompt_body: base, task_prompt_body: taskBody, assembled_prompt: assembled };
}

export async function getDueSchedulerEntries(): Promise<DueSchedulerEntry[]> {
  const rows = await qAll(`
    SELECT * FROM agent_scheduler
    WHERE enabled = 1
      AND schedule_type <> 'manual'
      AND (
        last_run_at IS NULL
        OR (
          schedule_type = 'interval'
          AND EXTRACT(EPOCH FROM NOW() - last_run_at::timestamp) >= schedule_value
        )
      )
    ORDER BY last_run_at ASC NULLS FIRST
  `);
  const enriched = await Promise.all(
    rows.map(async (row) => ({ ...row, ...(await resolveSchedulerPrompt(row)) }))
  );
  return enriched;
}

// ── Snapshot / Import / Export / Validate ─────────────────────────

export async function getAIConfigSnapshot(): Promise<AIConfigSnapshot> {
  return {
    providers: await getAIProviders(),
    harnesses: await getAIHarnesses(),
    models: await getAIModels(),
    roles: await getAIRoleConfigs(),
    bundles: await getAllConfigBundles(),
  };
}

export async function importAIConfig(
  data: AIConfigSnapshot & { bundles?: ConfigBundleRow[] },
): Promise<{ providers: number; harnesses: number; models: number; roles: number; bundles: number }> {
  let pCount = 0, hCount = 0, mCount = 0, bCount = 0;
  const now = new Date().toISOString();

  await withTransaction(async (client) => {
    await tRun(client, "DELETE FROM config_bundle");
    await tRun(client, "DELETE FROM models");
    await tRun(client, "DELETE FROM harnesses");
    await tRun(client, "DELETE FROM providers");

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

    for (const h of data.harnesses || []) {
      await tRun(client,
        `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
         VALUES (@id, @name, @invocation_semantics, @created_at, @updated_at)`,
        { id: h.id, name: h.name, invocation_semantics: h.invocation_semantics ?? "{}",
          created_at: h.created_at || now, updated_at: now }
      );
      hCount++;
    }

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

    for (const b of data.bundles || []) {
      await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode,
            command, endpoint_url, timeout_ms, valid_from, valid_to, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, @invocation_mode,
            @command, @endpoint_url, @timeout_ms, @valid_from, @valid_to, @is_active, @metadata, @created_at, @updated_at)`,
        {
          id: b.id || `cb-${b.role}-${b.model_id}`,
          name: b.name ?? `Bundle: ${b.model_id}`,
          role: b.role, model_id: b.model_id,
          provider_id: b.provider_id ?? null,
          harness_id: b.harness_id ?? null,
          priority: b.priority ?? 0,
          invocation_mode: b.invocation_mode ?? "CLI",
          command: b.command ?? null,
          endpoint_url: b.endpoint_url ?? null,
          timeout_ms: b.timeout_ms ?? null,
          valid_from: b.valid_from ?? null,
          valid_to: b.valid_to ?? null,
          is_active: b.is_active ?? 1,
          metadata: b.metadata ?? "{}",
          created_at: b.created_at || now,
          updated_at: now,
        }
      );
      bCount++;
    }
  });

  console.log(`[import-ai-config] Imported ${pCount} providers, ${hCount} harnesses, ${mCount} models, ${bCount} bundles.`);
  return { providers: pCount, harnesses: hCount, models: mCount, roles: (data.roles || []).length, bundles: bCount };
}

export async function validateAIConfig(): Promise<ConfigValidationWarning[]> {
  const cfg = await getAIConfigSnapshot();
  const warnings: ConfigValidationWarning[] = [];

  const harnessMap = new Map(cfg.harnesses.map(h => [h.id, h]));
  const modelMap = new Map(cfg.models.map(m => [m.id, m]));
  const providerMap = new Map(cfg.providers.map(p => [p.id, p]));
  const bundleMap = new Map<string, ConfigBundleRow[]>();
  for (const b of cfg.bundles) {
    const list = bundleMap.get(b.role) || [];
    list.push(b);
    bundleMap.set(b.role, list);
  }

  for (const rc of cfg.roles) {
    if (!modelMap.has(rc.model_id)) {
      warnings.push({
        role: rc.role, field: "model_id",
        message: `Primary model '${rc.model_id}' not found in models table.`,
        severity: "error",
      });
    } else {
      const model = modelMap.get(rc.model_id)!;
      const harness = harnessMap.get(model.harness_id);
      if (!harness) {
        warnings.push({
          role: rc.role, field: "harness_id",
          message: `Primary model '${rc.model_id}' references harness '${model.harness_id}' which does not exist.`,
          severity: "error",
        });
      } else {
        const sem = parseJsonSafe(harness.invocation_semantics, {});
        if (!sem?.binary) {
          warnings.push({
            role: rc.role, field: "invocation_semantics.binary",
            message: `Harness '${harness.name}' for primary model '${model.name}' has no 'binary' in invocation_semantics.`,
            severity: "error",
          });
        }
      }
      if (model.provider_id && !providerMap.has(model.provider_id)) {
        warnings.push({
          role: rc.role, field: "provider_id",
          message: `Primary model '${rc.model_id}' references provider '${model.provider_id}' which does not exist.`,
          severity: "warning",
        });
      }
    }

    const bundles = bundleMap.get(rc.role) || [];
    for (const b of bundles) {
      if (!modelMap.has(b.model_id)) {
        warnings.push({
          role: rc.role, field: "model_id",
          message: `Bundle model '${b.model_id}' not found in models table.`,
          severity: "error",
        });
        continue;
      }
      const model = modelMap.get(b.model_id)!;
      const harnessId = b.harness_id || model.harness_id;
      const harness = harnessMap.get(harnessId);
      if (!harness) {
        warnings.push({
          role: rc.role, field: "harness_id",
          message: `Bundle model '${b.model_id}' references harness '${harnessId}' which does not exist.`,
          severity: "error",
        });
      } else {
        const sem = parseJsonSafe(harness.invocation_semantics, {});
        if (!sem?.binary) {
          warnings.push({
            role: rc.role, field: "invocation_semantics.binary",
            message: `Harness '${harness.name}' for bundle model '${model.name}' has no 'binary' in invocation_semantics.`,
            severity: "warning",
          });
        }
      }
      const providerId = b.provider_id || model.provider_id;
      if (providerId && !providerMap.has(providerId)) {
        warnings.push({
          role: rc.role, field: "provider_id",
          message: `Bundle model '${b.model_id}' references provider '${providerId}' which does not exist.`,
          severity: "warning",
        });
      }
    }

    if (bundles.length === 0) {
      warnings.push({
        role: rc.role, field: "bundles",
        message: `Role '${rc.role}' has no config bundles configured. If the primary model fails, execution will halt.`,
        severity: "warning",
      });
    }
  }

  return warnings;
}

function parseJsonSafe(text: string, fallback: any): any {
  try { return JSON.parse(text); } catch { return fallback; }
}

// ── Resolved config (joined rows for agent_chat) ────────────────────

export interface ResolvedRoleConfig {
  role: string;
  model_identifier: string;
  provider_id: string;
  provider_name: string;
  provider_type: string;
  api_key: string | null;
  endpoint_url: string | null;
  harness_name: string;
  invocation_semantics: any;
  fallback_models: ResolvedFallbackModel[];
}

export interface ResolvedFallbackModel {
  priority: number;
  model_identifier: string;
  provider_type: string;
  provider_name: string;
  provider_id: string;
  api_key: string | null;
  endpoint_url: string | null;
  harness_name: string;
  harness_id: string;
  invocation_semantics: any;
  invocation_mode: string;
  command: string | null;
  timeout_ms: number | null;
}

export async function getResolvedRoleConfig(role: string): Promise<ResolvedRoleConfig | null> {
  const row = await qOne(
    `SELECT cb.role,
            m.model_identifier,
            p.id            AS provider_id,
            p.name          AS provider_name,
            COALESCE(p.type, '') AS provider_type,
            p.api_key,
            COALESCE(cb.endpoint_url, p.endpoint_url) AS endpoint_url,
            COALESCE(h.name, '') AS harness_name,
            COALESCE(h.invocation_semantics, '{}') AS invocation_semantics
     FROM config_bundle cb
     JOIN models m          ON cb.model_id = m.id
     LEFT JOIN providers p  ON COALESCE(cb.provider_id, m.provider_id) = p.id
     LEFT JOIN harnesses h  ON COALESCE(cb.harness_id, m.harness_id) = h.id
     WHERE cb.role = @role AND cb.is_active = 1
     ORDER BY cb.priority ASC LIMIT 1`,
    { role }
  );
  if (!row) return null;

  const fallbacks = await getResolvedFallbackModels(role);

  return {
    role: row.role,
    model_identifier: row.model_identifier,
    provider_id: row.provider_id ?? "",
    provider_name: row.provider_name ?? "",
    provider_type: row.provider_type,
    api_key: row.api_key ?? null,
    endpoint_url: row.endpoint_url ?? null,
    harness_name: row.harness_name,
    invocation_semantics: parseJsonSafe(row.invocation_semantics, {}),
    fallback_models: fallbacks,
  };
}

export async function getResolvedFallbackModels(role: string): Promise<ResolvedFallbackModel[]> {
  const rows = await qAll(
    `SELECT cb.priority,
            m.model_identifier,
            p.type          AS provider_type,
            p.name          AS provider_name,
            p.api_key,
            cb.endpoint_url,  -- bundle-level override
            p.id            AS provider_id,
            h.name          AS harness_name,
            h.id            AS harness_id,
            h.invocation_semantics,
            cb.invocation_mode,
            cb.command,
            cb.timeout_ms
     FROM config_bundle cb
     JOIN models m        ON cb.model_id = m.id
     LEFT JOIN providers p ON COALESCE(cb.provider_id, m.provider_id) = p.id
     LEFT JOIN harnesses h ON COALESCE(cb.harness_id, m.harness_id) = h.id
     WHERE cb.role = @role AND cb.is_active = 1
     ORDER BY cb.priority ASC`,
    { role }
  );

  return rows.map((row: any) => ({
    priority: row.priority,
    model_identifier: row.model_identifier,
    provider_type: row.provider_type ?? "",
    provider_name: row.provider_name ?? "",
    provider_id: row.provider_id ?? "",
    api_key: row.api_key ?? null,
    endpoint_url: row.endpoint_url ?? null,
    harness_name: row.harness_name ?? "",
    harness_id: row.harness_id ?? "",
    invocation_semantics: parseJsonSafe(row.invocation_semantics, {}),
    invocation_mode: row.invocation_mode ?? "CLI",
    command: row.command ?? null,
    timeout_ms: row.timeout_ms ?? null,
  }));
}

// Export resolved types with new fields
export interface ResolvedFallbackModelExtended extends ResolvedFallbackModel {
  invocation_mode: string;
  command: string | null;
  timeout_ms: number | null;
}

export type { ResolvedRoleConfig as RoleConfigResolved };

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

const ALL_ROLES = ["planner", "builder", "reviewer", "critic", "analyst", "architect", "inspector", "engineer", "engineer-ii", "engineer-iii", "devops", "topologist", "rover"] as const;

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
    for (const p of DEFAULT_PROVIDERS) {
      const id = `prov-${p.type}`;
      const changes = await tRun(client,
        `INSERT INTO providers (id, name, type, endpoint_url, api_key, config_json, created_at, updated_at)
         VALUES (@id, @name, @type, @endpoint_url, '', '{}', @now, @now)
         ON CONFLICT (id) DO NOTHING`,
        { id, name: p.name, type: p.type, endpoint_url: p.endpoint_url, now }
      );
      if (changes > 0) pCount++;
    }

    const opencodeSemantics = JSON.stringify({
      binary: "opencode", capabilities: { model: true, agent: true, working_directory: true, system_prompt: false },
      execution: { mode: "interactive", subcommand: "run" },
      semantics: { model: { type: "flag", flag: "--model" }, agent: { type: "flag", flag: "--agent" }, working_directory: { type: "flag", flag: "--dir" } },
      role_mapping: { strategy: "agent" },
    });
    let changes = await tRun(client,
      `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
       VALUES (@id, @name, @invocation_semantics, @now, @now)
       ON CONFLICT (id) DO NOTHING`,
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
      `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
       VALUES (@id, @name, @invocation_semantics, @now, @now)
       ON CONFLICT (id) DO NOTHING`,
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
      `INSERT INTO harnesses (id, name, invocation_semantics, created_at, updated_at)
       VALUES (@id, @name, @invocation_semantics, @now, @now)
       ON CONFLICT (id) DO NOTHING`,
      { id: "harn-codex-cli", name: "Codex CLI", invocation_semantics: codexSemantics, now }
    );
    if (changes > 0) hCount++;

    for (const md of DEFAULT_MODELS) {
      const changes = await tRun(client,
        `INSERT INTO models (id, name, harness_id, provider_id, model_identifier, created_at, updated_at)
         VALUES (@id, @name, @harness_id, @provider_id, @model_identifier, @now, @now)
         ON CONFLICT (id) DO NOTHING`,
        { id: md.id, name: md.name, harness_id: md.harnessId, provider_id: md.providerId, model_identifier: md.modelId, now }
      );
      if (changes > 0) mCount++;
    }

    for (const role of ALL_ROLES) {
      const changes = await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, priority, provider_id, harness_id, invocation_mode, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @priority, @provider_id, @harness_id, 'CLI', 1, '{}', @now, @now)
         ON CONFLICT (role, model_id) DO NOTHING`,
        { id: `cb-${role}-mod-gpt4o`, name: `Default: GPT-4o for ${role}`, role, model_id: "mod-gpt4o", priority: 0,
          provider_id: "prov-openai", harness_id: "harn-opencode", now }
      );
      if (changes > 0) rCount++;
    }
  });

  return {
    seeded: true, providers: pCount, harnesses: hCount, models: mCount, roles: rCount,
    message: `${force ? "Force re-s" : "S"}eeded ${pCount} providers, ${hCount} harnesses, ${mCount} models, ${rCount} role configs.`,
  };
}

