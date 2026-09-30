const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  parseCensusFlags,
  decideCensusMarker,
  buildExecutionEnvelope,
  toExecutionInsert,
  prepareExecutionEmission,
} = require('../lib/census-emit.ts')

const EXEC = '3f2504e0-4f89-41d3-9a0c-0305e82c3301'
const DOC = 'sha256:' + 'a'.repeat(64)

// ── flag surface: the a5991156 fail-loud rules ───────────────────────────────

test('A3: no flags means the census is disabled', () => {
  const r = parseCensusFlags([])
  assert.equal(r.ok, true)
  assert.deepEqual(r.flags, { census: false, rate: null, onReview: false })
})

test('A3: --census alone is 1/1 (master switch only)', () => {
  const r = parseCensusFlags(['--census'])
  assert.equal(r.ok, true)
  assert.deepEqual(r.flags, { census: true, rate: null, onReview: true === false ? true : false })
})

test('A3: --census-rate without --census is an ERROR, never silently inert', () => {
  const r = parseCensusFlags(['--census-rate', '1/20'])
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('would never take effect')))
})

test('A3: --census-on-review without --census is an ERROR', () => {
  const r = parseCensusFlags(['--census-on-review'])
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('review trigger would never fire')))
})

test('A3: malformed rates are rejected', () => {
  for (const rate of ['20/1', '1/0', 'nope', '1']) {
    const r = parseCensusFlags(['--census', '--census-rate', rate])
    assert.equal(r.ok, false, `expected rejection for ${rate}`)
  }
})

test('A3: 1/1 is valid and means every execution', () => {
  assert.equal(parseCensusFlags(['--census', '--census-rate', '1/1']).ok, true)
})

test('A3: both forms of --census-rate parse identically', () => {
  const spaced = parseCensusFlags(['--census', '--census-rate', '1/20'])
  const inline = parseCensusFlags(['--census', '--census-rate=1/20'])
  assert.equal(spaced.ok, true)
  assert.equal(inline.ok, true)
  assert.equal(spaced.flags.rate, '1/20')
  assert.equal(inline.flags.rate, '1/20')
})

test('A3: a near-miss flag is an error, not a silently different census', () => {
  const r = parseCensusFlags(['--census', '--census-rat', '1/20'])
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('unrecognised census flag')))
})

test('A3: a duplicated flag is an error', () => {
  const r = parseCensusFlags(['--census', '--census', '--census-rate', '1/5'])
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('more than once')))
})

test('A3: --census with a value is an error', () => {
  assert.equal(parseCensusFlags(['--census=1/20']).ok, false)
})

test('A3: non-census argv is ignored entirely', () => {
  const r = parseCensusFlags(['--port', '3999', '--hot', '--census', '--census-rate', '1/5'])
  assert.equal(r.ok, true)
  assert.equal(r.flags.rate, '1/5')
})

// ── sampling: deterministic, and the union rule ──────────────────────────────

test('A3: census disabled yields the disabled tri-state with a null rate', () => {
  const d = decideCensusMarker({ flags: { census: false, rate: null, onReview: false }, executionId: EXEC, reviewEvent: true })
  assert.deepEqual(d.marker, { enabled: false, sampled: false, rate: null })
  assert.equal(d.censusId, null)
  assert.equal(d.reason, 'census-disabled')
})

test('A3: --census alone samples every execution', () => {
  const d = decideCensusMarker({ flags: { census: true, rate: null, onReview: false }, executionId: EXEC, reviewEvent: false })
  assert.deepEqual(d.marker, { enabled: true, sampled: true, rate: '1/1' })
  assert.ok(d.censusId)
})

test('A3: sampling is DETERMINISTIC for a given execution id', () => {
  const flags = { census: true, rate: '1/20', onReview: false }
  const first = decideCensusMarker({ flags, executionId: EXEC, reviewEvent: false })
  for (let i = 0; i < 5; i++) {
    const again = decideCensusMarker({ flags, executionId: EXEC, reviewEvent: false })
    assert.equal(again.marker.sampled, first.marker.sampled, 're-deciding must not flip')
    assert.equal(again.reason, first.reason)
  }
})

test('A3: 1/N selects roughly 1 in N across distinct ids', () => {
  const flags = { census: true, rate: '1/5', onReview: false }
  const ids = Array.from({ length: 400 }, (_, i) =>
    `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`)
  const sampled = ids.filter((id) =>
    decideCensusMarker({ flags, executionId: id, reviewEvent: false }).marker.sampled)
  assert.ok(sampled.length > 40 && sampled.length < 140,
    `expected roughly 80 of 400 at 1/5, got ${sampled.length}`)
})

test('A3: the union rule — a review event fires the census even when the rate missed', () => {
  // Find an id the 1/1000 rate misses, so the union is genuinely exercised.
  const flags = { census: true, rate: '1/1000', onReview: true }
  let missed = null
  for (let i = 0; i < 200 && !missed; i++) {
    const id = `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`
    if (!decideCensusMarker({ flags, executionId: id, reviewEvent: false }).marker.sampled) missed = id
  }
  assert.ok(missed, 'expected at least one id the rate misses')
  const withReview = decideCensusMarker({ flags, executionId: missed, reviewEvent: true })
  assert.equal(withReview.marker.sampled, true)
  assert.equal(withReview.reason, 'review-trigger')
  const withoutReview = decideCensusMarker({ flags, executionId: missed, reviewEvent: false })
  assert.equal(withoutReview.marker.sampled, false)
  assert.equal(withoutReview.reason, 'rate-missed')
})

