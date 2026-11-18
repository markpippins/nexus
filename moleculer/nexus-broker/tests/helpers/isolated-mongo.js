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
 * The override is validated (`assertSafeTestEnv`): the name must look like a
 * test database, because this helper DROPS it on the way out.
 */
const crypto = require('node:crypto')

// Every PG pool in the harness points at a database that belongs to THIS run
// and was built from the canonical bootstrap (helpers/isolated-pg.js). It is
// deliberately not `process.env.PG_DB_NAME`: CI sets that to the shared
// throwaway database, and inheriting it would put tests back on shared state.
const { TEST_PG_DATABASE, ensureTestDatabase, dropTestDatabase } = require('./isolated-pg')

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
/**
 * Identity of this test process: pid, a base-36 timestamp and randomness.
 *
 * The outbox table name is derived from it, so no two runs — or two
 * `node --test` processes of one run — share a queue table. A stable name was
 * not enough: `ensureOutboxTable()` DROPs and recreates a drifted table, so one
 * process's drift recreate could drop the table another process's broker was
 * polling, and a table left behind by a killed run was silently reused by the
 * next one. Per-process tables make both impossible; the guard drops its own
 * table on the way out and `sweepStaleTestOutboxTables()` reaps ones a killed
 * run could not drop.
 */
const RUN_TAG = `${process.pid}_${Date.now().toString(36)}_${crypto.randomBytes(3).toString('hex')}`

const OUTBOX_TABLE =
  process.env.KEYCHAIN_OUTBOX_TABLE || `resolution_test.keychain_event_outbox_${RUN_TAG}`

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

/** Mongo databases that belong to a deployment, never to a test run. */
const PRODUCTION_MONGO_DATABASES = new Set(['keychains', 'admin', 'config', 'local'])

/** Schema production's outbox lives in; a test queue must never be created there. */
const PRODUCTION_OUTBOX_SCHEMA = 'resolution'

