/**
 * Per-test-process Mongo database isolation for the broker test harnesses.
 *
 * Why this exists
 * ---------------
 * `node --test` runs test FILES in parallel processes, and three of them
 * (broker-smoke, legacy-parity, pty-transport.e2e) each boot a real broker with
 * `moleculer-runner dist/services`. Ports were already isolated per process
 * (`helpers/ephemeral-ports.js`), but the keychain store was not:
 * `services/keychain-snapshot.service.ts` used `client.db("keychains")` — one
 * hardcoded database for every process, production and test alike.
 *
 * So several brokers shared one keychain namespace at once:
 *  - their outbox pollers drained `resolution.keychain_event_outbox` into the
 *    same `transitions` / `entries` / `ar_snapshots` collections;
 *  - each broker's boot ran `createIndex(..., {unique: true})` on that same
 *    database (a concurrent duplicate-key path);
 *  - broker-smoke's checkpoint tests snapshot `active_checkpoints`, `entries`
 *    and `record_type_state`, run the broker's delivery path, assert exactly
 *    one new checkpoint/version, then wipe and restore those collections —
 *    while another broker's poller was writing them.
 *
 * That is the whole flake: "rewind returns a committed state vector",
 * "checkpoints persist deltas" and "replaying a delivered event is idempotent"
 * passed when run alone and failed only inside the full suite, with
 * `latestSnapshot`/version assertions off by whatever the other broker did.
 * Weakening or serializing the tests would hide it; the honest fix is that each
 * broker process gets its own database, which is what this helper provides.
 *
 * How
 * ---
 * The database name is derived once per process (node --test gives each file
 * its own), handed to the spawned broker through MONGO_DB_NAME — read by
 * keychain-snapshot.service.ts — and used by the test's own Mongo client, so
 * both halves of a test see the same isolated store. `dropIsolatedDatabase`
 * removes it at the end of the file so repeat runs start clean.
 *
 * Override: set MONGO_DB_NAME yourself to pin a specific database (debugging).
 */
const crypto = require('node:crypto')

function mongoUrl() {
  return process.env.MONGO_URL || 'mongodb://localhost:27017'
}

/**
 * Isolated database for this test process. Never the production default:
 * when MONGO_DB_NAME is supplied the caller is opting into a specific store.
 */
const MONGO_DB_NAME =
  process.env.MONGO_DB_NAME ||
  `keychains_test_${process.pid}_${crypto.randomBytes(4).toString('hex')}`

/**
 * Namespace prefix this process owns in resolution.keychain_event_outbox.
 *
 * The outbox is one global queue and the poller claims with SKIP LOCKED, so
 * with several brokers up a seed row can be claimed by a broker that writes a
 * different database — the producer then waits forever for a delivery that
 * happened elsewhere. Scoping the claim to this process's prefix makes each
 * broker drain only its own test's events. The prefix is also marked reserved
 * (services/keychain-snapshot.service.ts excludes `keychain-test-` by default),
 * so a deployed broker can never claim a test event and write it into live
 * state — which is exactly what happened before this: the helium candidate
 * broker delivered two test seeds into the production keychains store.
 * Tests that INSERT outbox rows must prefix source_namespace with this.
 */
const OUTBOX_NAMESPACE_PREFIX =
  process.env.KEYCHAIN_OUTBOX_NAMESPACE_PREFIX ||
  `keychain-test-${process.pid}-${crypto.randomBytes(3).toString('hex')}-`

/**
 * Outbox table this test process uses INSTEAD of production's.
 *
 * The namespace prefix above only decides who CLAIMS a row; the rows still had
 * to be written somewhere, and the suite was writing them into
 * `resolution.keychain_event_outbox` — production's queue — because that was the
 * only table the broker knew about. Eighteen such rows are still there. The
 * broker now takes `KEYCHAIN_OUTBOX_TABLE` (default unchanged for production),
 * so tests point at their own table in their own schema and production's queue
 * is never written to at all.
 *
 * `ensureOutboxTable()` creates it idempotently (schema, table, indexes and
 * CHECK constraints mirroring production) before a broker boots, so the test
 * never depends on someone having provisioned it by hand.
 */
const OUTBOX_TABLE =
  process.env.KEYCHAIN_OUTBOX_TABLE || 'resolution_test.keychain_event_outbox'

/** Split `schema.table` into its parts (validated by the caller's DDL). */
function splitOutboxTable(table = OUTBOX_TABLE) {
  const match = /^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$/.exec(String(table).trim())
  if (!match) throw new Error(`outbox table must be schema.table, got ${JSON.stringify(table)}`)
  return { schema: match[1], table: match[2] }
}

