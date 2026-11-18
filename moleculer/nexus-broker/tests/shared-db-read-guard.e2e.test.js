/**
 * Control for the READ half of the shared-database guard.
 *
 * The guard (`helpers/shared-db-guard.js` → `startHarnessConnectionWatch`) fails
 * a run when a connection tagged with this file's `application_name` is attached
 * to the shared database. A row census cannot see that shape of bug: the
 * regression that hid for months was the poller READING `nexus` every five
 * seconds because `KEYCHAIN_SOURCE_DATABASES` names the databases the poller
 * polls.
 *
 * So this file reproduces it. Same broker, same build, one binary difference:
 *
 *   leak  → KEYCHAIN_SOURCE_DATABASES=nexus      → the guard must fail
 *   green → KEYCHAIN_SOURCE_DATABASES=<per-run>  → the guard must pass
 *
 * Safety (this deliberately points a real broker at production): the poller
 * claims rows with `WHERE source_namespace LIKE $prefix || '%'`. The namespace
 * prefix here matches nothing in `nexus`, so the claim selects zero rows and
 * rolls back — a pure read, which is exactly what the watch is for. No row in
 * the shared database is updated by this file.
 */
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { spawn } = require('node:child_process')

const {
  TEST_PG_DATABASE,
  TEST_APPLICATION_NAME,
  ensureTestDatabase,
  dropTestDatabase,
} = require('./helpers/isolated-pg')
const {
  MONGO_DB_NAME,
  brokerEnv,
  dropIsolatedDatabase,
} = require('./helpers/isolated-mongo')
const {
  SHARED_DATABASE,
  guardApplicationName,
  startHarnessConnectionWatch,
  listHarnessConnectionsOnShared,
  describeHarnessConnection,
} = require('./helpers/shared-db-guard')
const { pgBaseConfig } = require('./helpers/isolated-pg')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')
const { createBrokerLog } = require('./helpers/broker-log')

const BROKER_DIR = path.resolve(__dirname, '..')
const brokerLog = createBrokerLog('shared-db-read-guard')
const CONTROL_NAMESPACE_PREFIX = 'shared-db-guard-control-none-'
const BOOT_TIMEOUT_MS = 60_000

let children = []

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function startBroker({ sourceDatabases }) {
  const servicePort = await getEphemeralPort(4098)
  const ptyPort = await getEphemeralPort(4099)
  const logFd = brokerLog.open()
  const child = spawn(
    process.execPath,
    ['node_modules/.bin/moleculer-runner', '--mask', '**/*.js', 'dist/services'],
    {
      cwd: BROKER_DIR,
      env: {
        ...process.env,
        ...brokerEnv(),
        SERVICE_PORT: String(servicePort),
        PTY_WS_PORT: String(ptyPort),
        NODE_ENV: 'test',
        // THE variable under test.
        KEYCHAIN_SOURCE_DATABASES: sourceDatabases,
        // Present in both databases, and the prefix matches nothing, so the
        // claim is a read that returns no rows (see the header).
        KEYCHAIN_OUTBOX_TABLE: 'resolution.keychain_event_outbox',
        KEYCHAIN_OUTBOX_NAMESPACE_PREFIX: CONTROL_NAMESPACE_PREFIX,
        KEYCHAIN_OUTBOX_POLL_MS: '250',
        // The tag the guard watches for; inherited by every pool in the child.
        PGAPPNAME: TEST_APPLICATION_NAME,
      },
      stdio: ['ignore', logFd, logFd],
    },
  )
  child.on('exit', () => {
    children = children.filter((c) => c !== child)
  })
  children.push(child)
  brokerLog.close(logFd)
  return child
}

async function stopBroker(child) {
  if (!child || child.exitCode !== null || child.signalCode) return
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve()
    }, 5000)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
    child.kill('SIGTERM')
  })
}

/** Poll until `predicate()` is true or the deadline passes. */
async function waitFor(predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs
  let last = null
  while (Date.now() < deadline) {
    last = await predicate()
    if (last) return last
    await sleep(150)
  }
  throw new Error(`${label} did not happen within ${timeoutMs}ms (last: ${JSON.stringify(last)})`)
}

before(async () => {
  await ensureTestDatabase()
})

after(async () => {
  for (const child of [...children]) await stopBroker(child)
  await dropTestDatabase().catch((err) => console.warn(`[read-guard] drop skipped: ${err.message}`))
  await dropIsolatedDatabase().catch(() => undefined)
})

