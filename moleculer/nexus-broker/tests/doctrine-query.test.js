const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  buildDoctrineReconstructionReport,
  buildDoctrineSetReport,
  validDoctrineSnapshot,
} = require('../lib/doctrine-query.ts')

const hash = (char) => `sha256:${char.repeat(64)}`
const snapshot = {
  schema_version: 1,
  snapshot_id: hash('a'),
  system_prompt_hash: hash('b'),
  bootstrap_hash: hash('c'),
  active_procedure_cards: [hash('d'), hash('e')],
}

test('B3 groups transitions by doctrine set and reports successful Conduit tickets per co-active card', () => {
  const transitions = [
    { doctrine_snapshot_id: snapshot.snapshot_id, source_namespace: 'conduit', kind: 'conduit.ticket.completed', outcome: 'completed', ticket_id: 'ticket-1' },
    { doctrine_snapshot_id: snapshot.snapshot_id, source_namespace: 'conduit', kind: 'conduit.ticket.completed', outcome: 'completed', ticket_id: 'ticket-1' },
    { doctrine_snapshot_id: snapshot.snapshot_id, source_namespace: 'conduit', kind: 'conduit.ticket.failed', outcome: 'failed', ticket_id: 'ticket-2' },
    { outcome: 'committed', source_namespace: 'other-system' },
  ]
  const report = buildDoctrineSetReport(transitions, new Map([[snapshot.snapshot_id, snapshot]]), { highActivationMin: 2 })
  assert.equal(report.transition_count, 4)
  assert.equal(report.unattributed_transition_count, 1)
  assert.equal(report.doctrine_set_count, 1)
  const cohort = report.sets[0]
  assert.equal(cohort.transition_count, 3)
  assert.equal(cohort.successful_conduit_transition_count, 2)
  assert.equal(cohort.successful_conduit_ticket_count, 1)
  assert.deepEqual(cohort.outcomes, { completed: 2, failed: 1 })
  assert.equal(cohort.cards.length, 2)
  assert.ok(cohort.cards.every((card) => card.high_activation))
  assert.ok(cohort.cards.every((card) => card.successful_conduit_ticket_count === 1))
  assert.ok(cohort.cards.every((card) => card.low_influence_evidence.causal_conclusion === false))
})

test('B3 marks unavailable snapshots and reports no-success observations without claiming causality', () => {
  const id = hash('f')
  const report = buildDoctrineSetReport([
    { doctrine_snapshot_id: id, source_namespace: 'conduit', outcome: 'failed', ticket_id: 'ticket-9' },
  ], new Map())
  const cohort = report.sets[0]
  assert.equal(cohort.snapshot_available, false)
  assert.equal(cohort.cards.length, 0)
  assert.equal(cohort.successful_conduit_ticket_count, 0)
})

test('B4 identifies embedded historical evidence and reports reference coverage and snapshot dedupe', () => {
  const embedded = {
    checkpoint_id: 'checkpoint-reconstructable',
    version: 1,
    trigger_event: { doctrine_snapshot: snapshot },
  }
  const referenced = {
    checkpoint_id: 'checkpoint-referenced',
    version: 2,
    doctrine_snapshot_id: snapshot.snapshot_id,
  }
  const unrecoverable = { checkpoint_id: 'checkpoint-unknown', version: 3 }
  const report = buildDoctrineReconstructionReport(
    [embedded, referenced, unrecoverable],
    [],
  )
  assert.equal(report.read_only, true)
  assert.equal(report.checkpoint_count, 3)
  assert.equal(report.referenced_checkpoint_count, 2)
  assert.equal(report.catalog_resolved_checkpoint_count, 0)
  assert.equal(report.reference_coverage_percent, 66.67)
  assert.equal(report.recoverable_unreferenced_count, 0)
  assert.equal(report.referenced_catalog_repair_count, 1)
  assert.equal(report.unrecoverable_unreferenced_count, 1)
  assert.equal(report.backfill_candidates.length, 1)
  assert.equal(report.backfill_candidates[0].reconstructed_doctrine_snapshot_id, snapshot.snapshot_id)
  assert.equal(report.backfill_candidates[0].action, 'restore_catalog_entry')
  assert.equal(report.backfill_candidates[0].eligible_for_backfill, true)
  assert.equal(report.dedupe.identical_content_under_multiple_ids_count, 0)
  assert.deepEqual(report.dedupe.snapshot_ids_reused_across_checkpoints, [{ snapshot_id: snapshot.snapshot_id, checkpoint_count: 2 }])
})

test('B4 detects a valid-but-wrong reference and duplicate content under multiple snapshot IDs', () => {
  const conflictingSnapshot = { ...snapshot, snapshot_id: hash('f') }
  const report = buildDoctrineReconstructionReport(
    [{ checkpoint_id: 'checkpoint-conflict', version: 1, doctrine_snapshot_id: snapshot.snapshot_id, trigger_event: { doctrine_snapshot: conflictingSnapshot } }],
    [
      { ...snapshot, _id: snapshot.snapshot_id },
      { ...snapshot, snapshot_id: hash('e'), _id: hash('e') },
    ],
  )
  assert.equal(report.reference_conflict_count, 1)
  assert.equal(report.backfill_candidates.length, 0)
  assert.equal(report.dedupe.identical_content_under_multiple_ids_count, 1)
  assert.deepEqual(report.dedupe.conflicting_catalog_ids, [])
})

test('B4 counts an existing but incomplete catalog document as a repair candidate', () => {
  const report = buildDoctrineReconstructionReport(
    [{ checkpoint_id: 'checkpoint-catalog-repair', version: 9, doctrine_snapshot_id: snapshot.snapshot_id, trigger_event: { doctrine_snapshot: snapshot } }],
    [{ _id: snapshot.snapshot_id, created_at: 'old-incomplete-entry' }],
  )
  assert.equal(report.referenced_checkpoint_count, 1)
  assert.equal(report.catalog_resolved_checkpoint_count, 0)
  assert.equal(report.dangling_reference_count, 1)
  assert.equal(report.referenced_catalog_repair_count, 1)
  assert.equal(report.backfill_candidates.length, 1)
  assert.equal(report.backfill_candidates[0].action, 'restore_catalog_entry')
  assert.deepEqual(report.dedupe.conflicting_catalog_ids, [])
})

test('B4 detects one snapshot ID mapped to conflicting content', () => {
  const changedContent = { ...snapshot, bootstrap_hash: hash('f') }
  const report = buildDoctrineReconstructionReport(
    [],
    [
      { ...snapshot, _id: snapshot.snapshot_id },
      { ...changedContent, _id: snapshot.snapshot_id },
    ],
  )
  assert.deepEqual(report.dedupe.conflicting_catalog_ids, [snapshot.snapshot_id])
})

test('snapshot validation requires canonical sorted and unique hash lists', () => {
  assert.equal(validDoctrineSnapshot(snapshot), true)
  assert.equal(validDoctrineSnapshot({ ...snapshot, active_procedure_cards: [...snapshot.active_procedure_cards].reverse() }), false)
  assert.equal(validDoctrineSnapshot({ ...snapshot, active_procedure_cards: [hash('d'), hash('d')] }), false)
})
