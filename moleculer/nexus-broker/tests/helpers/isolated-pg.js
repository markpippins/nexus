/**
 * Per-run PostgreSQL database for the broker test harness.
 *
 * Why this exists
 * ---------------
 * Every PG-touching test resolved its database as `process.env.PG_DB_NAME ||
 * 'nexus'`. Two things were wrong with that:
 *
 *  1. `nexus` is the deployment database. On a developer machine the suite read
 *     (and some tests wrote) live production-shaped state; in CI it read a
 *     throwaway database that a workflow step had pre-seeded — so the suite was
 *     green only because an operator-managed step had prepared it first.
 *  2. "Pre-seeded" also meant "pre-populated with fixture rows". The hermetic
 *     job's `keychain-delta-ci-pg.sql` carried five `nebula.agent_records` rows
 *     that a test silently depended on, which is precisely how a fixture drifts
 *     away from the schema it is supposed to mirror: nothing regenerates it, and
 *     nothing fails when it is stale.
 *
 * So the harness now creates its own database, builds it from the repo-canonical
 * bootstrap (`sql/ci-bootstrap/nexus-ci-bootstrap.sql`), points the whole
 * process (and every child it spawns) at it, and drops it when the file is done.
 * No test needs pre-seeding, and no test can reach a shared database by default.
 *
 * Contract
 * --------
 *  - Name: `NEXUS_TEST_PG_DATABASE` if set (validated: it must look like a test
 *    database, and this helper DROPS it), otherwise
 *    `nexus_test_<epochms>_<pid>_<rand>`. `PG_DB_NAME` / `PGDATABASE` are
 *    deliberately NOT consulted: CI sets them to `nexus` for the kernel and
 *    execution-srv steps, and honouring them would put every test back on the
 *    shared database. Isolation is the default and cannot be lost by inheriting
 *    an env var.
 *  - Creation is idempotent, so a step that pre-provisions a database (the
 *    legacy-parity step needs the name before the test starts) and the test
 *    itself can agree on one database.
 *  - The bootstrap is applied once per database; presence of
 *    `resolution.keychain_event_outbox` is the "already provisioned" marker.
 *  - `dropTestDatabase()` ends backends and drops `WITH (FORCE)`, so repeat runs
 *    start from nothing even if a broker was still holding a connection.
 *  - Leftovers from a crashed run are swept (older than two hours) when the next
 *    database is created; nothing else is ever dropped, and the sweeper refuses
 *    any name outside the `nexus_test_` prefix.
 *  - Every connection this process and its children open carries an
 *    `application_name` of `nexus-test-<runid>-<pid>` (exported as
 *    `TEST_APPLICATION_NAME`, inherited by children as `PGAPPNAME`). A row
 *    census can only see WRITES; the name makes a harness connection visible in
 *    `pg_stat_activity` wherever it goes, so `helpers/shared-db-guard.js` can
 *    also fail a run that only READS the shared database — which is the shape
 *    the `KEYCHAIN_SOURCE_DATABASES` bug had.
 *
 * Cost: one `CREATE DATABASE`, one ~3.5 s bootstrap apply, and one drop per test
 * file. Files run in parallel processes, each with its own database, so there is
 * no cross-file state — which is the point.
 */
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { Client } = require('pg')

// The broker's own .env carries the deployment's PG credentials. Only the
// credentials are taken from here — never the database name (see contract).
require('dotenv').config({ path: path.resolve(__dirname, '..', '..', '.env') })

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..')
const BOOTSTRAP_SQL = path.join(REPO_ROOT, 'sql', 'ci-bootstrap', 'nexus-ci-bootstrap.sql')
const MAINTENANCE_DB = process.env.PG_MAINTENANCE_DB || 'postgres'
const TEST_DB_PREFIX = 'nexus_test_'
const SAFE_NAME = /^nexus_test_[a-z0-9_]+$/
const PROVISIONED_MARKER = 'resolution.keychain_event_outbox'
const STALE_AFTER_MS = 2 * 60 * 60 * 1000
// Cluster-wide lock serialising bootstrap application. The canonical dump
// creates thousands of objects, and each apply runs as one implicit transaction
// holding a lock per object — eight files applying it at once exhausts
// PostgreSQL's shared lock table ("out of shared memory / max_locks_per_transaction").
// One apply at a time is still fully parallel: only this step is serialised.
const BOOTSTRAP_LOCK_KEY = 74_601_853

