const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  buildCensusReportIndex,
  CENSUS_CATEGORIES,
  CENSUS_CATEGORY_PRECEDENCE,
  CENSUS_EVIDENCE_KINDS,
  CENSUS_TRIGGERS,
  validateCensusReportMetadata,
} = require('../lib/census-report.ts')

const card = (assetId, overrides = {}) => ({
  asset_id: assetId,
  instance_id: `inst-${assetId}`,
  record_type: 'procedure_card',
  record_id: assetId,
  version: 1,
  ...overrides,
})

const finding = (assetId, category, overrides = {}) => ({
  card: card(assetId),
  category,
  anticipated: true,
  evidence: [{ kind: 'observation', ref: `ref:${assetId}` }],
  ...overrides,
})

const report = (overrides = {}) => ({
  census: {
    schema_version: 1,
    census_id: 'census-1',
    execution_id: 'exec-1',
    sampled: true,
    rate: '1/20',
    triggers: ['rate'],
    executed_by_role: 'engineer-iii',
    executed_by_model: 'opencode/space-bunny-free',
  },
  frame: { doctrine_snapshot_id: 'sha256:' + 'a'.repeat(64) },
  findings: [],
  interview: { status: 'pending' },
  recommended_updates: [],
  ...overrides,
})

test('A2b accepts a well-formed census report', () => {
  const result = validateCensusReportMetadata(report({
    findings: [finding('card-a', 'MISSING')],
  }))
  assert.equal(result.ok, true, result.errors.join('; '))
  assert.deepEqual(result.errors, [])
})

