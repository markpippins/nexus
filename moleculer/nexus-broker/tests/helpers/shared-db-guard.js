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
 * Cost: nine count queries (~20 ms) before and after each broker file, plus one
 * long-lived sampler connection while the file runs (see below).
 *
 * The other half: READS
 * ---------------------
 * A row census is blind to a regression that only READS the shared database, and
 * that is exactly what hid for months: `KEYCHAIN_SOURCE_DATABASES` names the
 * databases the poller polls — every entry is opened, read every five seconds,
 * and its rows written into the keychain store. The outbox writes were the
 * symptom; the read was the bug.
 *
 * So every harness connection carries `application_name = nexus-test-<runid>-
 * <pid>` (helpers/isolated-pg.js; children inherit it as PGAPPNAME, and the
 * broker's pools pass it through). While a guarded file runs, a sampler
 * connection watches `pg_stat_activity` for exactly this file's tag on the
 * shared database. A hit fails the run and names the reader — pid, application
 * name, state and last query — whether it was reading or writing.
 *
 * Why sampling, and why it is reliable: the poller keeps a pooled client open
 * between polls (`setInterval` + `pool.query`), so a harness-tagged session on
 * the shared database is not a momentary event — it is a session that lives for
 * the length of the file. Sampling every 400 ms cannot miss it, and a leak that
 * connects and disconnects inside 400 ms would still have to have been spawned
 * by this process tree, which the census would then catch on the write side.
 * `pg_stat_statements` would add post-hoc evidence but is not installed in this
 * project's PostgreSQL instances (checked: `pg_available_extensions` lists it,
 * the view does not exist), so nothing depends on it.
 *
 * The sampler itself can die
 * --------------------------
 * A watch that loses its backend mid-run (server restart, failover, a
 * `pg_terminate_backend`, an idle timeout at a pooler) can no longer see
 * anything, and an empty offender list from a blind sampler is indistinguishable
 * from a clean run — the exact silent-pass shape this guard exists to remove.
 * So losses are recorded (`error`/`end` events and every failed sample) and
 * `stop()` THROWS when any were seen, naming the lost sampler instead of
 * returning a clean result. It is deliberately a throw rather than a flag a
 * caller has to remember to check: forgetting to check a flag is how a check
 * degrades to nothing, which is the same footgun the empty-tag and
 * missing-signature cases already had. `stop()` is the only way to end a watch,
 * so no caller can skip the verdict.
 *
 * What stays legitimate: the tag must match THIS file's tag exactly. Live
 * production traffic on a developer machine carries the service's own name
 * (`nexus-broker`, or nothing on older builds), never `nexus-test-*`, so it is
 * invisible here. A concurrent harness in another worktree has a different tag
 * for the same reason. The bridge test is bound to the external kernel's
 * database by design and does not register this guard at all.
 */
const {
  pgBaseConfig,
  TEST_APPLICATION_NAME,
  TEST_APPLICATION_PREFIX,
  TEST_RUN_ID,
} = require('./isolated-pg')
// One authority for the historical test signature: the quarantine tool owns it
// (it is what set the eighteen rows aside), and the run-scoped form the guards
// pass is that list plus this run's namespace prefix.
const { HISTORICAL_TEST_SIGNATURE } = require('../../bin/outbox-quarantine')

const SHARED_DATABASE = (process.env.NEXUS_SHARED_DB || 'nexus').trim()
const STRICT = String(process.env.NEXUS_TEST_STRICT_SHARED_DB || '') === '1'
// The guard's own connections are named with this suffix and excluded from the
// watch: a census or sampler session is itself a harness connection to the
// shared database, and reporting it would make the check fail on itself.
const GUARD_APPLICATION_SUFFIX = '-guard'
const CONNECTION_SAMPLE_MS = Number(process.env.NEXUS_TEST_CONNECTION_SAMPLE_MS || 400)
// Enough to identify a fault without growing a megabyte of duplicate errors when
// the connection is gone and every 400 ms tick reports the same thing.
const MAX_RECORDED_LOSSES = 5

/** This run's tag, plus the guard suffix, for the guard's own connections. */
function guardApplicationName(tag = TEST_APPLICATION_NAME) {
  return `${tag}${GUARD_APPLICATION_SUFFIX}`.slice(0, 63)
}

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
  const client = new Client({
    ...pgBaseConfig(),
    database: SHARED_DATABASE,
    application_name: guardApplicationName(),
    connectionTimeoutMillis: 3000,
  })
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

// ---------------------------------------------------------------------------
// Connection-level check: reads, not just writes
// ---------------------------------------------------------------------------

