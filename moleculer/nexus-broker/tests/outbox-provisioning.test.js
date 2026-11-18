/**
 * Outbox table provisioning — reconciliation and concurrency.
 *
 * `ensureOutboxTable()` used to be `CREATE TABLE IF NOT EXISTS ... (LIKE ...
 * INCLUDING ALL)`, which only clones at CREATE time. The test table survives
 * between runs, so a column added to production never reached it until someone
 * dropped the table by hand: the drift fix was real but silent-no-op-prone, and
 * nothing tested it. These tests pin the observable contract:
 *
 *   - a table already matching production reports `unchanged`, performs no DDL
 *     (the relation OID is stable) and loses no rows;
 *   - a drifted table — extra column, changed default, changed type/nullability
 *     — is detected, dropped and recreated, and the added column is gone;
 *   - a missing table is created;
 *   - a second immediate call is `unchanged`;
 *   - concurrent provisioners neither deadlock nor drop a table that is in use;
 *   - production's outbox is never mutated by any of it.
 *
 * It also pins the shared production-outbox guard
 * (`registerProductionOutboxGuard`): both node:test hooks come from one call, a
 * call that cannot guard is refused, and the assertion still runs — next to,
 * not instead of, the teardown error — when the file's teardown fails.
 *
 * Everything here happens on a private probe table
 * (`resolution_test.keychain_event_outbox_probe_<pid>`), never on the table the
 * broker-booting tests share and never on production's table, so this file can
 * run in parallel with the rest of the suite.
 *
 * Reverting `ensureOutboxTable()` to plain `CREATE TABLE IF NOT EXISTS` makes
 * the drift tests below fail (the table is reported unchanged and the added
 * column survives) — that is the regression this file exists to catch.
 */
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { randomUUID } = require('node:crypto')
const dotenv = require('dotenv')
const { Pool } = require('pg')
const {
  PRODUCTION_OUTBOX_TABLE,
  PRODUCTION_TEST_SIGNATURE,
  ensureOutboxTable,
  outboxShape,
  shapesMatch,
  registerProductionOutboxGuard,
} = require('./helpers/isolated-mongo')
const { TEST_PG_DATABASE, ensureTestDatabase, dropTestDatabase } = require('./helpers/isolated-pg')

dotenv.config({ path: path.join(__dirname, '..', '.env') })

const HELPER_PATH = require.resolve('./helpers/isolated-mongo')
const PROBE_TABLE = `resolution_test.keychain_event_outbox_probe_${process.pid}`
const PROBE_NAMESPACE = `keychain-test-provision-${process.pid}`
// One authority for the leak signature: the helper's (static history plus THIS
// run's namespace prefix). A local copy here would silently stop matching a
// run whose prefix differs from the historical four.
const TEST_SIGNATURE = PRODUCTION_TEST_SIGNATURE

function makePool() {
  return new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: TEST_PG_DATABASE,
  })
}

let pool = null
let productionShapeBefore = null
let productionSignatureBefore = null

/** Production's shape — the reference every probe assertion compares against. */
async function productionShape() {
  return outboxShape(pool, PRODUCTION_OUTBOX_TABLE)
}

async function probeShape() {
  return outboxShape(pool, PROBE_TABLE)
}

async function assertProbeMatchesProduction(where) {
  const production = await productionShape()
  const probe = await probeShape()
  assert.ok(
    shapesMatch(production, probe),
    `${where}: probe table shape must match production column-for-column;\n` +
      `production=${JSON.stringify(production)}\nprobe=${JSON.stringify(probe)}`,
  )
}

async function relationOid(qualified) {
  const res = await pool.query('SELECT $1::regclass::oid::int AS oid', [qualified])
  return res.rows[0].oid
}

async function rowCount(qualified) {
  const res = await pool.query(`SELECT count(*)::int AS n FROM ${qualified}`)
  return res.rows[0].n
}

async function insertProbeRow(namespace = PROBE_NAMESPACE) {
  const sourceEventId = randomUUID()
  await pool.query(
    `INSERT INTO ${PROBE_TABLE} (source_namespace, source_event_id, event_kind, outcome, actor)
     VALUES ($1, $2, 'sol.e2e.transition.committed', 'committed', 'provisioning-test')`,
    [namespace, sourceEventId],
  )
  return sourceEventId
}