test('A3: census_id is non-null exactly when sampled', () => {
  const flags = { census: true, rate: '1/2', onReview: true }
  for (let i = 0; i < 50; i++) {
    const id = `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`
    const d = decideCensusMarker({ flags, executionId: id, reviewEvent: i % 3 === 0 })
    assert.equal(d.censusId !== null, d.marker.sampled, 'census_id must track sampled')
  }
})

test('A3: rate-missed is distinct from rate-selected', () => {
  // 1/2, not 1/1000: at 1/1000 a 50-id sample is all misses and both outcomes are
  // unreachable, so the test was asserting something statistically impossible.
  const flags = { census: true, rate: '1/2', onReview: false }
  const reasons = new Set()
  for (let i = 0; i < 50; i++) {
    const id = `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`
    reasons.add(decideCensusMarker({ flags, executionId: id, reviewEvent: false }).reason)
  }
  assert.ok(reasons.has('rate-missed'), 'expected a rate-missed outcome')
  assert.ok(reasons.has('rate-selected'), 'expected a rate-selected outcome')
})

// ── envelope + insert ───────────────────────────────────────────────────────

const ctx = {
  sourceNamespace: 'conduit',
  doctrineSnapshotId: DOC,
  outcomeStatus: 'completed',
  executedByRole: 'engineer-iii',
  executedByModel: 'opencode/space-bunny-free',
}

test('A3: the insert is parameterised — no caller text is concatenated into SQL', () => {
  const r = prepareExecutionEmission(['--census', '--census-rate', '1/1'], { ...ctx, sessionId: "'; DROP TABLE x; --" })
  assert.equal(r.ok, true, r.errors.join('; '))
  assert.equal(r.insert.text.includes('$1'), true)
  assert.ok(r.insert.values.includes("'; DROP TABLE x; --"), 'value must be passed as a parameter')
  assert.equal(r.insert.values.length, 14)
})

test('A3: a uuid-shaped session_id is rejected before the statement is produced', () => {
  const r = prepareExecutionEmission(['--census'], { ...ctx, sessionId: '11111111-1111-4111-8111-111111111111' })
  assert.equal(r.ok, false)
  assert.equal(r.insert, null, 'a rejected envelope must not yield a statement')
  assert.ok(r.errors.some((e) => e.includes('must not be uuid-shaped')))
})

test('A3: flag errors stop emission before any SQL exists', () => {
  const r = prepareExecutionEmission(['--census-rate', '1/20'], ctx)
  assert.equal(r.ok, false)
  assert.equal(r.insert, null)
  assert.equal(r.envelope, null)
})

test('A3: the disabled-census row still carries the marker (A3 acceptance)', () => {
  const r = prepareExecutionEmission([], ctx)
  assert.equal(r.ok, true)
  assert.equal(r.insert.values[2], false, 'census_enabled must be present and false')
  assert.equal(r.insert.values[3], false, 'census_sampled must be present and false')
  assert.equal(r.insert.values[4], null, 'census_id must be null when not sampled')
})

test('A3: a sampled row carries census_id and the rate', () => {
  const r = prepareExecutionEmission(['--census', '--census-rate', '1/1'], ctx)
  assert.equal(r.ok, true)
  assert.equal(r.insert.values[2], true)
  assert.equal(r.insert.values[3], true)
  assert.ok(r.insert.values[4], 'census_id required when sampled')
  assert.equal(r.insert.values[5], '1/1')
})

test('A3: column order matches the applied migration 068', () => {
  const r = prepareExecutionEmission(['--census'], ctx)
  const cols = r.insert.text
    .slice(r.insert.text.indexOf('(') + 1, r.insert.text.indexOf(')'))
    .split(',')
    .map((s) => s.trim());
  assert.deepEqual(cols, [
    'execution_id', 'session_id', 'census_enabled', 'census_sampled', 'census_id', 'census_rate',
    'source_namespace', 'ticket_id', 'work_item_id', 'doctrine_snapshot_id',
    'procedure_card_set_hash', 'outcome_status', 'outcome_detail', 'created_at',
  ])
})

test('A3: optional fields are omitted rather than sent as undefined', () => {
  const r = prepareExecutionEmission(['--census'], ctx)
  assert.equal(r.insert.values[7], null, 'ticket_id should be null when absent')
  assert.equal(r.insert.values[10], null, 'procedure_card_set_hash should be null when absent')
  assert.ok(!JSON.stringify(r.insert.values).includes('undefined'))
})

test('A3: a supplied executionId is respected, so a replay is reproducible', () => {
  const a = prepareExecutionEmission(['--census', '--census-rate', '1/1'], ctx, { executionId: EXEC })
  assert.equal(a.envelope.execution.execution_id, EXEC)
  assert.equal(a.insert.values[0], EXEC)
})
