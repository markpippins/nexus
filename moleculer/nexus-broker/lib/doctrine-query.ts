export interface DoctrineSnapshotRecord {
  schema_version: 1;
  snapshot_id: string;
  system_prompt_hash: string;
  bootstrap_hash: string;
  active_procedure_cards: string[];
}

export interface DoctrineTransitionRecord {
  doctrine_snapshot_id?: string | null;
  doctrine_snapshot?: DoctrineSnapshotRecord | null;
  read_set?: any;
  payload?: any;
  meta?: any;
  decision_context?: any;
  source_namespace?: string | null;
  source_event_id?: string | null;
  work_item_id?: string | null;
  ticket_id?: string | null;
  outcome?: string | null;
  kind?: string | null;
  actor?: string | null;
  checkpoint_status?: string | null;
  created_at?: string | Date | null;
}

export interface DoctrineCheckpointRecord {
  checkpoint_id?: string;
  version?: number;
  checkpoint_status?: string;
  doctrine_snapshot_id?: string | null;
  trigger_event?: DoctrineTransitionRecord | null;
  decision_context?: any;
  source_namespace?: string | null;
  source_event_id?: string | null;
  backfill_reference?: { doctrine_snapshot_id?: string | null; snapshot?: DoctrineSnapshotRecord | null } | null;
}

const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const SUCCESS_OUTCOMES = new Set(["committed", "completed", "success", "succeeded"]);

export function doctrineSnapshotId(record: any): string | null {
  const candidates = [
    record?.doctrine_snapshot_id,
    record?.doctrine_snapshot?.snapshot_id,
    record?.decision_context?.doctrine_snapshot_id,
    record?.decision_context?.source_read_set?.doctrine_snapshot?.snapshot_id,
    record?.read_set?.doctrine_snapshot_id,
    record?.read_set?.doctrine_snapshot?.snapshot_id,
    record?.payload?.doctrine_snapshot_id,
    record?.payload?.doctrine_snapshot?.snapshot_id,
    record?.meta?.doctrine_snapshot_id,
    record?.meta?.doctrine_snapshot?.snapshot_id,
    record?.backfill_reference?.doctrine_snapshot_id,
  ];
  const found = candidates.find((candidate) => typeof candidate === "string" && candidate.length > 0);
  return found ? String(found) : null;
}

export function validDoctrineSnapshot(candidate: any): candidate is DoctrineSnapshotRecord {
  if (!candidate || typeof candidate !== "object"
    || candidate.schema_version !== 1
    || typeof candidate.snapshot_id !== "string" || !HASH_RE.test(candidate.snapshot_id)
    || typeof candidate.system_prompt_hash !== "string" || !HASH_RE.test(candidate.system_prompt_hash)
    || typeof candidate.bootstrap_hash !== "string" || !HASH_RE.test(candidate.bootstrap_hash)
    || !Array.isArray(candidate.active_procedure_cards)
    || !candidate.active_procedure_cards.every((hash: any) => typeof hash === "string" && HASH_RE.test(hash))) {
    return false;
  }
  const cards = candidate.active_procedure_cards as string[];
  return cards.every((card, index) => index === 0 || cards[index - 1] < card)
    && new Set(cards).size === cards.length;
}

function snapshotContent(snapshot: DoctrineSnapshotRecord): string {
  return JSON.stringify({
    schema_version: snapshot.schema_version,
    system_prompt_hash: snapshot.system_prompt_hash,
    bootstrap_hash: snapshot.bootstrap_hash,
    active_procedure_cards: snapshot.active_procedure_cards,
  });
}

function ticketIdentity(transition: DoctrineTransitionRecord): string | null {
  const payload = transition.payload && typeof transition.payload === "object" ? transition.payload : {};
  const readSet = transition.read_set && typeof transition.read_set === "object" ? transition.read_set : {};
  const id = transition.ticket_id
    || transition.work_item_id
    || payload.ticket_id
    || payload.work_item_id
    || readSet.ticket_id
    || readSet.work_item_id
    || transition.decision_context?.decision?.work_item_id;
  return id == null ? null : String(id);
}

