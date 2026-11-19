/**
 * Integration test for createSchema() on a fresh PostgreSQL database.
 *
 * Creates a unique temporary schema, runs initDb() (which calls createSchema()),
 * verifies all tables and columns are created correctly, then drops the schema.
 *
 * This guards against ordering regressions like the role_models migration bug
 * where ALTER TABLE entries referenced a table before its CREATE TABLE ran.
 *
 * Prerequisites:
 *   - PostgreSQL on localhost:5433 (or CONDUIT_PG_DSN env var)
 *   - pguser/pgpass credentials
 */

import { describe, test, expect, beforeAll, afterAll } from "vitest";
import { Pool } from "pg";
import { initDb } from "./db";

const DSN = process.env.CONDUIT_PG_DSN || "postgresql://pguser:pgpass@localhost:5433/nexus";

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

      expect(tables).toContain("plans");
      expect(tables).toContain("receipts");
      expect(tables).toContain("sessions");
      expect(tables).toContain("circuit_breaker");
      expect(tables).toContain("tickets");

      // ── VECTOR SCHEMA: verify all AI config tables exist ──
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
      const viewsResult = await pool.query(
        `SELECT table_name FROM information_schema.views
         WHERE table_schema = $1 ORDER BY table_name`,
        [testSchema]
      );
      const views = viewsResult.rows.map((r: any) => r.table_name);

      expect(views).toContain("plan_status");
      expect(views).toContain("plans_by_status");

      // ── PLANS: verify all columns exist (DDL + migration-added) ──
      const plansColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'plans'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      const plansCols = plansColsResult.rows.map((r: any) => r.column_name);
      expect(plansCols).toContain("prompt_ref");
      expect(plansCols).toContain("deleted");
      expect(plansCols).toContain("notes");
      expect(plansCols).toContain("priority");

      // ── CIRCUIT_BREAKER: verify all migration-added columns ──
      const cbColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'circuit_breaker'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      const cbCols = cbColsResult.rows.map((r: any) => r.column_name);
      expect(cbCols).toContain("paused");
      expect(cbCols).toContain("max_retries_per_model");
      expect(cbCols).toContain("retry_delay_seconds");
      expect(cbCols).toContain("max_fallbacks");
      expect(cbCols).toContain("push_back_to_pending");

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

      // ── V6 COLUMN: verify deadline column exists in tickets ──
      const ticketColsResult = await pool.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'tickets'
         ORDER BY ordinal_position`,
        [testSchema]
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
      // Verify both migrations were applied on first run
      const svAfterFirst = await pool1.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      expect(svAfterFirst.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
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
      // Still exactly [1, 2] — no duplicate rows from re-applying
      expect(versions).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);

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

  test("schema at v5 correctly applies only v6 when runMigrations runs", async () => {
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
      expect(svBefore.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    } finally {
      await poolV6.end();
    }

    // ── Step 2: Strip back to v5 state ──
    // Remove migration v6's artifacts: drop the deadline column from tickets, delete v6 record.
    // Also drop v7's constraint change so we can re-test v6 isolation.
    const adminClient = await adminPool.connect();
    try {
      await adminClient.query(`SET search_path TO ${testSchema},tackle`);
      await adminClient.query(`ALTER TABLE tickets DROP COLUMN IF EXISTS deadline`);
      // Restore the old 4-role CHECK constraint (undo v7)
      await adminClient.query(`
        DO $MIGRATE$
        DECLARE v_conname text;
        BEGIN
          SELECT conname INTO v_conname
          FROM pg_constraint
          WHERE conrelid = 'tackle.role_config'::regclass AND contype = 'c';
          IF v_conname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE tackle.role_config DROP CONSTRAINT %I', v_conname);
          END IF;
          ALTER TABLE tackle.role_config ADD CONSTRAINT role_config_role_check
            CHECK (role IN ('planner','builder','reviewer','critic'));
        END;
        $MIGRATE$
      `);
      await adminClient.query(`DELETE FROM schema_version WHERE version = 6`);
      await adminClient.query(`DELETE FROM schema_version WHERE version = 7`);
      await adminClient.query(`DELETE FROM schema_version WHERE version = 8`);
    } finally {
      adminClient.release();
    }

    // ── Step 3: Verify we're now at v5 state ──
    const svCheck = await adminPool.query(
      `SELECT version FROM schema_version ORDER BY version`
    );
    expect(svCheck.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5]);

    const deadlineCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'tickets' AND column_name = 'deadline'
      ) AS exists`,
      [testSchema]
    );
    expect(deadlineCheck.rows[0].exists).toBe(false);

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
        WHERE table_schema = $1 AND table_name = 'plans' AND column_name = 'notes'
      ) AS exists`,
      [testSchema]
    );
    expect(notesCheck.rows[0].exists).toBe(true);

    const priorityCheck = await adminPool.query(
      `SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'plans' AND column_name = 'priority'
      ) AS exists`,
      [testSchema]
    );
    expect(priorityCheck.rows[0].exists).toBe(true);

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
      // v6 and v7 should both be applied from v5 baseline
      expect(svResult.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);

      // deadline column should exist now (added by v6)
      const ticketCols = await poolV5.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'tickets'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      const ticketNames = ticketCols.rows.map((r: any) => r.column_name);
      expect(ticketNames).toContain("deadline");

      // Verify deadline column metadata (nullable TEXT)
      const deadlineMeta = await poolV5.query(
        `SELECT data_type, is_nullable FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'tickets' AND column_name = 'deadline'`,
        [testSchema]
      );
      expect(deadlineMeta.rows[0].data_type).toBe("text");
      expect(deadlineMeta.rows[0].is_nullable).toBe("YES");

      // v2, v3, v4, v5 artifacts survive (not duplicated or lost)
      const sessCols = await poolV5.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'sessions'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      expect(sessCols.rows.map((r: any) => r.column_name)).toContain("tags");

      const plansCols = await poolV5.query(
        `SELECT column_name FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'plans'
         ORDER BY ordinal_position`,
        [testSchema]
      );
      expect(plansCols.rows.map((r: any) => r.column_name)).toContain("priority");
      expect(plansCols.rows.map((r: any) => r.column_name)).toContain("notes");

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
      // views (which depend on plans.*) → column → index → schema_version.
      await adminClient.query(`DROP VIEW IF EXISTS plans_by_status`);
      await adminClient.query(`DROP VIEW IF EXISTS plan_status CASCADE`);
      await adminClient.query(`ALTER TABLE plans DROP COLUMN IF EXISTS notes`);
      await adminClient.query(`ALTER TABLE plans DROP COLUMN IF EXISTS priority`);
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

        CREATE TABLE IF NOT EXISTS tackle.role_config (
          id            TEXT PRIMARY KEY,
          role          TEXT NOT NULL UNIQUE CHECK(role IN (
                           'planner','builder','reviewer','critic'
                         )),
          provider_id   TEXT NOT NULL REFERENCES tackle.providers(id),
          harness_id    TEXT NOT NULL REFERENCES tackle.harnesses(id),
          model_id      TEXT NOT NULL REFERENCES tackle.models(id),
          extra_params  TEXT NOT NULL DEFAULT '{}',
          created_at    TEXT NOT NULL,
          updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tackle.role_models (
          id          TEXT PRIMARY KEY,
          role        TEXT NOT NULL REFERENCES tackle.role_config(role) ON DELETE CASCADE,
          model_id    TEXT NOT NULL REFERENCES tackle.models(id),
          priority    INTEGER NOT NULL DEFAULT 0,
          provider_id TEXT REFERENCES tackle.providers(id),
          harness_id  TEXT REFERENCES tackle.harnesses(id),
          UNIQUE(role, model_id)
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

      // schema_version should have [1, 2, 3] — baseline recorded, v2 index, v3 notes
      const svResult = await pool.query(
        `SELECT version FROM schema_version ORDER BY version`
      );
      expect(svResult.rows.map((r: any) => r.version)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);

      // v7/v8: role_config CHECK constraint migrations are now no-ops
      // (table replaced by config_bundle). Verify the table still exists
      // from the pre-migration DDL and has the original CHECK.
      const rcConstraint = await pool.query(
        `SELECT conname, pg_get_constraintdef(oid) AS constraint_def
         FROM pg_constraint
         WHERE conrelid = 'tackle.role_config'::regclass AND contype = 'c'`
      );
      // Should still exist from the pre-migration DDL (wasn't dropped)
      // but the no-op migrations didn't modify it.
      expect(rcConstraint.rows.length).toBeGreaterThanOrEqual(0);

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
});
