/**
 * Fail-closed environment guard — unit cases.
 *
 * `assertSafeTestEnv()` exists because the two ways this suite could still
 * reach deployment state were both environment overrides the code cannot
 * detect at use time:
 *
 *   MONGO_DB_NAME=keychains npm test
 *     → every broker boots into the live store and teardown DROPS it;
 *
 *   KEYCHAIN_OUTBOX_TABLE=resolution.keychain_event_outbox npm test
 *     → `ensureOutboxTable()` reports `skipped`, and the "isolated" suite
 *       claims and writes production's queue.
 *
 * These cases pin the decision table: what is refused, what is allowed, and
 * which single switch licenses sharing a store. Pure environment/inspection
 * only — no Mongo, no PostgreSQL, no broker — so it runs in the cheap CI step
 * with the drift units.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  assertSafeTestEnv,
  mongoDbFromUrl,
  PRODUCTION_OUTBOX_TABLE,
  OUTBOX_TABLE,
  MONGO_DB_NAME,
} = require('./helpers/isolated-mongo')

/** A configuration that is safe by construction, to vary one field at a time. */
const SAFE = {
  mongoDb: 'keychains_test_1234_abcd',
  outboxTable: 'resolution_test.keychain_event_outbox_1234_lx_abc123',
}

test('the suite’s own resolved configuration passes', () => {
  const result = assertSafeTestEnv()
  assert.equal(result.allowed, false)
  assert.deepEqual(result.problems, [])
  assert.ok(result.mongoDb.startsWith('keychains_test_'), `unexpected database ${result.mongoDb}`)
  assert.ok(result.outboxTable.startsWith('resolution_test.'), `unexpected table ${result.outboxTable}`)
  // The defaults the helper hands a broker are the validated ones.
  assert.equal(result.mongoDb, MONGO_DB_NAME)
  assert.equal(result.outboxTable, OUTBOX_TABLE)
})

test('a production Mongo database name is refused', () => {
  assert.throws(
    () => assertSafeTestEnv({ MONGO_DB_NAME: 'keychains' }),
    /MONGO_DB_NAME=keychains is a deployment database/,
    'MONGO_DB_NAME=keychains would make teardown drop the live store',
  )
})

test('a pinned database name must look like a test database', () => {
  assert.throws(
    () => assertSafeTestEnv({ MONGO_DB_NAME: 'keychains_scratch' }),
    /does not look like a test database/,
    'an arbitrary pinned name is refused: the suite drops whatever it is given',
  )
  assert.doesNotThrow(
    () => assertSafeTestEnv({ MONGO_DB_NAME: 'keychains_test_scratch' }, SAFE),
    'a name that says it is a test database is allowed',
  )
})

test('a resolved deployment database is refused even without MONGO_DB_NAME', () => {
  assert.throws(
    () => assertSafeTestEnv({}, { mongoDb: 'keychains', outboxTable: SAFE.outboxTable }),
    /resolved keychain database keychains is a deployment database/,
  )
})

test("production's outbox table is refused", () => {
  assert.throws(
    () => assertSafeTestEnv({ KEYCHAIN_OUTBOX_TABLE: PRODUCTION_OUTBOX_TABLE }, {
      ...SAFE,
      outboxTable: PRODUCTION_OUTBOX_TABLE,
    }),
    /is production's queue/,
    'pinning production’s queue would make the suite claim live rows',
  )
})

test("any outbox table in production's schema is refused", () => {
  assert.throws(
    () => assertSafeTestEnv({}, { ...SAFE, outboxTable: 'resolution.keychain_event_outbox_other' }),
    /lives in production's "resolution" schema/,
    'a differently-named table in resolution is still production’s schema',
  )
  assert.doesNotThrow(
    () => assertSafeTestEnv({}, { ...SAFE, outboxTable: 'resolution_test.keychain_event_outbox_probe_1' }),
    'resolution_test is the test schema and is allowed',
  )
})

test('a malformed outbox table name is refused as a problem, not a crash', () => {
  assert.throws(
    () => assertSafeTestEnv({}, { ...SAFE, outboxTable: 'keychain_event_outbox' }),
    /schema\.table/,
  )
})

test('NODE_ENV=production is refused', () => {
  assert.throws(
    () => assertSafeTestEnv({ NODE_ENV: 'production' }, SAFE),
    /NODE_ENV=production/,
  )
})

test('NEXUS_TEST_ALLOW_SHARED_STORES=1 is the only way to share a store', () => {
  const result = assertSafeTestEnv(
    { NEXUS_TEST_ALLOW_SHARED_STORES: '1', MONGO_DB_NAME: 'keychains' },
    { mongoDb: 'keychains', outboxTable: PRODUCTION_OUTBOX_TABLE },
  )
  assert.equal(result.allowed, true, 'the explicit opt-in must be honoured')
})

test('a MONGO_URL naming a deployment database warns but does not refuse', () => {
  // Every boot path in the suite overrides the database name, so the URL alone
  // cannot move a test's writes; it is how a NEW file would, hence the warning.
  assert.doesNotThrow(
    () => assertSafeTestEnv({ MONGO_URL: 'mongodb://user:pw@localhost:27017/keychains' }, SAFE),
  )
  assert.equal(mongoDbFromUrl('mongodb://user:pw@h:27017/keychains?tls=true'), 'keychains')
  assert.equal(mongoDbFromUrl('mongodb://localhost:27017'), null)
  assert.equal(mongoDbFromUrl('mongodb+srv://h/keychains_test_x'), 'keychains_test_x')
})

test('an error names every problem and the override', () => {
  try {
    assertSafeTestEnv({ MONGO_DB_NAME: 'keychains', NODE_ENV: 'production' }, {
      mongoDb: 'keychains',
      outboxTable: PRODUCTION_OUTBOX_TABLE,
    })
    assert.fail('expected the guard to throw')
  } catch (err) {
    assert.match(err.message, /NODE_ENV=production/)
    assert.match(err.message, /MONGO_DB_NAME=keychains/)
    assert.match(err.message, /is production's queue/)
    assert.match(err.message, /NEXUS_TEST_ALLOW_SHARED_STORES=1/, 'the message must say how to opt in')
  }
})