const TEST_PG_DATABASE =
  (process.env.NEXUS_TEST_PG_DATABASE || '').trim() ||
  `${TEST_DB_PREFIX}${Date.now()}_${process.pid}_${crypto.randomBytes(3).toString('hex')}`

if (!SAFE_NAME.test(TEST_PG_DATABASE)) {
  throw new Error(
    `refusing test database name ${JSON.stringify(TEST_PG_DATABASE)}: this helper CREATES and DROPS ` +
      `it, so the name must match ${SAFE_NAME} (use NEXUS_TEST_PG_DATABASE to pick one explicitly)`,
  )
}
if (TEST_PG_DATABASE === 'nexus' || TEST_PG_DATABASE === 'postgres') {
  throw new Error(`refusing to use ${TEST_PG_DATABASE} as the test database — it is a deployment database`)
}

//
// Connection identity.
//
// The tag is per PROCESS (per test file) and carries the run id as a prefix, so
// one run's connections are greppable together while two files can never be
// confused for each other. That matters for more than tidiness: the guard's
// watch treats "my tag on the shared database" as a failure, and a tag shared
// across files would let one file's deliberate-leak control trip another file's
// assertion. Postgres truncates application_name at 63 bytes, so truncate here
// rather than silently lose the pid.
const TEST_RUN_ID =
  (process.env.NEXUS_TEST_RUN_ID || '').trim() ||
  (process.env.GITHUB_RUN_ID ? `gh${process.env.GITHUB_RUN_ID}` : 'local')
const TEST_APPLICATION_NAME = `nexus-test-${TEST_RUN_ID}-${process.pid}`.slice(0, 63)
const TEST_APPLICATION_PREFIX = 'nexus-test-'

// Inherited by every child (broker, execution-srv, bridge). `pg` does not read
// PGAPPNAME itself — libpq's convention is only honoured because the services
// pass `application_name: process.env.PGAPPNAME` explicitly (see PG_CONFIG in
// services/keychain-snapshot.service.ts and the two workers).
process.env.NEXUS_TEST_RUN_ID = process.env.NEXUS_TEST_RUN_ID || TEST_RUN_ID
process.env.PGAPPNAME = TEST_APPLICATION_NAME

let ensured = null
let createdByThisProcess = false

function pgBaseConfig(overrides = {}) {
  return {
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    // So a connection is attributable to this run whatever database it reaches.
    application_name: TEST_APPLICATION_NAME,
    ...(overrides || {}),
  }
}

/** Pool/client config pointed at the per-run database. */
function pgConfig(overrides = {}) {
  return pgBaseConfig({ database: TEST_PG_DATABASE, ...(overrides || {}) })
}

async function adminClient() {
  const client = new Client(pgBaseConfig({ database: MAINTENANCE_DB }))
  await client.connect()
  return client
}

function quoted(name) {
  if (!SAFE_NAME.test(name)) throw new Error(`refusing to quote table-invalid database name ${name}`)
  return `"${name}"`
}

/** Epoch ms embedded in the name, or null when the name is not ours. */
function createdEpoch(name) {
  const match = new RegExp(`^${TEST_DB_PREFIX}(\\d{13})_`).exec(name)
  return match ? Number(match[1]) : null
}

/**
 * Drop `nexus_test_*` databases left behind by crashed runs. Age comes from the
 * name (there is no portable creation-time column), and the threshold is well
 * past any plausible test run, so a concurrent harness is never swept.
 */
