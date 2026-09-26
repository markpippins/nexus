import { Pool, PoolClient, types } from "pg";
import { readFileSync } from "fs";
import path from "path";
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
      release_reason  TEXT
                      CHECK (release_reason IN ('revoked','exhausted','expired')),
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  // D-2026-08-16-008 (R4): release_reason — additive, non-destructive.
  // Idempotent for tables created before the column existed.
  await exec(`
    ALTER TABLE ${TACKLE_SCHEMA}.role_leases
      ADD COLUMN IF NOT EXISTS release_reason TEXT
      CHECK (release_reason IN ('revoked','exhausted','expired'))
  `);

  // D-2026-08-16-007 (R1): ACTIVE uniqueness is per (role, channel) — a role
  // may hold one ACTIVE lease per channel concurrently. Drop the old
  // per-role index and recreate it on (role, channel). Additive/reversible;
  // no data migration required.
  await exec(`
    DROP INDEX IF EXISTS ${TACKLE_SCHEMA}.idx_role_leases_active_per_role
  `);
  await exec(`
    CREATE UNIQUE INDEX IF NOT EXISTS idx_role_leases_active_per_role_channel
      ON ${TACKLE_SCHEMA}.role_leases (role, channel)
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
 * - Versions 7-9 load external SQL files from nexus/schemas/migrations/tackle/ at
 *   runtime (prompts_tasks_tool_access.sql, seed_prompts.sql,
 *   roles_default_timestamps.sql). They were originally applied externally
 *   via psql on 2026-07-25; registered here so green-field installs
 *   auto-apply them. The SQL files are idempotent and self-stamp
 *   schema_version.
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
  // ── v7-v9 were originally applied externally via psql on 2026-07-25 and
  //    self-stamped tackle.schema_version. They are registered here so that
  //    green-field installs auto-apply them. Each Migration.up() loads the
  //    SQL file at runtime and executes it as a single query. Because the
  //    SQL files are idempotent (CREATE TABLE IF NOT EXISTS, ON CONFLICT
  //    DO NOTHING/UPDATE, ALTER ... SET DEFAULT is a no-op once applied) and
  //    self-stamp with ON CONFLICT (version) DO UPDATE, re-running on an
  //    already-current DB is safe — though runMigrations skips them via the
  //    version check before up() is ever called. Schema SQL lives under
  //    nexus/schemas/migrations/tackle/, resolved from this src/ dir.
  {
    version: 7,
    description: "Create tackle.prompts (reusable versioned prompt templates), tackle.tasks (concrete assignments FK->prompts), tackle.role_tool_access (per-tool default-deny allowlist). Architect decision d708c452. [file: prompts_tasks_tool_access.sql]",
    up: async (exec) => {
      const sqlPath = path.resolve(__dirname, "../../../schemas/migrations/tackle/prompts_tasks_tool_access.sql");
      const sql = readFileSync(sqlPath, "utf8");
      await exec(sql);
      console.log("[tackle-migrations] v7: Created tackle.prompts, tackle.tasks, tackle.role_tool_access");
    },
  },
  {
    version: 8,
    description: "Seed tackle.prompts with 11 rows (operator system-prompt-base/tail + 9 role opencode-persona v1) and one active inspector task. Add builder-fallback role. Engineer intent ab3befcc. [file: seed_prompts.sql]",
    up: async (exec) => {
      const sqlPath = path.resolve(__dirname, "../../../schemas/migrations/tackle/seed_prompts.sql");
      const sql = readFileSync(sqlPath, "utf8");
      await exec(sql);
      console.log("[tackle-migrations] v8: Seeded 11 prompts + 1 inspector task + builder-fallback role");
    },
  },
  {
    version: 9,
    description: "Add DEFAULT NOW() to tackle.roles.created_at and tackle.roles.updated_at (baseline inconsistency fix so casual role inserts work). Spotted when seeding builder-fallback during v8. Back-compatible. [file: roles_default_timestamps.sql]",
    up: async (exec) => {
      const sqlPath = path.resolve(__dirname, "../../../schemas/migrations/tackle/roles_default_timestamps.sql");
      const sql = readFileSync(sqlPath, "utf8");
      await exec(sql);
      console.log("[tackle-migrations] v9: Added DEFAULT NOW() to tackle.roles.created_at and updated_at");
    },
  },
  {
    version: 10,
    description: "Create tackle.system_logs for operational log persistence (GET/POST/DELETE /logs endpoints)",
    up: async (exec) => {
      await exec(`
        CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.system_logs (
          id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
          timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          level       TEXT NOT NULL CHECK (level IN ('INFO','WARN','ERROR','DEBUG')),
          category    TEXT NOT NULL,
          message     TEXT NOT NULL,
          source      TEXT,
          details     JSONB
        )
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_system_logs_level
          ON ${TACKLE_SCHEMA}.system_logs (level)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_system_logs_category
          ON ${TACKLE_SCHEMA}.system_logs (category)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp
          ON ${TACKLE_SCHEMA}.system_logs (timestamp DESC)
      `);
      console.log("[tackle-migrations] v10: Created tackle.system_logs table with indexes");
    },
  },
  {
    version: 11,
    description: "Register tackle.agent_timeclock — agent clock in/out table. Previously lived in the nebula schema (owned by the timeclock service via SQLAlchemy create_all); moved nebula -> tackle on 2026-08-02 via copy-repoint-drop. Idempotent: no-op where the table already exists (e.g. live DB), creates it on green-field installs. Mirrors the timeclock MCP model (python/timeclock/models.py) and the live table DDL (PK + clock_in/role/status indexes).",
    up: async (exec) => {
      await exec(`
        CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.agent_timeclock (
          id             UUID        NOT NULL DEFAULT gen_random_uuid(),
          role           TEXT        NOT NULL,
          model          TEXT        NOT NULL,
          session_id     TEXT,
          clock_in       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          clock_out      TIMESTAMPTZ,
          status         TEXT        NOT NULL DEFAULT 'active',
          metadata       JSONB,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          recorded_on_dt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          valid_from     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          valid_until    TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00'
        )
      `);
      await exec(`
        CREATE UNIQUE INDEX IF NOT EXISTS agent_timeclock_pkey
          ON ${TACKLE_SCHEMA}.agent_timeclock (id)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS agent_timeclock_clock_in_idx
          ON ${TACKLE_SCHEMA}.agent_timeclock (clock_in)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS agent_timeclock_role_idx
          ON ${TACKLE_SCHEMA}.agent_timeclock (role)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS agent_timeclock_status_idx
          ON ${TACKLE_SCHEMA}.agent_timeclock (status)
      `);
      console.log("[tackle-migrations] v11: Registered tackle.agent_timeclock table + indexes");
    },
  },
  {
    version: 12,
    description: "Add optional task_slug link to tackle.agent_scheduler — scheduled jobs can attach a task (from tackle.tasks) whose prompt is appended to the role's default persona when the job runs. Loose reference (no FK) so tasks can be deleted; deleteTackleTask clears references.",
    up: async (exec) => {
      await exec(`
        ALTER TABLE ${TACKLE_SCHEMA}.agent_scheduler
          ADD COLUMN IF NOT EXISTS task_slug TEXT
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_agent_scheduler_task_slug
          ON ${TACKLE_SCHEMA}.agent_scheduler (task_slug)
      `);
      console.log("[tackle-migrations] v12: Added task_slug to tackle.agent_scheduler");
    },
  },
  {
    version: 13,
    description: "Allow schedule_type 'manual' in tackle.agent_scheduler — the UI offers on-demand entries; the previous CHECK only allowed interval/cron so saving a manual row failed with a constraint violation.",
    up: async (exec) => {
      // Drop ANY existing CHECK on schedule_type regardless of its auto-generated
      // name, then re-add with 'manual' allowed.
      await exec(`
        DO $$
        DECLARE
          c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = '${TACKLE_SCHEMA}.agent_scheduler'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%schedule_type%'
          LOOP
            EXECUTE format('ALTER TABLE ${TACKLE_SCHEMA}.agent_scheduler DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
      `);
      await exec(`
        ALTER TABLE ${TACKLE_SCHEMA}.agent_scheduler
          ADD CONSTRAINT agent_scheduler_schedule_type_check
          CHECK (schedule_type IN ('interval', 'cron', 'manual'))
      `);
      console.log("[tackle-migrations] v13: Allowed schedule_type 'manual' in tackle.agent_scheduler");
    },
  },
  {
    version: 14,
    description: "ACP v1: Create tackle.projection_configs and seed six v1 projection families (opencode-agent-*, claude-md, gemini-md, agents-md, codex-index, agents-operating-model). Plan 1280.",
    up: async (exec) => {
      await exec(`
        CREATE TABLE IF NOT EXISTS ${TACKLE_SCHEMA}.projection_configs (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name            TEXT NOT NULL UNIQUE,
          description     TEXT NOT NULL DEFAULT '',
          type            TEXT NOT NULL DEFAULT 'deterministic'
                            CHECK(type IN ('deterministic','inference')),
          source_query    TEXT NOT NULL DEFAULT '',
          template        TEXT NOT NULL DEFAULT '',
          parameter_schema JSONB NOT NULL DEFAULT '{}',
          target_path     TEXT NOT NULL,
          schedule        TEXT NOT NULL DEFAULT '',
          enabled         INTEGER NOT NULL DEFAULT 1,
          last_rendered_at TIMESTAMPTZ,
          last_sha256     TEXT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
      `);

      await exec(`
        CREATE INDEX IF NOT EXISTS idx_projection_configs_enabled
          ON ${TACKLE_SCHEMA}.projection_configs (enabled)
      `);
      await exec(`
        CREATE INDEX IF NOT EXISTS idx_projection_configs_name
          ON ${TACKLE_SCHEMA}.projection_configs (name)
      `);

      // Seed the six v1 projection families
      const now = new Date().toISOString();

      // 1. opencode-agent-* — one file per role in .opencode/agents/
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic',
           'SELECT r.name AS role, r.description AS role_description FROM tackle.roles r ORDER BY r.name',
           $3,
           '/home/codex/dev/.opencode/agents/{{role}}.md',
           '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "opencode-agents",
          "Agent persona files — one .md per role under .opencode/agents/. Template embeds role name, role description, persona body from tackle.prompts, and procedure cards from tackle.role_memory.",
          `---
assumes_role: {{role}}
description: |
  {{role_description}}
mode: primary
permission:
  read: allow
  edit: allow
  bash: allow
  task: allow
---
{{persona_body}}

## Available Procedure Cards

{{procedures_list}}
`,
        ]
      );

      // 2. claude-md
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic', '', $3, '/home/codex/dev/CLAUDE.md', '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "claude-md",
          "CLAUDE.md — top-level agent baseline for Claude. Currently hand-maintained; projection preserves current content via template fidelity.",
          `<!-- GENERATED header will be prepended at render time -->
# CLAUDE.md

> **Version:** (see git history)
> **Scope:** Agent behavior for /home/codex/dev workspace.
> This file is a **GENERATED projection** from tackle data.
> Source: projection:claude-md
> Do not edit directly — changes will be overwritten on next render.

This file provides baseline agent behavior. Role-specific configuration lives in .opencode/agents/<role>.md (also generated).
`,
        ]
      );

      // 3. gemini-md
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic', '', $3, '/home/codex/dev/GEMINI.md', '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "gemini-md",
          "GEMINI.md — Gemini-specific agent configuration. Projection from tackle data.",
          `<!-- GENERATED header will be prepended at render time -->
# GEMINI.md

> **Scope:** Agent behavior for Gemini models in the /home/codex/dev workspace.
> This file is a **GENERATED projection** from tackle data.
> Source: projection:gemini-md
> Do not edit directly.

See CLAUDE.md and AGENTS.md for the governing doctrine. This file contains Gemini-specific overrides.
`,
        ]
      );

      // 4. agents-md
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic', '', $3, '/home/codex/dev/AGENTS.md', '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "agents-md",
          "AGENTS.md — Multi-model agent behavior specification. Projection from tackle data.",
          `<!-- GENERATED header will be prepended at render time -->
# AGENTS.md

> **Version:** 2.1 (trimmed 2026-06-24)
> **Scope:** Agent behavior for /home/codex/dev workspace.
> This file is a **GENERATED projection** from tackle data.
> Source: projection:agents-md
> Do not edit directly.

See CLAUDE.md for the full governing doctrine.
`,
        ]
      );

      // 5. codex-index
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic', '', $3, '/home/codex/dev/.codex/INDEX.md', '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "codex-index",
          ".codex/INDEX.md — Codex-specific index. Projection from tackle data.",
          `<!-- GENERATED header will be prepended at render time -->
# Codex Index

> This file is a **GENERATED projection** from tackle data.
> Source: projection:codex-index
> Do not edit directly.

Index of Codex-specific configuration and context.
`,
        ]
      );

      // 6. agents-operating-model
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.projection_configs (name, description, type, source_query, template, target_path, schedule)
         VALUES ($1, $2, 'deterministic', '', $3, '/home/codex/dev/nexus/.agents/OPERATING_MODEL.md', '')
         ON CONFLICT (name) DO NOTHING`,
        [
          "agents-operating-model",
          "nexus/.agents/OPERATING_MODEL.md — Nexus operating model. Projection from tackle data.",
          `<!-- GENERATED header will be prepended at render time -->
# Nexus Operating Model

> This file is a **GENERATED projection** from tackle data.
> Source: projection:agents-operating-model
> Do not edit directly.

## Operating Model

Nexus follows a database-first architecture: canonical state lives in PostgreSQL; filesystem artifacts are derived projections.
`,
        ]
      );

      console.log("[tackle-migrations] v14: Created tackle.projection_configs + seeded 6 projection families");
    },
  },
  {
    version: 15,
    description: "Add tackle.models.verified (BOOLEAN NOT NULL DEFAULT false) — marks models that have actually been exercised through a harness (inference test passed) and may therefore enter the resolver queue. Bundles whose model is unverified are forced inactive (verified-model gate). The live DB already carries the column; this makes green-field bootstraps match and stamps the migration.",
    up: async (exec) => {
      await exec(`
        ALTER TABLE ${TACKLE_SCHEMA}.models
          ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false
      `);
      console.log("[tackle-migrations] v15: Added tackle.models.verified");
    },
  },
  {
    version: 16,
    description: "Verified-model gate trigger on tackle.config_bundle — BEFORE INSERT OR UPDATE forces is_active=0 whenever the referenced model is unverified (or missing). One DB-level rule covers every write path (REST upserts, import, seed-defaults, CLI, external tooling) so an unverified model can never silently enter the resolver queue through a bypassing code path.",
    up: async (exec) => {
      await exec(`
        CREATE OR REPLACE FUNCTION ${TACKLE_SCHEMA}.config_bundle_verified_gate()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM ${TACKLE_SCHEMA}.models m
            WHERE m.id = NEW.model_id AND m.verified IS TRUE
          ) THEN
            NEW.is_active := 0;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
      `);
      await exec(`
        DROP TRIGGER IF EXISTS trg_config_bundle_verified_gate ON ${TACKLE_SCHEMA}.config_bundle
      `);
      await exec(`
        CREATE TRIGGER trg_config_bundle_verified_gate
        BEFORE INSERT OR UPDATE ON ${TACKLE_SCHEMA}.config_bundle
        FOR EACH ROW EXECUTE FUNCTION ${TACKLE_SCHEMA}.config_bundle_verified_gate()
      `);
      console.log("[tackle-migrations] v16: config_bundle verified-model gate trigger installed");
    },
  },
  {
    version: 17,
    description: "INTERACTIVE exemption in the verified-model gate — INTERACTIVE bundles (harn-freebuff, dispatched in Freebuff where the model is the human/CLI model, not an opencode provider reference) must stay active regardless of model verification. The gate exists to stop opencode spawning unresolvable model ids, which the INTERACTIVE channel never does.",
    up: async (exec) => {
      await exec(`
        CREATE OR REPLACE FUNCTION ${TACKLE_SCHEMA}.config_bundle_verified_gate()
        RETURNS trigger AS $$
        BEGIN
          -- INTERACTIVE bundles never spawn a harness with the model id —
          -- the model is the human/CLI model driving Freebuff. The verified
          -- gate (which exists to stop opencode spawning dead model ids)
          -- does not apply to this channel.
          IF NEW.invocation_mode = 'INTERACTIVE' THEN
            RETURN NEW;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM ${TACKLE_SCHEMA}.models m
            WHERE m.id = NEW.model_id AND m.verified IS TRUE
          ) THEN
            NEW.is_active := 0;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
      `);          console.log("[tackle-migrations] v17: verified-model gate INTERACTIVE exemption installed");
    },
  },
  {
    version: 18,
    description: "INTERACTIVE-hosted priority pin on tackle.config_bundle — a BEFORE INSERT OR UPDATE trigger pins the priority of any INTERACTIVE bundle to the role's minimum (best) priority, so a CLI primary (e.g. upsertAIRoleConfig's hardcoded priority=0) can never shadow an INTERACTIVE-hosted (harn-freebuff) role in the resolver's ascending-priority pick. Complements the app-level guard in upsertAIRoleConfig/upsertConfigBundles; the DB rule covers every write path (REST, import, seed-defaults, CLI, external tooling).",
    up: async (exec) => {
      await exec(`
        CREATE OR REPLACE FUNCTION ${TACKLE_SCHEMA}.config_bundle_interactive_priority_pin()
        RETURNS trigger AS $$
        DECLARE
          v_min_launchable_priority integer;
        BEGIN
          IF NEW.invocation_mode = 'INTERACTIVE' THEN
            -- Pin strictly ABOVE (numerically below) every launchable sibling
            -- so the ascending-priority resolver can never pick a CLI bundle
            -- over an INTERACTIVE-hosted role.
            SELECT MIN(priority) INTO v_min_launchable_priority
              FROM ${TACKLE_SCHEMA}.config_bundle
             WHERE role = NEW.role AND id <> NEW.id
               AND invocation_mode <> 'INTERACTIVE';
            IF v_min_launchable_priority IS NOT NULL
               AND NEW.priority >= v_min_launchable_priority THEN
              NEW.priority := v_min_launchable_priority - 1;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
      `);
      await exec(`
        DROP TRIGGER IF EXISTS trg_config_bundle_interactive_priority_pin ON ${TACKLE_SCHEMA}.config_bundle
      `);
      await exec(`
        CREATE TRIGGER trg_config_bundle_interactive_priority_pin
        BEFORE INSERT OR UPDATE ON ${TACKLE_SCHEMA}.config_bundle
        FOR EACH ROW EXECUTE FUNCTION ${TACKLE_SCHEMA}.config_bundle_interactive_priority_pin()
      `);
      console.log("[tackle-migrations] v18: INTERACTIVE-hosted priority pin trigger installed");
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
      // ON CONFLICT (version) DO UPDATE: some migrations (v7-v9) self-stamp
      // schema_version at the bottom of their SQL file. This wrapper stamp
      // must be idempotent so a self-stamped migration doesn't crash on PK
      // violation. Re-stamping also refreshes the timestamp if a migration
      // is re-applied in any future recovery scenario.
      await exec(
        `INSERT INTO ${TACKLE_SCHEMA}.schema_version (version, description, applied_at)
         VALUES ($1, $2, $3)
         ON CONFLICT (version) DO UPDATE
         SET description = EXCLUDED.description,
             applied_at  = EXCLUDED.applied_at`,
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
  { name: "inspector", description: "Compliance auditor — verifies invariants, issues violation reports" },
  { name: "supervisor", description: "Role-system administrator — registers and configures roles, regenerates doctrine and OpenCode projections, and verifies role-surface coverage; no WorkRequest execution authority" },
  { name: "test", description: "Internal test harness role — used for test invoke sessions and ad-hoc agent runs" },
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
  verified: boolean;
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

// ── Verification ────────────────────────────────────────────────────
// setModelVerified marks a model as verified after a successful harness
// run; rearmBundlesForModel re-activates every config bundle referencing
// the model (the verified-model gate trigger allows the flip now that the
// model is verified).

export async function setModelVerified(id: string, verified: boolean): Promise<void> {
  await qRun(
    "UPDATE models SET verified = @verified, updated_at = @updated_at WHERE id = @id",
    { id, verified, updated_at: new Date().toISOString() }
  );
}

export async function rearmBundlesForModel(modelId: string): Promise<number> {
  return qRun(
    "UPDATE config_bundle SET is_active = 1, updated_at = @updated_at WHERE model_id = @model_id AND is_active = 0",
    { model_id: modelId, updated_at: new Date().toISOString() }
  );
}

export interface PurgeResult {
  role: string;
  bundleIds: string[];
}

// deactivateBundlesForUnverifiedModels force-deactivates every config bundle
// (across all roles) whose model is unverified or missing, so no role's
// resolver queue can select a bundle whose model has not been certified.
// Returns per-role affected bundle counts (role + bundle ids).
export async function deactivateBundlesForUnverifiedModels(): Promise<PurgeResult[]> {
  const affected: PurgeResult[] = [];
  // All bundles whose model is missing OR present-but-unverified.
  const bundles = await qAll(
    `SELECT cb.id, cb.role, cb.model_id
     FROM config_bundle cb
     LEFT JOIN models m ON m.id = cb.model_id
     WHERE cb.is_active = 1 AND (m.id IS NULL OR m.verified IS NOT TRUE)`
  );
  for (const b of bundles) {
    await qRun(
      "UPDATE config_bundle SET is_active = 0, updated_at = @updated_at WHERE id = @id AND is_active = 1",
      { id: b.id, updated_at: new Date().toISOString() }
    );
    let entry = affected.find((r) => r.role === b.role);
    if (!entry) {
      entry = { role: b.role, bundleIds: [] };
      affected.push(entry);
    }
    entry.bundleIds.push(b.id);
  }
  return affected;
}

// ── Verified-model gate helpers ────────────────────────────────────
// A model only becomes usable once it has been verified (an inference
// test actually passed through a harness). Bundles referencing unverified
// models are forced inactive on every write path so they can never enter
// the resolver queue — the UI filters them from model dropdowns, and the
// test-invoke endpoint refuses them outright.

async function getVerifiedModelIds(ids: string[]): Promise<Set<string>> {
  if (ids.length === 0) return new Set();
  const rows = await qAll(
    "SELECT id FROM models WHERE id = ANY(@ids) AND verified IS TRUE",
    { ids }
  );
  return new Set(rows.map((r) => r.id));
}

async function isModelVerified(id: string): Promise<boolean> {
  if (!id) return false;
  const row = await qOne("SELECT verified FROM models WHERE id = @id", { id });
  return !!(row && row.verified);
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
  // Verified-model gate: a primary role config over an unverified model is
  // stored inactive so it never resolves.
  const verified = await isModelVerified(rc.model_id);
  const isActive = verified ? 1 : 0;
  // INTERACTIVE-hosted guard: this single-role upsert always writes a CLI
  // primary at priority 0. If the role also has an INTERACTIVE (harn-freebuff)
  // bundle — i.e. the role is Freebuff-resident — slot the CLI primary BELOW
  // it so the resolver's ascending-priority pick still resolves INTERACTIVE.
  // Without this, saving a primary config for a Freebuff-resident role makes
  // it CLI-launchable (wr-conf-005 drift, seen 2026-09-02 on leased-builder).
  const interactiveBundle = await qOne(
    `SELECT id, priority FROM config_bundle
      WHERE role = @role AND invocation_mode = 'INTERACTIVE'
      ORDER BY priority ASC LIMIT 1`,
    { role: rc.role }
  );
  let priority = 0;
  if (interactiveBundle) {
    priority = (interactiveBundle.priority ?? 0) + 1;
    // Keep the INTERACTIVE bundle pinned at the top (it may itself have been
    // displaced by an earlier drift — restore it while we are here).
    if (interactiveBundle.priority !== 0) {
      await qRun(`UPDATE config_bundle SET priority = 0, updated_at = @now WHERE id = @id`,
        { now, id: interactiveBundle.id });
    }
  }
  await qRun(
    `INSERT INTO config_bundle (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, metadata, created_at, updated_at)
     VALUES (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, 'CLI', @is_active, '{}', @created_at, @updated_at)
     ON CONFLICT (id) DO UPDATE SET
       name = EXCLUDED.name, role = EXCLUDED.role, model_id = EXCLUDED.model_id,
       provider_id = EXCLUDED.provider_id, harness_id = EXCLUDED.harness_id,
       priority = EXCLUDED.priority, is_active = EXCLUDED.is_active, updated_at = EXCLUDED.updated_at`,
    { ...rc, name: `Primary: ${rc.model_id} for ${rc.role}`,
      is_active: isActive,
      priority,
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
  // Verified-model gate: a bundle whose model has not been verified can never
  // be active, regardless of what the caller requested. When the model IS
  // verified, preserve the historic default (absent is_active → active).
  // is_active is INTEGER in the schema; the route normalizes booleans to 1/0
  // before calling, so 0 is the only "inactive" representation seen here.
  const verified = await isModelVerified(b.model_id);
  // INTERACTIVE bundles are dispatched in Freebuff — the model is the
  // human/CLI model, not an opencode provider reference — so the verified
  // gate (which exists to stop opencode spawning dead model ids) does not
  // apply to this channel.
  const isActive = b.invocation_mode === "INTERACTIVE"
    ? (b.is_active === 0 ? 0 : 1)
    : (verified ? (b.is_active === 0 ? 0 : 1) : 0);
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
      is_active: isActive,
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

  // Static format string — role/bundle values passed as args
  // (CodeQL js/tainted-format-string remediation, alert #674).
  console.log("[upsertConfigBundles] Starting for role: %s, bundles: %s", role, bundles.length);

  // Verified-model gate: any bundle in the batch whose model is unverified is
  // inserted inactive (this path previously hardcoded is_active=1, which would
  // have re-activated unverified models on every role save).
  const verifiedIds = await getVerifiedModelIds(bundles.map((b) => b.model_id));

  await withTransaction(async (client) => {
    const deleteResult = await tRun(client, "DELETE FROM config_bundle WHERE role = @role", { role });
    console.log("[upsertConfigBundles] Deleted rows for role %s:", role, deleteResult);
    
    // INTERACTIVE-hosted guard: the resolver picks the ascending-priority
    // first active bundle, so an INTERACTIVE (harn-freebuff) bundle must sit
    // at the role's best priority or a CLI sibling can shadow it and make a
    // Freebuff-resident role launchable (wr-conf-005 drift, 2026-09-02).
    const minInteractivePriority = Math.min(
      ...bundles.filter((b) => b.invocation_mode === "INTERACTIVE").map((b) => b.priority),
      Infinity
    );
    for (const b of bundles) {
      const id = `cb-${role}-${b.model_id}`;
      // Slide non-INTERACTIVE bundles below the best INTERACTIVE one.
      const priority =
        b.invocation_mode !== "INTERACTIVE" && Number.isFinite(minInteractivePriority) && b.priority < minInteractivePriority
          ? minInteractivePriority + b.priority
          : b.priority;
    console.log("[upsertConfigBundles] Inserting bundle: %s (priority %s)", id, priority);
      await tRun(client,
        `INSERT INTO config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode,
            command, endpoint_url, timeout_ms, is_active, metadata, created_at, updated_at)
         VALUES
           (@id, @name, @role, @model_id, @provider_id, @harness_id, @priority, @invocation_mode,
            @command, @endpoint_url, @timeout_ms, @is_active, '{}', @now, @now)`,
        {
          id,
          name: b.name ?? `Bundle: ${b.model_id}`,
          role,
          model_id: b.model_id,
          priority,
          // INTERACTIVE bundles never spawn a harness — exemption from the
          // verified gate (see trigger v17). Note: this batch path has no
          // is_active channel in its input, so INTERACTIVE always lands
          // active here (the single upsert can honor an explicit inactive).
          is_active: b.invocation_mode === "INTERACTIVE"
            ? 1
            : (verifiedIds.has(b.model_id) ? 1 : 0),
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
    console.log("[upsertConfigBundles] Completed for role: %s", role);
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

// ── Task registry (tackle.tasks) ────────────────────────────────────
//
// v7 created tackle.tasks; v8 seeded one inspector task. These exports
// expose the registry to the /tasks REST route and to the inspector
// dispatch flow. The CLI (nexus/typescript/tackle-cli) talks to PG
// directly for its own queries — these functions are the server-side
// mirror for in-process and HTTP consumers.

export interface TackleTaskRow {
  id: string;
  role: string;
  task_slug: string;
  scope: string;
  acceptance_criteria: string[];
  prompt_id: string;
  active: boolean;
  created_at: string;
  updated_at: string;
  // Joined from tackle.prompts for dispatch convenience.
  prompt_role?: string;
  prompt_slug?: string;
  prompt_version?: number;
}

/**
 * List tasks. Default: active only. If includeInactive=true, return all.
 * If role is given, filter by role.
 */
export async function listTackleTasks(
  role?: string,
  includeInactive = false
): Promise<TackleTaskRow[]> {
  const conditions: string[] = [];
  const params: Record<string, any> = {};
  if (!includeInactive) conditions.push("active = TRUE");
  if (role) {
    params.role = role;
    conditions.push("role = @role");
  }
  const where = conditions.length ? "WHERE " + conditions.join(" AND ") : "";
  return qAll(
    `SELECT id, role, task_slug, scope, acceptance_criteria,
            prompt_id, active, created_at, updated_at
     FROM tasks
     ${where}
     ORDER BY role, task_slug`,
    params
  );
}

/**
 * Fetch one task by task_slug, joining the referenced prompt so the
 * caller gets the (role/slug/version) triple for the template the task
 * binds to. Returns the most-recent active row, falling back to the
 * most-recent inactive row if no active task exists with that slug.
 */
export async function getTackleTask(
  taskSlug: string
): Promise<TackleTaskRow | undefined> {
  return qOne(
    `SELECT t.id, t.role, t.task_slug, t.scope, t.acceptance_criteria,
            t.prompt_id, t.active, t.created_at, t.updated_at,
            p.role      AS prompt_role,
            p.slug      AS prompt_slug,
            p.version   AS prompt_version
     FROM tasks t
     LEFT JOIN prompts p ON p.id = t.prompt_id
     WHERE t.task_slug = @slug
     ORDER BY t.active DESC, t.updated_at DESC
     LIMIT 1`,
    { slug: taskSlug }
  );
}

/**
 * Resolve the dispatch payload for the inspector role: every active
 * task assigned to `inspector`, each bundled with the full prompt body
 * for its referenced template (latest version of that (role, slug)).
 *
 * This is the "wire" in "wire inspector task dispatch" — the /tasks
 * route returns this and the inspector (or any consumer) gets a single
 * JSON document containing both the task definition AND the prompt body
 * needed to execute it, so the consumer doesn't need a second round-trip
 * to resolve the template.
 */
export async function getInspectorDispatch(): Promise<{
  tasks: Array<TackleTaskRow & {
    prompt_body_md: string | null;
    prompt_title: string | null;
    prompt_parameter_schema: Record<string, any> | null;
    prompt_tags: string[] | null;
  }>;
}> {
  const tasks = await qAll(
    `SELECT id, role, task_slug, scope, acceptance_criteria,
            prompt_id, active, created_at, updated_at
     FROM tasks
     WHERE role = @role AND active = TRUE
     ORDER BY task_slug`,
    { role: "inspector" }
  );

  // Resolve each task's prompt_id to the full latest-version template.
  // We do this with one extra query per task rather than a single JOIN
  // because the task references a *specific* prompt_id (a specific
  // version), but the consumer wants the LATEST version of the same
  // (role, slug). Fetching the latest requires a DISTINCT ON window,
  // which is fiddly to express as a JOIN — the per-task round-trip is
  // small (today: exactly one inspector task) and keeps the query legible.
  const enriched = await Promise.all(
    tasks.map(async (t) => {
      const prompt = await qOne(
        `SELECT DISTINCT ON (role, slug)
                id, role, slug, version, title, body_md,
                parameter_schema, tags, created_at, updated_at
         FROM prompts
         WHERE role = (SELECT role FROM prompts WHERE id = @pid)
           AND slug = (SELECT slug FROM prompts WHERE id = @pid)
         ORDER BY role, slug, version DESC`,
        { pid: t.prompt_id }
      );
      return {
        ...t,
        prompt_role: prompt?.role ?? null,
        prompt_slug: prompt?.slug ?? null,
        prompt_version: prompt?.version ?? null,
        prompt_body_md: prompt?.body_md ?? null,
        prompt_title: prompt?.title ?? null,
        prompt_parameter_schema: prompt?.parameter_schema ?? null,
        prompt_tags: prompt?.tags ?? null,
      };
    })
  );

  return { tasks: enriched };
}

// ── Prompts (tackle.prompts) ───────────────────────────────────────

export async function listPrompts(
  role?: string
): Promise<any[]> {
  const params: Record<string, any> = {};
  let where = "";
  if (role) { params.role = role; where = "WHERE role = @role"; }
  return qAll(
    `SELECT id, role, slug, version, title, body_md, parameter_schema, tags, created_at, updated_at
     FROM prompts ${where} ORDER BY role, slug, version DESC`,
    params
  );
}

export async function getPromptByRoleSlug(
  role: string,
  slug: string
): Promise<any | undefined> {
  return qOne(
    `SELECT id, role, slug, version, title, body_md, parameter_schema, tags, created_at, updated_at
     FROM prompts WHERE role = @role AND slug = @slug
     ORDER BY version DESC LIMIT 1`,
    { role, slug }
  );
}

export async function upsertPrompt(data: {
  id?: string;
  role: string;
  slug: string;
  version?: number;
  title: string;
  body_md: string;
  parameter_schema?: Record<string, any>;
  tags?: string[];
}): Promise<any> {
  const latest = await qOne(
    "SELECT version FROM prompts WHERE role = @role AND slug = @slug ORDER BY version DESC LIMIT 1",
    { role: data.role, slug: data.slug }
  );
  const version = data.version || (latest ? latest.version + 1 : 1);

  if (data.id) {
    const rows = await qRun(
      `UPDATE prompts SET role = @role, slug = @slug, version = @version,
       title = @title, body_md = @body_md,
       parameter_schema = @ps::jsonb, tags = @tags, updated_at = NOW()
       WHERE id = @id`,
      { id: data.id, role: data.role, slug: data.slug, version,
        title: data.title, body_md: data.body_md,
        ps: JSON.stringify(data.parameter_schema || {}),
        tags: data.tags || [] }
    );
    if (rows > 0) {
      return { id: data.id, role: data.role, slug: data.slug, version };
    }
    // ID not found — fall through to insert
  }

  const result = await q(
    `INSERT INTO prompts (role, slug, version, title, body_md, parameter_schema, tags)
     VALUES (@role, @slug, @version, @title, @body_md, @ps::jsonb, @tags)
     ON CONFLICT (role, slug, version) DO UPDATE
     SET title = EXCLUDED.title, body_md = EXCLUDED.body_md,
         parameter_schema = EXCLUDED.parameter_schema, tags = EXCLUDED.tags,
         updated_at = NOW()
     RETURNING id`,
    { role: data.role, slug: data.slug, version,
      title: data.title, body_md: data.body_md,
      ps: JSON.stringify(data.parameter_schema || {}),
      tags: data.tags || [] }
  );
  return { id: result.rows[0].id, role: data.role, slug: data.slug, version };
}

// ── Tool Access (tackle.role_tool_access) ───────────────────────────

export async function listToolAccess(role?: string): Promise<any[]> {
  const params: Record<string, any> = {};
  let where = "";
  if (role) { params.role = role; where = "WHERE role = @role"; }
  return qAll(
    `SELECT id, role, mcp_id, tool_slug, created_at
     FROM role_tool_access ${where} ORDER BY role, tool_slug`,
    params
  );
}

export async function updateToolAccess(
  id: string,
  data: { allowed: boolean }
): Promise<any | undefined> {
  if (data.allowed === false) {
    await qRun("DELETE FROM role_tool_access WHERE id = @id", { id });
    return { id, deleted: true };
  }
  return qOne("SELECT id, role, mcp_id, tool_slug FROM role_tool_access WHERE id = @id", { id });
}

// ── Role Provisioning (architect gap analysis 2026-08-17) ────────────
//
// Implements the missing API pieces from the architect's "Role Provisioning
// Gap Analysis": bulk tool-access create/seed, procedure-card assignment
// via tackle.role_memory, a per-role readiness check, and an atomic
// POST /roles/provision orchestrator that collapses the 4-6 manual API
// calls into one and keeps tackle.roles ↔ nebula.roles in sync.

/**
 * Bulk-create tool-access allowlist rows for a role (default-deny positive
 * allowlist). Each entry is { mcp_id, tool_slug }. Duplicate (role, tool_slug)
 * rows are skipped (UNIQUE constraint). Returns the number of rows created.
 */
export async function createToolAccess(
  role: string,
  tools: { mcp_id: string; tool_slug: string }[],
): Promise<number> {
  if (tools.length === 0) return 0;
  const clean = tools.filter((t) => t && t.tool_slug);
  if (clean.length === 0) return 0;
  const result = await q(
    `INSERT INTO role_tool_access (role, mcp_id, tool_slug)
     SELECT role, mcp_id, tool_slug
     FROM unnest(@roles::text[], @mcps::text[], @slugs::text[]) AS t(role, mcp_id, tool_slug)
     ON CONFLICT DO NOTHING
     RETURNING id`,
    {
      roles: clean.map(() => role),
      mcps: clean.map((t) => t.mcp_id || ""),
      slugs: clean.map((t) => t.tool_slug),
    }
  );
  return result.rows.length;
}

/**
 * Seed a role's tool allowlist from a template role (copy all rows).
 * Returns the number of rows copied. Default-deny: a new role starts with
 * zero tools, so provisioning generally seeds from an existing role.
 */
export async function seedToolAccess(role: string, fromRole: string): Promise<number> {
  const result = await q(
    `INSERT INTO role_tool_access (role, mcp_id, tool_slug)
     SELECT @role, mcp_id, tool_slug
     FROM role_tool_access
     WHERE role = @fromRole
     ON CONFLICT DO NOTHING
     RETURNING id`,
    { role, fromRole }
  );
  return result.rows.length;
}

/** Resolve a procedure card's UUID by slug (tackle.memory). */
export async function getMemoryIdBySlug(slug: string): Promise<string | undefined> {
  const row = await qOne("SELECT id FROM memory WHERE slug = @slug", { slug });
  return row?.id;
}

/**
 * Assign procedure cards to a role (tackle.role_memory). Active (unexpired)
 * assignments are left untouched. Returns the number of assignments made.
 */
export async function assignProcedures(role: string, slugs: string[]): Promise<number> {
  let assigned = 0;
  for (const slug of slugs) {
    const memoryId = await getMemoryIdBySlug(slug);
    if (!memoryId) continue;
    const r = await q(
      `INSERT INTO role_memory (memory_id, role, as_of_dt, expiration_dt)
       SELECT @memoryId, @role, NOW(), NULL
       WHERE NOT EXISTS (
         SELECT 1 FROM role_memory
         WHERE role = @role AND memory_id = @memoryId AND expiration_dt IS NULL
       )
       RETURNING id`,
      { memoryId, role }
    );
    assigned += r.rows.length;
  }
  return assigned;
}

/**
 * Unassign a procedure card from a role by expiring the active assignment
 * (bitemporal-preserving soft delete — history is retained). Returns true if
 * an active assignment was expired.
 */
export async function unassignProcedure(role: string, slug: string): Promise<boolean> {
  const memoryId = await getMemoryIdBySlug(slug);
  if (!memoryId) return false;
  const r = await qRun(
    `UPDATE role_memory SET expiration_dt = NOW()
     WHERE role = @role AND memory_id = @memoryId AND expiration_dt IS NULL`,
    { role, memoryId }
  );
  return r > 0;
}

// ── Role readiness (Gap 6) ──────────────────────────────────────────

function titleCaseProvision(s: string): string {
  return s.split(/[_-]+/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

export interface ReadinessPiece {
  key: string;
  label: string;
  required: boolean;
  present: boolean;
  detail: string;
}

export interface RoleReadiness {
  role: string;
  ready: boolean;
  requiredPresent: number;
  requiredTotal: number;
  pieces: ReadinessPiece[];
}

/**
 * Readiness check for a role across all ten participation pieces from the
 * architect's gap analysis. Cross-schema reads (nebula.roles, assembly.users)
 * degrade to a per-piece error detail instead of failing the whole check.
 */
export async function getRoleReadiness(name: string): Promise<RoleReadiness> {
  const pieces: ReadinessPiece[] = [];
  const add = (key: string, label: string, required: boolean, present: boolean, detail = "") =>
    pieces.push({ key, label, required, present, detail });

  const safe = async (
    key: string, label: string, required: boolean,
    sql: string, params: Record<string, any>,
    presentOf: (row: any) => boolean, detailOf?: (row: any) => string,
  ) => {
    try {
      const row = await qOne(sql, params);
      add(key, label, required, presentOf(row), detailOf ? detailOf(row) : "");
    } catch (e: any) {
      add(key, label, required, false, `check error: ${e.message}`);
    }
  };

  await safe("identity", "tackle.roles identity", true,
    "SELECT 1 AS p FROM roles WHERE name = @name", { name }, (r) => !!r);
  await safe("ai_config", "AI config bundle", true,
    "SELECT count(*)::int AS c FROM config_bundle WHERE role = @name", { name },
    (r) => (r?.c || 0) > 0, (r) => `${r?.c || 0} bundle(s)`);
  await safe("persona", "Persona prompt (opencode-persona)", true,
    "SELECT 1 AS p FROM prompts WHERE role = @name AND slug = 'opencode-persona' LIMIT 1", { name }, (r) => !!r);
  await safe("tool_access", "Tool access allowlist", true,
    "SELECT count(*)::int AS c FROM role_tool_access WHERE role = @name", { name },
    (r) => (r?.c || 0) > 0, (r) => `${r?.c || 0} tool(s)`);
  await safe("procedures", "Procedure cards", true,
    "SELECT count(*)::int AS c FROM role_memory WHERE role = @name AND expiration_dt IS NULL", { name },
    (r) => (r?.c || 0) > 0, (r) => `${r?.c || 0} card(s)`);
  await safe("nebula_roles", "nebula.roles metadata", true,
    "SELECT 1 AS p FROM nebula.roles WHERE name = @name", { name }, (r) => !!r);
  await safe("lease", "Role lease (active)", false,
    "SELECT 1 AS p FROM role_leases WHERE role = @name AND status = 'ACTIVE' LIMIT 1", { name }, (r) => !!r);
  await safe("assembly_user", "Assembly user", true,
    "SELECT 1 AS p FROM assembly.users WHERE alias = @name", { name }, (r) => !!r);
  await safe("scheduler", "Scheduler entry", false,
    "SELECT count(*)::int AS c FROM agent_scheduler WHERE role = @name", { name },
    (r) => (r?.c || 0) > 0, (r) => `${r?.c || 0} entry/ies`);
  await safe("task", "Task definition", false,
    "SELECT count(*)::int AS c FROM tasks WHERE role = @name", { name },
    (r) => (r?.c || 0) > 0, (r) => `${r?.c || 0} task(s)`);

  const required = pieces.filter((p) => p.required);
  const requiredPresent = required.filter((p) => p.present).length;
  return {
    role: name,
    ready: requiredPresent === required.length,
    requiredPresent,
    requiredTotal: required.length,
    pieces,
  };
}

// ── Atomic role provisioning (Gap 1 + Gap 7) ────────────────────────

export interface ProvisionSpec {
  name: string;
  description?: string;
  displayName?: string;
  // config bundle
  modelId?: string;
  providerId?: string;
  harnessId?: string;
  bundle?: Partial<ConfigBundleRow>;
  // persona prompt
  persona?: { body_md: string; title?: string; slug?: string };
  // tool access
  tools?: (string | { mcp_id: string; tool_slug: string })[];
  toolTemplateRole?: string;
  // procedure cards
  procedures?: string[];
  procedureTemplateRole?: string;
  // nebula.roles metadata (dual-registry sync)
  nebula?: Record<string, any>;
  // assembly user
  createAssemblyUser?: boolean;
  assemblyEmail?: string;
}

/**
 * Atomic "create role" orchestrator. Collapses the 4-6 manual API calls
 * into one transaction across tackle.roles / tackle.config_bundle /
 * tackle.prompts / tackle.role_tool_access / tackle.role_memory /
 * nebula.roles / assembly.users. Any failure rolls back the whole role.
 */
export async function provisionRole(spec: ProvisionSpec): Promise<{
  role: string;
  steps: string[];
}> {
  return withTransaction(async (client) => {
    const now = new Date().toISOString();
    const name = spec.name;
    const steps: string[] = [];

    // 1. tackle.roles identity
    await client.query(
      `INSERT INTO tackle.roles (name, description, created_at, updated_at)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (name) DO UPDATE
       SET description = EXCLUDED.description, updated_at = EXCLUDED.updated_at`,
      [name, spec.description ?? "", now, now]
    );
    steps.push("role_identity");

    // 2. config bundle (verified-model gate trigger applies on insert)
    if (spec.modelId) {
      const m = await client.query("SELECT id FROM tackle.models WHERE id = $1", [spec.modelId]);
      if (m.rows.length === 0) {
        throw new Error(`Model '${spec.modelId}' not found in tackle.models`);
      }
      const bundleId = `bundle-${name}-default`;
      await client.query(
        `INSERT INTO tackle.config_bundle
           (id, name, role, model_id, provider_id, harness_id, priority,
            invocation_mode, command, endpoint_url, timeout_ms, is_active,
            metadata, created_at, updated_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,1,$12,$13,$14)
         ON CONFLICT (id) DO UPDATE
         SET name = EXCLUDED.name, role = EXCLUDED.role, model_id = EXCLUDED.model_id,
             provider_id = EXCLUDED.provider_id, harness_id = EXCLUDED.harness_id,
             priority = EXCLUDED.priority, invocation_mode = EXCLUDED.invocation_mode,
             command = EXCLUDED.command, endpoint_url = EXCLUDED.endpoint_url,
             timeout_ms = EXCLUDED.timeout_ms, metadata = EXCLUDED.metadata,
             updated_at = EXCLUDED.updated_at`,
        [bundleId, spec.bundle?.name || `${name}-default`, name, spec.modelId,
         spec.providerId ?? null, spec.harnessId ?? null, spec.bundle?.priority ?? 0,
         spec.bundle?.invocation_mode ?? "CLI", spec.bundle?.command ?? null,
         spec.bundle?.endpoint_url ?? null, spec.bundle?.timeout_ms ?? null,
         spec.bundle?.metadata ?? "{}", now, now]
      );
      steps.push("config_bundle");
    }

    // 3. persona prompt
    if (spec.persona?.body_md) {
      const slug = spec.persona.slug || "opencode-persona";
      const latest = await client.query(
        "SELECT version FROM tackle.prompts WHERE role = $1 AND slug = $2 ORDER BY version DESC LIMIT 1",
        [name, slug]
      );
      const version = (latest.rows[0]?.version ?? 0) + 1;
      await client.query(
        `INSERT INTO tackle.prompts (role, slug, version, title, body_md, parameter_schema, tags)
         VALUES ($1,$2,$3,$4,$5,'{}'::jsonb,'{}'::text[])
         ON CONFLICT (role, slug, version) DO UPDATE
         SET title = EXCLUDED.title, body_md = EXCLUDED.body_md, updated_at = NOW()`,
        [name, slug, version, spec.persona.title || `${name} persona`, spec.persona.body_md]
      );
      steps.push("persona_prompt");
    }

    // 4. tool access allowlist
    if (spec.tools && spec.tools.length > 0) {
      const tools = spec.tools.map((t) =>
        typeof t === "string" ? { mcp_id: "", tool_slug: t } : t
      );
      const r = await client.query(
        `INSERT INTO tackle.role_tool_access (role, mcp_id, tool_slug)
         SELECT $1, mcp_id, tool_slug
         FROM unnest($2::text[], $3::text[]) AS t(mcp_id, tool_slug)
         ON CONFLICT DO NOTHING`,
        [name, tools.map((t) => t.mcp_id || ""), tools.map((t) => t.tool_slug)]
      );
      steps.push(`tool_access(${r.rowCount ?? 0})`);
    }
    if (spec.toolTemplateRole) {
      const r = await client.query(
        `INSERT INTO tackle.role_tool_access (role, mcp_id, tool_slug)
         SELECT $1, mcp_id, tool_slug FROM tackle.role_tool_access WHERE role = $2
         ON CONFLICT DO NOTHING`,
        [name, spec.toolTemplateRole]
      );
      steps.push(`tool_access_seed(${r.rowCount ?? 0})`);
    }

    // 5. procedure cards
    if (spec.procedures && spec.procedures.length > 0) {
      for (const slug of spec.procedures) {
        await client.query(
          `INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
           SELECT m.id, $1, NOW(), NULL FROM tackle.memory m WHERE m.slug = $2
           AND NOT EXISTS (
             SELECT 1 FROM tackle.role_memory rm
             WHERE rm.role = $1 AND rm.memory_id = m.id AND rm.expiration_dt IS NULL
           )`,
          [name, slug]
        );
      }
      steps.push(`procedures(${spec.procedures.length})`);
    }
    if (spec.procedureTemplateRole) {
      const r = await client.query(
        `INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
         SELECT rm.memory_id, $1, NOW(), NULL FROM tackle.role_memory rm
         WHERE rm.role = $2 AND rm.expiration_dt IS NULL
         AND NOT EXISTS (
           SELECT 1 FROM tackle.role_memory x
           WHERE x.role = $1 AND x.memory_id = rm.memory_id AND x.expiration_dt IS NULL
         )`,
        [name, spec.procedureTemplateRole]
      );
      steps.push(`procedures_seed(${r.rowCount ?? 0})`);
    }

    // 6. nebula.roles dual-registry sync (Gap 7)
    const nb = spec.nebula || {};
    const displayName = spec.displayName || titleCaseProvision(name);
    await client.query(
      `INSERT INTO nebula.roles
         (name, display_name, description, owns_domains,
          can_greenlight, can_create_questions, can_create_agendas,
          can_resolve_questions, can_verify_work_requests,
          max_open_questions, requires_approval_from,
          cron_enabled, cron_expression, cron_description,
          escalates_to, escalation_triggers,
          level_filter_primary, level_filter_allowed, visibility_scope)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
       ON CONFLICT (name) DO UPDATE
       SET display_name = EXCLUDED.display_name, description = EXCLUDED.description,
           owns_domains = EXCLUDED.owns_domains, can_greenlight = EXCLUDED.can_greenlight,
           can_create_questions = EXCLUDED.can_create_questions,
           can_create_agendas = EXCLUDED.can_create_agendas,
           can_resolve_questions = EXCLUDED.can_resolve_questions,
           can_verify_work_requests = EXCLUDED.can_verify_work_requests,
           max_open_questions = EXCLUDED.max_open_questions,
           requires_approval_from = EXCLUDED.requires_approval_from,
           cron_enabled = EXCLUDED.cron_enabled, cron_expression = EXCLUDED.cron_expression,
           cron_description = EXCLUDED.cron_description, escalates_to = EXCLUDED.escalates_to,
           escalation_triggers = EXCLUDED.escalation_triggers,
           level_filter_primary = EXCLUDED.level_filter_primary,
           level_filter_allowed = EXCLUDED.level_filter_allowed,
           visibility_scope = EXCLUDED.visibility_scope, updated_at = NOW()`,
      [name, displayName, spec.description ?? null, nb.ownsDomains ?? [],
       nb.canGreenlight ?? false, nb.canCreateQuestions ?? false, nb.canCreateAgendas ?? false,
       nb.canResolveQuestions ?? false, nb.canVerifyWorkRequests ?? false,
       nb.maxOpenQuestions ?? null, nb.requiresApprovalFrom ?? [],
       nb.cronEnabled ?? false, nb.cronExpression ?? null, nb.cronDescription ?? null,
       nb.escalatesTo ?? [], nb.escalationTriggers ?? [],
       nb.levelFilterPrimary ?? "level <= 2", nb.levelFilterAllowed ?? "level <= 3",
       nb.visibilityScope ?? ["planner", "all"]]
    );
    steps.push("nebula_roles_sync");

    // 7. assembly user (posting identity)
    if (spec.createAssemblyUser) {
      const email = spec.assemblyEmail || `${name}@nexus.local`;
      await client.query(
        `INSERT INTO assembly.users (id, alias, email, password, avatar_url, admin)
         VALUES (gen_random_uuid(), $1, $2, 'changeme', NULL, false)
         ON CONFLICT (alias) DO NOTHING`,
        [name, email]
      );
      steps.push("assembly_user");
    }

    return { role: name, steps };
  });
}

// ── Tasks (extend) ──────────────────────────────────────────────────

export async function upsertTackleTask(data: {
  id?: string;
  role: string;
  task_slug: string;
  scope?: string;
  acceptance_criteria?: string[];
  prompt_id: string;
  active?: boolean;
}): Promise<any> {
  return qOne(
    `INSERT INTO tasks (role, task_slug, scope, acceptance_criteria, prompt_id, active)
     VALUES (@role, @slug, @scope, @ac, @pid, @active)
     ON CONFLICT (role, task_slug) DO UPDATE
     SET scope = EXCLUDED.scope, acceptance_criteria = EXCLUDED.acceptance_criteria,
         prompt_id = EXCLUDED.prompt_id, active = EXCLUDED.active, updated_at = NOW()
     RETURNING *`,
    { role: data.role, slug: data.task_slug, scope: data.scope || '',
      ac: data.acceptance_criteria || [], pid: data.prompt_id,
      active: data.active !== false }
  );
}

/**
 * Delete a task by (role, task_slug). The tasks table only guarantees
 * task_slug uniqueness WITHIN a role (UNIQUE(role, task_slug)), so the
 * delete is scoped by role to avoid collateral damage to a same-slug
 * task belonging to another role. Because agent_scheduler references
 * tasks by task_slug (loose link, no FK — migration v12), any scheduler
 * entries pointing at the task have their task_slug cleared so scheduled
 * jobs gracefully fall back to the role's default persona only.
 */
export async function deleteTackleTask(taskSlug: string, role?: string): Promise<boolean> {
  if (role) {
    await qRun(
      "UPDATE agent_scheduler SET task_slug = NULL WHERE task_slug = @slug AND role = @role",
      { slug: taskSlug, role }
    );
  } else {
    await qRun(
      "UPDATE agent_scheduler SET task_slug = NULL WHERE task_slug = @slug",
      { slug: taskSlug }
    );
  }
  const params: Record<string, any> = { slug: taskSlug };
  let where = "task_slug = @slug";
  if (role) {
    params.role = role;
    where += " AND role = @role";
  }
  const changes = await qRun(`DELETE FROM tasks WHERE ${where}`, params);
  return changes > 0;
}

// ── Role Checkpoints ────────────────────────────────────────────────

export async function getRoleCheckpoints(): Promise<Record<string, { role: string; last_active: string }>> {
  const rows = await qAll(
    `SELECT role, MAX(as_of_dt) as last_active
     FROM role_memory GROUP BY role ORDER BY role`
  );
  const result: Record<string, any> = {};
  for (const r of rows) {
    result[r.role] = { role: r.role, last_active: r.last_active };
  }
  return result;
}

export interface AgentSchedulerRow {
  id: number;
  role: string;
  model_id: string | null;
  harness: string;
  agent_config: string;
  schedule_type: string;
  schedule_value: number;
  cron_expr: string | null;
  event_criteria: string | null;
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

// ── Scheduler coercion helpers ─────────────────────────────────────
// agent_scheduler stores `enabled` and `schedule_value` as INTEGER
// columns, but tackle-ui sends `enabled: true/false` (boolean) and
// schedule values as strings. Coerce to the DB types here so live mode
// matches mock mode instead of failing with integer-cast errors.
function toEnabledInt(v: unknown, dflt: number): number {
  if (v === undefined || v === null || v === "") return dflt;
  if (v === true || v === 1 || v === "1" || v === "true") return 1;
  if (v === false || v === 0 || v === "0" || v === "false") return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

// ── Schedule value coercion ────────────────────────────────────────
// agent_scheduler.schedule_value is INTEGER seconds. The UI sends
// durations like "15m", "1h", "90" or cron strings; cron strings cannot
// be expressed as seconds so they fall back to the default (the runner
// only re-fires interval entries anyway). Durations ARE parseable, so
// "15m" stores 900 instead of silently collapsing to 3600.
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
  cron_expr?: string; event_criteria?: string | object | null;
  project_dir?: string; task_slug?: string | null;
  enabled?: number | boolean | string;
}): Promise<AgentSchedulerRow> {
  const now = new Date().toISOString();
  const row = await qOne(`
    INSERT INTO agent_scheduler (role, model_id, harness, agent_config, schedule_type, schedule_value, cron_expr, event_criteria, project_dir, task_slug, enabled, metadata, created_at, updated_at)
    VALUES (@role, @model_id, @harness, @agent_config, @schedule_type, @schedule_value, @cron_expr, @event_criteria, @project_dir, @task_slug, @enabled, '{}', @now, @now)
    RETURNING *
  `, {
    role: data.role,
    model_id: data.model_id ?? null,
    harness: data.harness ?? "opencode",
    agent_config: data.agent_config ?? "{}",
    schedule_type: data.schedule_type ?? "interval",
    schedule_value: toScheduleSeconds(data.schedule_value, 3600),
    cron_expr: data.cron_expr ?? null,
    event_criteria: data.event_criteria == null ? null
      : typeof data.event_criteria === "string" ? data.event_criteria
      : JSON.stringify(data.event_criteria),
    project_dir: data.project_dir ?? "/home/codex/dev",
    task_slug: data.task_slug ?? null,
    enabled: toEnabledInt(data.enabled, 1),
    now,
  });
  return row;
}

export async function updateSchedulerEntry(id: number, data: Partial<{
  role: string; model_id: string | null; harness: string;
  agent_config: string; schedule_type: string; schedule_value: number | string;
  cron_expr: string | null; event_criteria: string | object | null;
  project_dir: string; enabled: number | boolean | string; last_run_at: string;
  last_run_status: string; metadata: string;
}>): Promise<AgentSchedulerRow | undefined> {
  const now = new Date().toISOString();
  const sets: string[] = ["updated_at = @now"];
  const params: Record<string, any> = { id, now };
  const fields = ["role", "model_id", "harness", "agent_config", "schedule_type",
    "schedule_value", "cron_expr", "event_criteria", "project_dir", "task_slug", "enabled", "last_run_at", "last_run_status", "metadata"];
  for (const f of fields) {
    if ((data as any)[f] !== undefined) {
      sets.push(`${f} = @${f}`);
      params[f] = f === "enabled"
        ? toEnabledInt((data as any)[f], 1)
        : f === "schedule_value"
        ? toScheduleSeconds((data as any)[f], 3600)
        : f === "event_criteria" && (data as any)[f] != null
        ? typeof (data as any)[f] === "string"
          ? (data as any)[f]
          : JSON.stringify((data as any)[f])
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

async function resolveSchedulerPrompt(
  entry: AgentSchedulerRow
): Promise<Pick<DueSchedulerEntry, "base_prompt_body" | "task_prompt_body" | "assembled_prompt">> {
  // Default system prompt for the role: latest `opencode-persona` template.
  const persona = await getPromptByRoleSlug(entry.role, "opencode-persona");
  const base = persona?.body_md ?? null;

  // Attached task (if any): resolve its bound template (latest version of
  // that (role, slug)) and append its body to the base persona.
  let taskBody: string | null = null;
  if (entry.task_slug) {
    const task = await getTackleTask(entry.task_slug);
    if (task?.prompt_role && task?.prompt_slug) {
      const p = await getPromptByRoleSlug(task.prompt_role, task.prompt_slug);
      taskBody = p?.body_md ?? null;
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
  // NOTE: the canonical evaluator is the Python runner
  // (python/tackle/agent_scheduler_runner.py evaluate_tick — T15). This
  // endpoint is a UI convenience mirror for the interval-based legacy
  // evaluation; cron/event entries are matched + stamped by the runner only.
  const rows = await qAll(`
    SELECT * FROM agent_scheduler
    WHERE enabled = 1
      AND schedule_type <> 'manual'
      AND (
        last_run_at IS NULL
        OR (
          schedule_type = 'interval'
          AND EXTRACT(EPOCH FROM NOW() - last_run_at) >= schedule_value
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

// ── System Logs ──────────────────────────────────────────────────

export interface SystemLogRow {
  id: string;
  timestamp: string;
  level: string;
  category: string;
  message: string;
  source?: string;
  details?: any;
}

export async function insertLog(params: {
  level: string;
  category: string;
  message: string;
  source?: string;
  details?: any;
}): Promise<void> {
  await qRun(
    `INSERT INTO system_logs (level, category, message, source, details)
     VALUES (@level, @category, @message, @source, @details)`,
    params
  );
}

export async function queryLogs(params: {
  level?: string;
  category?: string;
  search?: string;
  since?: string;
  limit?: number;
}): Promise<{ total: number; filtered_count: number; logs: SystemLogRow[] }> {
  const conditions: string[] = [];
  const vals: Record<string, any> = {};

  if (params.level && params.level !== 'ALL') {
    const levels = params.level.toUpperCase().split(',');
    conditions.push(`level = ANY(ARRAY[${levels.map((_, i) => `@level${i}`).join(', ')}])`);
    levels.forEach((l, i) => { vals[`level${i}`] = l; });
  }
  if (params.category && params.category !== 'ALL') {
    const cats = params.category.toUpperCase().split(',');
    conditions.push(`category = ANY(ARRAY[${cats.map((_, i) => `@cat${i}`).join(', ')}])`);
    cats.forEach((c, i) => { vals[`cat${i}`] = c; });
  }
  if (params.search) {
    vals.search = `%${params.search.toLowerCase()}%`;
    conditions.push(`(LOWER(message) LIKE @search OR LOWER(category) LIKE @search OR LOWER(COALESCE(source,'')) LIKE @search)`);
  }
  if (params.since) {
    vals.since = params.since;
    conditions.push(`timestamp > @since`);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
  const limitNum = Math.min(Math.max(1, params.limit || 100), 500);

  const total = await qOne(`SELECT COUNT(*)::int AS count FROM system_logs`);
  const filtered = await qOne(
    `SELECT COUNT(*)::int AS count FROM system_logs ${where}`,
    vals
  );
  const logs = await qAll(
    `SELECT * FROM system_logs ${where} ORDER BY timestamp DESC LIMIT @limit`,
    { ...vals, limit: limitNum }
  );

  return {
    total: total?.count || 0,
    filtered_count: filtered?.count || 0,
    logs,
  };
}

export async function clearLogs(): Promise<void> {
  // V157 (+V159): audit rows (REGISTRY_AUDIT / NEBULA_AUDIT / KG_AUDIT — the V155/V156/V159
  // statement-level audit trail) are erase-guarded at the DB level and
  // deliberately EXCLUDED here: routine log clearing must never wipe the
  // audit trail. Removing audit rows requires direct DB access inside a
  // transaction that sets tackle.allow_audit_erase = 'on' (the V157
  // escape hatch), so erasure is always a conscious, all-or-nothing act.
  await qRun(
    `DELETE FROM system_logs WHERE category NOT IN ('REGISTRY_AUDIT','NEBULA_AUDIT','KG_AUDIT')`
  );
}

// ── Projection Configs (ACP v1, plan 1280) ────────────────────────

export interface ProjectionConfig {
  id: string;
  name: string;
  description: string;
  type: string;
  source_query: string;
  template: string;
  parameter_schema: any;
  target_path: string;
  schedule: string;
  enabled: number;
  last_rendered_at: string | null;
  last_sha256: string | null;
  created_at: string;
  updated_at: string;
}

export async function listProjections(): Promise<ProjectionConfig[]> {
  return qAll(`SELECT * FROM tackle.projection_configs ORDER BY name`);
}

export async function getProjection(id: string): Promise<ProjectionConfig | undefined> {
  return qOne(`SELECT * FROM tackle.projection_configs WHERE id = @id`, { id });
}

export async function createProjection(params: {
  name: string;
  description: string;
  type: string;
  source_query: string;
  template: string;
  parameter_schema: any;
  target_path: string;
  schedule: string;
  enabled: number;
}): Promise<ProjectionConfig> {
  const row = await qOne(
    `INSERT INTO tackle.projection_configs (name, description, type, source_query, template, parameter_schema, target_path, schedule, enabled)
     VALUES (@name, @description, @type, @source_query, @template, @parameter_schema, @target_path, @schedule, @enabled)
     RETURNING *`,
    params
  );
  return row;
}

export async function updateProjection(id: string, updates: Record<string, any>): Promise<ProjectionConfig | undefined> {
  const setters: string[] = [];
  const vals: Record<string, any> = { id };
  for (const [k, v] of Object.entries(updates)) {
    setters.push(`${k} = @${k}`);
    vals[k] = v;
  }
  setters.push("updated_at = NOW()");
  return qOne(
    `UPDATE tackle.projection_configs SET ${setters.join(", ")} WHERE id = @id RETURNING *`,
    vals
  );
}

/** Delete a projection config by id. Returns true if a row was removed. */
export async function deleteProjection(id: string): Promise<boolean> {
  const changes = await qRun(
    `DELETE FROM tackle.projection_configs WHERE id = @id`,
    { id }
  );
  return changes > 0;
}

/** Get the latest opencode-persona body for a role from tackle.prompts. */
export async function getPersonaForRole(role: string): Promise<string | null> {
  const row = await qOne(
    `SELECT body_md FROM tackle.prompts
     WHERE role = @role AND slug = 'opencode-persona'
     ORDER BY version DESC LIMIT 1`,
    { role }
  );
  return row?.body_md || null;
}

/** Get procedure card summaries for a role from tackle.role_memory → tackle.memory. */
export async function getProceduresForRole(role: string): Promise<string | null> {
  const rows = await qAll(
    `SELECT m.title, m.summary, m.slug
     FROM tackle.role_memory rm
     JOIN tackle.memory m ON m.id = rm.memory_id
     WHERE rm.role = @role
       AND (rm.expiration_dt IS NULL OR rm.expiration_dt > NOW())
     ORDER BY m.slug`,
    { role }
  );
  if (!rows.length) return null;
  return rows.map((r: any) => `- **${r.title}** (\`${r.slug}\`): ${r.summary}`).join("\n");
}