/**
 * The table production actually uses. The test table is created as a structural
 * copy of THIS table — never from a hand-written column list — so it cannot
 * drift from production as columns, defaults, constraints or indexes change.
 */
const PRODUCTION_OUTBOX_TABLE = 'resolution.keychain_event_outbox'

/**
 * Create the test outbox table if it is missing — idempotent and safe to call
 * from every broker-booting test file at once.
 *
 * Provisioned with `CREATE TABLE ... (LIKE resolution.keychain_event_outbox
 * INCLUDING ALL)`: PostgreSQL copies the column list, types, NOT NULLs, defaults
 * (including the `gen_random_uuid()` primary key), CHECK constraints, indexes
 * (the `(checkpoint_status, recorded_at)` partial pending index included) and
 * the unique `(source_namespace, source_event_id)` key. A migration that adds a
 * column to production therefore shows up in the test table on the next run
 * instead of silently diverging, and a row that would insert here would insert
 * there too — the tests keep exercising the same deliveries, only in a table
 * production never reads or writes.
 *
 * Deliberately no fallback DDL: if the production table is missing (a bare CI
 * database before `tests/fixtures/keychain-delta-ci-pg.sql` is applied), this
 * fails loudly rather than inventing a transcript that could drift.
 *
 * Concurrent CREATE ... IF NOT EXISTS from several test processes can still
 * race on PostgreSQL's catalog unique indexes (23505 / 42P06 / 42P07). Those
 * are benign here: retry a few times, then confirm the table is present.
 */
async function ensureOutboxTable(poolOrOptions = {}) {
  const { Pool } = require('pg')
  const { schema, table } = splitOutboxTable()
  const pool = poolOrOptions && typeof poolOrOptions.query === 'function'
    ? poolOrOptions
    : new Pool({
        host: process.env.PG_HOST || 'localhost',
        port: Number(process.env.PG_PORT || 5432),
        user: process.env.PG_USER || 'pguser',
        password: process.env.PG_PASSWORD || 'pgpass',
        database: process.env.PG_DB_NAME || 'nexus',
        ...(poolOrOptions || {}),
      })
  const owned = pool !== poolOrOptions
  const target = `${schema}.${table}`
  try {
    // Pinned at production for debugging: nothing to create, nothing to own.
    if (target === PRODUCTION_OUTBOX_TABLE) {
      const present = await pool.query('SELECT to_regclass($1) AS reg', [target])
      if (!present.rows[0] || !present.rows[0].reg) {
        throw new Error(`${target} does not exist; cannot run the suite against it`)
      }
      return
    }
    const template = await pool.query('SELECT to_regclass($1) AS reg', [PRODUCTION_OUTBOX_TABLE])
    if (!template.rows[0] || !template.rows[0].reg) {
      throw new Error(
        `cannot provision ${target}: template table ${PRODUCTION_OUTBOX_TABLE} does not exist ` +
        '(apply tests/fixtures/keychain-delta-ci-pg.sql on a fresh CI database)',
      )
    }
    const ddl = `
      CREATE SCHEMA IF NOT EXISTS ${schema};
      CREATE TABLE IF NOT EXISTS ${target} (LIKE ${PRODUCTION_OUTBOX_TABLE} INCLUDING ALL);
    `
    let lastError = null
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        await pool.query(ddl)
        lastError = null
        break
      } catch (err) {
        // 23505 duplicate key, 42P06 schema exists, 42P07 relation exists: a
        // concurrent creator won the race — harmless.
        if (['23505', '42P06', '42P07', '42710'].includes(err && err.code)) {
          lastError = err
          await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)))
          continue
        }
        throw err
      }
    }
    const present = await pool.query(`SELECT to_regclass($1) AS reg`, [`${schema}.${table}`])
    if (!present.rows[0] || !present.rows[0].reg) {
      throw lastError || new Error(`test outbox table ${schema}.${table} is not present after provisioning`)
    }
  } finally {
    if (owned) await pool.end().catch(() => {})
  }
}

/**
 * Env for a spawned broker: spreads overrides last so callers can still set
 * ports/NODE_ENV, but database name and outbox namespace default to this
 * process's isolated ones.
 */
function brokerEnv(overrides = {}) {
  return {
    MONGO_DB_NAME,
    KEYCHAIN_OUTBOX_NAMESPACE_PREFIX: OUTBOX_NAMESPACE_PREFIX,
    KEYCHAIN_OUTBOX_TABLE: OUTBOX_TABLE,
    ...overrides,
  }
}

