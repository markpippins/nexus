/**
 * Keychain checkpoint storage — base+delta (D6) test.
 *
 * Historical shape: every checkpoint embedded a full state_vector plus
 * parallel instance_ids / current_record_ids arrays (3.74 MB/doc measured on
 * the live store) and wrote a complete copy of every resolved entry into
 * checkpoint_entries (5.7 GB for 201 checkpoints). Nothing read the entry
 * copies back — rewind reconstructs from state_vector, and the only other
 * reference was a countDocuments feeding one status field.
 *
 * This test drives real checkpoints through the broker and asserts:
 *   1. a delta checkpoint persists only what moved — no state_vector, no
 *      entry copies, no derivable full-manifest arrays;
 *   2. rewind reconstructs a delta checkpoint's state vector correctly,
 *      verified against record_type_state, the independently materialized
 *      current-state view (a real oracle, not a self-comparison);
 *   3. every reconstructed delta has the instance count its own entryCount
 *      recorded, so reconstruction is checked per step, not just at the tip;
 *   4. a pre-change checkpoint still reads back through the unchanged
 *      full-manifest path;
 *   5. the bounded prior-state pointer advances, and a replayed event does
 *      not produce a second checkpoint.
 *
 * Isolation notes:
 *   - SERVICE_PORT isolates the HTTP gateway, but worker.pty-transport binds
 *     its own PTY_WS_PORT (default 3130). Without overriding that a second
 *     broker cannot boot while the production unit is running — it dies with
 *     EADDRINUSE on 127.0.0.1:3130 before the gateway ever answers.
 *   - The test does NOT write into the shared `keychains` database or
 *     production's outbox queue: the broker boots against this process's own
 *     Mongo database and claims from this process's own outbox table
 *     (helpers/isolated-mongo.js). The pre-state capture / restore below is a
 *     re-run guard for a pinned store — a fresh-store run seeds its anchor and
 *     deletes only its own documents.
 *   - That restore is still registered on SIGINT/SIGTERM: an interrupted run
 *     leaves committed checkpoints behind, and on a pinned store those would
 *     advance the active pointer for the next run.
 *   - Deltas are addressed by prevVersion pointers, not contiguous version
 *     ranges, so removing the checkpoints this test creates cannot break
 *     reconstruction of any later checkpoint.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { createHash, randomUUID } = require('node:crypto')
const { spawn } = require('node:child_process')
const path = require('node:path')
const dotenv = require('dotenv')
const { MongoClient } = require('mongodb')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')
const { createBrokerLog } = require('./helpers/broker-log')
const {
  MONGO_DB_NAME: TEST_MONGO_DB,
  OUTBOX_NAMESPACE_PREFIX,
  OUTBOX_TABLE,
  mongoUrl,
  registerProductionOutboxGuard,
  dropIsolatedDatabase,
  waitForKeychainReady,
} = require('./helpers/isolated-mongo')
const { TEST_PG_DATABASE, ensureTestDatabase, pgConfig, TEST_APPLICATION_NAME } = require('./helpers/isolated-pg')

//
// PG records THIS test owns.
//
// The delta assertions need a non-empty current-state view: the service builds
// the anchor state vector from `nebula.agent_records` (the view over the
// temporal `agent_records_history`), and an empty view materializes no
// `record_type_state` rows, so "rewind reconstructs the materialized current
// state" has nothing to compare against.
//
// Those rows used to be shipped by the CI shim (`keychain-delta-ci-pg.sql`),
// which is fixture-as-scaffolding: the suite was green because an operator-managed
// step had prepared the database, and the fixture could drift from the schema it
// mirrors with nothing to notice. They are created here instead, with ids this
// test owns, and deleted on the way out. The shapes are the ones that exercise
// the resolver's grouping rules: an amendment chain (2 -> 1 instance), a
// supersession chain (2 -> 1) and a standalone (1), i.e. 4 logical instances.
const PG_RECORD_IDS = [
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  '33333333-3333-4333-8333-333333333333',
  '44444444-4444-4444-8444-444444444444',
  '55555555-5555-4555-8555-555555555555',
]

async function seedPgRecords() {
  const { Pool } = require('pg')
  const pool = new Pool({ ...pgConfig(), connectionTimeoutMillis: 3000 })
  try {
    // Fixed recorded_on_dt so the (id, recorded_on_dt) PK makes a re-seed a no-op.
    await pool.query(
      `INSERT INTO nebula.agent_records_history
         (id, record_type, role, title, content, tags, created_at, level, visibility_scope,
          recorded_on_dt, recorded_until_dt, valid_from, valid_until)
       VALUES
         ('11111111-1111-4111-8111-111111111111', 'report', 'engineer', 'Report 05aa1111: initial issuance', 'initial body', '{}', '2026-01-01T00:29:00Z', 1, 'all', '2026-01-01T00:00:00Z', 'infinity', '2026-01-01T00:00:00Z', 'infinity'),
         ('22222222-2222-4222-8222-222222222222', 'report', 'engineer', 'Report 05aa1111: amendment one', 'amended body', '{}', '2026-01-01T00:20:00Z', 1, 'all', '2026-01-01T00:00:01Z', 'infinity', '2026-01-01T00:00:01Z', 'infinity'),
         ('33333333-3333-4333-8333-333333333333', 'analysis', 'analyst', 'Analysis: superseded baseline', 'baseline body', '{supersedes:}', '2026-01-01T00:15:00Z', 2, 'all', '2026-01-01T00:00:02Z', 'infinity', '2026-01-01T00:00:02Z', 'infinity'),
         ('44444444-4444-4444-8444-444444444444', 'analysis', 'analyst', 'Analysis: superseding revision', 'revision body', '{supersedes:33333333}', '2026-01-01T00:10:00Z', 2, 'all', '2026-01-01T00:00:03Z', 'infinity', '2026-01-01T00:00:03Z', 'infinity'),
         ('55555555-5555-4555-8555-555555555555', 'inspection', 'inspector', 'Inspection: standalone observation', 'inspection body', '{}', '2026-01-01T00:05:00Z', 3, 'all', '2026-01-01T00:00:04Z', 'infinity', '2026-01-01T00:00:04Z', 'infinity')
       ON CONFLICT (id, recorded_on_dt) DO NOTHING`,
    )
  } finally {
    await pool.end().catch(() => undefined)
  }
}

async function removePgRecords() {
  const { Pool } = require('pg')
  const pool = new Pool({ ...pgConfig(), connectionTimeoutMillis: 3000 })
  try {
    await pool.query('DELETE FROM nebula.agent_records_history WHERE id = ANY($1::uuid[])', [PG_RECORD_IDS])
  } finally {
    await pool.end().catch(() => undefined)
  }
}

dotenv.config({ path: path.join(__dirname, '..', '.env') })

const BROKER_DIR = path.resolve(__dirname, '..')
// OS-assigned ports by default (see tests/helpers/ephemeral-ports.js);
// HARNESS_FIXED_PORTS=1 restores the historical DELTA_TEST_* values.
const FIXED_SERVICE_PORT = process.env.DELTA_TEST_PORT || '4100'
const FIXED_PTY_WS_PORT = process.env.DELTA_TEST_PTY_WS_PORT || String(Number(FIXED_SERVICE_PORT) + 2)
  const MONGO_URL = mongoUrl()
let TEST_PORT = null
let TEST_PTY_WS_PORT = null
let BASE = null
const sha256 = (value) => `sha256:${createHash('sha256').update(value).digest('hex')}`

let child = null

// The broker's output is captured, not discarded — see helpers/broker-log.js
// for why (a boot failure used to say nothing but "did not become healthy").
const brokerLog = createBrokerLog('keychain-delta')

async function startBroker() {
  TEST_PORT = await getEphemeralPort(FIXED_SERVICE_PORT)
  TEST_PTY_WS_PORT = await getEphemeralPort(FIXED_PTY_WS_PORT)
  BASE = `http://localhost:${TEST_PORT}/api`
  // One descriptor for stdout and stderr, released in this process right
  // after the spawn: the child keeps its own copies.
  const brokerLogFd = brokerLog.open()
  child = spawn(
    process.execPath,
    ['node_modules/.bin/moleculer-runner', '--mask', '**/*.js', 'dist/services'],
    {
      cwd: BROKER_DIR,
      env: {
        ...process.env,
                  SERVICE_PORT: TEST_PORT,
          PTY_WS_PORT: TEST_PTY_WS_PORT,
          NODE_ENV: 'test',
          // Isolated keychain store per broker process (helpers/isolated-mongo.js):
          // this test writes checkpoints and previously did so in the shared
          // "keychains" database while other brokers booted against it.
          MONGO_DB_NAME: TEST_MONGO_DB,
          KEYCHAIN_OUTBOX_NAMESPACE_PREFIX: OUTBOX_NAMESPACE_PREFIX,
          // Never production's queue: this suite writes its own outbox table.
          KEYCHAIN_OUTBOX_TABLE: OUTBOX_TABLE,
          // Poll this run's database by name — see the note in broker-smoke.
          KEYCHAIN_SOURCE_DATABASES: TEST_PG_DATABASE,
          // Name this child's PG connections after the run (helpers/isolated-pg.js);
          // a harness connection to a shared database is then attributable in
          // pg_stat_activity even when it only reads. Inherited as PGAPPNAME too.
          PGAPPNAME: TEST_APPLICATION_NAME,
      },
      // Captured, not discarded: see helpers/broker-log.js.
      stdio: ['ignore', brokerLogFd, brokerLogFd],
    }
  )
  brokerLog.close(brokerLogFd)
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
              const res = await fetch(`${BASE}/health`)
        if (res.ok) {
          // Gateway health is not service readiness — see helpers/isolated-mongo.js.
          // Without this the first snapshot POST can hit an unregistered
          // keychain service and return 503.
          await waitForKeychainReady(BASE)
          return
        }
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(
    brokerLog.describe('broker did not become healthy within 30s', child),
  )
}