/** Fresh, matching probe table as the starting point for a case. */
async function resetProbeTable() {
  await pool.query(`DROP TABLE IF EXISTS ${PROBE_TABLE}`)
  const result = await ensureOutboxTable(pool, PROBE_TABLE)
  assert.equal(result.action, 'created', 'resetProbeTable must create the probe table')
  await assertProbeMatchesProduction('after reset')
  return result
}

before(async () => {
  // Per-run database from the canonical bootstrap: the clone source is this
  // run's `resolution.keychain_event_outbox`, not a pre-seeded shared one.
  await ensureTestDatabase()
  pool = makePool()
  productionShapeBefore = await productionShape()
  const sig = await pool.query(
    `SELECT count(*)::int AS n FROM ${PRODUCTION_OUTBOX_TABLE} WHERE ${TEST_SIGNATURE}`,
  )
  productionSignatureBefore = sig.rows[0].n
  assert.ok(productionShapeBefore.length > 0, 'production outbox table must exist to clone from')
  await resetProbeTable()
})

after(async () => {
  if (pool) {
    await pool.query(`DROP TABLE IF EXISTS ${PROBE_TABLE}`).catch(() => undefined)
    await pool.end().catch(() => undefined)
  }
  await dropTestDatabase().catch((err) => console.warn(`[outbox-provisioning] drop skipped: ${err.message}`))
})

test('a matching table is reported unchanged, with no DDL and no data loss', async () => {
  const marker = await insertProbeRow()
  const oidBefore = await relationOid(PROBE_TABLE)
  const rowsBefore = await rowCount(PROBE_TABLE)

  const result = await ensureOutboxTable(pool, PROBE_TABLE)

  assert.equal(result.action, 'unchanged', 'an already-matching table must not be recreated')
  assert.equal(await relationOid(PROBE_TABLE), oidBefore, 'no DROP/CREATE: relation OID must be stable')
  assert.equal(await rowCount(PROBE_TABLE), rowsBefore, 'no rows may be lost on the unchanged path')
  const kept = await pool.query(
    `SELECT count(*)::int AS n FROM ${PROBE_TABLE} WHERE source_event_id = $1`,
    [marker],
  )
  assert.equal(kept.rows[0].n, 1, 'the marker row must survive an unchanged provisioning pass')
})

test('an extra column is detected and recreated away', async () => {
  await pool.query(`ALTER TABLE ${PROBE_TABLE} ADD COLUMN drift_extra text`)
  const oidDrifted = await relationOid(PROBE_TABLE)

  const result = await ensureOutboxTable(pool, PROBE_TABLE)

  assert.equal(result.action, 'recreated', 'drift must be reconciled, not ignored')
  assert.ok(
    result.diff.some((note) => note.includes('extra column drift_extra')),
    `diff must name the drift, got ${JSON.stringify(result.diff)}`,
  )
  assert.notEqual(await relationOid(PROBE_TABLE), oidDrifted, 'recreate means a new relation')
  const gone = await pool.query(
    `SELECT count(*)::int AS n FROM information_schema.columns
      WHERE table_schema = 'resolution_test' AND table_name = $1 AND column_name = 'drift_extra'`,
    [PROBE_TABLE.split('.')[1]],
  )
  assert.equal(gone.rows[0].n, 0, 'the added column must be gone after reconciliation')
  await assertProbeMatchesProduction('after removing an extra column')
})

test('a changed column default is detected and recreated away', async () => {
  await pool.query(`ALTER TABLE ${PROBE_TABLE} ALTER COLUMN checkpoint_status SET DEFAULT 'delivered'`)

  const result = await ensureOutboxTable(pool, PROBE_TABLE)

  assert.equal(result.action, 'recreated', 'a default change is drift')
  assert.ok(
    result.diff.some((note) => note.startsWith('checkpoint_status: default')),
    `diff must name the default change, got ${JSON.stringify(result.diff)}`,
  )
  const def = await pool.query(
    `SELECT coalesce(column_default, '') AS d FROM information_schema.columns
      WHERE table_schema = 'resolution_test' AND table_name = $1 AND column_name = 'checkpoint_status'`,
    [PROBE_TABLE.split('.')[1]],
  )
  assert.match(def.rows[0].d, /'pending'/, `default must be back to production's, got ${def.rows[0].d}`)
  await assertProbeMatchesProduction('after reverting a default change')
})

