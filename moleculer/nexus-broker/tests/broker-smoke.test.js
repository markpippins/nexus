/**
 * nexus-broker boot smoke test — P1-1 remediation.
 *
 * Spawns the real broker (compiled dist, same path as `npm start`) on an
 * isolated port, waits for the gateway to come up, and asserts the
 * representative read paths: health, worker list, and worker actions.
 * Exercises the exact boot path systemd runs, so it fails fast if the
 * broker can no longer boot all 6 services.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { randomUUID } = require('node:crypto')
const { spawn } = require('node:child_process')
const path = require('node:path')
const dotenv = require('dotenv')
const { Pool } = require('pg')
const { MongoClient } = require('mongodb')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')

dotenv.config({ path: path.join(__dirname, '..', '.env') })

const BROKER_DIR = path.resolve(__dirname, '..')
// Ports are OS-assigned (ephemeral) by default — a fixed-port collision
// used to make the health poll connect to an unrelated local server
// (dev-server SPA fallbacks answer 200 on any port). HARNESS_FIXED_PORTS=1
// restores the historical 4098/4099 for port-specific debugging.
const FIXED_SERVICE_PORT = process.env.TEST_SERVICE_PORT || '4098'
const FIXED_PTY_WS_PORT = process.env.TEST_PTY_WS_PORT || String(Number(FIXED_SERVICE_PORT) + 1)
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
      // Drain output so a large projection cannot block the child on a full
      // pipe before the test reaches its assertions.
      stdio: ['ignore', 'ignore', 'ignore'],
    }
  )

  // Wait for gateway to answer /api/health (up to 30s)
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

async function waitFor(read, description, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  let lastValue
  while (Date.now() < deadline) {
    lastValue = await read()
    if (lastValue) return lastValue
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`timed out waiting for ${description}; last value: ${JSON.stringify(lastValue)}`)
}

// CI/titanium-shape guarantee: the keychain-checkpoint family (status,
// replay-idempotency, rewind) needs a committed checkpoint chain plus a
// delivered outbox event. Production stores always have one; a fresh CI
// store does not. Seed it through the service's own delivery path — an
// outbox row the poller delivers into a checkpoint — never hand-crafted
// mongo documents. Idempotent: skipped whenever a chain already exists.
// The manifest rows go into nebula.agent_records_history (the temporal
// table behind the agent_records view) with a fixed recorded_on_dt so the
// PK makes re-seeding a no-op.
async function ensureKeychainSeed() {
  const probe = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  if (Number.isInteger(probe.latestSnapshot) && probe.latestSnapshot > 0) return
  const { Pool } = require('pg')
  const pool = new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: 'sol',
  })
  try {
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
    await pool.query(
      `INSERT INTO resolution.keychain_event_outbox
         (source_namespace, source_event_id, event_kind, outcome, actor, read_set, payload)
       VALUES ($1, $2, 'sol.e2e.transition.committed', 'committed', 'broker-smoke-seed', '{}', '{}')
       ON CONFLICT (source_namespace, source_event_id) DO NOTHING`,
      [`broker-smoke-seed-${process.pid}`, randomUUID()],
    )
    const deadline = Date.now() + 20_000
    for (;;) {
      const status = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
      // The projection finalizes the transitions doc (checkpoint_status:
      // 'delivered') only AFTER the checkpoint commits and promotes —
      // /status flipping to latestSnapshot>0 does not imply the transition
      // is visible yet. Downstream tests (replay idempotency) read
      // /transitions, so the gate must wait through the finalize too.
      if (Number.isInteger(status.latestSnapshot) && status.latestSnapshot > 0) {
        const transitions = await (await fetch(`${BASE}/keychain-snapshot/agent-records/transitions?limit=50`)).json()
        if ((transitions.items || []).some((t) => t.checkpoint_status === 'delivered')) return
      }
      if (Date.now() > deadline) throw new Error('keychain seed: outbox row never delivered into a checkpoint')
      await new Promise((r) => setTimeout(r, 1000))
    }
  } finally {
    await pool.end().catch(() => {})
  }
}

test.before(async () => {
  await startBroker()
  await ensureKeychainSeed()
})

test.after(async () => {
  await stopBroker()
})

test('GET /api/health reports ok with all worker services', async () => {
  const res = await fetch(`${BASE}/health`)
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.status, 'ok')
  assert.equal(body.service, 'nexus-broker')
  assert.deepEqual(
    [...body.workers].sort(),
    ['worker.execution', 'worker.harness', 'worker.pty', 'worker.pty-transport']
  )
})

test('GET /api/workers lists the wave-4 workers as available', async () => {
  const res = await fetch(`${BASE}/workers`)
  assert.equal(res.status, 200)
  const body = await res.json()
  const names = body.workers.map((w) => w.name).sort()
  assert.deepEqual(names, ['worker.execution', 'worker.harness', 'worker.pty', 'worker.pty-transport'])
  for (const w of body.workers) {
    assert.equal(w.status, 'available')
  }
})

test('GET /api/workers/harness health action responds', async () => {
  const res = await fetch(`${BASE}/workers/harness`)
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.status, 'ok')
})

test('GET /api/keychain-snapshot/status exposes the event-driven D6 contract', async () => {
  const res = await fetch(`${BASE}/keychain-snapshot/status`)
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.enabled, true)
  assert.equal(body.mode, 'event_driven')
  assert.ok(Number.isInteger(body.outboxPollMs) && body.outboxPollMs > 0)
  assert.ok(Object.hasOwn(body, 'lastEventAt'))
  assert.ok(Object.hasOwn(body, 'lastSnapshotAt'))
  assert.ok(Object.hasOwn(body, 'chainHealth'))
  assert.ok(Number.isInteger(body.deepestRewindSinceBoot))
})

test('GET /api/keychain-snapshot/agent-records/status exposes the active checkpoint', async () => {
  const res = await fetch(`${BASE}/keychain-snapshot/agent-records/status`)
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.enabled, true)
  assert.ok(Number.isInteger(body.latestSnapshot) && body.latestSnapshot > 0)
  assert.ok(Number.isInteger(body.entryCount) && body.entryCount >= 0)
  assert.ok(body.checkpointId)
})

test('replaying a delivered event is idempotent', async () => {
  const status = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  const transitions = await (await fetch(`${BASE}/keychain-snapshot/agent-records/transitions?limit=200`)).json()
  const delivered = transitions.items.find((item) => item.checkpoint_status === 'delivered')
  assert.ok(delivered, 'expected at least one delivered event')
  const event = delivered.trigger_event || delivered
  const res = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggerEvent: event, label: 'broker-smoke-replay' }),
  })
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.ok, true)
  assert.equal(body.deduplicated, true)
  assert.equal(body.version, status.latestSnapshot)
})

test('negative outcomes are archived without advancing the checkpoint', async () => {
  const before = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  const eventId = `broker-smoke-refused-${Date.now()}`
  const res = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggerEvent: {
      source_namespace: 'broker-smoke',
      source_event_id: eventId,
      kind: 'verification.decision.refused',
      outcome: 'refused',
      actor: 'broker-smoke',
    } }),
  })
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.archived, true)
  assert.equal(body.checkpoint_created, false)
  const after = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  assert.equal(after.latestSnapshot, before.latestSnapshot)
  assert.equal(after.checkpointId, before.checkpointId)
})

test('deny_contract_promotion negative outcomes create immutable decision checkpoints', async () => {
  const sourceNamespace = `keychains-negative-${process.pid}-${Date.now()}`
  const sourceEventId = randomUUID()
  const before = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  const mongo = new MongoClient(process.env.MONGO_URL || 'mongodb://localhost:27017')
  let previousActive = null
  let previousEntries = null
  let previousRecordTypeState = null
  await mongo.connect()
  {
    const db = mongo.db('keychains')
    previousActive = await db.collection('active_checkpoints').findOne({ _id: 'agent-records' })
    previousEntries = await db.collection('entries').find({}).toArray()
    previousRecordTypeState = await db.collection('record_type_state').find({}).toArray()
  }
  const triggerEvent = {
    source_namespace: sourceNamespace,
    source_event_id: sourceEventId,
    kind: 'peb.deny_contract_promotion.refused',
    outcome: 'refused',
    actor: 'peb-kernel',
    decision_class: 'deny_contract_promotion',
    binding_owner: 'resolution',
    authority_level: 'narrowly_binding',
    authorization_ref: '986ec482',
    decision_id: 'decision-negative-w9',
    proposition_id: 'pg:ready',
    subject_id: 'candidate-1',
    work_item_id: 'work-1',
    disposition: 'refused',
    evidence_ids: ['evidence-negative-1'],
    replay_context: 'attempt-negative-1',
    as_of: '2026-09-05T12:00:00Z',
    evaluation_fingerprint: 'sha256:' + 'ef'.repeat(32),
    lineage_fingerprint: 'sha256:' + '12'.repeat(32),
    evaluator_id: 'peb-evaluator',
    evaluator_version: 'peb-evaluator-v1',
    contract_id: 'governed-trigger.v1',
    contract_version: 1,
    law_id: 'deny-contract-promotion-law',
    law_version: '2026-09-02',
    bridge_id: 'resolution-keychains-bridge',
    bridge_version: '1',
    read_set: { manifest_id: 'negative-manifest-v1', digest: 'sha256:negative' },
    observation_window: {
      activation_ref: 'w9-activation-1',
      activated_at: '2026-09-05T12:00:00Z',
      window_start: '2026-09-05T12:00:00Z',
      window_end: '2026-09-05T14:00:00Z',
      observed_at: '2026-09-05T12:45:00Z',
    },
  }

  const res = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggerEvent }),
  })
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.ok, true)
  assert.equal(body.deduplicated, false)
  assert.equal(body.trigger.outcome, 'refused')
  assert.equal(body.decision_context.decision_class, 'deny_contract_promotion')
  assert.equal(body.decision_context.decision.outcome, 'refused')
  assert.equal(body.decision_context.decision.disposition, 'refused')
  assert.deepEqual(body.decision_context.source_read_set, triggerEvent.read_set)
  assert.equal(body.decision_context.observation_window.phase, 'post_activation')
  assert.equal(body.decision_context.observation_window.in_window, true)
  assert.equal(body.decision_context.rollback.status, null)

  const after = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  assert.ok(after.latestSnapshot > before.latestSnapshot)
  const replay = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggerEvent }),
  })
  assert.equal(replay.status, 200)
  const replayBody = await replay.json()
  assert.equal(replayBody.deduplicated, true)
  assert.equal(replayBody.version, body.version)

  const rewind = await (await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${body.version}`)).json()
  assert.equal(rewind.ok, true)
  assert.deepEqual(rewind.decision_context, body.decision_context)

  const transitions = await (await fetch(`${BASE}/keychain-snapshot/agent-records/transitions?limit=200`)).json()
  const transition = transitions.items.find((item) => item.source_namespace === sourceNamespace)
  assert.ok(transition)
  assert.equal(transition.checkpoint_status, 'delivered')
  assert.equal(transition.snapshot_version, body.version)
  assert.deepEqual(transition.read_set, triggerEvent.read_set)

  try {
    const db = mongo.db('keychains')
    assert.equal(await db.collection('ar_snapshots').countDocuments({
      source_namespace: sourceNamespace,
      source_event_id: sourceEventId,
    }), 1)
  } finally {
    const db = mongo.db('keychains')
    const checkpoint = await db.collection('ar_snapshots').findOne({
      source_namespace: sourceNamespace,
      source_event_id: sourceEventId,
    })
    await db.collection('transitions').deleteMany({ source_namespace: sourceNamespace })
    await db.collection('ar_drift_findings').deleteMany({ checkpoint_id: checkpoint?.checkpoint_id })
    await db.collection('checkpoint_entries').deleteMany({ checkpoint_id: checkpoint?.checkpoint_id })
    await db.collection('ar_snapshots').deleteMany({ source_namespace: sourceNamespace })
    if (previousActive) {
      await db.collection('active_checkpoints').replaceOne(
        { _id: 'agent-records' },
        previousActive,
        { upsert: true },
      )
    } else {
      await db.collection('active_checkpoints').deleteOne({ _id: 'agent-records' })
    }
    await db.collection('entries').deleteMany({})
    if (previousEntries.length > 0) await db.collection('entries').insertMany(previousEntries)
    await db.collection('record_type_state').deleteMany({})
    if (previousRecordTypeState.length > 0) {
      await db.collection('record_type_state').insertMany(previousRecordTypeState)
    }
    await mongo.close()
  }
})


test('observation window classifies pre-activation and outside-window evidence without rewriting history', async () => {
  const sourceNamespace = `keychains-window-${process.pid}-${Date.now()}`
  const mongo = new MongoClient(process.env.MONGO_URL || 'mongodb://localhost:27017')
  const events = [
    {
      source_event_id: 'pre-activation',
      observed_at: '2026-09-04T23:00:00Z',
      expectedPhase: 'pre_activation',
      expectedInWindow: false,
      outcome: 'committed',
    },
    {
      source_event_id: 'outside-window',
      observed_at: '2026-09-05T15:00:00Z',
      expectedPhase: 'outside_window',
      expectedInWindow: false,
      outcome: 'rolled_back',
    },
  ]
  let previousActive = null
  let previousEntries = null
  let previousRecordTypeState = null
  const checkpointIds = []
  try {
    await mongo.connect()
    const db = mongo.db('keychains')
    previousActive = await db.collection('active_checkpoints').findOne({ _id: 'agent-records' })
    previousEntries = await db.collection('entries').find({}).toArray()
    previousRecordTypeState = await db.collection('record_type_state').find({}).toArray()

    for (const item of events) {
      const triggerEvent = {
        source_namespace: sourceNamespace,
        source_event_id: item.source_event_id,
        kind: `peb.deny_contract_promotion.${item.outcome}`,
        outcome: item.outcome,
        actor: 'broker-smoke',
        decision_class: 'deny_contract_promotion',
        binding_owner: 'resolution',
        authority_level: 'narrowly_binding',
        authorization_ref: '986ec482',
        activation_ref: 'w9-activation-1',
        activated_at: '2026-09-05T12:00:00Z',
        decision_id: `decision-${item.source_event_id}`,
        disposition: item.outcome === 'committed' ? 'allow' : item.outcome,
        evidence_ids: [`evidence-${item.source_event_id}`],
        replay_context: `replay-${item.source_event_id}`,
        as_of: item.observed_at,
        evaluation_fingerprint: `sha256:${item.source_event_id === 'pre-activation' ? 'aa' : 'bb'}`.repeat(32),
        lineage_fingerprint: 'sha256:' + 'cc'.repeat(32),
        evaluator_id: 'peb-evaluator',
        evaluator_version: 'peb-evaluator-v1',
        contract_id: 'governed-trigger.v1',
        contract_version: 1,
        law_id: 'deny-contract-promotion-law',
        law_version: '2026-09-02',
        bridge_id: 'resolution-keychains-bridge',
        bridge_version: '1',
        rollback_ref: item.outcome === 'rolled_back' ? 'rollback-w9' : null,
        rollback_status: item.outcome === 'rolled_back' ? 'executed' : null,
        rollback_evidence_ids: item.outcome === 'rolled_back' ? ['rollback-evidence-w9'] : null,
        observation_window: {
          activation_ref: 'w9-activation-1',
          activated_at: '2026-09-05T12:00:00Z',
          window_start: '2026-09-05T12:00:00Z',
          window_end: '2026-09-05T14:00:00Z',
          observed_at: item.observed_at,
        },
        read_set: { manifest_id: `window-${item.source_event_id}`, digest: 'sha256:window' },
      }
      const response = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggerEvent }),
      })
      assert.equal(response.status, 200)
      const body = await response.json()
      assert.equal(body.ok, true)
      assert.equal(body.decision_context.observation_window.phase, item.expectedPhase)
      assert.equal(body.decision_context.observation_window.in_window, item.expectedInWindow)
      if (item.outcome === 'rolled_back') {
        assert.equal(body.decision_context.rollback.reference, 'rollback-w9')
        assert.equal(body.decision_context.rollback.status, 'executed')
      }
      checkpointIds.push(body.decision_context.checkpoint_reference.checkpoint_id)

      const replay = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggerEvent }),
      })
      assert.equal(replay.status, 200)
      const replayBody = await replay.json()
      assert.equal(replayBody.deduplicated, true)
      assert.equal(replayBody.version, body.version)
    }

    const filtered = await (await fetch(
      `${BASE}/keychain-snapshot/agent-records/transitions?activation_ref=w9-activation-1&observation_status=outside_window&outcome=rolled_back`,
    )).json()
    assert.equal(filtered.items.length, 1)
    assert.equal(filtered.items[0].source_namespace, sourceNamespace)
    assert.equal(filtered.items[0].observation_window.phase, 'outside_window')
  } finally {
    const db = mongo.db('keychains')
    await db.collection('transitions').deleteMany({ source_namespace: sourceNamespace })
    if (checkpointIds.length) {
      await db.collection('ar_drift_findings').deleteMany({ checkpoint_id: { $in: checkpointIds } })
      await db.collection('checkpoint_entries').deleteMany({ checkpoint_id: { $in: checkpointIds } })
      await db.collection('ar_snapshots').deleteMany({ checkpoint_id: { $in: checkpointIds } })
    }
    if (previousActive) {
      await db.collection('active_checkpoints').replaceOne({ _id: 'agent-records' }, previousActive, { upsert: true })
    } else {
      await db.collection('active_checkpoints').deleteOne({ _id: 'agent-records' })
    }
    await db.collection('entries').deleteMany({})
    if (previousEntries.length) await db.collection('entries').insertMany(previousEntries)
    await db.collection('record_type_state').deleteMany({})
    if (previousRecordTypeState.length) await db.collection('record_type_state').insertMany(previousRecordTypeState)
    await mongo.close()
  }
})

test('rewind returns a committed state vector and rejects invalid versions', async () => {
  const status = await (await fetch(`${BASE}/keychain-snapshot/agent-records/status`)).json()
  const rewind = await (await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${status.latestSnapshot}`)).json()
  assert.equal(rewind.ok, true)
  assert.equal(rewind.version, status.latestSnapshot)
  assert.ok(rewind.state_vector)
  assert.ok(rewind.recordTypeCount > 0)

  const invalid = await (await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=0`)).json()
  assert.equal(invalid.ok, false)
})

test('decision checkpoint preserves owner, authorization, and evaluator read-set provenance', async () => {
  const sourceNamespace = `keychains-read-set-${process.pid}-${Date.now()}`
  const sourceEventId = randomUUID()
  const triggerEvent = {
    source_namespace: sourceNamespace,
    source_event_id: sourceEventId,
    kind: 'peb.deny_contract_promotion.committed',
    outcome: 'committed',
    actor: 'peb-kernel',
    decision_class: 'deny_contract_promotion',
    binding_owner: 'resolution',
    authority_level: 'narrowly_binding',
    authorization_ref: '986ec482',
    activation_ref: 'w9-activation-1',
    activated_at: '2026-09-05T12:00:00Z',
    grant_id: '05d0fe54',
    authority_ref: 'g1-verdict-986ec482',
    decision_id: 'decision-w9-observation',
    proposition_id: 'pg:ready',
    subject_id: 'candidate-1',
    work_item_id: 'work-1',
    disposition: 'allow',
    evidence_ids: ['evidence-1'],
    replay_context: 'attempt-1',
    as_of: '2026-09-05T12:00:00Z',
    evaluation_fingerprint: 'sha256:' + 'ab'.repeat(32),
    lineage_fingerprint: 'sha256:' + 'cd'.repeat(32),
    rollback_ref: 'rollback-plan-w9',
    rollback_status: 'armed',
    rollback_evidence_ids: ['rollback-evidence-1'],
    observation_window: {
      activation_ref: 'w9-activation-1',
      activated_at: '2026-09-05T12:00:00Z',
      window_start: '2026-09-05T12:00:00Z',
      window_end: '2026-09-05T14:00:00Z',
      observed_at: '2026-09-05T12:30:00Z',
    },
    evaluator_id: 'peb-evaluator',
    evaluator_version: 'peb-evaluator-v1',
    contract_id: 'governed-trigger.v1',
    contract_version: 1,
    law_id: 'deny-contract-promotion-law',
    law_version: '2026-09-02',
    bridge_id: 'resolution-keychains-bridge',
    bridge_version: '1',
    read_set: {
      manifest_id: 'keychains-read-set-manifest-v1',
      digest: 'sha256:keychains-read-set-test',
      source_revision: 'test-revision-1',
    },
    payload: {
      decision: 'committed',
      activation_ref: 'w9-activation-1',
      activated_at: '2026-09-05T12:00:00Z',
      grant_id: '05d0fe54',
      authority_ref: 'g1-verdict-986ec482',
      decision_id: 'decision-w9-observation',
      proposition_id: 'pg:ready',
      subject_id: 'candidate-1',
      work_item_id: 'work-1',
      disposition: 'allow',
      evidence_ids: ['evidence-1'],
      replay_context: 'attempt-1',
      as_of: '2026-09-05T12:00:00Z',
      evaluation_fingerprint: 'sha256:' + 'ab'.repeat(32),
      lineage_fingerprint: 'sha256:' + 'cd'.repeat(32),
      rollback_ref: 'rollback-plan-w9',
      rollback_status: 'armed',
      rollback_evidence_ids: ['rollback-evidence-1'],
      observation_window: {
        activation_ref: 'w9-activation-1',
        activated_at: '2026-09-05T12:00:00Z',
        window_start: '2026-09-05T12:00:00Z',
        window_end: '2026-09-05T14:00:00Z',
        observed_at: '2026-09-05T12:30:00Z',
      },
    },
  }
  const mongo = new MongoClient(process.env.MONGO_URL || 'mongodb://localhost:27017')
  let checkpointId = null
  let previousActive = null
  let previousEntries = null
  let previousRecordTypeState = null

  try {
    await mongo.connect()
    const db = mongo.db('keychains')
    previousActive = await db.collection('active_checkpoints').findOne({ _id: 'agent-records' })
    previousEntries = await db.collection('entries').find({}).toArray()
    previousRecordTypeState = await db.collection('record_type_state').find({}).toArray()

    const res = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ triggerEvent }),
    })
    assert.equal(res.status, 200)
    const body = await res.json()
    assert.equal(body.ok, true)
    assert.equal(body.deduplicated, false)
    const expectedDecisionContext = {
      schema_version: 1,
      checkpoint_class: 'decision',
      decision_class: 'deny_contract_promotion',
      source_namespace: sourceNamespace,
      source_event_id: sourceEventId,
      binding_decision_owner: {
        role: 'resolution',
        authority_level: 'narrowly_binding',
        authorization_ref: '986ec482',
      },
      activation: {
        authorization_ref: '986ec482',
        activation_ref: 'w9-activation-1',
        grant_id: '05d0fe54',
        authority_ref: 'g1-verdict-986ec482',
        activated_at: '2026-09-05T12:00:00Z',
      },
      decision: {
        id: 'decision-w9-observation',
        proposition_id: 'pg:ready',
        subject_id: 'candidate-1',
        work_item_id: 'work-1',
        disposition: 'allow',
        outcome: 'committed',
        evidence_ids: ['evidence-1'],
        replay_context: 'attempt-1',
        as_of: '2026-09-05T12:00:00Z',
        evaluation_fingerprint: 'sha256:' + 'ab'.repeat(32),
        lineage_fingerprint: 'sha256:' + 'cd'.repeat(32),
      },
      rollback: {
        reference: 'rollback-plan-w9',
        status: 'armed',
        evidence_ids: ['rollback-evidence-1'],
      },
      observation_window: {
        activation_ref: 'w9-activation-1',
        activated_at: '2026-09-05T12:00:00Z',
        window_start: '2026-09-05T12:00:00Z',
        window_end: '2026-09-05T14:00:00Z',
        observed_at: '2026-09-05T12:30:00Z',
        in_window: true,
        phase: 'post_activation',
      },
      evaluator: { id: 'peb-evaluator', version: 'peb-evaluator-v1' },
      contract: { id: 'governed-trigger.v1', version: 1 },
      law: { id: 'deny-contract-promotion-law', version: '2026-09-02' },
      bridge: { id: 'resolution-keychains-bridge', version: '1' },
      source_read_set: triggerEvent.read_set,
      read_set_manifest: {
        manifest_id: 'keychains-read-set-manifest-v1',
        digest: 'sha256:keychains-read-set-test',
      },
    }
    assert.deepEqual(body.decision_context, {
      ...expectedDecisionContext,
      checkpoint_reference: body.decision_context.checkpoint_reference,
    })
    assert.match(body.decision_context.checkpoint_reference.checkpoint_id, /.+/)
    assert.equal(body.decision_context.checkpoint_reference.version, body.version)

    checkpointId = body.trigger && body.trigger.checkpoint_id
    const rewind = await (await fetch(`${BASE}/keychain-snapshot/agent-records/rewind?at=${body.version}`)).json()
    assert.equal(rewind.ok, true)
    assert.deepEqual(rewind.decision_context, body.decision_context)

    const transitions = await (await fetch(`${BASE}/keychain-snapshot/agent-records/transitions?limit=200`)).json()
    const transition = transitions.items.find((item) => item.source_namespace === sourceNamespace)
    assert.ok(transition)
    assert.deepEqual(transition.read_set, triggerEvent.read_set)
    assert.equal(transition.checkpoint_status, 'delivered')
  } finally {
    const db = mongo.db('keychains')
    if (!checkpointId) {
      const checkpoint = await db.collection('ar_snapshots').findOne({
        source_namespace: sourceNamespace,
        source_event_id: sourceEventId,
      })
      checkpointId = checkpoint?.checkpoint_id || null
    }
    await db.collection('transitions').deleteMany({
      $or: [
        { source_namespace: sourceNamespace },
        { keychain_event_id: `${sourceNamespace}:${sourceEventId}` },
      ],
    })
    await db.collection('ar_drift_findings').deleteMany({ checkpoint_id: checkpointId })
    await db.collection('checkpoint_entries').deleteMany({ checkpoint_id: checkpointId })
    await db.collection('ar_snapshots').deleteMany({
      source_namespace: sourceNamespace,
      source_event_id: sourceEventId,
    })
    if (previousActive) {
      await db.collection('active_checkpoints').replaceOne(
        { _id: 'agent-records' },
        previousActive,
        { upsert: true },
      )
    } else {
      await db.collection('active_checkpoints').deleteOne({ _id: 'agent-records' })
    }
    if (previousEntries) {
      await db.collection('entries').deleteMany({})
      if (previousEntries.length > 0) await db.collection('entries').insertMany(previousEntries)
    }
    if (previousRecordTypeState) {
      await db.collection('record_type_state').deleteMany({})
      if (previousRecordTypeState.length > 0) await db.collection('record_type_state').insertMany(previousRecordTypeState)
    }
    await mongo.close()
  }
})

test('SOL outbox events are delivered into Keychains and replayed idempotently', async () => {
  const pool = new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: 'sol',
  })
  const sourceNamespace = `sol-e2e-${process.pid}-${Date.now()}`
  const sourceEventId = randomUUID()
  const eventKind = 'sol.e2e.transition.committed'
  const payload = { test_run: sourceNamespace, decision: 'committed' }
  const readSet = { manifest_id: 'sol-e2e-manifest-v1', digest: 'sha256:test-only' }

  try {
    await pool.query(
      `INSERT INTO resolution.keychain_event_outbox
         (source_namespace, source_event_id, event_kind, outcome, actor, read_set, payload)
       VALUES ($1, $2, $3, 'committed', $4, $5::jsonb, $6::jsonb)`,
      [sourceNamespace, sourceEventId, eventKind, 'broker-smoke', JSON.stringify(readSet), JSON.stringify(payload)],
    )

    const delivered = await waitFor(async () => {
      const result = await pool.query(
        `SELECT source_namespace, source_event_id, checkpoint_status, delivery_attempts
           FROM resolution.keychain_event_outbox
          WHERE source_namespace = $1 AND source_event_id = $2`,
        [sourceNamespace, sourceEventId],
      )
      return result.rows[0]?.checkpoint_status === 'delivered' ? result.rows[0] : null
    }, 'SOL outbox delivery')
    assert.equal(delivered.source_namespace, sourceNamespace)
    assert.equal(delivered.source_event_id, sourceEventId)
    assert.ok(delivered.delivery_attempts >= 1)

    const transition = await waitFor(async () => {
      const result = await (await fetch(`${BASE}/keychain-snapshot/agent-records/transitions?limit=200`)).json()
      return result.items.find((item) =>
        item.source_namespace === sourceNamespace && item.source_event_id === sourceEventId,
      )
    }, 'SOL Keychains transition')
    assert.equal(transition.checkpoint_status, 'delivered')
    assert.equal(transition.kind, eventKind)
    assert.equal(transition.source_namespace, sourceNamespace)
    assert.equal(transition.source_event_id, sourceEventId)
    assert.ok(Number.isInteger(transition.snapshot_version))
    assert.ok(transition.checkpoint_id)
    assert.deepEqual(transition.payload, payload)
    assert.deepEqual(transition.read_set, readSet)

    const replay = await fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        triggerEvent: {
          source_namespace: sourceNamespace,
          source_event_id: sourceEventId,
          kind: eventKind,
          outcome: 'committed',
          actor: 'broker-smoke-replay',
          payload,
          read_set: readSet,
        },
      }),
    })
    assert.equal(replay.status, 200)
    const replayBody = await replay.json()
    assert.equal(replayBody.ok, true)
    assert.equal(replayBody.deduplicated, true)
    assert.equal(replayBody.version, transition.snapshot_version)

    const concurrentReplays = await Promise.all([
      fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggerEvent: {
          source_namespace: sourceNamespace,
          source_event_id: sourceEventId,
          kind: eventKind,
          outcome: 'committed',
        } }),
      }),
      fetch(`${BASE}/keychain-snapshot/agent-records/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggerEvent: {
          source_namespace: sourceNamespace,
          source_event_id: sourceEventId,
          kind: eventKind,
          outcome: 'committed',
        } }),
      }),
    ])
    const concurrentBodies = await Promise.all(concurrentReplays.map(async (response) => {
      assert.equal(response.status, 200)
      return response.json()
    }))
    assert.ok(concurrentBodies.every((body) => body.ok && body.deduplicated))
    assert.ok(concurrentBodies.every((body) => body.version === transition.snapshot_version))

    const afterReplay = await pool.query(
      `SELECT checkpoint_status, delivery_attempts
         FROM resolution.keychain_event_outbox
        WHERE source_namespace = $1 AND source_event_id = $2`,
      [sourceNamespace, sourceEventId],
    )
    assert.equal(afterReplay.rows[0].checkpoint_status, 'delivered')
    assert.equal(afterReplay.rows[0].delivery_attempts, delivered.delivery_attempts)
  } finally {
    await pool.query(
      `DELETE FROM resolution.keychain_event_outbox
        WHERE source_namespace = $1 AND source_event_id = $2`,
      [sourceNamespace, sourceEventId],
    )
    await pool.end()
  }
})

// ═══════════════════════════════════════════════════════════════════════
// M2 — execution read-catalog parity: the full legacy execution-srv
// surface, aliased 1:1 under /api/workers/execution. The differential
// parity family (same probe against both surfaces over one shared PG)
// lives in tests/legacy-parity.test.js so CI can run it where a real
// legacy surface exists (broker-pg-integration class).
