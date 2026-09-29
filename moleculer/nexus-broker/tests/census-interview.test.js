const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  validateCensusInterviewOutcome,
  CENSUS_INTERVIEW_EVIDENCE_CLASSES,
  CENSUS_INTERVIEW_STATUSES,
  CENSUS_INTERVIEW_SCHEMA_VERSION,
} = require('../lib/census-interview.ts')

const EXEC = { role: 'engineer-iii', model: 'opencode/space-bunny-free' }
const CENSUS = '9c858901-8a57-4791-81fe-4c455f099c9c'
const EXEC_ID = '3f2504e0-4f89-41d3-9a0c-0305e82c3301'

const outcome = (overrides = {}) => ({
  schemaVersion: CENSUS_INTERVIEW_SCHEMA_VERSION,
  census_id: CENSUS,
  execution_id: EXEC_ID,
  status: 'conducted',
  evidenceClass: 'self_report',
  interviewer: { role: EXEC.role, modelId: EXEC.model, independentOfExecutor: false },
  ...overrides,
})

test('D22: a self-report by the executing agent is valid', () => {
  const r = validateCensusInterviewOutcome(outcome(), EXEC)
  assert.equal(r.ok, true, r.errors.join('; '))
})

test('D22: an independent interrogation by a different role is valid', () => {
  const r = validateCensusInterviewOutcome(
    outcome({
      evidenceClass: 'independent_interrogation',
      interviewer: { role: 'reviewer', modelId: 'some/model', independentOfExecutor: true },
    }),
    EXEC,
  )
  assert.equal(r.ok, true, r.errors.join('; '))
})

test('D22: evidenceClass is REQUIRED when conducted — who is asking is the deciding fact', () => {
  const o = outcome()
  delete o.evidenceClass
  const r = validateCensusInterviewOutcome(o, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('evidenceClass is required')))
})

test('D22: interviewer identity is required when conducted', () => {
  const o = outcome()
  delete o.interviewer
  const r = validateCensusInterviewOutcome(o, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('interviewer is required')))
})

test('D22: independentOfExecutor must be stated, never inferred', () => {
  const o = outcome({ interviewer: { role: EXEC.role, modelId: EXEC.model } })
  const r = validateCensusInterviewOutcome(o, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('independentOfExecutor must be a boolean')))
})

test('D22: NO SELF-REVIEW — the executor cannot conduct an independent interrogation', () => {
  const r = validateCensusInterviewOutcome(
    outcome({
      evidenceClass: 'independent_interrogation',
      interviewer: { role: EXEC.role, modelId: EXEC.model, independentOfExecutor: true },
    }),
    EXEC,
  )
  assert.equal(r.ok, false)
  const e = r.errors.find((x) => x.includes('cannot be an independent_interrogation'))
  assert.ok(e, r.errors.join('; '))
  assert.ok(e.includes('forbids adjudicating your own participation'))
})

test('D22: a self_report must actually be the executing agent', () => {
  const r = validateCensusInterviewOutcome(
    outcome({ interviewer: { role: 'reviewer', modelId: 'x/y', independentOfExecutor: true } }),
    EXEC,
  )
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('a self-report must be the executing agent')))
})

test('D22: the same role on a different model is a different agent', () => {
  const r = validateCensusInterviewOutcome(
    outcome({
      evidenceClass: 'independent_interrogation',
      interviewer: { role: EXEC.role, modelId: 'other/model', independentOfExecutor: true },
    }),
    EXEC,
  )
  assert.equal(r.ok, true, r.errors.join('; '))
})

test('D22: declined requires a recorded reason and carries no interviewer', () => {
  const bare = validateCensusInterviewOutcome(
    { ...outcome(), status: 'declined', evidenceClass: undefined, interviewer: undefined },
    EXEC,
  )
  assert.equal(bare.ok, false)
  assert.ok(bare.errors.some((e) => e.includes('declineReason is required')))

  const stated = validateCensusInterviewOutcome(
    {
      ...outcome(), status: 'declined', evidenceClass: undefined, interviewer: undefined,
      declineReason: 'out of scope for this run',
    },
    EXEC,
  )
  assert.equal(stated.ok, true, stated.errors.join('; '))
})

test('D22: pending carries no evidence class or interviewer', () => {
  const clean = { ...outcome(), status: 'pending', evidenceClass: undefined, interviewer: undefined }
  assert.equal(validateCensusInterviewOutcome(clean, EXEC).ok, true)

  const dirty = { ...outcome(), status: 'pending' }
  const r = validateCensusInterviewOutcome(dirty, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('only meaningful when status is conducted')))
})

test('D22: the shape is closed — a widened census-report field is refused', () => {
  const r = validateCensusInterviewOutcome({ ...outcome(), evidence_class: 'self_report' }, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('not part of the Decision 22 outcome shape')))
  assert.ok(r.errors.some((e) => e.includes('follow-on record')))
})

test('D22: unknown status and evidence class are rejected', () => {
  assert.equal(validateCensusInterviewOutcome({ ...outcome(), status: 'sorta' }, EXEC).ok, false)
  assert.equal(validateCensusInterviewOutcome({ ...outcome(), evidenceClass: 'vibes' }, EXEC).ok, false)
})

test('D22: census_id is required — the outcome attaches to exactly one report', () => {
  const o = outcome(); delete o.census_id
  const r = validateCensusInterviewOutcome(o, EXEC)
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('census_id is required')))
})

test('D22: the self-review check is skipped, not faked, when no executor is supplied', () => {
  // Without the executor identity the rule cannot be evaluated, so the record is still valid
  // on shape. Callers on the write path always pass it.
  const r = validateCensusInterviewOutcome(
    outcome({ evidenceClass: 'independent_interrogation' }),
  )
  assert.equal(r.ok, true, r.errors.join('; '))
})

test('D22: vocabularies are exported and closed', () => {
  assert.deepEqual([...CENSUS_INTERVIEW_EVIDENCE_CLASSES], ['self_report', 'independent_interrogation'])
  assert.deepEqual([...CENSUS_INTERVIEW_STATUSES], ['pending', 'conducted', 'declined'])
})