async function sweepStaleTestDatabases(admin, { now = Date.now(), maxAgeMs = STALE_AFTER_MS } = {}) {
  const res = await admin.query(`SELECT datname FROM pg_database WHERE datname LIKE '${TEST_DB_PREFIX}%'`)
  const swept = []
  for (const { datname } of res.rows) {
    if (datname === TEST_PG_DATABASE) continue
    if (datname === MAINTENANCE_DB) continue
    if (!SAFE_NAME.test(datname)) continue
    const epoch = createdEpoch(datname)
    if (epoch === null || now - epoch < maxAgeMs) continue
    await admin.query(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1`, [datname])
    await admin.query(`DROP DATABASE IF EXISTS ${quoted(datname)} WITH (FORCE)`)
    swept.push(datname)
  }
  return swept
}

async function isProvisioned() {
  const client = new Client(pgConfig())
  await client.connect()
  try {
    const res = await client.query('SELECT to_regclass($1) AS oid', [PROVISIONED_MARKER])
    return res.rows[0].oid !== null
  } finally {
    await client.end().catch(() => {})
  }
}

async function applyBootstrap() {
  if (!fs.existsSync(BOOTSTRAP_SQL)) {
    throw new Error(`canonical bootstrap not found at ${BOOTSTRAP_SQL}; the isolated database cannot be built`)
  }
  const sql = fs.readFileSync(BOOTSTRAP_SQL, 'utf8')
  const client = new Client(pgConfig())
  await client.connect()
  const startedAt = Date.now()
  try {
    await client.query(sql)
    const check = await client.query('SELECT to_regclass($1) AS oid', [PROVISIONED_MARKER])
    if (check.rows[0].oid === null) {
      throw new Error(`bootstrap applied but ${PROVISIONED_MARKER} is still absent`)
    }
  } finally {
    await client.end().catch(() => {})
  }
  console.log(
    `[isolated-pg] applied canonical bootstrap to ${TEST_PG_DATABASE} in ${((Date.now() - startedAt) / 1000).toFixed(1)}s`,
  )
}

/**
 * Create (or adopt) this process's database, build it from the canonical
 * bootstrap, and point `PG_DB_NAME` / `PGDATABASE` at it so children inherit it.
 * Idempotent — call from a `before` hook; safe to call twice.
 */
async function ensureTestDatabase() {
  if (ensured) return ensured
  const admin = await adminClient()
  try {
    const existing = await admin.query('SELECT 1 FROM pg_database WHERE datname = $1', [TEST_PG_DATABASE])
    if (!existing.rows.length) {
      await admin.query(`CREATE DATABASE ${quoted(TEST_PG_DATABASE)}`)
      createdByThisProcess = true
      console.log(`[isolated-pg] created ${TEST_PG_DATABASE}`)
    }
    const swept = await sweepStaleTestDatabases(admin)
    if (swept.length) console.log(`[isolated-pg] swept ${swept.length} stale test database(s): ${swept.join(', ')}`)

    // Only the bootstrap is serialised; everything else in the suite stays
    // parallel. Held on the maintenance connection so it spans databases.
    await admin.query('SELECT pg_advisory_lock($1)', [BOOTSTRAP_LOCK_KEY])
    try {
      if (!(await isProvisioned())) await applyBootstrap()
    } finally {
      await admin.query('SELECT pg_advisory_unlock($1)', [BOOTSTRAP_LOCK_KEY]).catch(() => undefined)
    }
  } finally {
    await admin.end().catch(() => {})
  }

  // Children (broker, execution-srv, python bridge) read one of these two.
  process.env.PG_DB_NAME = TEST_PG_DATABASE
  process.env.PGDATABASE = TEST_PG_DATABASE
  ensured = TEST_PG_DATABASE
  return ensured
}

/**
 * End backends and drop this process's database. Called from each file's
 * teardown; a crash leaves the database behind, and the sweeper collects it on
 * the next run.
 */
async function dropTestDatabase() {
  if (process.env.NEXUS_TEST_KEEP_PG_DATABASE === '1') {
    console.log(`[isolated-pg] keeping ${TEST_PG_DATABASE} (NEXUS_TEST_KEEP_PG_DATABASE=1)`)
    return
  }
  const admin = await adminClient()
  try {
    await admin.query(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1`, [TEST_PG_DATABASE])
    await admin.query(`DROP DATABASE IF EXISTS ${quoted(TEST_PG_DATABASE)} WITH (FORCE)`)
  } finally {
    await admin.end().catch(() => {})
  }
  ensured = null
}

module.exports = {
  TEST_PG_DATABASE,
  TEST_RUN_ID,
  TEST_APPLICATION_NAME,
  TEST_APPLICATION_PREFIX,
  BOOTSTRAP_SQL,
  TEST_DB_PREFIX,
  createdEpoch,
  pgBaseConfig,
  pgConfig,
  ensureTestDatabase,
  dropTestDatabase,
  sweepStaleTestDatabases,
  wasCreatedByThisProcess: () => createdByThisProcess,
}