test('a type/nullability change is detected and recreated away', async () => {
  await pool.query(`ALTER TABLE ${PROBE_TABLE} ALTER COLUMN actor TYPE varchar(64)`)
  await pool.query(`ALTER TABLE ${PROBE_TABLE} ALTER COLUMN last_error SET NOT NULL`)

  const result = await ensureOutboxTable(pool, PROBE_TABLE)

  assert.equal(result.action, 'recreated', 'a type or nullability change is drift')
  const notes = result.diff.join(' ; ')
  assert.match(notes, /actor: type/, `diff must name the type change, got ${JSON.stringify(result.diff)}`)
  assert.match(notes, /last_error: nullability/, `diff must name the nullability change, got ${JSON.stringify(result.diff)}`)
  const type = await pool.query(
    `SELECT data_type FROM information_schema.columns
      WHERE table_schema = 'resolution_test' AND table_name = $1 AND column_name = 'actor'`,
    [PROBE_TABLE.split('.')[1]],
  )
  assert.equal(type.rows[0].data_type, 'text', 'actor must be back to production’s type')
  await assertProbeMatchesProduction('after reverting a type change')
})

test('a missing table is created from production', async () => {
  await pool.query(`DROP TABLE ${PROBE_TABLE}`)

  const result = await ensureOutboxTable(pool, PROBE_TABLE)

  assert.equal(result.action, 'created', 'a missing table must be provisioned')
  await assertProbeMatchesProduction('after creating a missing table')
})

test('a second immediate call reports unchanged', async () => {
  const first = await ensureOutboxTable(pool, PROBE_TABLE)
  const second = await ensureOutboxTable(pool, PROBE_TABLE)
  assert.equal(first.action, 'unchanged')
  assert.equal(second.action, 'unchanged')
})

test('concurrent provisioners neither deadlock nor drop a table in use', async () => {
  await resetProbeTable()
  const marker = await insertProbeRow()
  const rowsAtStart = await rowCount(PROBE_TABLE)

  // Real cross-process contention: each child is a separate node process with
  // its own Pool, exactly like the four broker-booting test files at boot.
  const script = `
    const h = require(${JSON.stringify(HELPER_PATH)});
    h.ensureOutboxTable()
      .then((r) => { process.stdout.write(JSON.stringify({ action: r.action })); })
      .catch((e) => { console.error(e && e.message); process.exit(1); });
  `
  const children = [0, 1, 2].map(() => spawn(process.execPath, ['-e', script], {
    cwd: path.resolve(__dirname, '..'),
    // NEXUS_TEST_PG_DATABASE pins the children to THIS run's database. Without
    // it each child derives its own name (pid-based) and races against a
    // database nobody created — which is not the contention this test is about.
    env: { ...process.env, KEYCHAIN_OUTBOX_TABLE: PROBE_TABLE, NEXUS_TEST_PG_DATABASE: TEST_PG_DATABASE },
    stdio: ['ignore', 'pipe', 'pipe'],
  }))

  const outputs = children.map((child) => new Promise((resolve, reject) => {
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      reject(new Error(`provisioner deadlocked (no exit within 30s); stderr=${stderr}`))
    }, 30_000)
    child.on('error', (err) => { clearTimeout(timer); reject(err) })
    child.on('exit', (code) => {
      clearTimeout(timer)
      if (code !== 0) reject(new Error(`provisioner exited ${code}: ${stderr}`))
      else resolve(stdout)
    })
  }))

  // While they race, keep the table in use. A dropped table shows up here as a
  // failed insert (or as lost rows below).
  const inserted = []
  const failures = []
  const deadline = Date.now() + 1_500
  while (Date.now() < deadline) {
    try {
      inserted.push(await insertProbeRow())
    } catch (err) {
      failures.push(err.message)
    }
    await new Promise((resolve) => setTimeout(resolve, 25))
  }

  const results = await Promise.all(outputs)
  assert.deepEqual(failures, [], 'no insert may fail while provisioners run')
  for (const out of results) {
    assert.equal(JSON.parse(out).action, 'unchanged', 'a matching table must be left alone by every provisioner')
  }
  assert.ok(inserted.length > 0, 'the table must have stayed usable during provisioning')

  const rowsNow = await rowCount(PROBE_TABLE)
  assert.equal(rowsNow, rowsAtStart + inserted.length, 'no rows may be lost (a drop would lose them)')
  const kept = await pool.query(
    `SELECT count(*)::int AS n FROM ${PROBE_TABLE} WHERE source_event_id = $1`,
    [marker],
  )
  assert.equal(kept.rows[0].n, 1, 'the pre-existing marker row must survive the race')
  await assertProbeMatchesProduction('after concurrent provisioning')
})

