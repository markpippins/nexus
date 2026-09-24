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
 *   - The test writes into the shared `keychains` database, so it captures the
 *     pointer/state documents first and restores them on the way out —
 *     including on SIGINT/SIGTERM, because an interrupted run otherwise
 *     leaves committed checkpoints that advance the live active pointer.
 *   - Deltas are addressed by prevVersion pointers, not contiguous version
 *     ranges, so removing the checkpoints this test creates cannot break
 *     reconstruction of any later checkpoint.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { randomUUID } = require('node:crypto')
const { spawn } = require('node:child_process')
const path = require('node:path')
const dotenv = require('dotenv')
const { MongoClient } = require('mongodb')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')

dotenv.config({ path: path.join(__dirname, '..', '.env') })

const BROKER_DIR = path.resolve(__dirname, '..')
// OS-assigned ports by default (see tests/helpers/ephemeral-ports.js);
// HARNESS_FIXED_PORTS=1 restores the historical DELTA_TEST_* values.
const FIXED_SERVICE_PORT = process.env.DELTA_TEST_PORT || '4100'
const FIXED_PTY_WS_PORT = process.env.DELTA_TEST_PTY_WS_PORT || String(Number(FIXED_SERVICE_PORT) + 2)
const MONGO_URL = process.env.MONGO_URL || 'mongodb://localhost:27017'
let TEST_PORT = null
let TEST_PTY_WS_PORT = null
let BASE = null

let child = null

async function startBroker() {
  TEST_PORT = await getEphemeralPort(FIXED_SERVICE_PORT)
  TEST_PTY_WS_PORT = await getEphemeralPort(FIXED_PTY_WS_PORT)
  BASE = `http://localhost:${TEST_PORT}/api`
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
      },
      stdio: ['ignore', 'ignore', 'ignore'],
    }
  )
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/health`)
      if (res.ok) return
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('broker did not become healthy within 30s')
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

test.before(async () => {
  await startBroker()
})

test.after(async () => {
  await stopBroker()
})

test('checkpoints persist deltas, not full copies, and rewind reconstructs them', async () => {
  const sourceNamespace = `keychains-delta-${process.pid}-${Date.now()}`
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
    db = mongo.db('keychains')
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
        source_namespace: `keychains-anchor-${process.pid}-${Date.now()}`,
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
        recorded_at: new Date().toISOString(),
      }
      events.push(triggerEvent)
      const res = await postSnapshot(triggerEvent)
      assert.equal(res.status, 200)
      const body = await res.json()
      assert.equal(body.ok, true)
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

    // The chain must root at the full-manifest baseline we identified.
    const first = docs.find((doc) => doc.version === Math.min(...versions))
    assert.equal(first.prevVersion, baseline.version, 'first delta chains to the full-manifest baseline')
    assert.equal(first.base_version, baseline.version)

    // Explicitly prove the persisted pointer chain terminates at a full base
    // rather than merely trusting base_version metadata. This catches cycles,
    // missing predecessors, and deltas whose advertised base is not reachable.
    const byVersion = new Map(docs.concat([baseline]).map((doc) => [doc.version, doc]))
    let cursor = docs.find((doc) => doc.version === Math.max(...versions))
    const seen = new Set()
    while (cursor.storage === 'delta') {
      assert.ok(!seen.has(cursor.version), `delta chain cycles at v${cursor.version}`)
      seen.add(cursor.version)
      assert.ok(Number.isInteger(cursor.prevVersion), `v${cursor.version} has a pointer predecessor`)
      cursor = byVersion.get(cursor.prevVersion)
      assert.ok(cursor, `delta predecessor v${cursor?.version || 'unknown'} exists in the test chain`)
    }
    assert.equal(cursor.version, baseline.version, 'delta chain terminates at the full-manifest baseline')
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
