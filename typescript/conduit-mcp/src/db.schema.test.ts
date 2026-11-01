/**
 * Integration test for createSchema() on a fresh PostgreSQL database.
 *
 * Creates a unique temporary schema, runs initDb() (which calls createSchema()),
 * verifies all tables and columns are created correctly, then drops the schema.
 *
 * This guards against ordering regressions where ALTER TABLE entries
 * referenced a table before its CREATE TABLE ran (the historical
 * role_models migration bug), and against migration DDL depending on
 * tables that have since been removed (role_config/role_models were
 * replaced by config_bundle and dropped 2026-08-07).
 *
 * Prerequisites:
 *   - PostgreSQL on localhost:5432 (or CONDUIT_PG_DSN env var)
 *   - pguser/pgpass credentials
 */

import { describe, test, expect, beforeAll, afterAll } from "vitest";
import { Pool } from "pg";
import {
  initDb,
  insertCompileVerdict,
  getNewestCompileVerdict,
  getNewestCompileVerdictForPlan,
  resolveEntityKeysForPlan,
  createWorkRequest,
  getCompileVerdictStats,
  runCompileGate,
  verdictBlocksBootstrap,
} from "./db";

const DSN = process.env.CONDUIT_PG_DSN || "postgresql://pguser:pgpass@localhost:5432/nexus";

let adminPool: Pool;

beforeAll(async () => {
  adminPool = new Pool({ connectionString: DSN });

  // Ensure a clean slate: drop any leftover from a previous failed run
  const schema = process.env.CONDUIT_PG_SCHEMA!;
  await adminPool.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
});

afterAll(async () => {
  // Clean up test schema
  const schema = process.env.CONDUIT_PG_SCHEMA!;
  try {
    await adminPool.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
  } catch (e: any) {
    console.warn(`Could not drop test schema "${schema}":`, e.message);
  }
  await adminPool.end();
});

