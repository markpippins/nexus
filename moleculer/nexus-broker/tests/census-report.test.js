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
  interview: { status: 'pending-ratification' },
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

test('A2b refuses an interview outcome until the roundtable rules', () => {
  const result = validateCensusReportMetadata(report({
    interview: { status: 'pending-ratification', answers: ['a'] },
  }))
  assert.equal(result.ok, false)
  assert.ok(result.errors.some((e) => e.includes('roundtable')))
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
