/**
 * Regression pin for keychain contextual drift — the comparison must be on
 * `instance_id`, never on the entries map's keys.
 *
 * The entries map produced by `resolveKeychainEntries` is keyed with entry
 * prefixes (`single:<uuid>`, `amendment:<token>`, `explicit:<prefix>`) while
 * `KeychainEntry.instance_id` is unprefixed. The prior index
 * (`checkpoint_heads.index` / `indexFromManifests`) is keyed by `instance_id`.
 * Comparing the map's keys to the index therefore matched nothing: every
 * instance was reported as `new_instance` on every checkpoint and the
 * `superseded` branch could never fire. These cases fail on that comparison and
 * pass on the fixed one.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  computeAgentRecordDrift,
  priorStateFromIndex,
} = require('../lib/keychain-drift.ts')

const NOW = '2026-11-01T12:00:00.000Z'

function entry(instanceId, recordId, { version = 1, recordType = 'report', role = 'engineer' } = {}) {
  return {
    instance_id: instanceId,
    record_type: recordType,
    role,
    current: { record_id: recordId, version },
  }
}

/** Build the entries map the service holds: prefixed keys, unprefixed ids. */
function entryMap(entries) {
  const map = new Map()
  for (const item of entries) {
    map.set(`single:${item.instance_id}`, item)
  }
  return map
}

test('an unchanged population reports no drift when fed the entries values', () => {
  const entries = entryMap([entry('05d0fe54', 'rec-a'), entry('uuid-b', 'rec-b')])
  const prior = priorStateFromIndex({ '05d0fe54': 'rec-a', 'uuid-b': 'rec-b' })

  const findings = computeAgentRecordDrift(entries.values(), prior, 2, NOW)

  assert.deepEqual(findings, [])
})

test('comparing the entries MAP (prefixed keys) reports every instance as new — the defect', () => {
  const entries = entryMap([entry('05d0fe54', 'rec-a'), entry('uuid-b', 'rec-b')])
  const prior = priorStateFromIndex({ '05d0fe54': 'rec-a', 'uuid-b': 'rec-b' })

  // The old implementation compared `entries.keys()` against `prior.instanceIds`.
  // Reproduced here so the failure mode cannot return silently.
  const fromKeys = computeAgentRecordDrift(
    [...entries.keys()].map((key) => ({ instance_id: key, record_type: 'report', role: 'engineer', current: { record_id: key, version: 1 } })),
    prior,
    2,
    NOW,
  )

  assert.equal(fromKeys.length, 2)
  assert.ok(fromKeys.every((finding) => finding.kind === 'new_instance'))
})

test('a genuinely new instance is reported once, with its identity and description', () => {
  const prior = priorStateFromIndex({ 'uuid-a': 'rec-a' })
  const entries = [entry('uuid-a', 'rec-a'), entry('uuid-new', 'rec-new', { recordType: 'decision', role: 'architect' })]

  const findings = computeAgentRecordDrift(entries, prior, 7, NOW)

  assert.deepEqual(findings, [
    {
      snapshot_version: 7,
      kind: 'new_instance',
      instance_id: 'uuid-new',
      record_type: 'decision',
      role: 'architect',
      description: 'New keychain instance uuid-new (decision/architect)',
      severity: 'info',
      detected_at: NOW,
    },
  ])
})

test('a superseded instance reports the new record and version', () => {
  const prior = priorStateFromIndex({ '05d0fe54': 'rec-v4' })
  const entries = [entry('05d0fe54', 'rec-v5', { version: 5 })]

  const findings = computeAgentRecordDrift(entries, prior, 9, NOW)

  assert.deepEqual(findings, [
    {
      snapshot_version: 9,
      kind: 'superseded',
      instance_id: '05d0fe54',
      record_type: 'report',
      role: 'engineer',
      new_record_id: 'rec-v5',
      new_version: 5,
      description: 'Instance 05d0fe54 superseded: now v5 (rec-v5)',
      severity: 'info',
      detected_at: NOW,
    },
  ])
})

test('a removed instance produces no finding (removals are carried by the delta)', () => {
  const prior = priorStateFromIndex({ 'gone': 'rec-gone', 'stays': 'rec-stays' })
  const entries = [entry('stays', 'rec-stays')]

  assert.deepEqual(computeAgentRecordDrift(entries, prior, 3, NOW), [])
})

test('findings follow entry order, so the stored order is deterministic', () => {
  const prior = priorStateFromIndex({})
  const entries = [entry('first', 'r1'), entry('second', 'r2'), entry('third', 'r3')]

  const ids = computeAgentRecordDrift(entries, prior, 1, NOW).map((finding) => finding.instance_id)

  assert.deepEqual(ids, ['first', 'second', 'third'])
})

test('an empty prior index reports the whole population as new (first checkpoint)', () => {
  const findings = computeAgentRecordDrift([entry('a', 'r1'), entry('b', 'r2')], priorStateFromIndex({}), 1, NOW)

  assert.equal(findings.length, 2)
  assert.ok(findings.every((finding) => finding.snapshot_version === 1))
})

test('priorStateFromIndex tolerates null', () => {
  const prior = priorStateFromIndex(null)

  assert.equal(prior.instanceIds.size, 0)
  assert.equal(prior.recordIds.size, 0)
})
