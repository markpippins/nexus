/**
 * Bridge E2E (ruling ac38fa9b phase B): invoke harness_bridge.py exactly as
 * the worker does — temp git repo on stdin — and verify the full producer
 * cycle against live PG + the PEB kernel.
 *
 * Mirrors the PR #224 live-test precedent (ruling 6677c394 authorized real
 * producer rows for the test attempt id). Rows are keyed to the synthetic
 * attempt uuid and correlate to nothing else. Skips cleanly when the kernel
 * or PG is unreachable (CI-safe; run locally where both are up).
 */
const { test, describe } = require('node:test')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { mkdtempSync, mkdirSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const { randomUUID } = require('node:crypto')
const dotenv = require('dotenv')
const { Pool } = require('pg')

const BROKER_DIR = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(BROKER_DIR, '..', '..')
const BRIDGE = path.join(REPO_ROOT, 'python', 'nexus_core', 'wrp', 'harness_bridge.py')
dotenv.config({ path: path.join(BROKER_DIR, '.env') })

// Kernel base URL is env-overridable (defaults to the historical
// localhost:8098). CI and parallel local runs retarget it to an
// OS-assigned port; the python bridge spawn below inherits this same
// env (git_claim_producer reads PEB_BASE_URL), so probe and producer
// can never disagree about where the kernel lives.
const PEB_BASE_URL = process.env.PEB_BASE_URL || 'http://localhost:8098'

function kernelUp() {
  // The kernel's health path is /actuator/health (Spring management port =
  // server port). /api/health is NOT served — probing it made this gate
  // silently skip everywhere, including against the live titanium kernel.
  // Substring match tolerates both the standard actuator body
  // ({"status":"UP","components":...}) and the custom fleet shape
  // ({"status":"UP","database":"reachable",...}).
  try {
    const body = execFileSync('curl', ['-s', '--max-time', '2', `${PEB_BASE_URL}/actuator/health`], { stdio: 'pipe' }).toString()
    return body.includes('"status":"UP"')
  } catch {
    return false
  }
}

async function pgUp() {
  const pool = new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: process.env.PG_DB_NAME || 'nexus',
    connectionTimeoutMillis: 2000,
  })
  try {
    await pool.query('SELECT 1')
    return pool
  } catch {
    await pool.end().catch(() => {})
    return null
  }
}

function makeTempRepo() {
  const dir = mkdtempSync(path.join(tmpdir(), 'harness-bridge-e2e-'))
  const run = (args, opts = {}) => execFileSync('git', args, { cwd: dir, stdio: 'pipe', ...opts })
  run(['init', '-b', 'main'])
  run(['config', 'user.email', 'bridge-e2e@nexus.local'])
  run(['config', 'user.name', 'bridge-e2e'])
  mkdirSync(path.join(dir, 'src'), { recursive: true })
  writeFileSync(path.join(dir, 'src', 'module.py'), 'VALUE = 1\n')
  run(['add', '.'])
  run(['commit', '-m', 'initial', '--no-verify'])
  return dir
}

function runBridge(payload) {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', [BRIDGE], { cwd: REPO_ROOT })
    let out = ''
    let err = ''
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    child.on('error', reject)
    child.on('close', (code) => resolve({ code, out, err }))
    child.stdin.end(JSON.stringify(payload))
  })
}

