/**
 * Guard: the test run must not write to the shared database.
 *
 * Why this exists
 * ---------------
 * The harness used to reach `nexus` — the deployment database — through three
 * separate paths that nobody had noticed: `PG_DB_NAME || 'nexus'` as the pool
 * default, the CI workflow pre-seeding it, and `KEYCHAIN_SOURCE_DATABASES`
 * naming the databases the broker's poller READS (a database list wearing a
 * source list's name). Eighteen test-harness rows were delivered into that queue
 * and two checkpoints landed in live keychains before anyone saw it. The
 * outbox leak guard caught only the last hop, and only for one table, because it
 * counted a specific row signature.
 *
 * So: before and after every broker-booting test file, take a census of the
 * tables the harness could plausibly write, and fail the run if it moved.
 *
 * Two measurements per table
 * --------------------------
 *  - `total`: `count(*)`.
 *  - `attributable`: `count(*) FILTER (WHERE <harness marker>)` — rows the
 *    harness is known to create (test namespaces, the agent-record ids both
 *    suites insert, the `smoke`/`probe` executors, `witnessed-v3-smoke-*` keys).
 *
 * An `attributable` change always fails: only the harness can create those rows.
 *
 * A `total` change fails when `NEXUS_TEST_STRICT_SHARED_DB=1` and is a warning
 * otherwise. The reason is honest rather than lenient: on a developer machine
 * `nexus` is production and a live broker/kernel keeps writing to it while the
 * suite runs — asserting totals there would fail for traffic the suite did not
 * cause. CI sets the strict flag, where the only writer is the kernel the job
 * booted, so the broad net is asserted where it can be trusted.
 *
 * Cost: nine count queries (~20 ms) before and after each broker file.
 *
 * Limits worth stating: this catches WRITES. A regression that only READS the
 * shared database (which is what the `KEYCHAIN_SOURCE_DATABASES` bug did — the
 * poller read `nexus` every five seconds) is invisible to a row census. The
 * complementary check is a connection-level assertion (a per-run
 * `application_name` on harness connections, asserted absent from `nexus`),
 * which is a separate change.
 */
const { pgBaseConfig } = require('./isolated-pg')
// One authority for the historical test signature: the quarantine tool owns it
// (it is what set the eighteen rows aside), and the run-scoped form the guards
// pass is that list plus this run's namespace prefix.
const { HISTORICAL_TEST_SIGNATURE } = require('../../bin/outbox-quarantine')

const SHARED_DATABASE = (process.env.NEXUS_SHARED_DB || 'nexus').trim()
const STRICT = String(process.env.NEXUS_TEST_STRICT_SHARED_DB || '') === '1'

/** Agent-record ids the delta and smoke suites insert into the temporal table. */
const TEST_AGENT_RECORD_IDS = [
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  '33333333-3333-4333-8333-333333333333',
  '44444444-4444-4444-8444-444444444444',
  '55555555-5555-4555-8555-555555555555',
]

const MONITORED_TABLES = [
  'resolution.keychain_event_outbox',
  'nebula.agent_records_history',
  'resolution.execution_evidence',
  'resolution.execution_claim',
  'resolution.execution_claim_evidence',
  'resolution.execution_admission_receipt',
  'execution.requests',
  'execution.leases',
  'execution.attempts',
]

/**
 * SQL predicate selecting rows only the harness creates. Tables absent from the
 * map are measured by total only. `keychain_event_outbox` takes the caller's
 * signature so there is one authority for the four historical prefixes.
 */
function attributablePredicate(table, outboxSignature) {
  switch (table) {
    case 'resolution.keychain_event_outbox':
      // Default to the historical list rather than null: a caller that forgets
      // the run-scoped signature must not silently downgrade this table to a
      // total-only check (which a dev machine then only warns about).
      return outboxSignature || HISTORICAL_TEST_SIGNATURE
    case 'nebula.agent_records_history':
      return `id IN (${TEST_AGENT_RECORD_IDS.map((id) => `'${id}'`).join(', ')})`
    case 'resolution.execution_evidence':
      return "captured_by IN ('broker-smoke', 'smoke') OR evidence_key LIKE 'witnessed-v3-smoke:%'"
    case 'execution.requests':
      return "business_key LIKE 'witnessed-v3-smoke-%' OR business_key LIKE 'attempt-lifecycle-probe:%'"
    case 'execution.leases':
    case 'execution.attempts':
      return "executor_id IN ('smoke', 'probe')"
    default:
      return null
  }
}