function stopBroker() {
  return new Promise((resolve) => {
    if (!child) return resolve()
    child.on('exit', () => resolve())
    child.kill('SIGTERM')
    setTimeout(() => {
      if (child && child.exitCode === null) child.kill('SIGKILL')
      resolve()
    }, 3000)
  })
}

function postSnapshot(triggerEvent) {
  return fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggerEvent }),
  })
}

/**
 * Order-insensitive SEMANTIC view of a state vector: which instance is
 * currently carried by which record, at what version and role. Incidental
 * fields (asset_id, tags, …) legitimately differ between the base entries,
 * delta manifests, and the record_type_state compatibility writer, so they
 * are projected out rather than compared.
 */
function normalizeStateVector(stateVector) {
  const out = {}
  for (const [recordType, instances] of Object.entries(stateVector || {})) {
    out[recordType] = [...instances]
      .map((instance) => ({
        instance_id: instance.instance_id,
        record_id: instance.record_id,
        version: instance.version,
        role: instance.role,
      }))
      .sort((a, b) => String(a.instance_id).localeCompare(String(b.instance_id)))
  }
  return out
}

function instanceCount(stateVector) {
  return Object.values(stateVector || {}).reduce((sum, instances) => sum + instances.length, 0)
}

// Guard first (a matching outbox table, then production's baseline), then the
// broker; teardown stops it and drops this process's store before the
// production-outbox assertion.
registerProductionOutboxGuard({
  before: test.before,
  after: test.after,
  async setup() {
    await ensureTestDatabase()
    await seedPgRecords()
    await startBroker()
  },
  async teardown() {
    await stopBroker()
    // Remove this test's own PG records (the database is dropped right after by
    // the guard, but leaving them behind would be wrong if it were kept).
    await removePgRecords().catch((err) => console.warn(`[keychain-delta] pg cleanup skipped: ${err.message}`))
    // Drop this process's isolated keychain database so repeat runs are clean.
    const mongo = new MongoClient(MONGO_URL)
    try {
      await mongo.connect()
      await dropIsolatedDatabase(mongo)
    } finally {
      await mongo.close()
    }
  },
})