describe("createSchema on fresh database", () => {
  test("initDb creates all tables and views without throwing", async () => {
    const testSchema = process.env.CONDUIT_PG_SCHEMA!;
    expect(testSchema).toMatch(/^test_conduit_/);

    // This calls createSchema() internally — should not throw
    const pool = await initDb();
    expect(pool).toBeDefined();

    try {
      // ── CONDUIT SCHEMA: verify all core tables exist ──
      const tablesResult = await pool.query(
        `SELECT table_name FROM information_schema.tables
         WHERE table_schema = $1 ORDER BY table_name`,
        [testSchema]
      );
      const tables = tablesResult.rows.map((r: any) => r.table_name);

      // conduit.plans removed 2026-08-07 (empty legacy table; runtime reads
      // nebula.plans, the legacy-compat view over nebula.implementation_plans_history)
      expect(tables).toContain("sessions");
      expect(tables).toContain("circuit_breaker");

      // tickets & receipts live in the shared vision schema — createSchema
      // creates them qualified as ${VISION_SCHEMA}.tickets / .receipts
      const visionTablesResult = await pool.query(
        `SELECT table_name FROM information_schema.tables
         WHERE table_schema = 'vision' ORDER BY table_name`
      );
      const visionTables = visionTablesResult.rows.map((r: any) => r.table_name);
      expect(visionTables).toContain("tickets");
      expect(visionTables).toContain("receipts");

      // ── TACKLE SCHEMA: verify all AI config tables exist ──
      const vectorTablesResult = await pool.query(
        `SELECT table_name FROM information_schema.tables
         WHERE table_schema = 'tackle' ORDER BY table_name`
      );
      const vectorTables = vectorTablesResult.rows.map((r: any) => r.table_name);

      expect(vectorTables).toContain("providers");
      expect(vectorTables).toContain("harnesses");
      expect(vectorTables).toContain("models");
      expect(vectorTables).toContain("config_bundle");

      // ── CONFIG_BUNDLE: verify provider_id / harness_id columns ──
      // Replaces role_config + role_models tables.
      const cbColsResult = await pool.query(
        `SELECT column_name, data_type, is_nullable
         FROM information_schema.columns
         WHERE table_schema = 'tackle' AND table_name = 'config_bundle'
         ORDER BY ordinal_position`
      );
      const cbCols = cbColsResult.rows.map((r: any) => r.column_name);

      expect(cbCols).toContain("provider_id");
      expect(cbCols).toContain("harness_id");

      // Confirm they're nullable TEXT (REFERENCES columns are nullable)
      const providerCol = cbColsResult.rows.find(
        (r: any) => r.column_name === "provider_id"
      );
      const harnessCol = cbColsResult.rows.find(
        (r: any) => r.column_name === "harness_id"
      );
      expect(providerCol?.data_type).toBe("text");
      expect(providerCol?.is_nullable).toBe("YES");
      expect(harnessCol?.data_type).toBe("text");
      expect(harnessCol?.is_nullable).toBe("YES");

      // ── VIEWS: plan_status and plans_by_status ──
      // These views are owned by nebula-srv (migration 040) and live in the
      // nebula schema — conduit-mcp no longer creates them (createSchema
      // drops any leftover test-schema views). Runtime reads use
      // nebula.plan_status / nebula.plans_by_status explicitly.
      const viewsResult = await pool.query(
        `SELECT table_name FROM information_schema.views
         WHERE table_schema = 'nebula' ORDER BY table_name`
      );
      const views = viewsResult.rows.map((r: any) => r.table_name);

      expect(views).toContain("plan_status");
      expect(views).toContain("plans_by_status");

      // ── PLANS: no longer created on fresh schemas (removed 2026-08-07) ──
      // Runtime reads nebula.plans; legacy-DB preservation is covered by the
      // "legacy schema without schema_version" test below.

      // ── CIRCUIT_BREAKER: verify all migration-added columns ──
      const cbBreakerColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'circuit_breaker'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      const cbBreakerCols = cbBreakerColsResult.rows.map((r: any) => r.column_name);
      expect(cbBreakerCols).toContain("paused");
      expect(cbBreakerCols).toContain("max_retries_per_model");
      expect(cbBreakerCols).toContain("retry_delay_seconds");
      expect(cbBreakerCols).toContain("max_fallbacks");
      expect(cbBreakerCols).toContain("push_back_to_pending");

      // ── SCHEMA VERSIONING: verify migration records exist ──
      // initDb() runs both v1 (baseline, no-op) and v2 (creates index).
      // schema_version is resolved via search_path (set to testSchema).
      const svResult = await pool.query(
        `SELECT version, description FROM schema_version ORDER BY version`
      );
      const versions = svResult.rows.map((r: any) => r.version);
      expect(versions).toContain(1);
      expect(versions).toContain(2);
      expect(versions).toContain(3);
      expect(versions).toContain(4);
      expect(versions).toContain(5);
      expect(versions).toContain(6);
      expect(versions).toContain(7);
      expect(versions).toContain(8);

      // ── V6 COLUMN: verify deadline column exists in vision.tickets ──
      const ticketColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = 'vision' AND table_name = 'tickets'
         ORDER BY ordinal_position`
      );
      const ticketCols = ticketColsResult.rows.map((r: any) => r.column_name);
      expect(ticketCols).toContain("deadline");

      // ── V5 COLUMN: verify tags column exists in sessions ──
      const sessColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'sessions'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      const sessCols = sessColsResult.rows.map((r: any) => r.column_name);
      expect(sessCols).toContain("tags");

      // ── V2 INDEX: verify idx_sessions_created_at exists ──
      const indexResult = await pool.query(
        `SELECT indexname FROM pg_indexes
         WHERE schemaname = $1 AND tablename = 'sessions' AND indexname = 'idx_sessions_created_at'`,
        [testSchema]
      );
      expect(indexResult.rows.length).toBe(1);
      expect(indexResult.rows[0].indexname).toBe("idx_sessions_created_at");
    } finally {
      await pool.end();
    }
  }, 30000);

  test("runMigrations skips already-applied versions on second initDb call", async () => {
    const testSchema = process.env.CONDUIT_PG_SCHEMA!;

    // First initDb: creates schema, applies v1 and v2 migrations
    const pool1 = await initDb();
    expect(pool1).toBeDefined();

    try {
      // Verify all migrations were applied on first run (35 total as of 2026-08-07;
      // declared out of order in the migrations array, so compare sorted)
      const svAfterFirst = await pool1.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      expect(svAfterFirst.rows.map((r: any) => r.version).sort((a: number, b: number) => a - b))
        .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40]);
    } finally {
      await pool1.end();
    }

    // Second initDb on the same schema — runMigrations should skip
    // both v1 and v2 since they're already recorded in schema_version.
    const pool2 = await initDb();
    expect(pool2).toBeDefined();

    try {
      const svAfterSecond = await pool2.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      const versions = svAfterSecond.rows.map((r: any) => r.version);
      // Still exactly [1..35] — no duplicate rows from re-applying
      expect(versions.sort((a: number, b: number) => a - b))
        .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40]);

      // The v2 index should still exist (not re-created, just still there)
      const indexResult = await pool2.query(
        `SELECT indexname FROM pg_indexes
         WHERE schemaname = $1 AND tablename = 'sessions' AND indexname = 'idx_sessions_created_at'`,
        [testSchema]
      );
      expect(indexResult.rows.length).toBe(1);
    } finally {
      await pool2.end();
    }
  }, 30000);

  test("schema stripped to v5 correctly replays all pending migrations (v6-v35)", async () => {
    const testSchema = process.env.CONDUIT_PG_SCHEMA!;
    expect(testSchema).toMatch(/^test_conduit_/);

    // ── Step 1: Create the full v6 schema via initDb() ──
    const poolV6 = await initDb();
    expect(poolV6).toBeDefined();

    try {
      // Confirm we start at v6
      const svBefore = await poolV6.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      expect(svBefore.rows.map((r: any) => r.version).sort((a: number, b: number) => a - b))
        .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40]);
    } finally {
      await poolV6.end();
    }

    // ── Step 2: Strip back to v5 state ──
    // Remove migration v6's artifacts: drop the deadline column from tickets, delete v6 record.
    // v7/v8 are no-ops (role_config/role_models replaced by config_bundle, tables removed
    // 2026-08-07) — nothing to undo for them; just delete their schema_version rows.
    const adminClient = await adminPool.connect();
    try {
      // Strip the schema back to v5 state: remove every schema_version row
      // above 5. (v6's deadline column lives on the shared vision.tickets
      // table — createSchema DDL — and cannot be stripped; the migration
      // itself is replayed idempotently in Step 4.)
      // Schema-qualified: the admin pool's default search_path does not
      // include the test schema (a shared public.schema_version exists).
      await adminClient.query(`DELETE FROM ${testSchema}.schema_version WHERE version > 5`);
    } finally {
      adminClient.release();
    }

    // ── Step 3: Verify we're now at v5 state ──
    const svCheck = await adminPool.query(
      `SELECT version FROM ${testSchema}.schema_version ORDER BY version`
    );
    expect(svCheck.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5]);

    // deadline is a shared vision.tickets column (createSchema DDL) — it
    // must NOT be stripped by the v5 rollback.
    const deadlineCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'vision' AND table_name = 'tickets' AND column_name = 'deadline'
      ) AS exists`
    );
    expect(deadlineCheck.rows[0].exists).toBe(true);

    // Confirm v2, v3, v4, v5 artifacts are still present
    const tagsCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'sessions' AND column_name = 'tags'
      ) AS exists`,
      [testSchema]
    );
    expect(tagsCheck.rows[0].exists).toBe(true);

    const notesCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'sessions' AND column_name = 'notes'
      ) AS exists`,
      [testSchema]
    );
    expect(notesCheck.rows[0].exists).toBe(false);

    // NOTE: plans-table checks removed — conduit.plans is no longer created on
    // fresh schemas (2026-08-07). Legacy-DB plans preservation is covered by
    // the "legacy schema without schema_version" test below.

    const indexCheck = await adminPool.query(
      `SELECT indexname FROM pg_indexes
       WHERE schemaname = $1 AND tablename = 'sessions' AND indexname = 'idx_sessions_created_at'`,
      [testSchema]
    );
    expect(indexCheck.rows.length).toBe(1);

    // ── Step 4: Run initDb() — should apply only v6 ──
    const poolV5 = await initDb();
    expect(poolV5).toBeDefined();

    try {
      // ── Step 5: Verify only v6 was applied ──
      const svResult = await poolV5.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      // v6..v35 should all be re-applied from the v5 baseline (replay path)
      expect(svResult.rows.map((r: any) => r.version).sort((a: number, b: number) => a - b))
        .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40]);

      // deadline column exists on shared vision.tickets (createSchema DDL;
      // v6 replay is a no-op on fresh schemas)
      const ticketCols = await poolV5.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = 'vision' AND table_name = 'tickets'
         ORDER BY ordinal_position`
      );
      const ticketNames = ticketCols.rows.map((r: any) => r.column_name);
      expect(ticketNames).toContain("deadline");

      // Verify deadline column metadata (TIMESTAMPTZ)
      const deadlineMeta = await poolV5.query(
        `SELECT data_type, is_nullable FROM information_schema.columns
         WHERE table_schema = 'vision' AND table_name = 'tickets' AND column_name = 'deadline'`
      );
      expect(deadlineMeta.rows[0].data_type).toBe("timestamp with time zone");
      expect(deadlineMeta.rows[0].is_nullable).toBe("YES");

      // v2, v3, v4, v5 artifacts survive (not duplicated or lost)
      const sessCols = await poolV5.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'sessions'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      expect(sessCols.rows.map((r: any) => r.column_name)).toContain("tags");

      // NOTE: plans-table column checks removed — conduit.plans is no longer
      // created on fresh schemas (2026-08-07).

      const indexResult = await poolV5.query(
        `SELECT indexname FROM pg_indexes
         WHERE schemaname = $1 AND tablename = 'sessions' AND indexname = 'idx_sessions_created_at'`,
        [testSchema]
      );
      expect(indexResult.rows.length).toBe(1);
    } finally {
      await poolV5.end();
    }
  }, 30000);

  test("legacy schema without schema_version correctly bootstraps via initDb", async () => {
    const testSchema = process.env.CONDUIT_PG_SCHEMA!;
    expect(testSchema).toMatch(/^test_conduit_/);

    // ── Step 1: Simulate a legacy schema that predates the formal migration system ──
    // Create all core tables WITHOUT schema_version, WITHOUT notes column in plans,
    // and WITHOUT idx_sessions_created_at index.
    //
    // This replicates what the DDL in createSchema() would have produced before
    // v2 (index) and v3 (notes column) were added.
    //
    // Use a dedicated client from the pool for consistent search_path.

    const adminClient = await adminPool.connect();
    try {
      await adminClient.query(`CREATE SCHEMA IF NOT EXISTS ${testSchema}`);
      await adminClient.query(`CREATE SCHEMA IF NOT EXISTS tackle`);
      await adminClient.query(`SET search_path TO ${testSchema},tackle`);

      // Strip all migration artifacts left by tests 1 and 2 so we start from
      // a pure legacy state. Dependencies must be dropped in order:
      // views → index → schema_version. (No plans ALTERs here — test 1 no
      // longer creates conduit.plans; the legacy plans is created fresh below.)
      await adminClient.query(`DROP VIEW IF EXISTS plans_by_status`);
      await adminClient.query(`DROP VIEW IF EXISTS plan_status CASCADE`);
      await adminClient.query(`DROP INDEX IF EXISTS idx_sessions_created_at`);
      await adminClient.query(`DROP TABLE IF EXISTS schema_version`);

      // Legacy plans — no notes column, no schema_version
      await adminClient.query(`
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
          deleted       INTEGER NOT NULL DEFAULT 0,
          created_at    TEXT NOT NULL,
          updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(updated_at);
      `);

      // Legacy tickets, receipts, sessions, circuit_breaker
      await adminClient.query(`
        CREATE TABLE IF NOT EXISTS tickets (
          id                  TEXT PRIMARY KEY,
          plan_id             TEXT NOT NULL REFERENCES plans(id),
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
          parent_ticket_id    TEXT REFERENCES tickets(id),
          spawn_reason        TEXT,
          last_activity       TEXT,
          expires_at          TEXT,
          confidence          REAL,
          closure_reason      TEXT,
          replacement_of      TEXT REFERENCES tickets(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_open
          ON tickets(plan_id, role) WHERE status = 'open';

        CREATE TABLE IF NOT EXISTS receipts (
          id            TEXT PRIMARY KEY,
          plan_id       TEXT NOT NULL REFERENCES plans(id),
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
          ticket_id     TEXT REFERENCES tickets(id),
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
          created_at      TEXT NOT NULL
        );

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
      `);

      // Legacy tackle schema tables
      await adminClient.query(`
        CREATE TABLE IF NOT EXISTS tackle.providers (
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

        CREATE TABLE IF NOT EXISTS tackle.harnesses (
          id                   TEXT PRIMARY KEY,
          name                 TEXT NOT NULL,
          invocation_semantics TEXT NOT NULL DEFAULT '{}',
          created_at           TEXT NOT NULL,
          updated_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tackle.models (
          id               TEXT PRIMARY KEY,
          name             TEXT NOT NULL,
          harness_id       TEXT NOT NULL REFERENCES tackle.harnesses(id) ON DELETE CASCADE,
          provider_id      TEXT REFERENCES tackle.providers(id),
          model_identifier TEXT NOT NULL,
          created_at       TEXT NOT NULL,
          updated_at       TEXT NOT NULL
        );
      `);

      // Legacy views — drop first in case a previous test left them
      await adminClient.query(`
        DROP VIEW IF EXISTS plans_by_status;
        DROP VIEW IF EXISTS plan_status CASCADE;
        CREATE VIEW plan_status AS
        SELECT
          p.*,
          CASE
            WHEN (
              SELECT r.type FROM receipts r
              WHERE r.plan_id = p.id
              AND r.type NOT IN ('PROPOSED', 'PLANNING')
              ORDER BY r.created_at DESC LIMIT 1
            ) = 'REQUEUED' THEN 'PLAN_CREATE'
            WHEN EXISTS (
              SELECT 1 FROM receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_PASS'
              AND NOT EXISTS (
                SELECT 1 FROM receipts r2
                WHERE r2.plan_id = p.id
                AND r2.type IN ('BLOCK', 'PLAN_BLOCK', 'CANCELLED', 'ABANDONED')
                AND r2.created_at > r.created_at
              )
            ) THEN 'REVIEW_PASS'
            WHEN EXISTS (
              SELECT 1 FROM receipts r WHERE r.plan_id = p.id AND r.type = 'REVIEW_REJECT'
            ) THEN COALESCE(
              (SELECT r.type FROM receipts r
               WHERE r.plan_id = p.id
               AND r.type != 'BLOCK'
               ORDER BY r.created_at DESC LIMIT 1),
              'PLAN_CREATE'
            )
            ELSE COALESCE(
              (SELECT r.type FROM receipts r
               WHERE r.plan_id = p.id
               AND r.type NOT IN ('PROPOSED', 'PLANNING')
               ORDER BY r.created_at DESC LIMIT 1),
              (SELECT r.type FROM receipts r
               WHERE r.plan_id = p.id
               ORDER BY r.created_at DESC LIMIT 1),
              NULL
            )
          END AS derived_status
        FROM plans p
        WHERE p.deleted = 0;

        CREATE VIEW plans_by_status AS
        SELECT
          ps.derived_status AS status,
          ps.*
        FROM plan_status ps;
      `);
    } finally {
      adminClient.release();
    }

    // Double-check: schema_version must NOT exist yet
    const svCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = $1 AND table_name = 'schema_version'
      ) AS exists`,
      [testSchema]
    );
    expect(svCheck.rows[0].exists).toBe(false);

    // Double-check: notes and priority columns must NOT exist in plans yet
    const notesCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'plans' AND column_name = 'notes'
      ) AS exists`,
      [testSchema]
    );
    expect(notesCheck.rows[0].exists).toBe(false);

    const priorityCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'plans' AND column_name = 'priority'
      ) AS exists`,
      [testSchema]
    );
    expect(priorityCheck.rows[0].exists).toBe(false);

    // ── Step 2: Run initDb() which creates schema_version + applies all migrations ──
    const pool = await initDb();
    expect(pool).toBeDefined();

    try {
      // ── Step 3: Verify all migrations applied to the legacy schema ──

      // schema_version should be [1..35] — initDb bootstrapped the legacy
      // schema through the full chain (v7/v8 role_config no-ops; v24/v30
      // legacy-view guards; v17 v18-shape guard; v3/v4 plans guards).
      const svResult = await pool.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      expect(svResult.rows.map((r: any) => r.version))
        .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40]);

      // v7/v8: role_config CHECK constraint migrations are no-ops
      // (table replaced by config_bundle, tables removed 2026-08-07).
      // Verify the no-op replay does NOT recreate the removed tables.
      const rcTableCheck = await pool.query(
        `SELECT EXISTS (
           SELECT 1 FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'tackle' AND c.relname = 'role_config'
         ) AS exists`
      );
      expect(rcTableCheck.rows[0].exists).toBe(false);
      const rmTableCheck = await pool.query(
        `SELECT EXISTS (
           SELECT 1 FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'tackle' AND c.relname = 'role_models'
         ) AS exists`
      );
      expect(rmTableCheck.rows[0].exists).toBe(false);

      // v6: deadline column should exist now
      const ticketCols = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'tickets'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      expect(ticketCols.rows.map((r: any) => r.column_name)).toContain("deadline");

      // v3: notes column should exist now
      const plansCols = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'plans'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      expect(plansCols.rows.map((r: any) => r.column_name)).toContain("notes");
      expect(plansCols.rows.map((r: any) => r.column_name)).toContain("priority");

      // Verify priority column has correct type and default
      const priorityColMeta = await pool.query(
        `SELECT data_type, column_default FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'plans' AND column_name = 'priority'`,
        [testSchema]
      );
      expect(priorityColMeta.rows[0].data_type).toBe("integer");
      expect(priorityColMeta.rows[0].column_default).toBe("0");

      // v2: idx_sessions_created_at should exist now
      const indexResult = await pool.query(
        `SELECT indexname FROM pg_indexes
         WHERE schemaname = $1 AND tablename = 'sessions' AND indexname = 'idx_sessions_created_at'`,
        [testSchema]
      );
      expect(indexResult.rows.length).toBe(1);

      // Confirm all other legacy data is intact (plans table still has
      // its original columns and nothing was duplicated)
      const allTables = await pool.query(
        `SELECT table_name FROM information_schema.tables
         WHERE table_schema = $1 ORDER BY table_name`,
        [testSchema]
      );
      const tables = allTables.rows.map((r: any) => r.table_name);
      expect(tables).toContain("plans");
      expect(tables).toContain("schema_version");
      expect(tables).toContain("tickets");
      expect(tables).toContain("receipts");
      expect(tables).toContain("sessions");
      expect(tables).toContain("circuit_breaker");
    } finally {
      await pool.end();
    }
  }, 30000);

  // ── WR compile verdict store (R-A-2026-08-15-010 option c) ──────────
  // vision.wr_compile_verdicts is created by v38. Behaviour lock: insert is
  // idempotent, newest verdict wins the gate, re-parented verdicts resolve by
  // plan_id, and UPDATE/DELETE are rejected by the immutability guard.
  test("v38 wr_compile_verdicts: idempotent insert + newest-wins + re-parent + immutability", async () => {
    const pool = await initDb();
    const suite = `test:wr-verdict:${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    try {
      const ek = `${suite}:ek`;
      const planId = `test-plan-${suite}`;

      // AC2 — idempotent: same verdict twice → one row.
      const pass = {
        entity_key: ek,
        verdict_type: "WR_COMPILE_PASS" as const,
        rule_version: "1",
        description: "idempotency probe",
      };
      await insertCompileVerdict(pass);
      await insertCompileVerdict(pass);
      const cnt = await adminPool.query(
        `SELECT count(*)::int AS cnt FROM vision.wr_compile_verdicts WHERE entity_key = $1`,
        [ek],
      );
      expect(cnt.rows[0].cnt).toBe(1);

      // AC3 — newest wins: a later FAIL is returned over the earlier PASS.
      await new Promise((r) => setTimeout(r, 10));
      await insertCompileVerdict({
        entity_key: ek,
        verdict_type: "WR_COMPILE_FAIL",
        rule_version: "1",
        description: "later fail",
      });
      const newest = await getNewestCompileVerdict(ek);
      expect(newest?.verdict_type).toBe("WR_COMPILE_FAIL");
      expect(newest?.entity_key).toBe(ek);

      // AC4 — re-parented verdict resolves by plan_id.
      await insertCompileVerdict({
        entity_key: `${suite}:ek2`,
        plan_id: planId,
        verdict_type: "WR_COMPILE_PASS",
        rule_version: "1",
        description: "re-parented at release",
      });
      const byPlan = await getNewestCompileVerdictForPlan(planId);
      expect(byPlan?.plan_id).toBe(planId);
      expect(byPlan?.verdict_type).toBe("WR_COMPILE_PASS");

      // AC5 — immutability guard rejects UPDATE and DELETE.
      await expect(
        adminPool.query(
          `UPDATE vision.wr_compile_verdicts SET description = 'mutated' WHERE entity_key = $1`,
          [ek],
        ),
      ).rejects.toThrow(/immutable/);
      await expect(
        adminPool.query(
          `DELETE FROM vision.wr_compile_verdicts WHERE entity_key = $1`,
          [ek],
        ),
      ).rejects.toThrow(/immutable/);
    } finally {
      // vision.wr_compile_verdicts is shared (VISION_SCHEMA is not
      // test-isolated) and append-only, so lift the guard, remove this
      // suite's rows, and restore the guard.
      try {
        await adminPool.query(
          `DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON vision.wr_compile_verdicts`,
        );
        await adminPool.query(
          `DELETE FROM vision.wr_compile_verdicts WHERE entity_key LIKE $1`,
          [`${suite}:%`],
        );
      } catch (e: any) {
        console.warn("verdict test cleanup failed:", e?.message);
      } finally {
        await adminPool.query(
          `CREATE TRIGGER trg_wr_compile_verdicts_immutable
             BEFORE UPDATE OR DELETE ON vision.wr_compile_verdicts
             FOR EACH ROW
             EXECUTE FUNCTION vision.wr_compile_verdicts_immutable()`,
        );
      }
      await pool.end();
    }
  }, 30000);

  // v40 — finding bf7e8f49: nebula.plans.project was the literal 'wrp' (v24),
  // so getPlanById returned a placeholder that updatePlanMetadata then wrote
  // back into the canonical payload. The view must project the stored value.
  //
  // nebula.plans lives in the nebula schema (not the test schema), so this
  // check is read-only against shared state: it asserts the *definition*
  // carries no constant and the *rows* agree with blueprints_history.
  test("v40: nebula.plans.project projects the canonical payload, not 'wrp'", async () => {
    const pool = await initDb();
    try {
      const def = await pool.query(
        `SELECT pg_get_viewdef('nebula.plans'::regclass, true) AS d`
      );
      const viewdef: string = def.rows[0].d;
      expect(viewdef).not.toMatch(/'wrp'::text AS project/);
      expect(viewdef).toMatch(/metadata\s*->>\s*'project'/);

      // Every row's project must equal the payload value it stands for.
      // Absent-in-payload and NULL-in-view count as agreement.
      const drift = await pool.query(
        `SELECT count(*)::int AS n
           FROM nebula.plans p
           JOIN nebula.blueprints_history b ON b.plan_number = p.id
          WHERE p.project IS DISTINCT FROM (b.payload ->> 'project')`
      );
      expect(drift.rows[0].n).toBe(0);
    } finally {
      await pool.end();
    }
  }, 30000);

  // CP-9 delta sub-item 1 — analytics surface: pass/fail counts measurable
  // by verdict_type and rule_version (D5 downstream).
  test("v38 analytics: compile verdict counts by verdict_type/rule_version", async () => {
    const pool = await initDb();
    const suite = `test:wr-stats:${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const ekP = `${suite}:pass`;
    const ekF = `${suite}:fail`;
    try {
      const before = await getCompileVerdictStats();
      await insertCompileVerdict({
        entity_key: ekP,
        verdict_type: "WR_COMPILE_PASS",
        rule_version: "1",
        description: "stats pass",
      });
      await insertCompileVerdict({
        entity_key: ekF,
        verdict_type: "WR_COMPILE_FAIL",
        rule_version: "1",
        description: "stats fail",
      });
      const after = await getCompileVerdictStats();
      expect(after.total).toBe(before.total + 2);
      expect(after.byVerdictType.find((r) => r.verdict_type === "WR_COMPILE_PASS")).toBeDefined();
      expect(after.byVerdictType.find((r) => r.verdict_type === "WR_COMPILE_FAIL")).toBeDefined();
      const v1 = after.byRuleVersion.find((r) => r.rule_version === "1");
      expect(v1).toBeDefined();
      expect(v1!.count).toBeGreaterThanOrEqual(2);
    } finally {
      try {
        await adminPool.query(
          `DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON vision.wr_compile_verdicts`,
        );
        await adminPool.query(`DELETE FROM vision.wr_compile_verdicts WHERE entity_key IN ($1, $2)`, [ekP, ekF]);
      } catch (e: any) {
        console.warn("stats cleanup failed:", e?.message);
      } finally {
        await adminPool.query(
          `CREATE TRIGGER trg_wr_compile_verdicts_immutable
             BEFORE UPDATE OR DELETE ON vision.wr_compile_verdicts
             FOR EACH ROW
             EXECUTE FUNCTION vision.wr_compile_verdicts_immutable()`,
        );
      }
      await pool.end();
    }
  }, 30000);

  // CP-9 end-to-end core: runCompileGate fuses compare → classify → persist.
  test("v38 runCompileGate: compare → classify → persist verdict", async () => {
    const pool = await initDb();
    const suite = `test:gate:${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const ek = `${suite}:ek`;
    const ek2 = `${suite}:ek2`;
    const plan = {
      goal: "inventory hardcoded ports",
      filesAffected: [],
      acceptanceCriteria: ["inventory of ports"],
      deliverable: "docs/inventory.md",
    };
    try {
      const pass = await runCompileGate({
        wr: { ...plan },
        plan,
        assignment: { ripple: "R0", shape: "E" },
        entityKey: ek,
      });
      expect(pass.verdict).toBe("WR_COMPILE_PASS");
      expect(pass.release).toBe(true);
      const newestPass = await getNewestCompileVerdict(ek);
      expect(newestPass?.verdict_type).toBe("WR_COMPILE_PASS");
      expect(newestPass?.verdict_id).toBe(pass.verdict_id);

      const fail = await runCompileGate({
        wr: { ...plan, filesAffected: ["extra.ts"] },
        plan,
        assignment: { ripple: "R1", shape: "E" },
        entityKey: ek2,
      });
      expect(fail.verdict).toBe("WR_COMPILE_FAIL");
      expect(fail.release).toBe(false);
      expect((await getNewestCompileVerdict(ek2))?.verdict_type).toBe("WR_COMPILE_FAIL");
    } finally {
      try {
        await adminPool.query(
          `DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON vision.wr_compile_verdicts`,
        );
        await adminPool.query(`DELETE FROM vision.wr_compile_verdicts WHERE entity_key LIKE $1`, [`${suite}:%`]);
      } catch (e: any) {
        console.warn("gate cleanup failed:", e?.message);
      } finally {
        await adminPool.query(
          `CREATE TRIGGER trg_wr_compile_verdicts_immutable
             BEFORE UPDATE OR DELETE ON vision.wr_compile_verdicts
             FOR EACH ROW
             EXECUTE FUNCTION vision.wr_compile_verdicts_immutable()`,
        );
      }
      await pool.end();
    }
  }, 30000);

  // CP-9 review (a5f096e9): a PASS verdict on a reserved (R3/R4) route must
  // block the bootstrap gate — route is persisted and verdictBlocksBootstrap
  // holds it (R-A-003: reserved is never auto-armed).
  test("v39 route: PASS + reserved → bootstrap blocked; conduit PASS releases", async () => {
    const pool = await initDb();
    const suite = `test:reserved:${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const ek = `${suite}:ek`;
    const plan = {
      goal: "inventory hardcoded ports",
      filesAffected: [],
      acceptanceCriteria: ["inventory of ports"],
      deliverable: "docs/inventory.md",
    };
    try {
      // R3 → reserved route; compare passes → PASS verdict, release=false.
      const decision = await runCompileGate({
        wr: { ...plan },
        plan,
        assignment: { ripple: "R3", shape: "E" },
        entityKey: ek,
      });
      expect(decision.verdict).toBe("WR_COMPILE_PASS");
      expect(decision.classification.route).toBe("reserved");
      expect(decision.release).toBe(false);

      const newest = await getNewestCompileVerdict(ek);
      expect(newest?.verdict_type).toBe("WR_COMPILE_PASS");
      expect(newest?.route).toBe("reserved");
      expect(verdictBlocksBootstrap(newest)).toBe(true);

      // A conduit PASS (R0) must NOT block — the gate only holds reserved.
      const ek2 = `${suite}:ek2`;
      await runCompileGate({
        wr: { ...plan },
        plan,
        assignment: { ripple: "R0", shape: "E" },
        entityKey: ek2,
      });
      const conduitPass = await getNewestCompileVerdict(ek2);
      expect(conduitPass?.route).toBe("conduit");
      expect(verdictBlocksBootstrap(conduitPass)).toBe(false);

      // No verdict → legacy unchanged (not blocked).
      expect(verdictBlocksBootstrap(undefined)).toBe(false);
    } finally {
      try {
        await adminPool.query(
          `DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON vision.wr_compile_verdicts`,
        );
        await adminPool.query(`DELETE FROM vision.wr_compile_verdicts WHERE entity_key LIKE $1`, [`${suite}:%`]);
      } catch (e: any) {
        console.warn("reserved test cleanup failed:", e?.message);
      } finally {
        await adminPool.query(
          `CREATE TRIGGER trg_wr_compile_verdicts_immutable
             BEFORE UPDATE OR DELETE ON vision.wr_compile_verdicts
             FOR EACH ROW
             EXECUTE FUNCTION vision.wr_compile_verdicts_immutable()`,
        );
      }
      await pool.end();
    }
  }, 30000);

  // CP-9 back-compat: a plan with no verdict/entityKey is legacy-unchanged
  // (the gate resolves nothing → bootstrap proceeds as today).
  test("v38 back-compat: no verdict/entityKey → legacy unchanged", async () => {
    const pool = await initDb();
    const planId = `legacy-${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    try {
      expect(await resolveEntityKeysForPlan(planId)).toEqual([]);
      expect(await getNewestCompileVerdictForPlan(planId)).toBeUndefined();
    } finally {
      await pool.end();
    }
  }, 30000);

  // D5 gate entityKey linkage: a plan resolves its compile-unit entityKey
  // from the released WorkRequest (context.plan_id) so a pre-release verdict
  // keyed by entityKey (plan_id NULL) still gates the bootstrap pass.
  test("v38 gate resolves plan entityKey for the D5 verdict lookup", async () => {
    const pool = await initDb();
    const suite = `test:wr-link:${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const planId = `plan-${suite}`;
    const wrId = `wr-${suite}`;
    const ek = `ek-${suite}`;
    try {
      // The release-emission boundary (CP-9) writes the plan link into the
      // WR's context.plan_id; the WR carries its D3 entityKey from birth.
      await createWorkRequest({
        id: wrId,
        dco_json: "{}",
        context: { plan_id: planId },
        status: "VALIDATED",
        entity_key: ek,
      });
      expect(await resolveEntityKeysForPlan(planId)).toContain(ek);

      // A pre-release verdict (plan_id NULL) keyed by entityKey must gate.
      await insertCompileVerdict({
        entity_key: ek,
        verdict_type: "WR_COMPILE_FAIL",
        rule_version: "1",
        description: "pre-release fail",
      });
      const verdict = await getNewestCompileVerdictForPlan(planId);
      expect(verdict?.verdict_type).toBe("WR_COMPILE_FAIL");
      expect(verdict?.entity_key).toBe(ek);
    } finally {
      try {
        await adminPool.query(`DELETE FROM vision.work_requests WHERE wr_id = $1`, [wrId]);
        await adminPool.query(
          `DROP TRIGGER IF EXISTS trg_wr_compile_verdicts_immutable ON vision.wr_compile_verdicts`,
        );
        await adminPool.query(`DELETE FROM vision.wr_compile_verdicts WHERE entity_key = $1`, [ek]);
      } catch (e: any) {
        console.warn("linkage test cleanup failed:", e?.message);
      } finally {
        await adminPool.query(
          `CREATE TRIGGER trg_wr_compile_verdicts_immutable
             BEFORE UPDATE OR DELETE ON vision.wr_compile_verdicts
             FOR EACH ROW
             EXECUTE FUNCTION vision.wr_compile_verdicts_immutable()`,
        );
      }
      await pool.end();
    }
  }, 30000);
});
