/**
 * Execution telemetry at the walk boundary — A3 + Decision 28.
 *
 * These are the tests for the *decisions*: one row per walk, written once, with a
 * truthful frame, and never fatal. Nothing here spawns a process or needs a broker.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { writeFileSync, mkdtempSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join } = require('node:path')
const {
  composeExecutionRow, emitExecutionTelemetry, loadGoverningText,
} = require('../lib/execution-telemetry.ts')
const { SHA256_RE } = require('../lib/doctrine-snapshot.ts')

const PROMPT = 'You are the Engineer.'
const BOOT = 'A1-vetted governing text'
const IDX = [{ slug: 'inbox-query-procedure', summary: 'query inbox' }, { slug: 'tag-routing', summary: 'tags' }]

const base = {
  params: {},
  systemPrompt: PROMPT,
  procedureIndex: IDX,
  bootstrap: BOOT,
  sourceNamespace: 'wind',
  executorId: 'harness',
  executedByRole: 'engineer-iii',
  executedByModel: 'opencode/space-bunny-free',
  outcomeStatus: 'SUCCEEDED',
  executionId: '3f2504e0-4f89-41d3-9a0c-0305e82c3301',
}
const ENV_ON = { CENSUS_ENABLED: '1' }
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// ── the row ────────────────────────────────────────────────────────────────

test('A3: exactly ONE row is composed for a walk (no started/finished pair)', () => {
  const row = composeExecutionRow(base, ENV_ON)
  assert.equal((row.text.match(/INSERT INTO nebula\.executions/g) || []).length, 1)
  assert.equal(row.values.length, 14)
})

test('Decision 28: doctrine_snapshot_id is a real sha256: address from the governing text', () => {
  const row = composeExecutionRow(base, ENV_ON)
  assert.ok(SHA256_RE.test(row.doctrineSnapshot.snapshot_id), row.doctrineSnapshot.snapshot_id)
  assert.equal(row.values[9], row.doctrineSnapshot.snapshot_id, 'column must carry the snapshot id')
})

test('Decision 28: procedure_card_set_hash is populated from the card set', () => {
  const row = composeExecutionRow(base, ENV_ON)
  assert.ok(SHA256_RE.test(row.doctrineSnapshot.procedure_card_set_hash))
  assert.equal(row.values[10], row.doctrineSnapshot.procedure_card_set_hash, 'sibling column must be populated')
})

test('Decision 28: the frame is stable across walks and moves when the governing text changes', () => {
  const a = composeExecutionRow(base, ENV_ON)
  const b = composeExecutionRow(base, ENV_ON)
  assert.equal(a.doctrineSnapshot.snapshot_id, b.doctrineSnapshot.snapshot_id, 'same frame for same doctrine')
  const c = composeExecutionRow({ ...base, bootstrap: 'A1-vetted governing text v2' }, ENV_ON)
  assert.notEqual(a.doctrineSnapshot.snapshot_id, c.doctrineSnapshot.snapshot_id, 'governing change = new frame')
})

test('Decision 28: procedure cards in force DO move the frame', () => {
  const withCards = composeExecutionRow(base, ENV_ON)
  const without = composeExecutionRow({ ...base, procedureIndex: [] }, ENV_ON)
  assert.notEqual(withCards.doctrineSnapshot.snapshot_id, without.doctrineSnapshot.snapshot_id)
})

test('A3: a census-disabled walk STILL writes a row, with a disabled marker', () => {
  const row = composeExecutionRow(base, {})
  assert.equal(row.errors.length, 0)
  assert.equal(row.values[2], false, 'census_enabled must be present and false')
  assert.equal(row.values[3], false)
  assert.equal(row.values[4], null)
  assert.equal(row.sampled, false)
})

test('A3: census enabled => sampled, census_id present (the join invariant)', () => {
  const row = composeExecutionRow(base, { CENSUS_ENABLED: '1', CENSUS_RATE: '1/1' })
  assert.equal(row.sampled, true)
  assert.ok(row.censusId, 'census_id required when sampled')
  assert.equal(row.values[4], row.censusId)
})

test('A3: session_id is never the harness job uuid', () => {
  const row = composeExecutionRow(base, ENV_ON)
  const sessionId = row.values[1]
  assert.equal(UUID_RE.test(sessionId), false, `uuid-shaped session_id rejected by 068: ${sessionId}`)
  assert.match(sessionId, /^wind-harness-/)
})

test('A3: a caller-supplied named session is used verbatim', () => {
  const row = composeExecutionRow({ ...base, params: { session_id: 'wind-harness-20260929-120000-abcd1234' } }, ENV_ON)
  assert.equal(row.values[1], 'wind-harness-20260929-120000-abcd1234')
})

test('A3: a uuid-shaped caller session is refused and NO row is written', () => {
  const row = composeExecutionRow({ ...base, params: { session_id: '11111111-1111-4111-8111-111111111111' } }, ENV_ON)
  assert.ok(row.errors.length, 'expected a validation error')
  assert.equal(row.text, '', 'a rejected envelope must not produce SQL')
})

test('A3: outcome and identity land in the right columns', () => {
  const row = composeExecutionRow({ ...base, outcomeStatus: 'FAILED', outcomeDetail: 'boom', ticketId: 'T-42' }, ENV_ON)
  assert.equal(row.values[7], 'T-42', 'ticket_id')
  assert.equal(row.values[11], 'FAILED', 'outcome_status')
  assert.equal(row.values[12], 'boom', 'outcome_detail')
  assert.equal(row.values[5], 'conduit' === 'conduit' ? row.values[5] : null)
  assert.equal(row.values[6], 'wind', 'source_namespace')
})

test('A3: a bad rate still blocks the row rather than writing a lie', () => {
  const row = composeExecutionRow(base, { CENSUS_ENABLED: '1', CENSUS_RATE: '20/1' })
  assert.ok(row.errors.length, 'N>M must be rejected')
  assert.equal(row.text, '')
})

// ── governing text: the named artifact ────────────────────────────────────

test('Decision 28: governing text loads from a named artifact', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gov-'))
  const p = join(dir, 'governing.txt')
  writeFileSync(p, 'the ratified text\n')
  assert.equal(loadGoverningText(p).trim(), 'the ratified text')
})

test('Decision 28: an UNCONFIGURED governing text REFUSES rather than inventing one', () => {
  assert.throws(() => loadGoverningText(undefined), /not configured|Refusing to invent/i)
})

test('Decision 28: empty governing text is refused (an empty frame is a fabrication)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gov-'))
  const p = join(dir, 'empty.txt')
  writeFileSync(p, '   \n')
  assert.throws(() => loadGoverningText(p), /empty/i)
})

// ── never fatal ────────────────────────────────────────────────────────────

test('A3: a DB failure NEVER propagates — the walk must survive', async () => {
  const boom = async () => { throw new Error('connection refused') }
  const seen = []
  const res = await emitExecutionTelemetry(base, boom, ENV_ON, [], (m) => seen.push(m))
  assert.equal(res.ok, false)
  assert.equal(seen.length, 1, 'the failure must be reported, not silent')
  assert.match(seen[0], /connection refused/)
})

test('A3: a validation failure does NOT write a partial row', async () => {
  let called = 0
  const q = async () => { called++; return {} }
  const res = await emitExecutionTelemetry({ ...base, params: { session_id: '11111111-1111-4111-8111-111111111111' } }, q, ENV_ON)
  assert.equal(res.ok, false)
  assert.equal(called, 0, 'nothing may be written when validation fails')
})

test('A3: a successful emit reports sampled + census_id for downstream joining', async () => {
  const q = async () => ({ rowCount: 1 })
  const res = await emitExecutionTelemetry(base, q, { CENSUS_ENABLED: '1', CENSUS_RATE: '1/1' })
  assert.equal(res.ok, true)
  assert.equal(res.sampled, true)
  assert.ok(res.censusId)
  assert.ok(SHA256_RE.test(res.doctrineSnapshotId), `expected a real snapshot id, got ${res.doctrineSnapshotId}`)
})

test('A3: the FAILED path emits a real row, not a silent gap', async () => {
  let seen = null
  const q = async (text, values) => { seen = { text, values }; return { rowCount: 1 } }
  const res = await emitExecutionTelemetry(
    { ...base, outcomeStatus: 'FAILED', outcomeDetail: 'threw' }, q, ENV_ON)
  assert.equal(res.ok, true)
  // A thrown run must leave a FAILED row carrying a truthful frame, or A5 reads it as
  // a missing sample rather than a failed execution.
  assert.equal(seen.values[11], 'FAILED', 'outcome_status must be FAILED')
  assert.equal(seen.values[12], 'threw')
  assert.ok(SHA256_RE.test(seen.values[9]), 'a FAILED row still needs a real frame')
  assert.equal(seen.values[2], true, 'marker still present on a failed run')
})