/**
 * Stand-ins for node:test's hook registrars, so a guard can be driven without
 * provisioning the shared table this file deliberately stays off.
 */
function captureHooks() {
  const registered = {}
  return {
    registered,
    before: (fn) => { registered.before = fn },
    after: (fn) => { registered.after = fn },
  }
}

test('the production guard registers both hooks from one call', () => {
  const hooks = captureHooks()
  registerProductionOutboxGuard({ before: hooks.before, after: hooks.after })
  assert.equal(typeof hooks.registered.before, 'function', 'the guard must register its before hook')
  assert.equal(typeof hooks.registered.after, 'function', 'the guard must register its after hook')
})

test('the production guard refuses a call that would not guard anything', () => {
  assert.throws(() => registerProductionOutboxGuard({}), /before/, 'missing registrars must not pass silently')
  assert.throws(() => registerProductionOutboxGuard({ before: () => {} }), /after/, 'a half-wired guard must be refused')
  assert.throws(() => registerProductionOutboxGuard(), TypeError)
})

test('the production guard always runs its assertion in the after hook', async () => {
  const hooks = captureHooks()
  registerProductionOutboxGuard({ before: hooks.before, after: hooks.after })
  // The before hook never ran, so there is no baseline: the assertion has to be
  // what rejects, which is only true if it is wired into the after hook.
  await assert.rejects(hooks.registered.after(), /no baseline snapshot was taken/)
})

test('the production guard asserts even when teardown throws', async () => {
  const hooks = captureHooks()
  registerProductionOutboxGuard({
    before: hooks.before,
    after: hooks.after,
    teardown: async () => { throw new Error('teardown exploded') },
  })
  await assert.rejects(hooks.registered.after(), (err) => {
    assert.ok(err instanceof AggregateError, `expected both failures to surface, got ${err}`)
    assert.equal(
      err.errors.length,
      2,
      `expected teardown + assertion errors, got ${JSON.stringify(err.errors.map((e) => e.message))}`,
    )
    assert.match(err.errors[0].message, /teardown exploded/)
    assert.match(err.errors[1].message, /no baseline snapshot/)
    return true
  })
})

test("production's outbox is never mutated by provisioning", async () => {
  const shapeAfter = await productionShape()
  assert.ok(
    shapesMatch(productionShapeBefore, shapeAfter),
    'production outbox schema must be unchanged by any provisioning this file did',
  )

  const sig = await pool.query(
    `SELECT count(*)::int AS n FROM ${PRODUCTION_OUTBOX_TABLE} WHERE ${TEST_SIGNATURE}`,
  )
  assert.equal(
    sig.rows[0].n,
    productionSignatureBefore,
    'no test-signature row may appear in production’s outbox',
  )

  const leaked = await pool.query(
    `SELECT count(*)::int AS n FROM ${PRODUCTION_OUTBOX_TABLE} WHERE source_namespace = $1`,
    [PROBE_NAMESPACE],
  )
  assert.equal(leaked.rows[0].n, 0, 'probe rows must never reach production')
})
