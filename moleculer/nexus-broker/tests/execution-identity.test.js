const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  assertCensusJoin,
  censusMarkerState,
  validateExecutionEnvelope,
  EXECUTION_SCHEMA_VERSION,
} = require('../lib/execution-identity.ts')

const EXEC_ID = '3f2504e0-4f89-41d3-9a0c-0305e82c3301'
const CENSUS_ID = '9c858901-8a57-4791-81fe-4c455f099c9c'
const DOCTRINE = 'sha256:' + 'a'.repeat(64)
const SESSION = 'reviewer-20260703-112402-831cbac9'

const envelope = (overrides = {}) => ({
  execution: {
    schema_version: EXECUTION_SCHEMA_VERSION,
    execution_id: EXEC_ID,
    session_id: SESSION,
    census_id: null,
  },
  census: { enabled: true, sampled: false, rate: '1/20' },
  subject: { source_namespace: 'conduit', ticket_id: 'ticket-1' },
  frame: { doctrine_snapshot_id: DOCTRINE },
  outcome: { status: 'completed' },
  ...overrides,
})

// Structural Tier-3 reference. A2a checks the relationship between tiers; the census read
// path owns whether a Tier-3 payload satisfies its own contract.
const censusReport = (overrides = {}) => ({
  census_id: CENSUS_ID,
  execution_id: EXEC_ID,
  sampled: true,
  ...overrides,
})

// ---------------------------------------------------------------- identity