/** Build a bounded, observational report; it does not claim causal card influence. */
export function buildDoctrineSetReport(
  transitions: DoctrineTransitionRecord[],
  snapshots: Map<string, DoctrineSnapshotRecord>,
  options: { highActivationMin?: number; truncated?: boolean } = {},
) {
  const highActivationMin = Number.isInteger(options.highActivationMin) && Number(options.highActivationMin) > 0
    ? Number(options.highActivationMin)
    : 10;
  const groups = new Map<string, DoctrineTransitionRecord[]>();
  let unattributedTransitions = 0;
  for (const transition of transitions) {
    const snapshotId = doctrineSnapshotId(transition);
    if (!snapshotId) {
      unattributedTransitions += 1;
      continue;
    }
    const group = groups.get(snapshotId) || [];
    group.push(transition);
    groups.set(snapshotId, group);
  }

  const sets = [...groups.entries()].map(([snapshotId, events]) => {
    const snapshot = snapshots.get(snapshotId) || null;
    const outcomes: Record<string, number> = {};
    const cardStats = new Map<string, { activations: number; successfulTransitions: number; ticketIds: Set<string>; successfulTicketCountKnown: boolean }>();
    const successfulTicketIds = new Set<string>();
    let successfulConduitTransitions = 0;
    let successfulConduitTicketCountComplete = true;

    for (const event of events) {
      const outcome = String(event.outcome || "unknown");
      outcomes[outcome] = (outcomes[outcome] || 0) + 1;
      const source = String(event.source_namespace || "");
      const conduitMarker = [source, event.actor, event.kind]
        .some((value) => /conduit/i.test(String(value || "")));
      const isSuccessfulConduit = conduitMarker && SUCCESS_OUTCOMES.has(outcome.toLowerCase());
      const identity = isSuccessfulConduit ? ticketIdentity(event) : null;
      if (identity) successfulTicketIds.add(identity);
      if (isSuccessfulConduit) {
        successfulConduitTransitions += 1;
        if (!identity) successfulConduitTicketCountComplete = false;
      }

      for (const cardHash of snapshot?.active_procedure_cards || []) {
        const stats = cardStats.get(cardHash) || {
          activations: 0,
          successfulTransitions: 0,
          ticketIds: new Set<string>(),
          successfulTicketCountKnown: true,
        };
        stats.activations += 1;
        if (isSuccessfulConduit) stats.successfulTransitions += 1;
        if (isSuccessfulConduit && !identity) stats.successfulTicketCountKnown = false;
        if (isSuccessfulConduit && identity) stats.ticketIds.add(identity);
        cardStats.set(cardHash, stats);
      }
    }

    const cards = snapshot
      ? snapshot.active_procedure_cards.map((cardHash) => {
          const stats = cardStats.get(cardHash)!;
          return {
            procedure_card_hash: cardHash,
            activation_count: stats.activations,
            high_activation: stats.activations >= highActivationMin,
            successful_conduit_transition_count: stats.successfulTransitions,
            successful_conduit_ticket_count: stats.ticketIds.size,
            successful_conduit_ticket_count_complete: stats.successfulTicketCountKnown,
            low_influence_evidence: {
              status: stats.activations > 0 && stats.successfulTransitions === 0
                ? "no_success_observed_in_exposed_cohort"
                : "not_established",
              exposed_transition_count: stats.activations,
              successful_conduit_transition_count: stats.successfulTransitions,
              causal_conclusion: false,
              limitation: "Cards co-occur within doctrine sets; these observations cannot isolate an individual card's causal influence.",
            },
          };
        })
      : [];
    return {
      doctrine_snapshot_id: snapshotId,
      snapshot_available: Boolean(snapshot),
      system_prompt_hash: snapshot?.system_prompt_hash || null,
      bootstrap_hash: snapshot?.bootstrap_hash || null,
      active_procedure_card_count: snapshot?.active_procedure_cards.length ?? null,
      transition_count: events.length,
      outcomes,
      successful_conduit_transition_count: successfulConduitTransitions,
      successful_conduit_ticket_count: successfulTicketIds.size,
      successful_conduit_ticket_count_complete: successfulConduitTicketCountComplete,
      cards,
    };
  });

  sets.sort((left, right) => right.transition_count - left.transition_count
    || left.doctrine_snapshot_id.localeCompare(right.doctrine_snapshot_id));
  return {
    generated_at: new Date().toISOString(),
    attribution_basis: "Recorded transitions with a doctrine snapshot ID; activation counts are observed transition exposure, not independently verified agent invocation counts. Per-card results are cohort association only, not causal influence.",
    transition_count: transitions.length,
    doctrine_set_count: sets.length,
    unattributed_transition_count: unattributedTransitions,
    truncated: Boolean(options.truncated),
    high_activation_min: highActivationMin,
    success_outcomes: [...SUCCESS_OUTCOMES],
    sets,
  };
}

