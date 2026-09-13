/**
 * Attempt lifecycle tests (ruling ac38fa9b — phase B).
 *
 * Three layers:
 *   1. Pure helper units (marker extraction with the Q1 traversal guard,
 *      exit-code → status mapping, repo-root resolution).
 *   2. Live-PG transaction probe — executes the REAL exported SQL constants
 *      against the live schema inside a rolled-back transaction, so the
 *      statement shapes cannot drift from the schema (columns, NOT NULLs,
 *      CHECK constraints, the lease-consistency trigger).
 *   3. Q4 four-path consistency check — every complete_attempt caller
 *      (broker + 3 dormant conduit paths) must use status literals inside
 *      the attempts CHECK vocabulary.
 *
 * Layer 2 needs live PG (broker .env); it skips cleanly when unreachable.
 */
const { test, describe } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const dotenv = require('dotenv')
const { Pool } = require('pg')

const BROKER_DIR = path.resolve(__dirname, '..')
dotenv.config({ path: path.join(BROKER_DIR, '.env') })

// Compiled output (dist/services) or TS source via the build — the tests run
// post-build (npm test runs after npm run build in CI and locally).
let mod
try {
  mod = require(path.join(BROKER_DIR, 'dist', 'services', 'harness.worker.js'))
} catch {
  mod = null
}

const {
  extractGitVerificationMarker,
  attemptStatusFromExitCode,
  resolveRepoRoot,
  ATTEMPT_LEASE_INSERT_SQL,
  ATTEMPT_INSERT_SQL,
  ATTEMPT_TERMINAL_UPDATE_SQL,
  LEASE_RELEASE_SQL,
  REQUEST_GET_OR_CREATE_SQL,
  REQUEST_ADOPT_MARKER_SQL,
  RECEIPT_INSERT_SQL,
  ATTEMPT_RECOVER_SQL,
  RECOVERED_LEASE_EXPIRE_SQL,
  REQUEST_STATUS_UPDATE_SQL,
} = mod || {}

const REPO_ROOT = path.resolve(BROKER_DIR, '..', '..') // broker → moleculer → repo root

// ── 1. Pure helpers ─────────────────────────────────────────────────────

describe('extractGitVerificationMarker (Q1 + traversal amendment)', () => {
  test('accepts a well-formed marker', () => {
    const m = extractGitVerificationMarker({
      git_verification: {
        repository_root: '/home/codex/dev/nexus',
        base_ref: 'refs/heads/main',
        declared_paths: ['moleculer/nexus-broker/services/harness.worker.ts'],
      },
    })
    assert.deepEqual(m, {
      repository_root: '/home/codex/dev/nexus',
      base_ref: 'refs/heads/main',
      declared_paths: ['moleculer/nexus-broker/services/harness.worker.ts'],
    })
  })

  test('returns null for absent/empty inputs', () => {
    assert.equal(extractGitVerificationMarker(null), null)
    assert.equal(extractGitVerificationMarker(undefined), null)
    assert.equal(extractGitVerificationMarker({}), null)
    assert.equal(extractGitVerificationMarker({ git_verification: null }), null)
    assert.equal(extractGitVerificationMarker({ git_verification: 'x' }), null)
    assert.equal(extractGitVerificationMarker({ git_verification: [] }), null)
  })

  test('returns null when required fields are missing', () => {
    assert.equal(
      extractGitVerificationMarker({ git_verification: { base_ref: 'refs/heads/main' } }),
      null,
    )
    assert.equal(
      extractGitVerificationMarker({ git_verification: { repository_root: '/repo' } }),
      null,
    )
    assert.equal(
      extractGitVerificationMarker({
        git_verification: { repository_root: 'relative/path', base_ref: 'refs/heads/main' },
      }),
      null,
    )
  })

  test('Q1 amendment: rejects absolute declared_paths (traversal hardening)', () => {
    assert.equal(
      extractGitVerificationMarker({
        git_verification: {
          repository_root: '/repo',
          base_ref: 'refs/heads/main',
          declared_paths: ['/etc/passwd'],
        },
      }),
      null,
    )
  })

  test('Q1 amendment: rejects .. traversal and backslashes', () => {
    assert.equal(
      extractGitVerificationMarker({
        git_verification: {
          repository_root: '/repo',
          base_ref: 'refs/heads/main',
          declared_paths: ['../outside'],
        },
      }),
      null,
    )
    assert.equal(
      extractGitVerificationMarker({
        git_verification: {
          repository_root: '/repo',
          base_ref: 'refs/heads/main',
          declared_paths: ['a\\..\\..\\windows'],
        },
      }),
      null,
    )
    // Interior dot segments are also rejected (mirrors _normalize_relative_path).
    assert.equal(
      extractGitVerificationMarker({
        git_verification: {
          repository_root: '/repo',
          base_ref: 'refs/heads/main',
          declared_paths: ['a/./b'],
        },
      }),
      null,
    )
    assert.equal(
      extractGitVerificationMarker({
        git_verification: {
          repository_root: '/repo',
          base_ref: 'refs/heads/main',
          declared_paths: ['a//b'],
        },
      }),
      null,
    )
  })

  test('normalizes interior paths and tolerates a missing declared_paths', () => {
    const m = extractGitVerificationMarker({
      git_verification: { repository_root: '/repo', base_ref: 'main' },
    })
    assert.deepEqual(m, { repository_root: '/repo', base_ref: 'main', declared_paths: [] })
  })
})