test('A2a accepts a well-formed envelope', () => {
  const result = validateExecutionEnvelope(envelope())
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('A2a requires execution_id to be a uuid (DBA condition 1)', () => {
  const result = validateExecutionEnvelope(envelope({
    execution: { ...envelope().execution, execution_id: 'exec-1' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('must be a uuid')))
})

test('A2a rejects a uuid-shaped session_id — the two-concept collision (DBA condition 3)', () => {
  const result = validateExecutionEnvelope(envelope({
    execution: { ...envelope().execution, session_id: EXEC_ID },
  }))
  assert.equal(result.ok, false)
  const error = result.errors.find((e) => e.includes('must not be uuid-shaped'))
  assert.ok(error, result.errors.join('; '))
  assert.ok(error.includes('own separately named column'))
})

test('A2a accepts the named-session text convention and null for un-shimmed', () => {
  for (const session_id of [SESSION, 'sess-e2e-0001', 'builder-20260622-172335', null]) {
    const result = validateExecutionEnvelope(envelope({
      execution: { ...envelope().execution, session_id },
    }))
    assert.equal(result.ok, true, `expected ${session_id} accepted: ${result.errors.join('; ')}`)
  }
})

test('A2a requires the doctrine frame (what was in force, per B1/B3)', () => {
  const result = validateExecutionEnvelope(envelope({ frame: { doctrine_snapshot_id: '' } }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('doctrine_snapshot_id')))
})

test('A2a requires what ran and an outcome, and does not police the outcome vocabulary', () => {
  const noSubject = validateExecutionEnvelope(envelope({ subject: {} }))
  assert.equal(noSubject.ok, false)
  assert.ok(noSubject.errors.some((e) => e.includes('subject.source_namespace')))

  const noOutcome = validateExecutionEnvelope(envelope({ outcome: { status: '' } }))
  assert.equal(noOutcome.ok, false)
  assert.ok(noOutcome.errors.some((e) => e.includes('outcome.status')))

  // No vocabulary is ratified for outcome, so any non-empty status is fine.
  for (const status of ['completed', 'weird-thing', 'x'.repeat(200)]) {
    const result = validateExecutionEnvelope(envelope({ outcome: { status } }))
    assert.equal(result.ok, true, result.errors.join('; '))
  }
})

// ------------------------------------------------------------ marker/tri-state

test('A2a: marker absence is illegal, not treated as "not enabled"', () => {
  const { census, ...withoutMarker } = envelope()
  const result = validateExecutionEnvelope(withoutMarker)
  assert.equal(result.ok, false)
  const error = result.errors.find((e) => e.includes('absence is illegal'))
  assert.ok(error, result.errors.join('; '))
  assert.ok(error.includes('30% sampled-missing'))
})

test('A2a: the marker is a strict tri-state', () => {
  assert.equal(censusMarkerState({ enabled: false, sampled: false, rate: null }), 'not-enabled')
  assert.equal(censusMarkerState({ enabled: true, sampled: false, rate: '1/20' }), 'enabled-not-sampled')
  assert.equal(censusMarkerState({ enabled: true, sampled: true, rate: '1/20' }), 'enabled-sampled')

  // sampled without enabled is incoherent
  assert.equal(censusMarkerState({ enabled: false, sampled: true, rate: null }), null)
  // disabled but carrying a rate
  assert.equal(censusMarkerState({ enabled: false, sampled: false, rate: '1/20' }), null)
})

test('A2a: a disabled census must carry rate=null', () => {
  const result = validateExecutionEnvelope(envelope({
    census: { enabled: false, sampled: false, rate: '1/20' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('tri-state')))
})

test('A2a: master-switch-only means a null rate (1/1), which is legal', () => {
  const result = validateExecutionEnvelope(envelope({
    census: { enabled: true, sampled: false, rate: null },
  }))
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('A2a: a rate must satisfy 0 <= N <= M', () => {
  for (const rate of ['20/1', '1/0', 'nope', '1']) {
    const result = validateExecutionEnvelope(envelope({
      census: { enabled: true, sampled: false, rate },
    }))
    assert.equal(result.ok, false, `expected rejection for rate=${rate}`)
  }
  for (const rate of ['1/1', '1/20', '0/5']) {
    const result = validateExecutionEnvelope(envelope({
      census: { enabled: true, sampled: false, rate },
    }))
    assert.equal(result.ok, true, `expected acceptance for rate=${rate}: ${result.errors.join('; ')}`)
  }
})

// ------------------------------------------------------------- join invariant

test('A2a: sampled requires a census_id; unsampled forbids one', () => {
  const sampledNoId = validateExecutionEnvelope(envelope({
    execution: { ...envelope().execution, census_id: null },
    census: { enabled: true, sampled: true, rate: '1/20' },
  }))
  assert.equal(sampledNoId.ok, false)
  assert.ok(sampledNoId.errors.some((e) => e.includes('census_id is required when')))

  const unsampledWithId = validateExecutionEnvelope(envelope({
    execution: { ...envelope().execution, census_id: CENSUS_ID },
  }))
  assert.equal(unsampledWithId.ok, false)
  assert.ok(unsampledWithId.errors.some((e) => e.includes('must be null unless')))
})

test('A2a: census_id must be a uuid when present', () => {
  const result = validateExecutionEnvelope(envelope({
    execution: { ...envelope().execution, census_id: 'census-1' },
    census: { enabled: true, sampled: true, rate: '1/20' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('census_id must be a uuid')))
})

test('A2a join: a sampled execution has exactly one Tier-3 report', () => {
  const sampled = envelope({
    execution: { ...envelope().execution, census_id: CENSUS_ID },
    census: { enabled: true, sampled: true, rate: '1/20' },
  })
  assert.equal(assertCensusJoin(sampled, [censusReport()]).ok, true)
})

test('A2a join: a sampled execution with zero findings still owes its row', () => {
  const sampled = envelope({
    execution: { ...envelope().execution, census_id: CENSUS_ID },
    census: { enabled: true, sampled: true, rate: '1/20' },
  })
  // A zero-finding sampled execution is exactly the case A5's denominator depends on.
  const result = assertCensusJoin(sampled, [censusReport()])
  assert.equal(result.ok, true, result.errors.join('; '))

  const missing = assertCensusJoin(sampled, [])
  assert.equal(missing.ok, false)
  assert.ok(missing.errors[0].includes('exactly one census report; found 0'))
})

test('A2a join: a duplicated report is rejected, not silently deduplicated', () => {
  const sampled = envelope({
    execution: { ...envelope().execution, census_id: CENSUS_ID },
    census: { enabled: true, sampled: true, rate: '1/20' },
  })
  const result = assertCensusJoin(sampled, [censusReport(), censusReport()])
  assert.equal(result.ok, false)
  assert.ok(result.errors[0].includes('found 2'))
})

test('A2a join: an unsampled execution must have no Tier-3 row', () => {
  const result = assertCensusJoin(envelope(), [censusReport()])
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('must have no census report')))
})

test('A2a join: a report naming the wrong execution is caught', () => {
  const sampled = envelope({
    execution: { ...envelope().execution, census_id: CENSUS_ID },
    census: { enabled: true, sampled: true, rate: '1/20' },
  })
  const mismatched = censusReport({ execution_id: '7d8f1e2a-0000-4000-8000-000000000000' })
  const result = assertCensusJoin(sampled, [mismatched])
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('does not match this execution')))
})

test('A2a join: validates the envelope before reasoning about the join', () => {
  const broken = envelope({ execution: { ...envelope().execution, execution_id: 'nope' } })
  const result = assertCensusJoin(broken, [])
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('must be a uuid')))
})

// ------------------------------------------------------------------- naming

test('A2a enforces the delta naming guard at every depth', () => {
  for (const payload of [
    envelope({ execution: { ...envelope().execution, delta: 'x' } }),
    envelope({ census: { enabled: true, sampled: false, rate: '1/20', base_version: 2 } }),
    envelope({ subject: { source_namespace: 'conduit', delta_steps: [] } }),
  ]) {
    const result = validateExecutionEnvelope(payload)
    assert.equal(result.ok, false)
    assert.ok(result.errors.some((e) => e.includes('B-series checkpoint key')))
  }
})
