/**
 * Unit cases for the shared-database write guard.
 *
 * The guard runs inside test hooks, where a wrong answer is either a silent
 * pass (worse than no guard) or a false failure on a developer machine. These
 * cases pin the decision table without a database:
 *
 *   attributable rows appeared  -> failure, always (only the harness makes them)
 *   only the total moved        -> failure in strict mode (CI), warning otherwise
 *   nothing moved               -> nothing
 *   census unavailable          -> nothing (no shared database, nothing to guard)
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  diffSharedDatabase,
  MONITORED_TABLES,
  attributablePredicate,
} = require('./helpers/shared-db-guard')
const { HISTORICAL_TEST_SIGNATURE } = require('../bin/outbox-quarantine')
const {
  TEST_APPLICATION_NAME,
  TEST_APPLICATION_PREFIX,
  GUARD_APPLICATION_SUFFIX,
  guardApplicationName,
  describeHarnessConnection,
  listHarnessConnectionsOnShared,
} = require('./helpers/shared-db-guard')

const OUTBOX = 'resolution.keychain_event_outbox'
const HISTORY = 'nebula.agent_records_history'

function census(tables) {
  return { available: true, database: 'nexus', at: '2026-11-18T00:00:00.000Z', tables }
}

function row(table, total, attributable) {
  return { table, total, attributable }
}

test('a quiet run reports nothing', () => {
  const before = census([row(OUTBOX, 439, 18), row(HISTORY, 17285, 0)])
  const after = census([row(OUTBOX, 439, 18), row(HISTORY, 17285, 0)])
  assert.deepEqual(diffSharedDatabase(before, after), { failures: [], warnings: [] })
})

test('harness-shaped rows in the shared database always fail, even outside strict mode', () => {
  const before = census([row(OUTBOX, 439, 18)])
  const after = census([row(OUTBOX, 440, 19)])
  const { failures, warnings } = diffSharedDatabase(before, after, { strict: false })
  assert.equal(failures.length, 1, 'an attributable row is proof the harness wrote the shared database')
  assert.match(failures[0], /gained 1 harness row/)
  assert.match(failures[0], /439 -> 440|18 -> 19/)
  assert.equal(warnings.length, 0)
})

test('rows only the live system could have written warn, and fail under strict mode', () => {
  const before = census([row(OUTBOX, 439, 18)])
  const after = census([row(OUTBOX, 442, 18)]) // live broker delivered three events
  const lenient = diffSharedDatabase(before, after, { strict: false })
  assert.equal(lenient.failures.length, 0, 'a dev machine has production writing; do not fail for it')
  assert.equal(lenient.warnings.length, 1)
  assert.match(lenient.warnings[0], /changed 439 -> 442/)

  const strict = diffSharedDatabase(before, after, { strict: true })
  assert.equal(strict.failures.length, 1, 'in CI nothing else writes this database')
  assert.match(strict.failures[0], /NEXUS_TEST_STRICT_SHARED_DB=1/)
})

test('an unreadable or absent census disables the check instead of failing the run', () => {
  const after = census([row(OUTBOX, 1, 0)])
  assert.deepEqual(diffSharedDatabase(null, after), { failures: [], warnings: [] })
  assert.deepEqual(diffSharedDatabase({ available: false, reason: 'no db', tables: [] }, after), {
    failures: [],
    warnings: [],
  })
})

test('the census covers all three schema families the harness could reach', () => {
  for (const prefix of ['resolution.', 'execution.', 'nebula.']) {
    assert.ok(
      MONITORED_TABLES.some((table) => table.startsWith(prefix)),
      `no monitored table in ${prefix} — a whole schema family would be unguarded`,
    )
  }
  assert.ok(MONITORED_TABLES.includes(OUTBOX))
  assert.ok(MONITORED_TABLES.includes(HISTORY))
})

test('this process has a run-unique connection tag that cannot be confused with production', () => {
  assert.match(TEST_APPLICATION_NAME, new RegExp(`^${TEST_APPLICATION_PREFIX}`))
  assert.ok(TEST_APPLICATION_NAME.includes(String(process.pid)), 'the tag must distinguish this file from its siblings')
  assert.ok(TEST_APPLICATION_NAME.length <= 63, 'Postgres truncates application_name at 63 bytes')
  assert.ok(!TEST_APPLICATION_NAME.includes('-guard'), 'the harness tag must not look like a guard connection')
})

test('the guard names its own connections so the watch cannot report itself', () => {
  assert.equal(guardApplicationName('nexus-test-x'), `nexus-test-x${GUARD_APPLICATION_SUFFIX}`)
  assert.notEqual(guardApplicationName('nexus-test-x'), 'nexus-test-x')
  assert.ok(guardApplicationName().endsWith(GUARD_APPLICATION_SUFFIX))
})

test('the failure message names the reader, the connection and the database', () => {
  const message = describeHarnessConnection(
    {
      pid: 4242,
      application_name: 'nexus-test-gh1-99',
      usename: 'pguser',
      state: 'idle',
      state_change: new Date('2026-11-18T04:00:00Z'),
      wait_event_type: 'Client',
      wait_event: 'ClientRead',
      query: 'SELECT id\n  FROM resolution.keychain_event_outbox\n WHERE checkpoint_status = \'pending\'',
    },
    { database: 'nexus' },
  )
  assert.match(message, /attached to nexus/)
  assert.match(message, /application_name=nexus-test-gh1-99/)
  assert.match(message, /pid=4242/)
  assert.match(message, /Client\/ClientRead/)
  assert.match(message, /SELECT id FROM resolution\.keychain_event_outbox/, 'the query is the actionable part')
  assert.match(message, /KEYCHAIN_SOURCE_DATABASES/, 'the message must point at the bug class')
})

test('an empty connection tag is refused rather than read as "clean"', async () => {
  // The footgun already found once: a missing argument that silently narrows the
  // check to nothing. An empty tag matches no session, so it must throw.
  await assert.rejects(() => listHarnessConnectionsOnShared({ tag: '' }), /a connection tag is required/)
  await assert.rejects(() => listHarnessConnectionsOnShared({}), /a connection tag is required/)
})

test('predicates name the markers each suite actually uses', () => {
  const signature = "(source_namespace ~ '^(keychain-test-)' OR actor ~ '^broker-smoke')"
  assert.equal(attributablePredicate(OUTBOX, signature), signature, 'the outbox must use the caller signature')
  // Forgetting to pass one must not weaken the check: the historical list is
  // the floor, not an optional nicety (found by a control run that inserted a
  // test-signature row and got a warning instead of a failure).
  assert.equal(attributablePredicate(OUTBOX), HISTORICAL_TEST_SIGNATURE)
  assert.match(attributablePredicate(OUTBOX), /keychain-test-/)
  assert.match(attributablePredicate(HISTORY), /11111111-1111-4111-8111-111111111111/)
  assert.match(attributablePredicate('execution.requests'), /witnessed-v3-smoke-/)
  assert.match(attributablePredicate('execution.requests'), /attempt-lifecycle-probe:/)
  assert.match(attributablePredicate('execution.attempts'), /executor_id IN \('smoke', 'probe'\)/)
  assert.equal(attributablePredicate('resolution.execution_claim'), null, 'unmarked tables are total-only')
})