describe('attemptStatusFromExitCode', () => {
  test('maps exit codes to CHECK-legal statuses', () => {
    assert.equal(attemptStatusFromExitCode(0), 'SUCCEEDED')
    assert.equal(attemptStatusFromExitCode(1), 'FAILED')
    assert.equal(attemptStatusFromExitCode(124), 'TIMED_OUT')
    assert.equal(attemptStatusFromExitCode(42), 'FAILED')
    assert.equal(attemptStatusFromExitCode(-1), 'FAILED')
  })
})

describe('resolveRepoRoot', () => {
  test('finds the repo carrying python/nexus_core (env override wins)', () => {
    const found = resolveRepoRoot()
    assert.equal(
      fs.existsSync(path.join(found, 'python', 'nexus_core', 'wrp', 'harness_bridge.py')),
      true,
      `resolveRepoRoot() => ${found} must carry the bridge`,
    )
    process.env.NEXUS_REPO_ROOT = '/opt/override'
    assert.equal(resolveRepoRoot(), '/opt/override')
    delete process.env.NEXUS_REPO_ROOT
  })
})

// ── 2. Live-PG transaction probe (real SQL, rolled back) ────────────────

describe('lifecycle SQL against live schema (rolled back)', { skip: !mod }, () => {
  let pool

  test('probe: create → terminal → release → receipt → request transitions → recovery', async (t) => {
    if (!process.env.PG_HOST && !process.env.PGUSER && !process.env.NEXUS_PG_DSN) {
      const probe = new Pool({
        host: 'localhost', port: 5432, user: 'pguser', password: 'pgpass', database: 'nexus',
        connectionTimeoutMillis: 2000,
      })
      try {
        await probe.query('SELECT 1')
      } catch {
        t.skip('live PG unreachable — SQL shape probe skipped')
        return
      } finally {
        await probe.end().catch(() => {})
      }
    }

    pool = new Pool({
      host: process.env.PG_HOST || 'localhost',
      port: Number(process.env.PG_PORT || 5432),
      user: process.env.PG_USER || 'pguser',
      password: process.env.PG_PASSWORD || 'pgpass',
      database: process.env.PG_DB_NAME || 'nexus',
      connectionTimeoutMillis: 3000,
    })

    const businessKey = `attempt-lifecycle-probe:${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const client = await pool.connect()
    try {
      await client.query('BEGIN')

      // get-or-create request
      const req = await client.query(REQUEST_GET_OR_CREATE_SQL, [
        businessKey,
        'lifecycle probe',
        'rolled-back probe request',
      ])
      const requestId = req.rows[0].id
      assert.ok(requestId)
      assert.equal(req.rows[0].status, 'DRAFT')

      // marker in inputs survives the upsert (Q1 home)
      const markerInputs = {
        git_verification: {
          repository_root: REPO_ROOT,
          base_ref: 'refs/heads/main',
          declared_paths: ['moleculer/nexus-broker/services/harness.worker.ts'],
        },
      }
      await client.query('UPDATE execution.requests SET inputs = $2::jsonb WHERE id = $1', [
        requestId,
        JSON.stringify(markerInputs),
      ])

      // recovery pre-noise: an orphaned RUNNING attempt with an expired lease
      const oldLease = await client.query(ATTEMPT_LEASE_INSERT_SQL, [requestId, 'probe', 1])
      await client.query(
        `UPDATE execution.leases SET acquired_at = NOW() - INTERVAL '1 hour',
           expires_at = NOW() - INTERVAL '59 minutes' WHERE id = $1`,
        [oldLease.rows[0].id],
      )
      const oldAttempt = await client.query(ATTEMPT_INSERT_SQL, [requestId, oldLease.rows[0].id, 'probe'])
      await client.query(RECOVERED_LEASE_EXPIRE_SQL, [requestId])
      const rec = await client.query(ATTEMPT_RECOVER_SQL, [requestId, 'probe recovery'])
      assert.equal(rec.rowCount, 1, 'the expired-lease RUNNING attempt is recovered')
      const oldRow = await client.query('SELECT status FROM execution.attempts WHERE id = $1', [
        oldAttempt.rows[0].id,
      ])
      assert.equal(oldRow.rows[0].status, 'FAILED')

      // fresh lease + attempt for THIS run (trigger enforces lease/request match)
      const lease = await client.query(ATTEMPT_LEASE_INSERT_SQL, [requestId, 'probe', 1800])
      const attempt = await client.query(ATTEMPT_INSERT_SQL, [requestId, lease.rows[0].id, 'probe'])
      const attemptId = attempt.rows[0].id

      // terminal — returns the lease id for release
      const upd = await client.query(ATTEMPT_TERMINAL_UPDATE_SQL, [
        attemptId, 'SUCCEEDED', 0, JSON.stringify({ probe: true }), null,
      ])
      assert.equal(upd.rowCount, 1)
      assert.equal(upd.rows[0].lease_id, lease.rows[0].id)

      // release + native receipt + request advance
      const rel = await client.query(LEASE_RELEASE_SQL, [upd.rows[0].lease_id])
      assert.equal(rel.rowCount, 1)
      const receipt = await client.query(RECEIPT_INSERT_SQL, [
        attemptId, requestId, 'probe',
        'worker.harness SUCCEEDED exit=0',
        JSON.stringify({ probe: true }),
      ])
      assert.ok(receipt.rows[0].id)
      const reqUpd = await client.query(REQUEST_STATUS_UPDATE_SQL, [requestId, 'COMPLETED'])
      assert.equal(reqUpd.rowCount, 1)

      // terminal-status idempotence guard: a second terminal update is a no-op
      const again = await client.query(ATTEMPT_TERMINAL_UPDATE_SQL, [
        attemptId, 'FAILED', 1, '{}', 'must not apply',
      ])
      assert.equal(again.rowCount, 0)

      // CHECK vocabulary enforced by the schema itself (savepoint: the
      // intentional violation aborts the tx until rolled back)
      await client.query('SAVEPOINT before_bad_status')
      await assert.rejects(
        () => client.query(
          "UPDATE execution.attempts SET status = 'FATAL_ERROR' WHERE id = $1", [attemptId],
        ),
        /attempts_status_check|check constraint/i,
      )
      await client.query('ROLLBACK TO SAVEPOINT before_bad_status')

      // marker extraction from the request row (worker parity path)
      assert.deepEqual(extractGitVerificationMarker(req.rows[0].inputs), null) // pre-update row
      const after = await client.query('SELECT inputs FROM execution.requests WHERE id = $1', [requestId])
      const marker = extractGitVerificationMarker(after.rows[0].inputs)
      assert.ok(marker, 'marker is extractable from the stored inputs')
      assert.equal(marker.repository_root, REPO_ROOT)

      // Q1 adoption: fill-if-absent from the wind task's input_spec
      const adopt = await client.query(REQUEST_ADOPT_MARKER_SQL, [
        requestId,
        JSON.stringify({ repository_root: '/tmp/adopt-repo', base_ref: 'refs/heads/main', declared_paths: ['src/x.py'] }),
      ])
      assert.equal(adopt.rowCount, 0, 'no adoption when the request already carries the marker')

      // fresh request without a marker → adoption fills it
      const req2 = await client.query(REQUEST_GET_OR_CREATE_SQL, [
        businessKey + ':adopt', 'adoption probe', 'no marker yet',
      ])
      const adopt2 = await client.query(REQUEST_ADOPT_MARKER_SQL, [
        req2.rows[0].id,
        JSON.stringify({ repository_root: '/tmp/adopt-repo', base_ref: 'refs/heads/main', declared_paths: ['src/x.py'] }),
      ])
      assert.equal(adopt2.rowCount, 1, 'adoption fills an absent marker')
      assert.equal(adopt2.rows[0].adopted_marker.repository_root, '/tmp/adopt-repo')
      // inputs merge is preserved (existing keys untouched)
      const inputs2 = await client.query('SELECT inputs FROM execution.requests WHERE id = $1', [req2.rows[0].id])
      await client.query("UPDATE execution.requests SET inputs = inputs || '{\"keepme\": \"yes\"}'::jsonb WHERE id = $1", [req2.rows[0].id])
      const adopt3 = await client.query(REQUEST_ADOPT_MARKER_SQL, [
        req2.rows[0].id,
        JSON.stringify({ repository_root: '/tmp/other', base_ref: 'refs/heads/main', declared_paths: [] }),
      ])
      assert.equal(adopt3.rowCount, 0, 'second adoption is a no-op (never overwrite adopters)')
      const inputs3 = await client.query('SELECT inputs FROM execution.requests WHERE id = $1', [req2.rows[0].id])
      assert.equal(inputs3.rows[0].inputs.keepme, 'yes', 'adoption preserves sibling inputs keys')
      assert.equal(inputs3.rows[0].inputs.git_verification.repository_root, '/tmp/adopt-repo', 'first adopter wins')
    } finally {
      await client.query('ROLLBACK').catch(() => {})
      client.release()
      await pool.end().catch(() => {})
    }
  })
})

// ── 3. Q4 four-path consistency check ───────────────────────────────────

describe('Q4 four-path complete_attempt status consistency', { skip: !mod }, () => {
  const LEGAL = new Set(['SUCCEEDED', 'FAILED', 'TIMED_OUT'])

  test('broker worker maps only legal statuses', () => {
    for (const code of [0, 1, 124, 2, -1]) {
      assert.ok(LEGAL.has(attemptStatusFromExitCode(code)), `exit ${code} maps to a legal status`)
    }
  })

  test('every conduit complete_attempt call site uses a legal status literal', () => {
    const conduitDir = path.join(REPO_ROOT, 'python', 'conduit')
    assert.ok(fs.existsSync(conduitDir), 'conduit sources present')
    const offenders = []
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name)
        if (entry.isDirectory()) { walk(p); continue }
        if (!entry.name.endsWith('.py')) continue
        const text = fs.readFileSync(p, 'utf8')
        const re = /complete_attempt\(\s*[^)]*?"([A-Z_]+)"/gs
        let m
        while ((m = re.exec(text)) !== null) {
          const status = m[1]
          if (!LEGAL.has(status)) {
            const line = text.slice(0, m.index).split('\n').length
            offenders.push(`${path.relative(REPO_ROOT, p)}:${line} status="${status}"`)
          }
        }
      }
    }
    walk(conduitDir)
    assert.deepEqual(offenders, [], `illegal status literals at complete_attempt call sites:\n${offenders.join('\n')}`)
  })
})
