/**
 * Legacy execution-surface parity tests (ruling ffa4ffc5 family).
 *
 * Extracted verbatim from broker-smoke.test.js (lines 790-1018 of the
 * pre-split file): every test here compares the moleculer worker surface
 * (/api/workers/execution/*) against the legacy execution-srv surface
 * (/api/execution/*) over the SAME PostgreSQL database — the v3 parity
 * contract. The family needs a seeded execution schema on both sides plus
 * a running legacy surface, which is what the broker-pg-integration CI
 * class provides (postgres:17 + nexus-ci-bootstrap.sql + execution-srv).
 *
 * broker-smoke keeps the boot-level and keychain-checkpoint tests; this
 * file owns the legacy-parity family so it can run in CI where a real
 * legacy surface exists.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { randomUUID } = require('node:crypto')
const { spawn } = require('node:child_process')
const path = require('node:path')
const dotenv = require('dotenv')
const { getEphemeralPort } = require('./helpers/ephemeral-ports')

const BROKER_DIR = path.resolve(__dirname, '..')
// OS-assigned ports by default (see tests/helpers/ephemeral-ports.js);
// HARNESS_FIXED_PORTS=1 restores the historical 4088/4089.
const FIXED_SERVICE_PORT = process.env.TEST_SERVICE_PORT || '4088'
const FIXED_PTY_WS_PORT = process.env.TEST_PTY_WS_PORT || String(Number(FIXED_SERVICE_PORT) + 1)
const LEGACY_BASE = process.env.LEGACY_EXECUTION_URL || 'http://localhost:3110'
let TEST_PORT = null
let TEST_PTY_WS_PORT = null
let BASE = null

dotenv.config({ path: path.join(BROKER_DIR, '.env') })

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
    } catch {}
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('broker did not become healthy within 30s')
}

function stopBroker() {
  if (!child) return
  try {
    child.kill('SIGTERM')
  } catch {}
  child = null
}

test.before(async () => {
  await startBroker()
})

test.after(async () => {
  stopBroker()
})

async function jsonOr404(url) {
  const res = await fetch(url)
  if (res.status === 404) return { __status: 404 }
  assert.equal(res.status, 200, `expected 200 from ${url}, got ${res.status}`)
  return res.json()
}

test('GET /api/workers/execution/requests shape matches legacy list contract', async () => {
  const ours = await jsonOr404(`${BASE}/workers/execution/requests?limit=3`)
  const theirs = await jsonOr404(`${LEGACY_BASE}/api/execution/requests?limit=3`)
  if (theirs.__status === 404 && ours.__status === 404) return // both empty DBs
  assert.equal(ours.total, theirs.total)
  assert.equal(ours.limit, theirs.limit)
  assert.equal(ours.offset, theirs.offset)
  assert.equal(ours.items.length, theirs.items.length)
  if (theirs.items.length > 0) {
    // DB-native field names must be identical — no mapping layer.
    assert.deepEqual(Object.keys(ours.items[0]).sort(), Object.keys(theirs.items[0]).sort())
  }
})

test('receipts list filters by type identically to legacy', async () => {
  const theirs = await jsonOr404(`${LEGACY_BASE}/api/execution/receipts?limit=5`)
  if (theirs.__status === 404) return
  const q = theirs.items.length > 0 && theirs.items[0].type ? `&type=${encodeURIComponent(theirs.items[0].type)}` : ''
  // Compare filtered-vs-filtered on both surfaces. (The previous form compared
  // the broker's FILTERED count against legacy's UNFILTERED count — it only
  // passed while the head type had >=5 rows, which the first live
  // EXECUTION_COMPLETE receipts exposed. Data-sensitivity, not parity drift.)
  const theirsFiltered = await jsonOr404(`${LEGACY_BASE}/api/execution/receipts?limit=5${q}`)
  const ours = await jsonOr404(`${BASE}/workers/execution/receipts?limit=5${q}`)
  assert.equal(ours.items.length, theirsFiltered.items.length)
  if (theirsFiltered.items.length > 0 && ours.items.length > 0) {
    assert.deepEqual(Object.keys(ours.items[0]).sort(), Object.keys(theirsFiltered.items[0]).sort())
  }
})

test('malformed UUID returns the legacy 400, not a 500', async () => {
  const res = await fetch(`${BASE}/workers/execution/requests/not-a-uuid/state`)
  assert.equal(res.status, 400)
  const body = await res.json()
  assert.match(body.error || body.message || '', /UUID/)
})

test('unknown UUID returns the legacy 404', async () => {
  const res = await fetch(`${BASE}/workers/execution/requests/00000000-0000-0000-0000-000000000000/state`)
  assert.equal(res.status, 404)
})

test('stale leases and status distribution match legacy shapes', async () => {
  const oursStale = await jsonOr404(`${BASE}/workers/execution/leases/stale`)
  const theirsStale = await jsonOr404(`${LEGACY_BASE}/api/execution/leases/stale`)
  if (theirsStale.__status !== 404 && oursStale.__status !== 404) {
    assert.equal(oursStale.count, theirsStale.count)
    assert.ok(Array.isArray(oursStale.stale_leases))
  }
  const oursDist = await jsonOr404(`${BASE}/workers/execution/health/status-distribution`)
  const theirsDist = await jsonOr404(`${LEGACY_BASE}/api/execution/health/status-distribution`)
  if (theirsDist.__status !== 404) {
    assert.deepEqual(oursDist.requests, theirsDist.requests)
    assert.deepEqual(oursDist.receipts_by_type, theirsDist.receipts_by_type)
  }
})

test('witnessed-runs requires both query params (legacy 400 parity)', async () => {
  const res = await fetch(`${BASE}/workers/execution/witnessed-runs`)
  assert.equal(res.status, 400)
  const body = await res.json()
  assert.match(body.error || body.message || '', /workflow_instance_id and node_id are required/)
})

test('witnessed-runs parity — shared 200 with identical projection JSON (ruling ffa4ffc5)', async () => {
  // v3 (ruling ffa4ffc5): the phantom metadata legs are gone — the family no
  // longer 500s. Parity now means: with a seeded business_key match, BOTH
  // surfaces return 200 and byte-identical projection JSON (W3.05 AC4), with
  // assessment/evidence sourced from resolution.* and envelope/manifest/law/
  // replay rendering null. The 404 path (no such business_key) is also
  // asserted for parity.
  const { Pool } = require('pg')
  const key = `witnessed-v3-smoke-${randomUUID()}`
  const pool = new Pool({
    // Defaults mirror services/execution.worker.ts so the suite runs with
    // only MONGO_URL set (titanium PG is the parity-shared database).
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: process.env.PG_DB_NAME || 'nexus',
  })
  const uuid = () => randomUUID()
  const attemptId = uuid()
  const evidenceId = uuid()
  const claimId = uuid()
  const receiptId = uuid()
  const leaseId = uuid()
  let requestId = null
  try {
    await pool.query('BEGIN')
    await pool.query("SET LOCAL search_path TO execution, resolution, public")
    const req = await pool.query(
      "INSERT INTO execution.requests (business_key, status) VALUES ($1, 'COMPILED') RETURNING id",
      [key],
    )
    requestId = req.rows[0].id
    await pool.query(
      "INSERT INTO execution.leases (id, request_id, executor_id, status, ttl_seconds, acquired_at, expires_at) VALUES ($1, $2, 'smoke', 'ACTIVE', 3600, now(), now() + interval '1 hour')",
      [leaseId, requestId],
    )
    await pool.query(
      "INSERT INTO execution.attempts (id, request_id, lease_id, executor_id, status) VALUES ($1, $2, $3, 'smoke', 'RUNNING')",
      [attemptId, requestId, leaseId],
    )
    // resolution.evidence is append-only (immutable trigger) with a
    // content-unique index (source_system, evidence_kind, source_hash):
    // seed idempotently by CONTENT — reuse the persisted row across runs.
    let seededEvidenceId = evidenceId
    const ev = await pool.query(
      "SELECT id FROM resolution.execution_evidence WHERE source_system = 'git-verifier' AND evidence_kind = 'git_commit' AND source_hash = 'smoke-hash'",
    )
    if (ev.rows.length > 0) {
      seededEvidenceId = ev.rows[0].id
    } else {
      await pool.query(
        "INSERT INTO resolution.execution_evidence (id, evidence_key, evidence_kind, source_system, source_hash, captured_at, captured_by) VALUES ($1, $2, 'git_commit', 'git-verifier', 'smoke-hash', now(), 'broker-smoke')",
        [evidenceId, `witnessed-v3-smoke:${key}`],
      )
    }
    await pool.query(
      "INSERT INTO resolution.execution_claim (id, claim_key, subject_kind, predicate, disposition, declared_by, attempt_id) VALUES ($1, $2, 'execution_attempt', 'witnessed-run-smoke', 'Proposed', 'broker-smoke', $3)",
      [claimId, `witnessed-v3-smoke:${key}`, attemptId],
    )
    await pool.query(
      "INSERT INTO resolution.execution_admission_receipt (id, peb_transaction_id, claim_id, evidence_id, evidence_kind, source_system, policy_version_hash, lease_id, grant_id, attempt_id, admitted, reason) VALUES ($1, $2, $3, $4, 'git_commit', 'git-verifier', 'smoke-policy', $5, 'smoke-grant', $6, true, 'smoke fixture: admitted')",
      [receiptId, uuid(), claimId, seededEvidenceId, leaseId, attemptId],
    )
    await pool.query('COMMIT')

    const q = `?workflow_instance_id=${encodeURIComponent(key)}&node_id=smoke`
    const ours = await fetch(`${BASE}/workers/execution/witnessed-runs${q}`)
    const theirs = await fetch(`${LEGACY_BASE}/api/execution/witnessed-runs${q}`)
    assert.equal(ours.status, 200, `broker witnessed-runs should 200, got ${ours.status}`)
    assert.equal(theirs.status, 200, `legacy witnessed-runs should 200, got ${theirs.status}`)
    const oursBody = await ours.json()
    const theirsBody = await theirs.json()
    assert.deepEqual(oursBody, theirsBody)
    const proj = oursBody.projection
    assert.equal(proj.receipts.pebAdmission != null, true, 'peb admission correlated')
    assert.equal(proj.assessment.disposition, true, 'assessment from admission receipt')
    assert.equal(proj.assessment.status, 'admitted', 'assessment status derived')
    assert.equal(proj.evidence.ids.length > 0, true, 'evidence id from resolution.execution_evidence')
    assert.equal(proj.envelope.id, null, 'envelope renders null (v3)')
    assert.equal(proj.manifest.id, null, 'manifest renders null (v3)')
    assert.equal(proj.status, 'missing_lineage', 'status honest: envelope/manifest not produced yet')

    const qMiss = `?workflow_instance_id=witnessed-v3-smoke-absent-${randomUUID()}&node_id=smoke`
    const oursMiss = await fetch(`${BASE}/workers/execution/witnessed-runs${qMiss}`)
    const theirsMiss = await fetch(`${LEGACY_BASE}/api/execution/witnessed-runs${qMiss}`)
    assert.equal(oursMiss.status, 404)
    assert.equal(theirsMiss.status, 404)

    // Governed projection surface (/projections/witnessed-runs) — parity on
    // the SAME receipt-bearing fixture. Regression guard: the projection
    // handler's SELECT omitted the v3 assessment/evidence columns in #226,
    // which rendered assessment null on legacy for every receipt-bearing run
    // (exposed by the first marker adoption, #229). generatedAt is stripped
    // before comparison — it is a per-request timestamp, not projection data.
    const oursP = await fetch(`${BASE}/workers/execution/projections/witnessed-runs${q}`)
    const theirsP = await fetch(`${LEGACY_BASE}/api/execution/projections/witnessed-runs${q}`)
    assert.equal(oursP.status, 200, `broker projections/witnessed-runs should 200, got ${oursP.status}`)
    assert.equal(theirsP.status, 200, `legacy projections/witnessed-runs should 200, got ${theirsP.status}`)
    const oursPBody = await oursP.json()
    const theirsPBody = await theirsP.json()
    delete oursPBody.generatedAt
    delete theirsPBody.generatedAt
    assert.deepEqual(oursPBody, theirsPBody, 'projection parity on receipt-bearing fixture')
    assert.equal(oursPBody.projectionVersion, 3)
    assert.equal(oursPBody.assessment.status, 'admitted', 'projection assessment from admission receipt')
    assert.equal(oursPBody.identities.evidenceIds.length > 0, true, 'projection evidenceIds populated')
    assert.equal(oursPBody.status, 'missing_lineage', 'projection status honest')
    assert.equal(oursPBody.missingLineage.includes('evidence_ids'), false, 'evidence not missing when receipt joined')
    const oursPMiss = await fetch(`${BASE}/workers/execution/projections/witnessed-runs${qMiss}`)
    const theirsPMiss = await fetch(`${LEGACY_BASE}/api/execution/projections/witnessed-runs${qMiss}`)
    assert.equal(oursPMiss.status, 404)
    assert.equal(theirsPMiss.status, 404)
  } finally {
    // Teardown in FK-safe order (claim → receipt → attempt → lease → request).
    // The immutable evidence row persists by design; inert without its receipt.
    try {
      await pool.query('BEGIN')
      await pool.query("SET LOCAL search_path TO execution, resolution, public")
      await pool.query('DELETE FROM resolution.execution_admission_receipt WHERE id = $1', [receiptId])
      await pool.query('DELETE FROM resolution.execution_claim WHERE id = $1', [claimId])
      await pool.query('DELETE FROM execution.attempts WHERE id = $1', [attemptId])
      await pool.query('DELETE FROM execution.leases WHERE id = $1', [leaseId])
      if (requestId) await pool.query('DELETE FROM execution.requests WHERE id = $1', [requestId])
      await pool.query('COMMIT')
    } catch {
      await pool.query('ROLLBACK').catch(() => {})
    }
    await pool.end().catch(() => {})
  }
})

test('governance metrics snapshot has the registry shape', async () => {
  const body = await jsonOr404(`${BASE}/workers/execution/metrics`)
  if (body.__status === 404) return
  assert.ok('counters' in body)
  assert.ok('latencies' in body)
  assert.ok('generatedAt' in body)
})

test('integrity scan returns named pathology kinds', async () => {
  const body = await jsonOr404(`${BASE}/workers/execution/health/integrity-scan`)
  if (body.__status === 404) return
  assert.equal(body.schema, 'execution')
  const kinds = body.scans.map((s) => s.kind)
  assert.ok(kinds.includes('orphan_lease_request_mismatch'))
  assert.ok(kinds.includes('receipt_attempt_mismatch'))
})

test('rich health (legacy GET /health) matches legacy per-status shape', async () => {
  // Post-deploy parity fix: the initial catalog aliased only the simple
  // health; the legacy router's rich probe (scanned_at + per-status counts)
  // was missing. Normalized diff on scanned_at (clock skew between surfaces).
  const norm = (b) => JSON.stringify({ ...b, scanned_at: '<TS>' })
  const ours = await jsonOr404(`${BASE}/workers/execution/health`)
  const theirs = await jsonOr404(`${LEGACY_BASE}/api/execution/health`)
  if (theirs.__status === 404 || ours.__status === 404) return
  assert.equal(norm(ours), norm(theirs))
})
