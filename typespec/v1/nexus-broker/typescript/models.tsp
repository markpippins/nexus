/**
 * nexus-broker — TypeSpec Models
 *
 * Worker-tier Moleculer broker (D-2026-08-14-002).
 */
namespace org.nexus.nexusbroker {

  /** GET /api/health response. */
  model Health {
    /** Literal "ok". */
    status: string;
    /** Broker name ("nexus-broker"). */
    service: string;
    /** Broker namespace ("nexus"). */
    `namespace`: string;
    /** Declared worker-tier services (populated as each Wave 4 migration lands). */
    workers: string[];
    /** ISO timestamp. */
    timestamp: string;
  }

  /** A worker-tier service entry. */
  model WorkerEntry {
    /** Service name, e.g. "worker.harness". */
    name: string;
    /** Scaffold status until Wave 4 lands the real handlers. */
    status: string;
    /** Migration wave that implements it. */
    wave: int32;
  }

  /** GET /api/workers response. */
  model Workers {
    workers: WorkerEntry[];
    nodeID: string;
  }

  /** GET /api/solir/status response. */
  model SolIrStatus {
    enabled: boolean;
    intervalMs: int32;
    latestSnapshot: int32 | null;
    latestSnapshotAt: string | null;
  }

  /** POST /api/solir/snapshot request body. */
  model SolIrSnapshotRequest {
    /** Optional label; defaults to snapshot-vN. */
    label?: string;
  }

  /** POST /api/solir/snapshot response. */
  model SolIrSnapshotResult {
    ok: boolean;
    version: int32;
    label: string;
    observations: int32;
    assessments: int32;
    driftFindings: int32;
    prevVersion: int32;
  }

  // ========================================================================
  // Keychains — agent-record contextual layer (state vector + rewind)
  // ========================================================================

  /** GET /api/keychain-snapshot/status response (event-driven observations projection). */
  model KeychainStatus {
    enabled: boolean;
    /** D3: literal "event_driven" — the periodic snapshot timer is retired. */
    mode: string;
    /** Outbox poll cadence in ms (KEYCHAIN_OUTBOX_POLL_MS). */
    outboxPollMs: int32;
    /** Newest outbox event timestamp, null if none. */
    lastEventAt: string | null;
    /** Newest committed legacy checkpoint timestamp, null if none. */
    lastSnapshotAt: string | null;
    latestSnapshot: int32 | null;
    latestSnapshotAt: string | null;
    /** D6 chain health for the newest committed agent-records checkpoint. */
    chainHealth: KeychainChainHealth | null;
    /** Deepest delta replay served by rewind since broker boot. */
    deepestRewindSinceBoot: int32;
  }

  /** D6 chain health for the newest committed checkpoint. */
  model KeychainChainHealth {
    version: int32;
    storage: string;
    nearestBaseDistance: int32;
    nearestBaseVersion: int32;
  }

  /** The current issuance of a keychain instance (latest version only). */
  model KeychainActiveInstance {
    instance_id: string;
    record_id: string;
    version: int32;
    role: string;
    supersession_type: string;
    asset_id: string | null;
  }

  /** Content-addressed doctrine manifest projected into Keychains. */
  model KeychainDoctrineSnapshot {
    schema_version: int32;
    snapshot_id: string;
    system_prompt_hash: string;
    bootstrap_hash: string;
    /** Sorted full-content hashes of the active procedure cards. */
    active_procedure_cards: string[];
  }

  /** Stable evaluator context attached to a decision checkpoint. */
  model KeychainDecisionContext {
    schema_version: int32;
    checkpoint_class: string;
    decision_class: string | null;
    source_namespace: string;
    source_event_id: string | null;
    binding_decision_owner: KeychainBindingDecisionOwner;
    activation: KeychainActivationProvenance;
    decision: KeychainDecisionProvenance;
    rollback: KeychainRollbackProvenance;
    evaluator: KeychainVersionedReference;
    contract: KeychainVersionedReference;
    law: KeychainVersionedReference;
    bridge: KeychainVersionedReference;
    source_read_set: Record<unknown>;
    read_set_manifest: Record<unknown> | null;
    doctrine_snapshot_id?: string;
    observation_window?: KeychainObservationWindow;
    checkpoint_reference?: KeychainCheckpointReference;
  }

  /** Bounded post-activation observation interval and classification. */
  model KeychainObservationWindow {
    activation_ref: string;
    activated_at: string;
    window_start: string;
    window_end: string;
    observed_at: string | null;
    in_window: boolean | null;
    phase: string;
  }

  /** Immutable reference to the checkpoint carrying the decision evidence. */
  model KeychainCheckpointReference {
    checkpoint_id: string;
    version: int32;
  }

  /** Post-activation authority and grant identity used by the decision. */
  model KeychainActivationProvenance {
    authorization_ref: string | null;
    activation_ref: string | null;
    grant_id: string | null;
    authority_ref: string | null;
    activated_at: string | null;
  }

  /** Immutable decision identity, outcome, evidence, and replay context. */
  model KeychainDecisionProvenance {
    id: string | null;
    proposition_id: string | null;
    subject_id: string | null;
    work_item_id: string | null;
    disposition: string | null;
    outcome: string | null;
    evidence_ids: Record<unknown> | null;
    replay_context: Record<unknown> | string | null;
    as_of: string | null;
    evaluation_fingerprint: string | null;
    lineage_fingerprint: string | null;
  }

  /** Rollback linkage captured when a governed decision is reversed. */
  model KeychainRollbackProvenance {
    reference: string | null;
    status: string | null;
    evidence_ids: Record<unknown> | null;
  }