function snapshotFromCheckpoint(checkpoint: DoctrineCheckpointRecord): any {
  const trigger = checkpoint.trigger_event || {};
  return trigger.doctrine_snapshot
    || trigger.meta?.doctrine_snapshot
    || trigger.read_set?.doctrine_snapshot
    || trigger.payload?.doctrine_snapshot
    || trigger.decision_context?.source_read_set?.doctrine_snapshot
    || checkpoint.decision_context?.source_read_set?.doctrine_snapshot
    || checkpoint.backfill_reference?.snapshot
    || null;
}

/** Analyze historical references and exact embedded snapshots without writing to Mongo. */
export function buildDoctrineReconstructionReport(
  checkpoints: DoctrineCheckpointRecord[],
  catalogRecords: any[],
  transitionRecords: DoctrineTransitionRecord[] = [],
) {
  const catalog = new Map<string, any>();
  const catalogContentConflicts: string[] = [];
  for (const record of catalogRecords) {
    const id = String(record?._id || record?.snapshot_id || "");
    if (!id) continue;
    const normalized = { ...record, snapshot_id: record._id || record.snapshot_id };
    if (record?._id && record?.snapshot_id && record._id !== record.snapshot_id && validDoctrineSnapshot(record)) {
      catalogContentConflicts.push(id);
    }
    const previous = catalog.get(id);
    if (previous && validDoctrineSnapshot(previous) && validDoctrineSnapshot(normalized)
      && snapshotContent(previous) !== snapshotContent(normalized)) {
      catalogContentConflicts.push(id);
    } else if (!previous) {
      catalog.set(id, normalized);
    }
  }

  const transitionsBySource = new Map<string, DoctrineTransitionRecord>();
  for (const transition of transitionRecords) {
    if (transition.source_namespace && transition.source_event_id) {
      transitionsBySource.set(`${transition.source_namespace}:${transition.source_event_id}`, transition);
    }
  }

  let referenced = 0;
  let catalogResolved = 0;
  let recoverable = 0;
  let catalogRepairable = 0;
  let unrecoverable = 0;
  let referenceConflicts = 0;
  const candidates: Array<{
    checkpoint_id: string | null;
    version: number | null;
    current_doctrine_snapshot_id: string | null;
    reconstructed_doctrine_snapshot_id: string;
    action: "restore_catalog_entry" | "attach_verified_reference";
    source: string;
    catalog_entry_exists: boolean;
    eligible_for_backfill: boolean;
    snapshot: DoctrineSnapshotRecord;
  }> = [];

  for (const checkpoint of checkpoints) {
    const trigger = checkpoint.trigger_event || {};
    const linkedTransition = checkpoint.source_namespace && checkpoint.source_event_id
      ? transitionsBySource.get(`${checkpoint.source_namespace}:${checkpoint.source_event_id}`)
      : null;
    const embedded = snapshotFromCheckpoint(checkpoint)
      || (linkedTransition ? snapshotFromCheckpoint({ trigger_event: linkedTransition } as DoctrineCheckpointRecord) : null);
    const reference = doctrineSnapshotId(checkpoint) || doctrineSnapshotId(trigger) || (linkedTransition ? doctrineSnapshotId(linkedTransition) : null);
    if (reference) referenced += 1;
    if (reference && catalog.has(reference) && !catalogContentConflicts.includes(reference)
      && validDoctrineSnapshot(catalog.get(reference))) catalogResolved += 1;

    const candidateIsValid = validDoctrineSnapshot(embedded);
    const catalogRecord = catalog.get(embedded?.snapshot_id);
    if (reference && (!HASH_RE.test(reference)
      || catalogContentConflicts.includes(reference)
      || (catalog.has(reference) && !validDoctrineSnapshot(catalog.get(reference))
        && (!candidateIsValid || reference !== embedded.snapshot_id)))) {
      referenceConflicts += 1;
      continue;
    }
    if (candidateIsValid && reference && reference !== embedded.snapshot_id) {
      referenceConflicts += 1;
      continue;
    }
    if (!candidateIsValid && !reference) unrecoverable += 1;
    if (candidateIsValid && catalogRecord && validDoctrineSnapshot({ ...catalogRecord, snapshot_id: catalogRecord._id || catalogRecord.snapshot_id })
      && snapshotContent({ ...catalogRecord, snapshot_id: catalogRecord._id || catalogRecord.snapshot_id }) !== snapshotContent(embedded)) {
      referenceConflicts += 1;
      continue;
    }
    if (candidateIsValid && (reference !== embedded.snapshot_id || !catalogRecord
      || !validDoctrineSnapshot({ ...catalogRecord, snapshot_id: catalogRecord._id || catalogRecord.snapshot_id }))) {
      if (!reference) recoverable += 1;
      else catalogRepairable += 1;
      candidates.push({
        checkpoint_id: checkpoint.checkpoint_id || null,
        version: Number.isInteger(checkpoint.version) ? Number(checkpoint.version) : null,
        current_doctrine_snapshot_id: reference,
        reconstructed_doctrine_snapshot_id: embedded.snapshot_id,
        action: reference ? "restore_catalog_entry" : "attach_verified_reference",
        source: checkpoint.trigger_event?.doctrine_snapshot ? "checkpoint.trigger_event.doctrine_snapshot"
          : linkedTransition?.doctrine_snapshot ? "linked transition doctrine_snapshot"
            : checkpoint.backfill_reference?.snapshot ? "append-only doctrine backfill record"
              : "embedded decision read-set snapshot",
        catalog_entry_exists: Boolean(catalogRecord),
        eligible_for_backfill: Boolean(checkpoint.checkpoint_id),
        snapshot: embedded,
      });
    }
  }

  const byContent = new Map<string, Set<string>>();
  for (const [id, record] of catalog) {
    const normalizedRecord = { ...record, snapshot_id: record._id || record.snapshot_id };
    if (!validDoctrineSnapshot(normalizedRecord)) continue;
    const content = snapshotContent(normalizedRecord);
    const ids = byContent.get(content) || new Set<string>();
    ids.add(id);
    byContent.set(content, ids);
  }
  const contentWithMultipleIds = [...byContent.values()].filter((ids) => ids.size > 1).length;
  const catalogIds = [...catalog.keys()];
  const referenceCounts = new Map<string, number>();
  for (const checkpoint of checkpoints) {
    const trigger = checkpoint.trigger_event || {};
    const linkedTransition = checkpoint.source_namespace && checkpoint.source_event_id
      ? transitionsBySource.get(`${checkpoint.source_namespace}:${checkpoint.source_event_id}`)
      : null;
    const id = doctrineSnapshotId(checkpoint) || doctrineSnapshotId(trigger)
      || (linkedTransition ? doctrineSnapshotId(linkedTransition) : null);
    if (id) referenceCounts.set(id, (referenceCounts.get(id) || 0) + 1);
  }
  const coveragePercent = checkpoints.length === 0
    ? 100
    : Math.round((referenced / checkpoints.length) * 10000) / 100;

  return {
    generated_at: new Date().toISOString(),
    read_only: true,
    checkpoint_count: checkpoints.length,
    referenced_checkpoint_count: referenced,
    catalog_resolved_checkpoint_count: catalogResolved,
    catalog_resolution_percent: checkpoints.length === 0
      ? 100
      : Math.round((catalogResolved / checkpoints.length) * 10000) / 100,
    dangling_reference_count: referenced - catalogResolved,
    reference_coverage_percent: coveragePercent,
    recoverable_unreferenced_count: recoverable,
    referenced_catalog_repair_count: catalogRepairable,
    unrecoverable_unreferenced_count: unrecoverable,
    reference_conflict_count: referenceConflicts,
    backfill_candidates: candidates,
    dedupe: {
      catalog_document_count: catalogRecords.length,
      unique_snapshot_id_count: catalogIds.length,
      conflicting_catalog_ids: [...new Set(catalogContentConflicts)].sort(),
      identical_content_under_multiple_ids_count: contentWithMultipleIds,
      snapshot_ids_reused_across_checkpoints: [...referenceCounts.entries()]
        .filter(([, count]) => count > 1)
        .map(([id, count]) => ({ snapshot_id: id, checkpoint_count: count }))
        .sort((a, b) => a.snapshot_id.localeCompare(b.snapshot_id)),
      note: "This report verifies stored ID-to-content consistency; it does not recompute snapshot_id because the producer's canonical ID serialization is not defined in the persisted contract.",
    },
    limitation: "No checkpoint or catalog documents are modified. A snapshot is considered recoverable only when its complete validated hash set is embedded in the historical checkpoint or its linked transition.",
  };
}
