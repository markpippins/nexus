/**
 * Outbox quarantine — dispositions and the live broadcast path.
 *
 * `resolution.keychain_event_outbox` is production's broadcast queue. The
 * harness used to write its seed rows into it, and eighteen such rows are still
 * there: all `delivered`, some of them delivered into the LIVE keychains store.
 * They are not claimable today only because `delivered` happens to be outside
 * the claim predicate — nothing distinguishes them from real traffic, and an
 * operator retry that sets one back to `pending` puts it straight back on the
 * queue.
 *
 * `bin/outbox-quarantine.js` gives them a durable, reversible disposition:
 * `not_applicable` (an existing state the claim predicate excludes) plus a note
 * in `last_error` recording when it was set aside, what state it was in, and
 * why. No column is added, so the CI fixture, the canonical bootstrap and the
 * fixture-vs-canonical check are untouched.
 *
 * What this file pins, against a private probe table:
 *   1. dry run changes nothing but reports exactly what it would touch;
 *   2. apply sets the rows aside with the evidence intact, and leaves fresh
 *      traffic alone;
 *   3. the claim predicate (the broker's own SQL) no longer reaches them,
 *      while it still reaches a fresh event;
 *   4. a second run is a no-op (idempotent), and `--restore` is lossless;
 *   5. a real broker booted on that table delivers a fresh event end to end
 *      and never touches the quarantined row (delivery_attempts stays 0).
 *
 * Point 5 is the one that matters: 1-4 prove the disposition at the SQL level,
 * 5 proves the actual poller respects it while still broadcasting.
 */
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { randomUUID } = require('node:crypto')
const path = require('node:path')
const dotenv = require('dotenv')
const { MongoClient } = require('mongodb')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')
const { createBrokerLog } = require('./helpers/broker-log')
const {
  MONGO_DB_NAME,
  mongoUrl,
  ensureOutboxTable,
  dropIsolatedDatabase,
  snapshotProductionOutbox,
  assertProductionOutboxUnchanged,
  PRODUCTION_TEST_SIGNATURE,
  waitForKeychainReady,
} = require('./helpers/isolated-mongo')
const {
  quarantineRows,
  HISTORICAL_TEST_SIGNATURE,
  QUARANTINE_STATUS,
  QUARANTINE_MARKER,
} = require('../bin/outbox-quarantine')
const { TEST_PG_DATABASE, ensureTestDatabase, dropTestDatabase, TEST_APPLICATION_NAME } = require('./helpers/isolated-pg')
const {
  snapshotSharedDatabase,
  diffSharedDatabase,
  startHarnessConnectionWatch,
  describeHarnessConnection,
} = require('./helpers/shared-db-guard')

dotenv.config({ path: path.join(__dirname, '..', '.env') })

const BROKER_DIR = path.resolve(__dirname, '..')
const PROBE_TABLE = `resolution_test.keychain_outbox_quarantine_${process.pid}`
const E2E_TABLE = `resolution_test.keychain_outbox_quarantine_e2e_${process.pid}`
const RESIDUE_NAMESPACE = `broker-smoke-seed-quarantine-${process.pid}`
const FRESH_NAMESPACE = `sol-producer-quarantine-${process.pid}-${randomUUID()}`
// Production's own claim filter: unset include-prefix, default exclusion.
const CLAIM_PARAMS = ['', 'keychain-test-']

let pool = null
let baseline = null
let sharedBaseline = null
let connectionWatch = null
let child = null
const brokerLog = createBrokerLog('outbox-quarantine')

function makePool() {
  const { Pool } = require('pg')
  return new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: TEST_PG_DATABASE,
  })
}

/**
 * The broker's claim predicate, as a SELECT: "which row would the poller take
 * next". Copied from keychain-snapshot.service.ts `claimNextOutboxRow`; if the
 * service's predicate changes, this test must change with it (that is the
 * point — the quarantine's guarantee is "outside this predicate").
 */
async function nextClaimable(table) {
  const sql = `
    WITH candidate AS (
      SELECT id, source_namespace, source_event_id
        FROM ${table}
       WHERE (checkpoint_status IN ('pending', 'failed')
          OR (checkpoint_status = 'delivering'
              AND claimed_at < now() - interval '5 minutes'))
         AND (
           CASE WHEN $1 <> '' THEN source_namespace LIKE $1 || '%'
                ELSE ($2 = '' OR source_namespace NOT LIKE $2 || '%')
           END
         )
       ORDER BY recorded_at, id
       FOR UPDATE SKIP LOCKED
       LIMIT 1
    )
    SELECT id, source_namespace, source_event_id FROM candidate`
  const res = await pool.query(sql, CLAIM_PARAMS)
  return res.rows[0] || null
}