const CONNECTION_QUERY = `
  SELECT pid,
         application_name,
         usename,
         state,
         backend_start,
         state_change,
         wait_event_type,
         wait_event,
         left(coalesce(query, ''), 300) AS query
    FROM pg_stat_activity
   WHERE datname = $1
     AND application_name = $2
     AND pid <> pg_backend_pid()
`

/**
 * One-shot census of harness-tagged sessions connected to `database`. The caller
 * passes a tag; it is never optional at this layer because an empty tag would
 * match nothing and read as "clean" (the same footgun the outbox signature had).
 */
async function listHarnessConnectionsOnShared({ tag, database = SHARED_DATABASE } = {}) {
  if (!tag) {
    throw new Error(
      'listHarnessConnectionsOnShared: a connection tag is required — an empty tag matches nothing and ' +
        'would silently pass. Use TEST_APPLICATION_NAME from helpers/isolated-pg.js.',
    )
  }
  const { Client } = require('pg')
  const client = new Client({
    ...pgBaseConfig(),
    database,
    application_name: guardApplicationName(tag),
    connectionTimeoutMillis: 3000,
  })
  await client.connect()
  try {
    const res = await client.query(CONNECTION_QUERY, [database, tag])
    return res.rows
  } finally {
    await client.end().catch(() => undefined)
  }
}

/** Human-readable failure line: what was seen, where, and what it was doing. */
function describeHarnessConnection(row, { database = SHARED_DATABASE } = {}) {
  const doing = row.state === 'active' ? 'running' : row.state || 'unknown'
  const waited =
    row.wait_event_type && row.wait_event
      ? `, waiting on ${row.wait_event_type}/${row.wait_event}`
      : ''
  const since = row.state_change ? new Date(row.state_change).toISOString() : 'unknown'
  const query = (row.query || '').replace(/\s+/g, ' ').trim()
  return (
    `shared database guard: a connection from this test run is attached to ${database} — ` +
    `application_name=${row.application_name} pid=${row.pid} user=${row.usename} ` +
    `${doing} since ${since}${waited}` +
    (query && query !== '<IDLE>' ? `, last query: ${query}` : ', no query recorded') +
    `\n  The harness must only connect to its own ${'nexus_test_*'} database. A read counts: ` +
    '`KEYCHAIN_SOURCE_DATABASES` names the databases the poller reads, so pointing it at the deployment ' +
    'database delivers its rows into the keychain store without the harness writing a single row.'
  )
}

/**
 * Verdict for a finished watch: `null` when the sampler stayed connected and
 * answered for the whole file, otherwise the failure message.
 *
 * Pure so the decision table is testable without a database: no losses is the
 * only clean answer, because a lost sampler's empty offender list is evidence of
 * nothing except that nobody was looking.
 */
function connectionWatchFailureReason({ tag, database = SHARED_DATABASE, samplerPid = null, losses = [], offenders = [] } = {}) {
  if (!losses.length) return null
  const seen = offenders.map((row) => `  - ${describeHarnessConnection(row, { database })}`)
  return (
    `shared database guard: the connection watch LOST ITS SAMPLER CONNECTION to ${database} ` +
    `(tag ${tag}${samplerPid ? `, sampler pid ${samplerPid}` : ''}) — ` +
    `${losses.length} failed sample(s), first: ${losses[0]}\n` +
    '  It stopped looking part-way through this file, so "no harness connection" is not something ' +
    'this run can claim: a reader that appeared after the sampler died would be invisible.\n' +
    (seen.length ? `  Seen before the loss:\n${seen.join('\n')}\n` : '') +
    '  The read guard reports clean only from a sampler that answered through to the end of the file. ' +
    'Re-run; if the sampler keeps dying, something is terminating or failing over its backend.'
  )
}

/**
 * Watch the shared database for this file's tagged connections while it runs.
 *
 * Returns `{ tag, database, pid, available, reason, lost, losses, offenders,
 * stop() }`. `stop()` clears the sampler, ends the connection and resolves to the
 * offenders seen (deduplicated by pid, latest state kept). It REJECTS when the
 * sampler lost its connection at any point, because a blind watch cannot testify
 * that the run was clean (see the header). When the shared database is
 * unreachable there is nothing to watch, so `available` is false, `offenders`
 * stays empty and `stop()` resolves — an environment with no shared database
 * must not fail for the absence of one.
 */