test('checkpoints persist deltas, not full copies, and rewind reconstructs them', async () => {
  const sourceNamespace = `keychains-delta-${process.pid}-${Date.now()}`
  const doctrineSnapshot = {
    schema_version: 1,
    snapshot_id: sha256(`snapshot:${sourceNamespace}`),
    system_prompt_hash: sha256(`system-prompt:${sourceNamespace}`),
    bootstrap_hash: sha256(`bootstrap:${sourceNamespace}`),
    active_procedure_cards: [
      sha256(`card-a:${sourceNamespace}`),
      sha256(`card-b:${sourceNamespace}`),
    ].sort(),
  }
  const mongo = new MongoClient(MONGO_URL)
  const checkpointIds = []
  let previousActive = null
  let previousHead = null
  let previousSequence = null
  let previousEntries = null
  let previousRecordTypeState = null
  let db = null
  let restored = false
  let seededAnchorVersion = null

  // Shared-store restore. Registered against process signals as well as the
  // normal exit path: the broker test harness has been interrupted mid-run
  // before, and a half-finished run leaves committed checkpoints behind.
  const restore = async () => {
    if (restored || !db) return
    restored = true
    await db.collection('transitions').deleteMany({ source_namespace: sourceNamespace })
    await db.collection('doctrine_snapshots').deleteOne({ _id: doctrineSnapshot.snapshot_id })
    if (checkpointIds.length) {
      await db.collection('ar_drift_findings').deleteMany({ checkpoint_id: { $in: checkpointIds } })
      await db.collection('ar_snapshots').deleteMany({ checkpoint_id: { $in: checkpointIds } })
      await db.collection('checkpoint_entries').deleteMany({ checkpoint_id: { $in: checkpointIds } })
    }
    if (seededAnchorVersion != null) {
      // Remove the anchor we seeded ourselves (fresh store only). Restore
      // never touches a pre-existing anchor on a seeded store.
      await db.collection('transitions').deleteOne({ snapshot_version: seededAnchorVersion })
      await db.collection('ar_snapshots').deleteMany({ version: seededAnchorVersion })
    }
    if (previousActive) {
      await db.collection('active_checkpoints').replaceOne({ _id: 'agent-records' }, previousActive, { upsert: true })
    } else {
      await db.collection('active_checkpoints').deleteOne({ _id: 'agent-records' })
    }
    if (previousHead) {
      await db.collection('checkpoint_heads').replaceOne({ _id: 'agent-records' }, previousHead, { upsert: true })
    } else {
      await db.collection('checkpoint_heads').deleteOne({ _id: 'agent-records' })
    }
    // The version sequence persists across deleted checkpoints by design (the
    // $max repair only moves it up), so it must be rolled back too — otherwise
    // every rerun burns versions and version-gap assumptions in other tooling
    // silently drift.
    if (previousSequence) {
      await db.collection('checkpoint_sequences').replaceOne({ _id: 'agent-records' }, previousSequence, { upsert: true })
    } else {
      await db.collection('checkpoint_sequences').deleteOne({ _id: 'agent-records' })
    }
    await db.collection('entries').deleteMany({})
    if (previousEntries?.length) await db.collection('entries').insertMany(previousEntries)
    await db.collection('record_type_state').deleteMany({})
    if (previousRecordTypeState?.length) {
      await db.collection('record_type_state').insertMany(previousRecordTypeState)
    }
  }
  const onSignal = () => {
    void restore().finally(() => process.exit(1))
  }
  process.once('SIGINT', onSignal)
  process.once('SIGTERM', onSignal)

  try {
    await mongo.connect()
          db = mongo.db(TEST_MONGO_DB)
    // Fresh-store guard: a dedicated CI/scratch Mongo has no prior collections,
    // so these pre-state reads return empty and restore() would wrongly wipe
    // the store after the run. On an empty store, skip pre-state capture and
    // restore only our own seeded/test documents.
    const freshStore =
      (await db.collection('entries').estimatedDocumentCount()) === 0 &&
      (await db.collection('record_type_state').estimatedDocumentCount()) === 0
    if (!freshStore) {
      previousActive = await db.collection('active_checkpoints').findOne({ _id: 'agent-records' })
      previousHead = await db.collection('checkpoint_heads').findOne({ _id: 'agent-records' })
      previousSequence = await db.collection('checkpoint_sequences').findOne({ _id: 'agent-records' })
      previousEntries = await db.collection('entries').find({}).toArray()
      previousRecordTypeState = await db.collection('record_type_state').find({}).toArray()
    }

    // The baseline must be the newest checkpoint that still carries a full
    // manifest — NOT simply the newest checkpoint, because a prior run of this
    // very test (or any future delta) can be the newest. Asserting against
    // `latestSnapshot` here would make the legacy-read check fail the moment a
    // delta became the tip.
    // Self-seeding: the delta chain needs a pre-existing full-manifest
    // committed anchor. On titanium that state is production-seeded; on a
    // fresh CI Mongo it is not. Seed it THROUGH THE SERVICE'S OWN WRITE
    // PATH: a snapshot POST against unknown prior state is a base (full
    // manifest) by D6 construction, so the fixture follows the real code
    // path and survives schema evolution (no hand-crafted documents).
    let baseline = await db.collection('ar_snapshots').findOne(
      {
        state_vector: { $exists: true },
        $or: [{ checkpoint_status: 'committed' }, { checkpoint_status: { $exists: false } }],
      },
      { sort: { version: -1 } }
    )
    if (!baseline) {
      const seedRes = await postSnapshot({
        source_namespace: `${OUTBOX_NAMESPACE_PREFIX}anchor-${process.pid}-${Date.now()}`,
        source_event_id: randomUUID(),
        kind: 'sol.transition.committed',
        outcome: 'committed',
        actor: 'keychain-delta-test-anchor-seed',
        recorded_at: new Date().toISOString(),
      })
      assert.equal(seedRes.status, 200, 'anchor seed POST should be accepted')
      const seedBody = await seedRes.json()
      assert.equal(seedBody.ok, true)
      assert.equal(
        seedBody.storage,
        'base',
        'first write on unknown prior state must be a full-manifest base (D6)'
      )
      seededAnchorVersion = seedBody.version
      console.log(`[delta-test] seeded full-manifest anchor at v${seededAnchorVersion}`)
      baseline = await db.collection('ar_snapshots').findOne({ version: seededAnchorVersion })
      assert.ok(baseline, 'seeded anchor is queryable')
    } else {
      console.log(`[delta-test] using pre-existing full-manifest anchor at v${baseline.version}`)
    }

    const legacyRewind = await (
      await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${baseline.version}`)
    ).json()
    assert.equal(legacyRewind.ok, true)
    assert.ok(legacyRewind.state_vector, 'full-manifest checkpoint still returns a state vector')
    assert.notEqual(legacyRewind.reconstructed, true, 'full-manifest checkpoint is not a reconstruction')
    assert.notEqual(legacyRewind.storage, 'delta')
    if (!Object.hasOwn(baseline, 'doctrine_snapshot_id')) {
      assert.equal(Object.hasOwn(legacyRewind, 'doctrine_snapshot_id'), false, 'legacy rewind shape is unchanged')
    }

    // Three committed events. A version is a base only on the first write
    // against unknown prior state or every KEYCHAIN_BASE_INTERVAL (50)
    // versions, so at least two of these are guaranteed to be deltas.
    const versions = []
    const events = []
    for (let i = 0; i < 3; i += 1) {
      const triggerEvent = {
        source_namespace: sourceNamespace,
        source_event_id: randomUUID(),
        kind: 'sol.transition.committed',
        outcome: 'committed',
        actor: 'keychain-delta-test',
        read_set: {
          doctrine_snapshot: doctrineSnapshot,
          doctrine_snapshot_id: doctrineSnapshot.snapshot_id,
        },
        payload: {
          doctrine_snapshot: doctrineSnapshot,
          doctrine_snapshot_id: doctrineSnapshot.snapshot_id,
        },
        recorded_at: new Date().toISOString(),
      }
      events.push(triggerEvent)
      const res = await postSnapshot(triggerEvent)
      assert.equal(res.status, 200)
      const body = await res.json()
      assert.equal(body.ok, true)
      assert.equal(body.doctrine_snapshot_id, doctrineSnapshot.snapshot_id)
      versions.push(body.version)

      console.log(`[delta-test] post ${i + 1}: version=${body.version} storage=${body.storage} entryCount=${body.entryCount}`)
      const checkpoint = await db.collection('ar_snapshots').findOne({ version: body.version })
      checkpointIds.push(checkpoint.checkpoint_id)
      assert.ok(body.storage === 'base' || body.storage === 'delta')
      assert.equal(body.storage, checkpoint.storage)
    }

    const docs = await db.collection('ar_snapshots').find({ version: { $in: versions } }).toArray()
    const deltas = docs.filter((doc) => doc.storage === 'delta')
    assert.ok(deltas.length >= 2, `expected at least 2 deltas, got ${deltas.length}`)
    assert.equal(
      await db.collection('doctrine_snapshots').countDocuments({ _id: doctrineSnapshot.snapshot_id }),
      1,
      'identical doctrine snapshots dedupe to one projected record',
    )
    for (const doc of docs) {
      assert.equal(doc.doctrine_snapshot_id, doctrineSnapshot.snapshot_id)
    }
    const transition = await db.collection('transitions').findOne({
      source_namespace: sourceNamespace,
      source_event_id: events[0].source_event_id,
    })
    assert.equal(transition.read_set.doctrine_snapshot_id, doctrineSnapshot.snapshot_id)

    for (const doc of deltas) {
      // A delta carries only what moved.
      assert.ok(doc.delta, `v${doc.version} stores a delta`)
      assert.equal(doc.state_vector, undefined, `v${doc.version} stores no full state vector`)
      assert.equal(doc.instance_ids, undefined, `v${doc.version} stores no instance_ids array`)
      assert.equal(doc.current_record_ids, undefined, `v${doc.version} stores no current_record_ids array`)
      assert.ok(Number.isInteger(doc.prevVersion) && doc.prevVersion >= 1)
      assert.ok(Number.isInteger(doc.base_version) && doc.base_version <= doc.prevVersion)
      // The retired content store receives nothing for this checkpoint.
      assert.equal(
        await db.collection('checkpoint_entries').countDocuments({ checkpoint_id: doc.checkpoint_id }),
        0,
        `v${doc.version} writes no checkpoint_entries copies`
      )
      assert.deepEqual(Object.keys(doc.delta).sort(), ['added', 'removed', 'updated'])
    }

    // Explicitly prove the persisted pointer chain terminates at a full base
    // rather than merely trusting base_version metadata. This catches cycles,
    // missing predecessors, and deltas whose advertised base is not reachable.
    const byVersion = new Map(docs.map((doc) => [doc.version, doc]))
    const loadCheckpoint = async (version) => {
      if (!byVersion.has(version)) {
        const doc = await db.collection('ar_snapshots').findOne({
          version,
          $or: [{ checkpoint_status: 'committed' }, { checkpoint_status: { $exists: false } }],
        })
        if (doc) byVersion.set(version, doc)
      }
      return byVersion.get(version)
    }
    let cursor = await loadCheckpoint(Math.max(...versions))
    const seen = new Set()
    while (cursor?.storage === 'delta') {
      assert.ok(!seen.has(cursor.version), `delta chain cycles at v${cursor.version}`)
      seen.add(cursor.version)
      assert.ok(Number.isInteger(cursor.prevVersion), `v${cursor.version} has a pointer predecessor`)
      cursor = await loadCheckpoint(Number(cursor.prevVersion))
      assert.ok(cursor, `delta predecessor v${cursor?.version || 'unknown'} exists in the test chain`)
    }
    assert.ok(cursor, 'delta chain has a root checkpoint')
    assert.ok(cursor.storage === 'base' || cursor.state_vector, 'chain terminates at a full-manifest base')

    // Rewind the newest checkpoint. It is a delta, so the response must be a
    // reconstruction, and its state vector must match the independently
    // materialized current-state view.
    const target = docs.find((doc) => doc.version === Math.max(...versions))
    const rewind = await (
      await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${target.version}`)
    ).json()
    assert.equal(rewind.ok, true)
    assert.equal(rewind.version, target.version)
    assert.equal(rewind.doctrine_snapshot_id, doctrineSnapshot.snapshot_id)

    if (target.storage === 'delta') {
      assert.equal(rewind.reconstructed, true)
      assert.equal(rewind.storage, 'delta')
      assert.ok(rewind.delta_steps >= 1, 'at least one delta step was replayed')
      assert.ok(Number.isInteger(rewind.base_version) && rewind.base_version < target.version)
    }

    const stateRows = await db.collection('record_type_state').find({}).toArray()
    assert.ok(stateRows.length > 0, 'current-state view is materialized')
    const oracle = Object.fromEntries(stateRows.map((row) => [row.record_type, row.active_instances]))
    assert.deepEqual(
      normalizeStateVector(rewind.state_vector),
      normalizeStateVector(oracle),
      'reconstructed state vector matches the materialized current state'
    )
    assert.equal(rewind.recordTypeCount, Object.keys(oracle).length)

    // Per-step check: every delta must rebuild to the instance count its own
    // entryCount recorded. This catches a dropped or mis-ordered delta rather
    // than only validating the tip.
    for (const doc of deltas) {
      const each = await (
        await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${doc.version}`)
      ).json()
      assert.equal(each.ok, true)
      assert.equal(
        instanceCount(each.state_vector),
        doc.entryCount,
        `reconstructed v${doc.version} instance count matches its recorded entryCount`
      )
    }

    // The bounded prior-state pointer advanced to the last checkpoint.
    const head = await db.collection('checkpoint_heads').findOne({ _id: 'agent-records' })
    assert.ok(head, 'checkpoint_heads pointer was written')
    assert.equal(head.version, Math.max(...versions))
    assert.equal(
      Object.keys(head.index || {}).length,
      target.entryCount,
      'pointer indexes every active instance'
    )

    // Replaying an event is idempotent: no second checkpoint, no new delta.
    const replay = await postSnapshot(events[0])
    assert.equal(replay.status, 200)
    const replayBody = await replay.json()
    assert.equal(replayBody.deduplicated, true)
    assert.equal(replayBody.version, versions[0])
    assert.equal(
      await db.collection('ar_snapshots').countDocuments({
        source_namespace: sourceNamespace,
        source_event_id: events[0].source_event_id,
      }),
      1,
      'replay did not create a second checkpoint'
    )
  } finally {
    process.removeListener('SIGINT', onSignal)
    process.removeListener('SIGTERM', onSignal)
    await restore()
    await mongo.close()
  }
})