  /** Binding authority provenance recorded by a decision checkpoint. */
  model KeychainBindingDecisionOwner {
    role: string | null;
    authority_level: string | null;
    authorization_ref: string | null;
  }

  /** Identity and exact version consumed by a governed decision. */
  model KeychainVersionedReference {
    id: string | null;
    version: string | int32 | null;
  }

  /**
   * Normalized durable source event (shared trigger/checkpoint contract,
   * Analyst catalogue 332d6831). Idempotent on source_namespace + source_event_id.
   */
  model KeychainTriggerEvent {
    source_namespace?: string;
    source_event_id?: string;
    event_id?: string;
    schema_version?: int32;
    kind: string;
    id?: string;
    outcome?: string;
    aggregate_id?: string;
    actor?: string;
    source?: string;
    correlation_id?: string;
    contract_id?: string;
    contract_version?: string | int32;
    evaluator_id?: string;
    evaluator_version?: string | int32;
    law_id?: string;
    law_version?: string | int32;
    bridge_id?: string;
    bridge_version?: string | int32;
    decision_class?: string;
    binding_owner?: string;
    authorization_ref?: string;
    authority_ref?: string;
    grant_id?: string;
    authority_level?: string;
    decision_id?: string;
    activation_ref?: string;
    proposition_id?: string;
    subject_id?: string;
    work_item_id?: string;
    disposition?: string;
    evidence_ids?: Record<unknown>;
    replay_context?: Record<unknown> | string;
    as_of?: string;
    evaluation_fingerprint?: string;
    lineage_fingerprint?: string;
    activated_at?: string;
    rollback_ref?: string;
    rollback_of?: string;
    rollback_reference?: string;
    rollback_status?: string;
    rollback_evidence_ids?: Record<unknown>;
    /** Bounded post-activation observation interval and classification. */
    observation_window?: KeychainObservationWindow;
    effective_at?: string;
    recorded_at?: string;
    read_set?: Record<unknown>;
    read_set_manifest?: Record<unknown>;
    doctrine_snapshot?: KeychainDoctrineSnapshot;
    doctrine_snapshot_id?: string;
    payload?: Record<unknown>;
    checkpoint_status?: string;
    idempotency_key?: string;
    meta?: Record<unknown>;
  }

  /** GET /api/keychain-snapshot/agent-records/status response. */
  model KeychainAgentRecordsStatus {
    enabled: boolean;
    latestSnapshot: int32 | null;
    latestSnapshotAt: string | null;
    entryCount: int32;
    totalRecordsProjected: int32 | null;
    supersededRecords: int32 | null;
  }

  /** POST /api/keychain-snapshot/agent-records/snapshot request. */
  model KeychainSnapshotRequest {
    label?: string;
    /** Legacy raw trigger string (backward compat). */
    trigger?: string;
    /** Structured trigger event (shared contract; preferred). */
    triggerEvent?: KeychainTriggerEvent;
  }

  /** POST /api/keychain-snapshot/agent-records/snapshot response. */
  model KeychainSnapshotResult {
    ok: boolean;
    version: int32;
    label: string;
    totalRecords: int32;
    entries: int32;
    supersededRecords: int32;
    driftFindings: int32;
    recordTypeCount: int32;
    trigger: KeychainTriggerEvent | null;
    decision_context: KeychainDecisionContext | null;
    doctrine_snapshot_id?: string | null;
    deduplicated: boolean;
    prevVersion: int32;
  }

  /** GET /api/keychain-snapshot/agent-records/transitions response. */
  model KeychainTransitionsResult {
    count: int32;
    items: KeychainTriggerEvent[];
  }

  /** GET /api/keychain-snapshot/agent-records/rewind response. */
  model KeychainRewindResult {
    ok: boolean;
    version: int32;
    label: string;
    created_at: string;
    trigger: string | null;
    totalRecords: int32 | null;
    entryCount: int32 | null;
    recordTypeCount: int32 | null;
    state_vector: Record<unknown> | null;
    decision_context?: KeychainDecisionContext | null;
    doctrine_snapshot_id?: string | null;
    note?: string;
    error?: string;
  }

  // ========================================================================
  // Worker tier (Wave 4 — harness/pty/execution as Moleculer workers)
  // ========================================================================

  /** Health response for a worker service. */
  model WorkerHealth {
    status: string;
    db: boolean;
    active_sessions?: int32;
    schema?: string;
    counts?: Record<unknown>;
  }

  /** A pty session descriptor. */
  model PtySession {
    id: string;
    pid: int32;
    shell: string;
    started_at: string;
    status: string;
    exit_code?: int32 | null;
    cols?: int32;
    rows?: int32;
  }

  /** GET /api/workers/pty response. */
  model PtyList {
    count: int32;
    sessions: PtySession[];
  }

  /** POST /api/workers/pty request body. */
  model PtySpawnRequest {
    command: string;
    args: string[];
    cwd?: string;
    env?: Record<unknown>;
    cols?: int32;
    rows?: int32;
  }

  /** A harness session descriptor. */
  model HarnessSession {
    job_id: string;
    role?: string;
    `model`?: string;
    started_at: string;
    elapsed_ms: int32;
    wind_task_id?: string;
    pid?: int32;
  }

  /** GET /api/workers/harness/sessions response. */
  model HarnessSessions {
    count: int32;
    sessions: HarnessSession[];
  }

  /** POST /api/workers/harness/resolve-context request body. */
  model HarnessResolveRequest {
    wind_task_id: string;
    context?: Record<unknown>;
  }

  /** POST /api/workers/harness/run request body. */
  model HarnessRunRequest {
    wind_task_id: string;
    context?: Record<unknown>;
    work_dir?: string;
    harness_id?: string;
    agent?: string;
    timeout_ms?: int32;
    resolve_only?: boolean;
  }
}