test('A2b accepts a sampled execution with zero findings (a complete record of nothing observed)', () => {
  const result = validateCensusReportMetadata(report({ findings: [] }))
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('A2b refuses the singular trigger key; triggers[] is the only representation', () => {
  const result = validateCensusReportMetadata(report({
    census: { ...report().census, trigger: 'rate' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('trigger is forbidden')))
})

test('A2b refuses an empty, unknown, or duplicated triggers array', () => {
  for (const triggers of [[], ['nope'], ['rate', 'rate']]) {
    const result = validateCensusReportMetadata(report({
      census: { ...report().census, triggers },
    }))
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(triggers)}`)
  }
})

test('A2b allows both triggers to fire at once (the union rule is not a conflict)', () => {
  const result = validateCensusReportMetadata(report({
    census: { ...report().census, triggers: ['rate', 'on-review'] },
  }))
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('A2b requires sampled true — unsampled executions carry no Tier-3 row', () => {
  const result = validateCensusReportMetadata(report({
    census: { ...report().census, sampled: false },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('sampled must be true')))
})

test('A2b requires both role and model — session identity is (role, model, session_id)', () => {
  const noModel = validateCensusReportMetadata(report({
    census: { ...report().census, executed_by_model: '' },
  }))
  assert.equal(noModel.ok, false)
  assert.ok(noModel.errors.some((e) => e.includes('executed_by_model')))

  const noRole = validateCensusReportMetadata(report({
    census: { ...report().census, executed_by_role: '' },
  }))
  assert.equal(noRole.ok, false)
  assert.ok(noRole.errors.some((e) => e.includes('executed_by_role')))
})

test('A2b requires the doctrine snapshot frame', () => {
  const result = validateCensusReportMetadata(report({ frame: { doctrine_snapshot_id: '' } }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('doctrine_snapshot_id')))
})

test('A2b rejects a rate that does not satisfy 0 <= N <= M', () => {
  for (const rate of ['20/1', '1/0', 'nope', '1']) {
    const result = validateCensusReportMetadata(report({
      census: { ...report().census, rate },
    }))
    assert.equal(result.ok, false, `expected rejection for rate=${rate}`)
  }
})

test('A2b accepts rate 1/1 (means every execution) and a null rate', () => {
  for (const rate of ['1/1', null]) {
    const result = validateCensusReportMetadata(report({
      census: { ...report().census, rate },
    }))
    assert.equal(result.ok, true, `expected acceptance for rate=${rate}: ${result.errors.join('; ')}`)
  }
})

test('A2b keeps the evidence vocabulary closed', () => {
  const open = validateCensusReportMetadata(report({
    findings: [{
      card: card('card-a'),
      category: 'MISSING',
      anticipated: true,
      evidence: [{ kind: 'invented_by_caller', ref: 'x' }],
    }],
  }))
  assert.equal(open.ok, false)
  assert.ok(open.errors.some((e) => e.includes('closed evidence vocabulary')))

  for (const kind of CENSUS_EVIDENCE_KINDS) {
    const result = validateCensusReportMetadata(report({
      findings: [{
        card: card('card-a'),
        category: 'MISSING',
        anticipated: true,
        evidence: [{ kind, ref: 'x' }],
      }],
    }))
    assert.equal(result.ok, true, `expected ${kind} to be accepted: ${result.errors.join('; ')}`)
  }
})

test('A2b rejects a category outside the seven', () => {
  for (const category of CENSUS_CATEGORIES) {
    const result = validateCensusReportMetadata(report({ findings: [finding('card-a', category)] }))
    assert.equal(result.ok, true, `expected ${category} accepted: ${result.errors.join('; ')}`)
  }
  const bogus = validateCensusReportMetadata(report({ findings: [finding('card-a', 'SIDEWAYS')] }))
  assert.equal(bogus.ok, false)
  assert.ok(bogus.errors.some((e) => e.includes('seven categories')))
})

test('A2b requires anticipated to be boolean — it is the orthogonal provenance axis', () => {
  const result = validateCensusReportMetadata(report({
    findings: [finding('card-a', 'MISSING', { anticipated: 'yes' })],
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('anticipated must be a boolean')))
})

test('A2b interview outcome keys are refused until the roundtable rules (superseded by D22)', () => {
  // Retained as a shape-closure check: the D22 vocabulary is closed, so a free-form
  // 'answers' blob is still refused even now that an outcome shape exists.
  const result = validateCensusReportMetadata(report({
    interview: { status: 'pending', answers: ['a'] },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('not part of the Decision 22 shape')))
})

test('A2b enforces the delta naming guard at every depth', () => {
  for (const metadata of [
    report({ census: { ...report().census, delta: 'x' } }),
    report({ findings: [{ ...finding('card-a', 'MISSING'), delta: 'x' }] }),
    report({ frame: { doctrine_snapshot_id: 'x', base_version: 3 } }),
  ]) {
    const result = validateCensusReportMetadata(metadata)
    assert.equal(result.ok, false)
    assert.ok(result.errors.some((e) => e.includes('B-series checkpoint key')))
  }
})

test('A2b requires supporting_census_ids so A5 same-gap signals stay auditable', () => {
  const result = validateCensusReportMetadata(report({
    recommended_updates: [{ kind: 'new-card', target_asset_id: null, rationale: 'why' }],
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('supporting_census_ids')))
})

test('A2b exports one precedence order for A4 rather than a second implementation', () => {
  assert.deepEqual([...CENSUS_CATEGORY_PRECEDENCE], [
    'MISSING', 'INCORRECT', 'OVERRIDDEN', 'REDUNDANT', 'UNUSED', 'RECOMMENDED',
  ])
  // EMERGENT is the provenance axis: it is a category, but never a precedence step.
  assert.ok(CENSUS_CATEGORIES.includes('EMERGENT'))
  assert.ok(!CENSUS_CATEGORY_PRECEDENCE.includes('EMERGENT'))
  assert.deepEqual([...CENSUS_TRIGGERS], ['rate', 'on-review'])
})

test('A2b index counts per-card findings, categories, and empty reports', () => {
  const reports = [
    { id: 'r1', created_at: '2026-09-27T00:00:00Z', metadata: report({
      findings: [finding('card-a', 'MISSING'), finding('card-b', 'UNUSED')],
    }) },
    { id: 'r2', created_at: '2026-09-27T00:01:00Z', metadata: report({
      census: { ...report().census, census_id: 'census-2' },
      findings: [finding('card-a', 'MISSING', { anticipated: false })],
    }) },
    { id: 'r3', created_at: '2026-09-27T00:02:00Z', metadata: report({ findings: [] }) },
  ]
  const index = buildCensusReportIndex(reports)
  assert.equal(index.read_only, true)
  assert.equal(index.report_count, 3)
  assert.equal(index.report_ids_with_no_findings, 1)
  assert.equal(index.doctrine_snapshot_count, 1)
  assert.deepEqual(index.trigger_counts, { rate: 3 })
  assert.equal(index.category_counts.MISSING, 2)
  assert.equal(index.category_counts.UNUSED, 1)

  const cardA = index.cards.find((c) => c.asset_id === 'card-a')
  assert.equal(cardA.finding_count, 2)
  assert.equal(cardA.report_count, 2)
  assert.equal(cardA.category_counts.MISSING, 2)
  assert.equal(cardA.anticipated_finding_count, 1)
})

test('A2b index applies no threshold — that policy is A5', () => {
  const reports = Array.from({ length: 10 }, (_, i) => ({
    id: `r${i}`,
    created_at: '2026-09-27T00:00:00Z',
    metadata: report({ findings: [finding('card-a', 'MISSING')] }),
  }))
  const index = buildCensusReportIndex(reports)
  // 10/10 sampled-missing would trip A5's 30% rule; A2b must not encode that judgement.
  assert.equal(index.cards[0].finding_count, 10)
  assert.equal(index.cards[0].high_activation, undefined)
  assert.ok(!('threshold' in index) && !('alerts' in index) && !('signals' in index))
})

test('A2b index is empty-safe and sorts deterministically', () => {
  const empty = buildCensusReportIndex([])
  assert.equal(empty.report_count, 0)
  assert.deepEqual(empty.cards, [])
  assert.equal(empty.report_ids_with_no_findings, 0)

  const reports = [
    { id: 'r1', created_at: 'x', metadata: report({ findings: [finding('card-z', 'MISSING')] }) },
    { id: 'r2', created_at: 'x', metadata: report({ findings: [finding('card-a', 'MISSING')] }) },
  ]
  const forward = buildCensusReportIndex(reports).cards.map((c) => c.asset_id)
  const reverse = buildCensusReportIndex([...reports].reverse()).cards.map((c) => c.asset_id)
  assert.deepEqual(forward, reverse)
  assert.deepEqual(forward, ['card-a', 'card-z'])
})

test('A2b Q6: daily_counts gives A6 a measurement to size retention against', () => {
  const reports = [
    { id: 'r1', created_at: '2026-09-27T01:00:00Z', metadata: report({ findings: [finding('card-a', 'MISSING')] }) },
    { id: 'r2', created_at: '2026-09-27T23:59:00Z', metadata: report({ findings: [] }) },
    { id: 'r3', created_at: '2026-09-28T00:00:00Z', metadata: report({ findings: [finding('card-b', 'UNUSED')] }) },
  ]
  const index = buildCensusReportIndex(reports)
  assert.equal(index.observed_day_count, 2)
  assert.deepEqual(index.daily_counts, [
    { day: '2026-09-27', report_count: 2, finding_count: 1, report_count_no_findings: 1 },
    { day: '2026-09-28', report_count: 1, finding_count: 1, report_count_no_findings: 0 },
  ])
})

test('A2b Q6: a truncated read reports a floor, and the limitation says so', () => {
  const reports = [{ id: 'r1', created_at: '2026-09-27T00:00:00Z', metadata: report({}) }]
  const truncated = buildCensusReportIndex(reports, { truncated: true })
  assert.equal(truncated.truncated, true)
  // The floor caveat is a static, always-true statement — not a claim that this call was
  // truncated — so a reader cannot mistake a bounded read for a complete one.
  assert.ok(truncated.limitation.includes('floor on true daily volume'))
  assert.equal(buildCensusReportIndex(reports, { truncated: false }).truncated, false)
  assert.equal(buildCensusReportIndex(reports).truncated, false)
})

test('A2b Q6: an unparseable created_at is skipped rather than bucketed as garbage', () => {
  const index = buildCensusReportIndex([
    { id: 'r1', created_at: 'not-a-date', metadata: report({}) },
    { id: 'r2', created_at: '2026-09-27T00:00:00Z', metadata: report({}) },
  ])
  assert.equal(index.observed_day_count, 1)
  assert.equal(index.report_count, 2)
})

// ── Decision 22: interview shape ────────────────────────────────────────────

// A self-report is the executing agent describing its own run, so it is by definition NOT
// independent of the executor. Marking it independent is the contradiction Decision 22 exists
// to prevent, so the fixture states it the coherent way.
const conducted = (overrides = {}) => ({
  status: 'conducted',
  evidence_class: 'self_report',
  interviewer: {
    role: 'engineer-iii',
    model_id: 'opencode/space-bunny-free',
    independent_of_executor: false,
  },
  ...overrides,
})

test('D22: a conducted self-report by the executing agent is valid', () => {
  const result = validateCensusReportMetadata(report({ interview: conducted() }))
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('D22: evidence_class is REQUIRED when conducted — who is asking is the deciding fact', () => {
  const result = validateCensusReportMetadata(report({
    interview: { status: 'conducted', interviewer: conducted().interviewer },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('evidence_class is required')))
})

test('D22: interviewer identity is required when conducted', () => {
  const result = validateCensusReportMetadata(report({
    interview: { status: 'conducted', evidence_class: 'self_report' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('interviewer is required')))
})

test('D22: independent_of_executor must be stated, never inferred', () => {
  const result = validateCensusReportMetadata(report({
    interview: conducted({ interviewer: { role: 'engineer-iii' } }),
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('independent_of_executor must be a boolean')))
})

test('D22: no self-review is enforced — the executing agent cannot be the independent interviewer', () => {
  const result = validateCensusReportMetadata(report({
    interview: conducted({
      evidence_class: 'independent_interrogation',
      interviewer: { role: 'engineer-iii', independent_of_executor: true },
    }),
  }))
  assert.equal(result.ok, false)
  const error = result.errors.find((e) => e.includes("executing agent's own role"))
  assert.ok(error, result.errors.join('; '))
  assert.ok(error.includes('forbids adjudicating your own participation'))
})

test('D22: a self-report must actually be the executing agent', () => {
  const result = validateCensusReportMetadata(report({
    interview: conducted({
      interviewer: { role: 'somebody-else', independent_of_executor: true },
    }),
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('a self-report must be the executing agent')))
})

test('D22: a genuine independent interrogation by another role is valid', () => {
  const result = validateCensusReportMetadata(report({
    interview: conducted({
      evidence_class: 'independent_interrogation',
      interviewer: { role: 'reviewer', independent_of_executor: true },
    }),
  }))
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('D22: declined is a first-class outcome and requires a recorded reason', () => {
  const bare = validateCensusReportMetadata(report({ interview: { status: 'declined' } }))
  assert.equal(bare.ok, false)
  assert.ok(bare.errors.some((e) => e.includes('decline_reason is required')))

  const stated = validateCensusReportMetadata(report({
    interview: { status: 'declined', decline_reason: 'out of scope for this run' },
  }))
  assert.equal(stated.ok, true, stated.errors.join('; '))
})

test('D22: pending carries no evidence class or interviewer', () => {
  assert.equal(validateCensusReportMetadata(report({ interview: { status: 'pending' } })).ok, true)

  const withExtras = validateCensusReportMetadata(report({
    interview: { status: 'pending', evidence_class: 'self_report' },
  }))
  assert.equal(withExtras.ok, false)
  assert.ok(withExtras.errors.some((e) => e.includes('only meaningful when status is conducted')))
})

test('D22: an unknown key is still refused — the shape is closed', () => {
  const result = validateCensusReportMetadata(report({
    interview: { status: 'pending', outcome: 'looks fine to me' },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('not part of the Decision 22 shape')))
})

test('D22: an unknown status or evidence class is rejected', () => {
  assert.equal(validateCensusReportMetadata(report({ interview: { status: 'sorta' } })).ok, false)
  assert.equal(validateCensusReportMetadata(report({
    interview: { status: 'conducted', evidence_class: 'vibes', interviewer: conducted().interviewer },
  })).ok, false)
})