describe('harness_bridge live E2E (producer cycle)', { skip: !kernelUp() }, () => {
  let pool

  test('verified outcome: claim + evidence + admission receipt persist', async (t) => {
    pool = await pgUp()
    if (!pool) { t.skip('live PG unreachable'); return }

    const attemptId = randomUUID()
    const repo = makeTempRepo()
    const res = await runBridge({
      attempt_id: attemptId,
      lease_id: attemptId, // synthetic grant: lease mirrors the attempt id
      repository_root: repo,
      base_ref: 'refs/heads/main',
      declared_paths: ['src/module.py'],
    })

    assert.equal(res.code, 0, `bridge stderr: ${res.err.slice(-400)}`)
    const parsed = JSON.parse(res.out)
    assert.equal(parsed.requested, true, JSON.stringify(parsed))
    assert.equal(parsed.outcome, 'verified')
    assert.equal(parsed.admitted, true)
    assert.equal(parsed.claimed_ref, 'refs/heads/main')
    assert.match(parsed.resolved_commit, /^[0-9a-f]{40}$/)
    assert.match(parsed.claim_id, /^[0-9a-f-]{36}$/)
    assert.match(parsed.evidence_id, /^[0-9a-f-]{36}$/)
    assert.ok(parsed.peb_transaction_id, 'kernel returned a transaction id')

    // Producer rows persisted and keyed to the synthetic attempt id.
    const claim = await pool.query(
      'SELECT policy_version_hash, disposition FROM resolution.execution_claim WHERE attempt_id = $1',
      [attemptId],
    )
    assert.equal(claim.rows.length, 1, 'exactly one claim row for the attempt')
    assert.match(claim.rows[0].policy_version_hash, /^sha256:/)

    const receipt = await pool.query(
      'SELECT admitted, reason, source_system FROM resolution.execution_admission_receipt WHERE attempt_id = $1',
      [attemptId],
    )
    assert.equal(receipt.rows.length, 1, 'exactly one admission receipt for the attempt')
    assert.equal(receipt.rows[0].admitted, true)

    // Idempotent replay: same attempt, same repo → same claim, no duplicate.
    const replay = await runBridge({
      attempt_id: attemptId,
      lease_id: attemptId,
      repository_root: repo,
      base_ref: 'refs/heads/main',
      declared_paths: ['src/module.py'],
    })
    assert.equal(replay.code, 0)
    const reparsed = JSON.parse(replay.out)
    assert.equal(reparsed.requested, true)
    assert.equal(reparsed.claim_id, parsed.claim_id, 'replay reuses the claim (unique-index dedup)')
    assert.equal(reparsed.peb_transaction_id, parsed.peb_transaction_id, 'kernel recorded-outcome branch')

    const claimsAfter = await pool.query(
      'SELECT count(*)::int AS n FROM resolution.execution_claim WHERE attempt_id = $1',
      [attemptId],
    )
    assert.equal(claimsAfter.rows[0].n, 1, 'replay did not duplicate the claim')

    await pool.end()
  })

  test('scope violation: undeclared path change still verifies only declared scope', async (t) => {
    // The verifier scopes to declared_paths; a second file outside the
    // declaration must not flip the outcome (scope_matches governs).
    pool = await pgUp()
    if (!pool) { t.skip('live PG unreachable'); return }

    const attemptId = randomUUID()
    const repo = makeTempRepo()
    writeFileSync(path.join(repo, 'undeclared.txt'), 'noise\n')
    execFileSync('git', ['add', '.'], { cwd: repo, stdio: 'pipe' })
    execFileSync('git', ['commit', '-m', 'undeclared file', '--no-verify'], { cwd: repo, stdio: 'pipe' })

    const res = await runBridge({
      attempt_id: attemptId,
      lease_id: attemptId,
      repository_root: repo,
      base_ref: 'refs/heads/main~1', // diff base: the initial commit
      declared_paths: ['src/module.py'],
    })
    assert.equal(res.code, 0, `bridge stderr: ${res.err.slice(-400)}`)
    const parsed = JSON.parse(res.out)
    // The undeclared.txt change is outside the declared scope — the cycle
    // completes (requested=true) but fail-closes: outcome rejected, kernel
    // admission denied, receipt persisted with admitted=false.
    assert.equal(parsed.requested, true, JSON.stringify(parsed))
    assert.equal(parsed.admitted, false, 'fail-closed: undeclared scope never admits')
    assert.notEqual(parsed.outcome, 'verified')
    const rcpt = await pool.query(
      'SELECT admitted FROM resolution.execution_admission_receipt WHERE attempt_id = $1',
      [attemptId],
    )
    assert.equal(rcpt.rows.length, 1, 'rejection is persisted as an admission receipt')
    assert.equal(rcpt.rows[0].admitted, false, 'fail-closed: undeclared scope never admits')
    await pool.end()
  })
})