function startHarnessConnectionWatch({
  tag = TEST_APPLICATION_NAME,
  database = SHARED_DATABASE,
  intervalMs = CONNECTION_SAMPLE_MS,
} = {}) {
  const state = {
    tag,
    database,
    available: false,
    reason: null,
    samplerPid: null,
    // pid -> row
    offenders: new Map(),
    // Why the sampler stopped being able to answer. Non-empty means this watch
    // cannot certify anything, so `stop()` throws.
    losses: [],
  }
  const { Client } = require('pg')
  const client = new Client({
    ...pgBaseConfig(),
    database,
    application_name: guardApplicationName(tag),
    connectionTimeoutMillis: 3000,
  })
  let timer = null
  let stopped = false
  let inFlight = null

  /** Record a lost sample. Beyond the first few these are the same fault. */
  const noteLoss = (message) => {
    if (state.losses.length < MAX_RECORDED_LOSSES) state.losses.push(message)
  }

  // node-postgres does not reconnect a dead Client, and it emits 'error' on the
  // client itself — with no listener that is an uncaught exception that kills the
  // test process mid-file. Listen for it so the drop becomes the named failure
  // below instead of an opaque crash, and so a dropped socket can never be read
  // as "the file ended quietly, so the sampler must have been fine".
  client.on('error', (err) => {
    if (!stopped) noteLoss(`connection error: ${err.message}`)
  })
  client.on('end', () => {
    if (!stopped) noteLoss('the server closed the sampler connection')
  })

  const sample = async () => {
    const res = await client.query(CONNECTION_QUERY, [database, tag])
    for (const row of res.rows) {
      if (String(row.application_name || '').endsWith(GUARD_APPLICATION_SUFFIX)) continue
      state.offenders.set(row.pid, row)
    }
  }

  const tick = () => {
    if (stopped || inFlight) return inFlight
    inFlight = sample()
      .catch((err) => {
        // One lost sample does not end the watch (the leak may already be in
        // `offenders`), but it does mean the watch can no longer certify the
        // run — `stop()` refuses to report clean once this list is non-empty.
        noteLoss(err.message)
      })
      .finally(() => {
        inFlight = null
      })
    return inFlight
  }

  const ready = client
    .connect()
    .then(async () => {
      state.available = true
      // The sampler's own backend id, so a control (or an operator) can kill it
      // and so the failure message names the session that died.
      const self = await client.query('SELECT pg_backend_pid()::int AS pid')
      state.samplerPid = self.rows[0].pid
      await tick() // sample once immediately: a broker that leaked at boot is already visible
      timer = setInterval(() => void tick(), intervalMs)
      if (typeof timer.unref === 'function') timer.unref()
    })
    .catch((err) => {
      state.available = false
      state.reason = `cannot watch ${database}: ${err.message}`
    })

  return {
    tag,
    database,
    /** Resolves once the sampler is connected (or has given up). */
    ready: () => ready,
    get available() {
      return state.available
    },
    get reason() {
      return state.reason
    },
    /** Backend id of the sampler session, once connected. */
    get pid() {
      return state.samplerPid
    },
    /** True once the sampler could no longer answer — the watch is blind. */
    get lost() {
      return state.losses.length > 0
    },
    get losses() {
      return [...state.losses]
    },
    get offenders() {
      return [...state.offenders.values()]
    },
    /**
     * End the watch and return the offenders seen. Rejects if the sampler lost
     * its connection: an unseeing watch must not be reported as a clean one.
     * The rejection carries `.offenders` so a caller can still report what it
     * did see before the loss.
     */
    async stop() {
      stopped = true
      await ready
      if (timer) clearInterval(timer)
      if (inFlight) await inFlight.catch(() => undefined)
      // One last look, so a leak that appeared between samples is not lost —
      // unless the sampler is already blind, where another tick would only
      // repeat the same failure.
      if (state.available && !state.losses.length) await tick()
      await client.end().catch(() => undefined)
      const offenders = [...state.offenders.values()]
      const failure = connectionWatchFailureReason({
        tag,
        database,
        samplerPid: state.samplerPid,
        losses: state.losses,
        offenders,
      })
      if (failure) {
        const err = new Error(failure)
        err.offenders = offenders
        err.watch = { tag, database, samplerPid: state.samplerPid, losses: [...state.losses] }
        throw err
      }
      return offenders
    },
  }
}

module.exports = {
  SHARED_DATABASE,
  STRICT,
  MONITORED_TABLES,
  TEST_AGENT_RECORD_IDS,
  TEST_APPLICATION_NAME,
  TEST_APPLICATION_PREFIX,
  TEST_RUN_ID,
  GUARD_APPLICATION_SUFFIX,
  guardApplicationName,
  attributablePredicate,
  snapshotSharedDatabase,
  diffSharedDatabase,
  assertSharedDatabaseUnchanged,
  listHarnessConnectionsOnShared,
  describeHarnessConnection,
  connectionWatchFailureReason,
  startHarnessConnectionWatch,
}