/**
 * Census of the shared database. `{ available: false, reason }` when the
 * database or every monitored table is absent — an environment with no shared
 * database has nothing to protect, and must not fail here.
 */
async function snapshotSharedDatabase({ outboxSignature = null } = {}) {
  const { Client } = require('pg')
  const client = new Client({ ...pgBaseConfig(), database: SHARED_DATABASE, connectionTimeoutMillis: 3000 })
  try {
    await client.connect()
  } catch (err) {
    return { available: false, reason: `cannot connect to ${SHARED_DATABASE}: ${err.message}`, tables: [] }
  }
  try {
    const tables = []
    for (const table of MONITORED_TABLES) {
      const present = await client.query('SELECT to_regclass($1) AS oid', [table])
      if (!present.rows[0].oid) continue
      const predicate = attributablePredicate(table, outboxSignature)
      const sql = predicate
        ? `SELECT count(*)::int AS total, count(*) FILTER (WHERE ${predicate})::int AS attributable FROM ${table}`
        : `SELECT count(*)::int AS total, NULL::int AS attributable FROM ${table}`
      const res = await client.query(sql)
      tables.push({ table, total: res.rows[0].total, attributable: res.rows[0].attributable })
    }
    return { available: tables.length > 0, reason: null, database: SHARED_DATABASE, at: new Date().toISOString(), tables }
  } finally {
    await client.end().catch(() => undefined)
  }
}

/**
 * Compare two censuses. Returns `{ failures, warnings }`; the caller decides how
 * to surface them (the outbox guard aggregates them with its own checks).
 */
function diffSharedDatabase(before, after, { strict = STRICT } = {}) {
  const failures = []
  const warnings = []
  if (!before || !before.available || !after || !after.available) return { failures, warnings }

  const beforeByTable = new Map(before.tables.map((row) => [row.table, row]))
  for (const row of after.tables) {
    const base = beforeByTable.get(row.table)
    if (!base) continue

    if (base.attributable !== null && row.attributable !== null && row.attributable !== base.attributable) {
      failures.push(
        `shared database guard: ${row.table} gained ${row.attributable - base.attributable} harness row(s) ` +
          `(${base.attributable} -> ${row.attributable}) in ${SHARED_DATABASE}. Only the test harness creates ` +
          'those rows, so something in this run is writing the shared database instead of its own.',
      )
      continue
    }

    if (row.total !== base.total) {
      const message =
        `shared database guard: ${row.table} changed ${base.total} -> ${row.total} row(s) in ${SHARED_DATABASE}` +
        (strict
          ? ' (NEXUS_TEST_STRICT_SHARED_DB=1: this job has no other writer, so the change is the suite\'s)'
          : ' (no harness signature matched; counted as live traffic — set NEXUS_TEST_STRICT_SHARED_DB=1 where ' +
            'nothing else writes this database)')
      if (strict) failures.push(message)
      else warnings.push(message)
    }
  }
  return { failures, warnings }
}

/** Convenience for files that own their own hooks (no outbox guard). */
async function assertSharedDatabaseUnchanged(before, options = {}) {
  const after = await snapshotSharedDatabase(options)
  const { failures, warnings } = diffSharedDatabase(before, after, options)
  for (const warning of warnings) console.warn(`[shared-db] ${warning}`)
  if (failures.length) throw new Error(failures.join('\n\n'))
  return { warnings, tables: after.tables.length }
}

module.exports = {
  SHARED_DATABASE,
  STRICT,
  MONITORED_TABLES,
  TEST_AGENT_RECORD_IDS,
  attributablePredicate,
  snapshotSharedDatabase,
  diffSharedDatabase,
  assertSharedDatabaseUnchanged,
}