test('the historical leak is caught: a poller pointed at the shared database fails the guard', async (t) => {
  // 1. Watch the shared database BEFORE the broker boots: the poller's source
  //    pool is opened in `started()`, so a late watch would miss the boot read.
  const watch = startHarnessConnectionWatch({ intervalMs: 150 })
  await watch.ready()
  if (!watch.available) {
    await watch.stop()
    t.skip(`no shared database to watch: ${watch.reason}`)
    return
  }

  const child = await startBroker({ sourceDatabases: SHARED_DATABASE })
  let offenders = []
  try {
    // 2. Reproduce the old configuration and wait for the evidence.
    await waitFor(() => watch.offenders.length > 0, BOOT_TIMEOUT_MS, 'a harness connection to the shared database')
  } catch (err) {
    await stopBroker(child)
    const seen = await watch.stop()
    throw new Error(
      `the guard did not see the leak it exists for.\n${err.message}\n` +
        `offenders: ${JSON.stringify(seen)}\n${brokerLog.describe('broker log')}`,
    )
  }
  await stopBroker(child)
  offenders = await watch.stop()

  assert.ok(offenders.length > 0, 'a tagged connection to the shared database must be reported')
  const message = describeHarnessConnection(offenders[0], { database: SHARED_DATABASE })
  // The message has to be actionable on its own: which reader, and where.
  assert.match(message, /attached to nexus/)
  assert.ok(message.includes(TEST_APPLICATION_NAME), 'the failure must name the offending application_name')
  assert.match(message, /pid=\d+/)
  assert.match(message, /KEYCHAIN_SOURCE_DATABASES/)
  console.log(`[read-guard] caught the leak as expected:\n${message.split('\n')[0]}`)

  // 3. And it must be a read: the control's namespace prefix matches nothing, so
  //    nothing here can have changed a row in the shared database.
  const after = await listHarnessConnectionsOnShared({ tag: 'never-matches-anything' })
  assert.deepEqual(after, [], 'the one-shot census of an unused tag returns nothing')
})

test('the correct configuration leaves no harness connection on the shared database', async (t) => {
  const watch = startHarnessConnectionWatch({ intervalMs: 150 })
  await watch.ready()
  if (!watch.available) {
    await watch.stop()
    t.skip(`no shared database to watch: ${watch.reason}`)
    return
  }

  const child = await startBroker({ sourceDatabases: TEST_PG_DATABASE })
  try {
    // Positive evidence that the poller really ran: this same tag is seen on the
    // per-run database. Without this, "no offender" would also be satisfied by a
    // broker that never started.
    await waitFor(
      async () => (await listHarnessConnectionsOnShared({ tag: TEST_APPLICATION_NAME, database: TEST_PG_DATABASE })).length > 0,
      BOOT_TIMEOUT_MS,
      'the poller to connect to its own database',
    )
    await sleep(1500) // several poll cycles at 250 ms
  } finally {
    await stopBroker(child)
  }

  const offenders = await watch.stop()
  assert.deepEqual(
    offenders.map((row) => describeHarnessConnection(row, { database: SHARED_DATABASE })),
    [],
    'a correctly configured run must leave no harness connection on the shared database',
  )
})

/**
 * Control for the OTHER way this check could go quiet.
 *
 * Everything above proves the watch sees a reader while it is connected. The
 * blind spot is the watch itself: `pg` does not reconnect a dead Client, so a
 * sampler whose backend is terminated keeps `offenders` empty for the rest of
 * the file — and an empty list was reported as "no harness connection to nexus".
 * That is the same silent pass as the bug this guard exists for, one layer up:
 * the run would go green while nothing was watching.
 *
 * So kill the sampler mid-run, the way a server restart, a failover or a
 * pooler's idle timeout does, and require the run to fail instead.
 */
test('a watch whose sampler dies fails the run instead of reporting clean', async (t) => {
  const watch = startHarnessConnectionWatch({ intervalMs: 100 })
  await watch.ready()
  if (!watch.available) {
    await watch.stop()
    t.skip(`no shared database to watch: ${watch.reason}`)
    return
  }
  assert.ok(watch.pid, 'the watch must expose its sampler backend id so it can be killed')
  // The state that used to be the final answer: connected, saw nothing, clean.
  assert.equal(watch.lost, false)
  assert.deepEqual(watch.offenders, [], 'nothing has connected yet')

  // Terminate the sampler from a different session.
  const { Client } = require('pg')
  const killer = new Client({
    ...pgBaseConfig(),
    database: SHARED_DATABASE,
    application_name: guardApplicationName(TEST_APPLICATION_NAME),
    connectionTimeoutMillis: 3000,
  })
  await killer.connect()
  try {
    const res = await killer.query('SELECT pg_terminate_backend($1) AS killed', [watch.pid])
    assert.equal(res.rows[0].killed, true, 'the sampler must actually die or this control proves nothing')
  } finally {
    await killer.end().catch(() => undefined)
  }

  await waitFor(() => watch.lost, 10_000, 'the watch to notice its sampler was killed')

  const err = await watch.stop().then(
    () => null,
    (e) => e,
  )
  assert.ok(err, 'stop() must reject — a sampler that stopped looking cannot certify a clean run')
  assert.match(err.message, /LOST ITS SAMPLER CONNECTION/)
  assert.ok(err.message.includes(watch.tag), 'the failure must name the tag that went blind')
  assert.match(err.message, new RegExp(`sampler pid ${watch.pid}`))
  assert.deepEqual(
    err.offenders,
    [],
    'nothing was seen before the loss — which is precisely why silence must not count as clean',
  )
  console.log(`[read-guard] a lost sampler fails the run as expected:\n  ${err.message.split('\n')[0]}`)
})