async function insertRow(table, { namespace, eventId = randomUUID(), actor, status = 'pending', attempts = 0, recordedAt }) {
  const res = await pool.query(
    `INSERT INTO ${table}
       (source_namespace, source_event_id, event_kind, outcome, actor,
        checkpoint_status, delivery_attempts, recorded_at, read_set, payload)
     VALUES ($1, $2, 'sol.e2e.transition.committed', 'committed', $3, $4, $5, coalesce($6::timestamptz, now()), '{}', '{}')
     RETURNING id`,
    [namespace, eventId, actor, status, attempts, recordedAt || null],
  )
  return res.rows[0].id
}

async function rowState(table, id) {
  const res = await pool.query(
    `SELECT checkpoint_status, delivery_attempts, last_error FROM ${table} WHERE id = $1`,
    [id],
  )
  return res.rows[0]
}

function stopBroker() {
  return new Promise((resolve) => {
    if (!child) return resolve()
    const done = () => resolve()
    child.on('exit', done)
    child.kill('SIGTERM')
    setTimeout(() => {
      if (child && child.exitCode === null) child.kill('SIGKILL')
      resolve()
    }, 3000)
  })
}

async function startBrokerOn(table) {
  const port = await getEphemeralPort('4200')
  const ptyPort = await getEphemeralPort('3200')
  const base = `http://localhost:${port}/api`
  const logFd = brokerLog.open()
  child = spawn(
    process.execPath,
    ['node_modules/.bin/moleculer-runner', '--mask', '**/*.js', 'dist/services'],
    {
      cwd: BROKER_DIR,
      env: {
        ...process.env,
        SERVICE_PORT: port,
        PTY_WS_PORT: ptyPort,
        NODE_ENV: 'test',
        MONGO_DB_NAME,
        KEYCHAIN_OUTBOX_TABLE: table,
        // The poller reads outbox rows from the PostgreSQL databases named
        // here (not from PG_DB_NAME), so without this it polls `nexus`.
        KEYCHAIN_SOURCE_DATABASES: TEST_PG_DATABASE,
        // Name this child's PG connections after the run (helpers/isolated-pg.js);
        // a harness connection to a shared database is then attributable in
        // pg_stat_activity even when it only reads. Inherited as PGAPPNAME too.
        PGAPPNAME: TEST_APPLICATION_NAME,
        // Deliberately NO include-prefix: this broker must behave like
        // production (claim everything except the reserved test namespaces).
        // The quarantined row is excluded by its status, not by its name.
        KEYCHAIN_OUTBOX_NAMESPACE_PREFIX: '',
      },
      stdio: ['ignore', logFd, logFd],
    },
  )
  brokerLog.close(logFd)

  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/health`)
      if (res.ok) {
        await waitForKeychainReady(base)
        return base
      }
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(brokerLog.describe('broker did not become healthy within 30s', child))
}

before(async () => {
  await ensureTestDatabase()
  // This file boots a broker, so it is covered by the same "no writes to the
  // shared database" census the outbox guard applies to the other broker files.
  sharedBaseline = await snapshotSharedDatabase({ outboxSignature: PRODUCTION_TEST_SIGNATURE })
  // Read half of the same guard: this file's broker must not be attached to the
  // shared database at all, whatever it does there.
  connectionWatch = startHarnessConnectionWatch({ tag: TEST_APPLICATION_NAME })
  pool = makePool()
  baseline = await snapshotProductionOutbox()
  await ensureOutboxTable(pool, PROBE_TABLE)
  await ensureOutboxTable(pool, E2E_TABLE)
})

after(async () => {
  try {
    const mongo = new MongoClient(mongoUrl())
    try {
      await mongo.connect()
      await dropIsolatedDatabase(mongo)
    } finally {
      await mongo.close()
    }
  } finally {
    if (pool) {
      await pool.query(`DROP TABLE IF EXISTS ${PROBE_TABLE}`).catch(() => undefined)
      await pool.query(`DROP TABLE IF EXISTS ${E2E_TABLE}`).catch(() => undefined)
      const outboxFailure = await assertProductionOutboxUnchanged(baseline).then(
        () => null,
        (err) => err,
      )
      const diff = diffSharedDatabase(
        sharedBaseline,
        await snapshotSharedDatabase({ outboxSignature: PRODUCTION_TEST_SIGNATURE }),
      )
      for (const warning of diff.warnings) console.warn(`[shared-db] ${warning}`)
      const offenders = connectionWatch ? await connectionWatch.stop() : []
      const connectionFailures = offenders.map((row) =>
        describeHarnessConnection(row, { database: connectionWatch.database }),
      )
      if (!connectionFailures.length) {
        console.log(`[shared-db] no harness connection to ${connectionWatch?.database ?? 'nexus'} (tag ${TEST_APPLICATION_NAME})`)
      }
      await pool.end().catch(() => undefined)
      if (outboxFailure) throw outboxFailure
      const all = [...diff.failures, ...connectionFailures]
      if (all.length) throw new Error(all.join('\n\n'))
    }
    await dropTestDatabase().catch((err) => console.warn(`[outbox-quarantine] drop skipped: ${err.message}`))
  }
})

test('quarantine is surgical, reversible and idempotent', async () => {
  // The signature the tool uses must be the harness's own historical list, or
  // "historical rows" would mean two different things to two components.
  assert.ok(
    PRODUCTION_TEST_SIGNATURE.startsWith(HISTORICAL_TEST_SIGNATURE.replace(/\)$/, '') + ' OR source_namespace LIKE'),
    'the tool must quarantine exactly the harness signature (static prefixes + actor): ' + PRODUCTION_TEST_SIGNATURE,
  )

  const pendingResidue = await insertRow(PROBE_TABLE, {
    namespace: `keychain-test-${process.pid}-residue`,
    actor: 'broker-smoke-seed',
    status: 'pending',
    recordedAt: '2026-09-26T02:55:03Z',
  })
  const deliveredResidue = await insertRow(PROBE_TABLE, {
    namespace: RESIDUE_NAMESPACE,
    actor: 'broker-smoke-seed',
    status: 'delivered',
    attempts: 1,
    recordedAt: '2026-09-27T02:50:44Z',
  })
  const freshRow = await insertRow(PROBE_TABLE, {
    namespace: FRESH_NAMESPACE,
    actor: 'sol',
  })

  // 1. Dry run reports both residues and writes nothing.
  const dry = await quarantineRows(pool, { table: PROBE_TABLE, dryRun: true })
  assert.equal(dry.matched, 2, `dry run must match both residues, got ${JSON.stringify(dry.rows.map((r) => r.id))}`)
  assert.equal(dry.changed, 2)
  assert.ok(dry.rows.every((row) => row.action === 'would-quarantine'))
  assert.equal((await rowState(PROBE_TABLE, pendingResidue)).checkpoint_status, 'pending', 'dry run must not write')
  assert.equal((await rowState(PROBE_TABLE, deliveredResidue)).checkpoint_status, 'delivered', 'dry run must not write')

  // 2. Apply: set aside with evidence, fresh traffic untouched.
  const at = new Date('2026-11-18T04:00:00Z')
  const applied = await quarantineRows(pool, { table: PROBE_TABLE, dryRun: false, at })
  assert.equal(applied.matched, 2)
  assert.equal(applied.changed, 2)

  const pendingAfter = await rowState(PROBE_TABLE, pendingResidue)
  assert.equal(pendingAfter.checkpoint_status, QUARANTINE_STATUS)
  assert.match(pendingAfter.last_error, new RegExp(`^${QUARANTINE_MARKER}2026-11-18T04:00:00\\.000Z`))
  assert.match(pendingAfter.last_error, /\(was: pending, delivery_attempts=0\)/)
  assert.match(pendingAfter.last_error, /historical test-harness outbox row/)

  const deliveredAfter = await rowState(PROBE_TABLE, deliveredResidue)
  assert.equal(deliveredAfter.checkpoint_status, QUARANTINE_STATUS)
  assert.match(
    deliveredAfter.last_error,
    /\(was: delivered, delivery_attempts=1\)/,
    'the prior status must survive in the note, or the disposition destroys provenance',
  )

  const freshAfter = await rowState(PROBE_TABLE, freshRow)
  assert.equal(freshAfter.checkpoint_status, 'pending', 'a legitimate row must not be touched')
  assert.equal(freshAfter.last_error, null)

  // 3. The broker's claim predicate no longer reaches them, and still reaches
  //    the fresh row (so this is not an empty-table tautology).
  const claimable = await nextClaimable(PROBE_TABLE)
  assert.ok(claimable, 'the fresh row must still be claimable')
  assert.equal(claimable.source_namespace, FRESH_NAMESPACE)
  const claimAll = await pool.query(
    `SELECT id FROM ${PROBE_TABLE}
      WHERE (checkpoint_status IN ('pending','failed')
         OR (checkpoint_status = 'delivering' AND claimed_at < now() - interval '5 minutes'))
        AND (CASE WHEN $1 <> '' THEN source_namespace LIKE $1 || '%'
                  ELSE ($2 = '' OR source_namespace NOT LIKE $2 || '%') END)`,
    CLAIM_PARAMS,
  )
  const claimableIds = claimAll.rows.map((row) => row.id)
  assert.ok(!claimableIds.includes(pendingResidue), 'a quarantined pending residue must be unreachable')
  assert.ok(!claimableIds.includes(deliveredResidue), 'a quarantined delivered residue must be unreachable')

  // 4. Idempotent, then losslessly reversible.
  const again = await quarantineRows(pool, { table: PROBE_TABLE, dryRun: false })
  assert.equal(again.matched, 0, 'a second run must find nothing to do')

  const restored = await quarantineRows(pool, { table: PROBE_TABLE, dryRun: false, restore: true })
  assert.equal(restored.matched, 2)
  const pendingRestored = await rowState(PROBE_TABLE, pendingResidue)
  assert.equal(pendingRestored.checkpoint_status, 'pending', 'restore must return the recorded prior status')
  assert.equal(pendingRestored.last_error, null)
  const deliveredRestored = await rowState(PROBE_TABLE, deliveredResidue)
  assert.equal(deliveredRestored.checkpoint_status, 'delivered')
  assert.equal((await rowState(PROBE_TABLE, freshRow)).checkpoint_status, 'pending', 'restore must not touch fresh rows')
})

test('a real broker still broadcasts a fresh event and skips the quarantined row', async () => {
  const residueId = await insertRow(E2E_TABLE, {
    namespace: RESIDUE_NAMESPACE,
    actor: 'broker-smoke-seed',
  })
  const freshEventId = randomUUID()
  const freshId = await insertRow(E2E_TABLE, {
    namespace: FRESH_NAMESPACE,
    eventId: freshEventId,
    actor: 'sol',
  })

  const applied = await quarantineRows(pool, { table: E2E_TABLE, dryRun: false })
  assert.equal(applied.matched, 1, 'only the residue row should be quarantined')
  assert.equal((await rowState(E2E_TABLE, residueId)).checkpoint_status, QUARANTINE_STATUS)

      const base = await startBrokerOn(E2E_TABLE)
  try {
    // The broker polls on an interval, so wait on the row's own settlement —
    // polling the snapshot count instead races the final UPDATE (the checkpoint
    // lands first, and the row is still `delivering` when the status endpoint
    // already reports it).
    const deadline = Date.now() + 30_000
    let freshAfter = await rowState(E2E_TABLE, freshId)
    while (Date.now() < deadline) {
      freshAfter = await rowState(E2E_TABLE, freshId)
      if (['delivered', 'not_applicable', 'failed'].includes(freshAfter.checkpoint_status)) break
      await new Promise((resolve) => setTimeout(resolve, 250))
    }

    // End to end: the row left `pending` through the broker's own delivery, and
    // the checkpoint it produced names this event.
    assert.ok(
      ['delivered', 'not_applicable'].includes(freshAfter.checkpoint_status),
      `unexpected delivery outcome: ${freshAfter.checkpoint_status} (${freshAfter.last_error})`,
    )

    const status = await (await fetch(`${base}/keychain-snapshot/agent-records/status`)).json()
    assert.ok(
      Number.isInteger(status.latestSnapshot) && status.latestSnapshot > 0,
      `a fresh event must still produce a checkpoint, got ${JSON.stringify(status)}`,
    )

    const mongo = new MongoClient(mongoUrl())
    try {
      await mongo.connect()
      const db = mongo.db(MONGO_DB_NAME)
      const snapshot = await db.collection('ar_snapshots').findOne({ source_event_id: freshEventId })
      assert.ok(snapshot, 'the committed checkpoint must be attributable to the fresh event')
      assert.equal(snapshot.source_namespace, FRESH_NAMESPACE)
    } finally {
      await mongo.close()
    }

    // And the quarantined row was never claimed: no attempt was spent on it.
    const residueAfter = await rowState(E2E_TABLE, residueId)
    assert.equal(residueAfter.checkpoint_status, QUARANTINE_STATUS)
    assert.equal(residueAfter.delivery_attempts, 0, 'the poller must not have claimed the quarantined row')
  } finally {
    await stopBroker()
  }
})