/** `because Mongo URL path` — the database a connection string selects, if any. */
function mongoDbFromUrl(url) {
  const match = /^mongodb(?:\+srv)?:\/\/[^/]+\/([^?/#]+)/.exec(String(url || ''))
  return match ? decodeURIComponent(match[1]) : null
}

/**
 * Refuse to run the suite against deployment state.
 *
 * The isolation in this file is opt-in by construction: `MONGO_DB_NAME`,
 * `KEYCHAIN_OUTBOX_TABLE` and the namespace prefix all default to per-process
 * values. The hazard that actually bit was a *human* override —
 * `MONGO_DB_NAME=keychains npm test` points every broker at the live store and
 * `dropIsolatedDatabase()` then drops production on the way out, and
 * `KEYCHAIN_OUTBOX_TABLE=resolution.keychain_event_outbox` silently switches
 * the "isolated" queue back to production's (`ensureOutboxTable()` reports
 * `skipped`, the suite reads and writes the live queue). Neither is a typo the
 * code can notice at use time; both are decidable from the environment alone,
 * so they are decided here, once, before anything connects.
 *
 * Fail-closed rules (`NEXUS_TEST_ALLOW_SHARED_STORES=1` is the deliberate
 * escape hatch, and is the only way to pin a shared store):
 *   - `NODE_ENV=production` is refused outright;
 *   - an explicit `MONGO_DB_NAME` must look like a test database (contain
 *     "test") and must not be a deployment database;
 *   - the resolved keychain database must not be a deployment database;
 *   - the outbox table must not be production's, and must not live in
 *     production's `resolution` schema.
 *
 * A `MONGO_URL` that selects a deployment database is only a warning: every
 * broker boot path in the suite overrides the database name explicitly (and
 * `dropIsolatedDatabase()` never uses the URL's), so the URL alone cannot move
 * a test's writes into production — but it is exactly how a *new* test file
 * would, so it is said out loud.
 *
 * Returns `{ mongoDb, outboxTable, problems, warnings }`; throws when any
 * problem is found, so the run fails at import time with the reason and the
 * override, not with a confusing authorization or data error later.
 */
function assertSafeTestEnv(env = process.env, overrides = {}) {
  const mongoDb = String(overrides.mongoDb ?? MONGO_DB_NAME).trim()
  const outboxTable = String(overrides.outboxTable ?? OUTBOX_TABLE).trim()
  const problems = []
  const warnings = []

  if (String(env.NEXUS_TEST_ALLOW_SHARED_STORES || '') === '1') {
    // Explicit, greppable opt-in for debugging against a shared store.
    return { mongoDb, outboxTable, problems, warnings, allowed: true }
  }

  if (String(env.NODE_ENV || '').toLowerCase() === 'production') {
    problems.push('NODE_ENV=production — a test run must never identify as production')
  }

  const requestedDb = String(env.MONGO_DB_NAME || '').trim()
  if (requestedDb) {
    if (PRODUCTION_MONGO_DATABASES.has(requestedDb.toLowerCase())) {
      problems.push(
        `MONGO_DB_NAME=${requestedDb} is a deployment database and this suite DROPS it on the way out`,
      )
    } else if (!/test/i.test(requestedDb)) {
      problems.push(
        `MONGO_DB_NAME=${requestedDb} does not look like a test database (no "test" in the name); ` +
        'this suite drops it in teardown, so pinning an arbitrary name is refused',
      )
    }
  }
  if (PRODUCTION_MONGO_DATABASES.has(mongoDb.toLowerCase())) {
    problems.push(`resolved keychain database ${mongoDb} is a deployment database`)
  }

  const urlDb = mongoDbFromUrl(env.MONGO_URL)
  if (urlDb && PRODUCTION_MONGO_DATABASES.has(urlDb.toLowerCase()) && !requestedDb) {
    warnings.push(
      `MONGO_URL selects the deployment database "${urlDb}"; every boot in this suite overrides ` +
      'MONGO_DB_NAME, but a new test file that forgets to would write live state',
    )
  }

  if (outboxTable === PRODUCTION_OUTBOX_TABLE) {
    problems.push(
      `KEYCHAIN_OUTBOX_TABLE=${outboxTable} is production's queue; the suite would claim and write ` +
      'live outbox rows',
    )
  } else {
    try {
      const { schema } = splitOutboxTable(outboxTable)
      if (schema === PRODUCTION_OUTBOX_SCHEMA) {
        problems.push(
          `KEYCHAIN_OUTBOX_TABLE=${outboxTable} lives in production's "${PRODUCTION_OUTBOX_SCHEMA}" ` +
          'schema; test queues belong in resolution_test',
        )
      }
    } catch (err) {
      problems.push(err.message)
    }
  }

  for (const warning of warnings) console.warn(`[isolated-mongo] WARNING: ${warning}`)
  if (problems.length) {
    throw new Error(
      'refusing to run: this test configuration would touch deployment state\n' +
      problems.map((p) => `  - ${p}`).join('\n') +
      '\nSet NEXUS_TEST_ALLOW_SHARED_STORES=1 only if you really mean to share a live store.',
    )
  }
  return { mongoDb, outboxTable, problems, warnings, allowed: false }
}

/**
 * Shape of an outbox table as PostgreSQL reports it: column names in order,
 * types, nullability and defaults. Enough to catch the drift that matters — a
 * migration adding/removing a column, or changing a type or default — without
 * diffing constraint and index text, which LIKE copies wholesale anyway.
 */
async function outboxShape(queryable, qualified) {
  const [schema, table] = qualified.split('.')
  const res = await queryable.query(
    `SELECT column_name,
            ordinal_position::int AS position,
            data_type,
            udt_name,
            is_nullable,
            coalesce(column_default, '') AS column_default
       FROM information_schema.columns
      WHERE table_schema = $1 AND table_name = $2
      ORDER BY ordinal_position`,
    [schema, table],
  )
  return res.rows
}

function shapesMatch(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** Human-readable first differences, so a recreate explains itself in the log. */
function describeShapeDiff(production, test) {
  const prod = new Map(production.map((c) => [c.column_name, c]))
  const tst = new Map(test.map((c) => [c.column_name, c]))
  const notes = []
  for (const [name, col] of prod) {
    const other = tst.get(name)
    if (!other) {
      notes.push(`missing column ${name} (${col.data_type})`)
      continue
    }
    if (other.data_type !== col.data_type) notes.push(`${name}: type ${other.data_type} -> ${col.data_type}`)
    if (other.is_nullable !== col.is_nullable) notes.push(`${name}: nullability ${other.is_nullable} -> ${col.is_nullable}`)
    if (other.column_default !== col.column_default) {
      notes.push(`${name}: default ${JSON.stringify(other.column_default)} -> ${JSON.stringify(col.column_default)}`)
    }
    if (other.position !== col.position) notes.push(`${name}: position ${other.position} -> ${col.position}`)
  }
  for (const name of tst.keys()) if (!prod.has(name)) notes.push(`extra column ${name}`)
  return notes
}

/**
 * Create — or, when it has drifted, recreate — the test outbox table.
 *
 * `CREATE TABLE ... (LIKE resolution.keychain_event_outbox INCLUDING ALL)`
 * copies columns, types, NOT NULLs, defaults (including the `gen_random_uuid()`
 * primary key), CHECK constraints, indexes (the `(checkpoint_status,
 * recorded_at)` partial pending index included) and the unique
 * `(source_namespace, source_event_id)` key from the live table. A row that
 * would insert here would insert there too: the tests keep exercising the same
 * deliveries, only in a table production never reads or writes.
 *
 * LIKE only applies at CREATE time, and the table survives between runs — so
 * `IF NOT EXISTS` alone would silently keep a stale copy after a production
 * migration. This compares the existing table's shape (columns, types,
 * nullability, defaults, order) against production and DROPs and recreates it
 * when they differ. The table is a test artifact; losing its rows on a schema
 * change is the intended behaviour.
 *
 * Deliberately no fallback DDL: if the production table is missing (a bare CI
 * database before `tests/fixtures/keychain-delta-ci-pg.sql` is applied), this
 * fails loudly rather than inventing a transcript that could drift.
 *
 * Compare-and-DDL runs in one transaction under a transaction-scoped advisory
 * lock, because four test files call this at boot and one may be dropping the
 * table while another is about to use it. Catalog races (23505 / 42P06 / 42P07 /
 * 42710) are retried a few times.
 *
 * Returns `{ action: 'created' | 'recreated' | 'unchanged' | 'skipped', target }`
 * (plus `diff` on recreate); callers may ignore it.
 */
async function ensureOutboxTable(poolOrOptions = {}, tableOverride = null) {
  const { Pool } = require('pg')
  // `tableOverride` lets a caller reconcile a private probe table (the
  // provisioning tests do this so they cannot race the broker files that share
  // OUTBOX_TABLE). Normal callers pass nothing.
  const { schema, table } = splitOutboxTable(tableOverride || OUTBOX_TABLE)
  const pool = poolOrOptions && typeof poolOrOptions.query === 'function'
    ? poolOrOptions
    : new Pool({
        host: process.env.PG_HOST || 'localhost',
        port: Number(process.env.PG_PORT || 5432),
        user: process.env.PG_USER || 'pguser',
        password: process.env.PG_PASSWORD || 'pgpass',
        database: TEST_PG_DATABASE,
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
      return { action: 'skipped', target }
    }
    const template = await pool.query('SELECT to_regclass($1) AS reg', [PRODUCTION_OUTBOX_TABLE])
    if (!template.rows[0] || !template.rows[0].reg) {
      throw new Error(
        `cannot provision ${target}: template table ${PRODUCTION_OUTBOX_TABLE} does not exist ` +
        '(apply tests/fixtures/keychain-delta-ci-pg.sql on a fresh CI database)',
      )
    }
    let lastError = null
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const client = await pool.connect()
      try {
        await client.query('BEGIN')
        await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', ['keychain_test_outbox_provision'])
        await client.query(`CREATE SCHEMA IF NOT EXISTS ${schema}`)
        const exists = await client.query('SELECT to_regclass($1) AS reg', [target])
        let action = 'created'
        let diff = []
        if (exists.rows[0] && exists.rows[0].reg) {
          const productionShape = await outboxShape(client, PRODUCTION_OUTBOX_TABLE)
          const testShape = await outboxShape(client, target)
          if (shapesMatch(productionShape, testShape)) {
            action = 'unchanged'
          } else {
            diff = describeShapeDiff(productionShape, testShape)
            action = 'recreated'
            // LIKE only takes effect at CREATE, so a drifted table has to go.
            await client.query(`DROP TABLE ${target}`)
          }
        }
        if (action !== 'unchanged') {
          await client.query(`CREATE TABLE ${target} (LIKE ${PRODUCTION_OUTBOX_TABLE} INCLUDING ALL)`)
        }
        await client.query('COMMIT')
        if (action === 'recreated') {
          console.log(
            `[isolated-mongo] recreated ${target} from ${PRODUCTION_OUTBOX_TABLE} ` +
            `after schema drift: ${diff.join('; ')}`,
          )
        }
        return { action, target, diff }
      } catch (err) {
        await client.query('ROLLBACK').catch(() => undefined)
        if (['23505', '42P06', '42P07', '42710'].includes(err && err.code) && attempt < 3) {
          lastError = err
          await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)))
          continue
        }
        throw err
      } finally {
        client.release()
      }
    }
    throw lastError || new Error(`could not provision ${target}`)
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

/**
 * Drop this process's isolated database (call from the file's after hook) and
 * verify it is actually gone.
 *
 * `dropDatabase()` returning without an error is not proof: a prior run left
 * three `keychains_test_*` databases behind because the client could not
 * authenticate (the PTY suite never loaded `.env`, so its URL had no
 * credentials) and every caller ignored the result. The assertion is cheap and
 * turns "the suite cleans up after itself" from a claim into a check. When the
 * client lacks `listDatabases` (code 13) verification is skipped with a
 * warning rather than failing a run that did drop the database.
 */
async function dropIsolatedDatabase(mongo) {
  if (!mongo) return
  await mongo.db(MONGO_DB_NAME).dropDatabase()
  try {
    const { databases } = await mongo.db('admin').admin().listDatabases({ nameOnly: true })
    if (databases.some((db) => db.name === MONGO_DB_NAME)) {
      throw new Error(
        `dropDatabase() returned but ${MONGO_DB_NAME} is still listed — the next run would inherit ` +
        'these checkpoints',
      )
    }
  } catch (err) {
    if (err && err.code === 13) {
      console.warn('[isolated-mongo] could not verify the drop (no listDatabases privilege)')
      return
    }
    throw err
  }
}

/**
 * Drop this process's test outbox table. Refuses production's table and
 * production's schema outright — the name is derived here, so the only way to
 * reach this function with either is a deliberate override.
 */
/** Whether this process's test outbox table currently exists. */
async function testOutboxTableExists(overrides = {}) {
  const { schema, table } = splitOutboxTable(OUTBOX_TABLE)
  const pool = outboxPool(overrides)
  try {
    const res = await pool.query('SELECT to_regclass($1) AS reg', [`${schema}.${table}`])
    return Boolean(res.rows[0] && res.rows[0].reg)
  } finally {
    await pool.end().catch(() => {})
  }
}

async function dropTestOutboxTable(overrides = {}, tableOverride = null) {
  const { schema, table } = splitOutboxTable(tableOverride || OUTBOX_TABLE)
  const target = `${schema}.${table}`
  if (target === PRODUCTION_OUTBOX_TABLE || schema === PRODUCTION_OUTBOX_SCHEMA) {
    throw new Error(`refusing to drop ${target}: that is production's outbox`)
  }
  const pool = outboxPool(overrides)
  try {
    await pool.query(`DROP TABLE IF EXISTS ${schema}.${table}`)
    return target
  } finally {
    await pool.end().catch(() => {})
  }
}

/**
 * Reap test outbox tables from runs that were killed before teardown.
 *
 * Per-process table names prevent cross-run interference, but a `kill -9` (or a
 * CI job timeout) skips the drop and the tables would accumulate. Names embed
 * the run's start time (base-36), so a table is only swept when it is older
 * than `olderThanMs` and belongs to another process. Tables from the old shared
 * naming scheme (`keychain_event_outbox`) do not match the pattern and are left
 * alone.
 */
async function sweepStaleTestOutboxTables({ olderThanMs = 6 * 60 * 60 * 1000, overrides = {} } = {}) {
  const pool = outboxPool(overrides)
  const dropped = []
  try {
    const res = await pool.query(
      `SELECT table_name
         FROM information_schema.tables
        WHERE table_schema = 'resolution_test'
          AND table_name LIKE 'keychain_event_outbox\\_%'`,
    )
    const cutoff = Date.now() - olderThanMs
    for (const row of res.rows) {
      const match = /^keychain_event_outbox_(\d+)_([0-9a-z]+)_[0-9a-f]+$/.exec(row.table_name)
      if (!match) continue
      if (Number(match[1]) === process.pid) continue
      const startedAt = parseInt(match[2], 36)
      if (!Number.isFinite(startedAt) || startedAt > cutoff) continue
      await pool.query(`DROP TABLE IF EXISTS resolution_test.${row.table_name}`)
      dropped.push(row.table_name)
    }
    return dropped
  } finally {
    await pool.end().catch(() => {})
  }
}

/** SQL string literal, quoted for embedding in a predicate. */
function sqlLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`
}

/**
 * Rows in production's outbox that came from a test run — the signature the
 * suite's own inserts leave behind. Production traffic (`peb`, `vision`, …)
 * never matches it, so this counter is quiet even while the live system is
 * delivering.
 *
 * The prefix set is history: those four namespaces are what earlier runs wrote
 * before the suite had its own table (eighteen rows are still in production
 * because of it), so they stay matched. The last clause is NOT history — it is
 * this run's own namespace prefix, taken from the environment, which is what
 * makes the guard cover a custom `KEYCHAIN_OUTBOX_NAMESPACE_PREFIX` (e.g.
 * `sol-e2e-…`) that no fixed regex knows about. A hardcoded signature only
 * ever catches leaks that look like past leaks.
 */
const PRODUCTION_TEST_SIGNATURE =
  "(source_namespace ~ '^(keychain-test-|sol-e2e-|broker-smoke|keychains-)' " +
  "OR actor ~ '^broker-smoke' " +
  `OR source_namespace LIKE ${sqlLiteral(OUTBOX_NAMESPACE_PREFIX)} || '%')`

function outboxPool(overrides = {}) {
  const { Pool } = require('pg')
  return new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: TEST_PG_DATABASE,
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

/**
 * Register the production-outbox guard for a test file — one call, both hooks.
 *
 * Four test files carried their own copy of provision → snapshot → assert in
 * their before/after hooks, so a step could be lost in an edit and the files
 * could drift apart. Here the wiring exists once:
 *
 *   1. `ensureOutboxTable()` provisions this process's own queue;
 *   2. `snapshotProductionOutbox()` records the test-signature baseline in
 *      production's outbox;
 *   3. `setup()` boots the file's broker (and any seed it needs);
 *   4. `teardown()` stops it — this runs even when a test failed;
 *   5. `assertProductionOutboxUnchanged()` fails the file if a test-signature
 *      row reached production's queue. It runs last and runs even when
 *      `teardown()` throws, so a shutdown error cannot mask a leak;
 *   6. the file's own test outbox table is dropped — unless step 5 reported a
 *      leak, in which case it is kept for post-mortem (the stale-table sweep
 *      reaps it later).
 *
 * Step 0, before provisioning, reaps stale tables from killed runs; a sweep
 * failure is a warning, never a failed run.
 *
 * The node:test hook registrars are passed in rather than registered on import
 * so that ordering is the guard's business, not the file's: the baseline is
 * taken before `setup()` boots anything, because the guard is what calls it.
 *
 * Usage (broker-booting files):
 *
 *   registerProductionOutboxGuard({
 *     before, after,
 *     async setup() { await startBroker() },
 *     async teardown() { await stopBroker(); await dropTestDatabase() },
 *   })
 */
function registerProductionOutboxGuard({ before, after, setup = null, teardown = null } = {}) {
  for (const [name, register] of [['before', before], ['after', after]]) {
    if (typeof register !== 'function') {
      throw new TypeError(
        `registerProductionOutboxGuard: ${name} must be node:test's ${name} hook registrar ` +
        '(pass `before`/`after` or `test.before`/`test.after`)',
      )
    }
  }
  let baseline = null
  before(async () => {
    // The per-run database must exist before ANY of the guard's work touches
    // PG: the sweep, the probe table clone and the production-queue baseline
    // all connect. It is created here rather than in each file's `setup()`
    // because `setup()` runs after those three, and a file that forgot the call
    // would fail with "database ... does not exist" instead of a clear message.
    await ensureTestDatabase()
    try {
      const swept = await sweepStaleTestOutboxTables()
      if (swept.length) {
        console.log(`[isolated-mongo] swept ${swept.length} stale test outbox table(s): ${swept.join(', ')}`)
      }
    } catch (err) {
      console.warn(`[isolated-mongo] stale test-outbox sweep skipped: ${err.message}`)
    }
    // Provision first: the file's broker claims from this table, and the
    // baseline must predate any test write.
    await ensureOutboxTable()
    baseline = await snapshotProductionOutbox()
    if (setup) await setup()
  })
  after(async () => {
    const failures = []
    if (teardown) {
      try {
        await teardown()
      } catch (err) {
        failures.push(err)
      }
    }
    // Always attempted, and always before the table drop: a leak into
    // production's queue must never be reported as a plain teardown failure.
    let leaked = false
    try {
      await assertProductionOutboxUnchanged(baseline)
    } catch (err) {
      failures.push(err)
      leaked = true
    }
    if (leaked) {
      // Keep the rows that would explain the leak; the sweep reaps the table.
      const present = await testOutboxTableExists().catch(() => false)
      if (present) {
        console.warn(`[isolated-mongo] keeping ${OUTBOX_TABLE} for post-mortem (a leak was reported)`)
      }
    } else {
      try {
        await dropTestOutboxTable()
      } catch (err) {
        failures.push(err)
      }
      // The run's database goes last, after the leak check has read it — and
      // not at all when a leak was reported, so the rows stay inspectable.
      try {
        await dropTestDatabase()
      } catch (err) {
        failures.push(err)
      }
    }
    if (failures.length === 1) throw failures[0]
    if (failures.length > 1) {
      throw new AggregateError(failures, 'production-outbox guard: more than one guard step failed')
    }
  })
}

// Fail closed at import time: a misconfigured environment must break the run
// before a client connects, not after something has been written.
assertSafeTestEnv()

module.exports = {
  MONGO_DB_NAME,
  OUTBOX_NAMESPACE_PREFIX,
  OUTBOX_TABLE,
  RUN_TAG,
  PRODUCTION_OUTBOX_TABLE,
  PRODUCTION_TEST_SIGNATURE,
  PRODUCTION_MONGO_DATABASES,
  PRODUCTION_OUTBOX_SCHEMA,
  assertSafeTestEnv,
  mongoDbFromUrl,
  testOutboxTableExists,
  dropTestOutboxTable,
  sweepStaleTestOutboxTables,
  mongoUrl,
  brokerEnv,
  splitOutboxTable,
  outboxShape,
  shapesMatch,
  describeShapeDiff,
  ensureOutboxTable,
  snapshotProductionOutbox,
  assertProductionOutboxUnchanged,
  registerProductionOutboxGuard,
  dropIsolatedDatabase,
  waitForKeychainReady,
}