/**
 * Wait until the keychain actions are actually registered, not just until the
 * gateway answers /api/health.
 *
 * Each service registers when ITS `started()` resolves (Mongo connect, index
 * creation, checkpoint reconciliation), which is independent of the API
 * gateway. Under a full concurrent suite that can lag the gateway by seconds,
 * and a call to an unregistered service comes back 503 ServiceUnavailable —
 * the failure that made the delta test's very first snapshot POST flake. Wait
 * on the endpoint the test is about to use instead of assuming gateway health
 * implies service readiness.
 */
async function waitForKeychainReady(baseUrl, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  let last = 'no attempt'
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/keychain-snapshot/status`)
      if (res.ok) {
        await res.json()
        return
      }
      last = `HTTP ${res.status}`
    } catch (err) {
      last = err && err.message ? err.message : String(err)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`keychain service not ready after ${timeoutMs}ms (last: ${last})`)
}

/** Drop this process's isolated database (call from the file's after hook). */
async function dropIsolatedDatabase(mongo) {
  if (!mongo) return
  await mongo.db(MONGO_DB_NAME).dropDatabase()
}

/**
 * Rows in production's outbox that came from a test run — the signature the
 * suite's own inserts leave behind. Production traffic (`peb`, `vision`, …)
 * never matches it, so this counter is quiet even while the live system is
 * delivering.
 */
const PRODUCTION_TEST_SIGNATURE =
  "(source_namespace ~ '^(keychain-test-|sol-e2e-|broker-smoke|keychains-)' " +
  "OR actor ~ '^broker-smoke')"

function outboxPool(overrides = {}) {
  const { Pool } = require('pg')
  return new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: process.env.PG_DB_NAME || 'nexus',
    ...(overrides || {}),
  })
}

/**
 * Cheap guard, one half: count test-signature rows in PRODUCTION's outbox
 * before the test file starts. `assertProductionOutboxUnchanged` re-counts at
 * the end and fails the run if the number moved.
 *
 * Counting the signature rather than the total is deliberate: the live
 * peb-kernel writes real `peb.admission.*` rows while the suite runs, so a bare
 * total would flap for reasons the suite cannot control. A test-signature row
 * can only come from the harness, which is exactly what must never happen.
 *
 * Cost: one aggregate query over an indexed table, per test file.
 */
async function snapshotProductionOutbox(overrides = {}) {
  const pool = outboxPool(overrides)
  try {
    const res = await pool.query(
      `SELECT count(*)::int AS n, now() AS at
         FROM ${PRODUCTION_OUTBOX_TABLE}
        WHERE ${PRODUCTION_TEST_SIGNATURE}`,
    )
    return { count: res.rows[0].n, since: res.rows[0].at }
  } finally {
    await pool.end().catch(() => {})
  }
}

/**
 * Cheap guard, second half: fail the test file (and so the run) if any
 * test-signature row appeared in production's outbox since the snapshot.
 */
async function assertProductionOutboxUnchanged(baseline, overrides = {}) {
  if (!baseline) throw new Error('production outbox guard: no baseline snapshot was taken')
  const pool = outboxPool(overrides)
  try {
    const res = await pool.query(
      `SELECT count(*)::int AS n
         FROM ${PRODUCTION_OUTBOX_TABLE}
        WHERE ${PRODUCTION_TEST_SIGNATURE}`,
    )
    const now = res.rows[0].n
    if (now !== baseline.count) {
      const offenders = await pool.query(
        `SELECT source_namespace, source_event_id, coalesce(actor, '-') AS actor, recorded_at
           FROM ${PRODUCTION_OUTBOX_TABLE}
          WHERE ${PRODUCTION_TEST_SIGNATURE}
            AND recorded_at >= $1
          ORDER BY recorded_at DESC
          LIMIT 5`,
        [baseline.since],
      )
      const detail = offenders.rows
        .map((r) => `  ${r.source_namespace} / ${r.actor} / ${r.source_event_id} @ ${r.recorded_at}`)
        .join('\n')
      throw new Error(
        `the suite wrote ${now - baseline.count} test row(s) into production's ` +
        `${PRODUCTION_OUTBOX_TABLE} (${baseline.count} -> ${now}). ` +
        `A test must use KEYCHAIN_OUTBOX_TABLE (${OUTBOX_TABLE}), never production's queue.\n` +
        detail,
      )
    }
  } finally {
    await pool.end().catch(() => {})
  }
}

module.exports = {
  MONGO_DB_NAME,
  OUTBOX_NAMESPACE_PREFIX,
  OUTBOX_TABLE,
  PRODUCTION_OUTBOX_TABLE,
  mongoUrl,
  brokerEnv,
  ensureOutboxTable,
  snapshotProductionOutbox,
  assertProductionOutboxUnchanged,
  dropIsolatedDatabase,
  waitForKeychainReady,
}
