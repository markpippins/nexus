/**
 * nexus-broker — TypeSpec Operations
 *
 * Worker-tier Moleculer broker (D-2026-08-14-002). The moleculer-web API
 * gateway exposes broker-level introspection (health + worker status);
 * worker actions land with each Wave 4 migration.
 */

import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.nexusbroker {

  // ========================================================================
  // Health
  // ========================================================================

  @route("/api/health")
  @get
  @doc("Broker health: status, namespace, declared worker tier.")
  op health(): Health;

  // ========================================================================
  // Workers
  // ========================================================================

  @route("/api/workers")
  @get
  @doc("List the declared worker-tier services and their scaffold status.")
  op listWorkers(): Workers;

  // ========================================================================
  // RETIRED: SOL IR — the surface was renamed to keychain-snapshot when the
  // candidate-state groundwork landed (PR #111, eb320db8, 2026-09-01); the
  // implementation now serves the /api/keychain-snapshot/* surface below.
  // Note the SolIr* model names in models.tsp still back the live keychain
  // ops — renaming those types is a separate contract change. These two ops
  // are RETIRED, not planned: no source route exists and none is planned.
  // Do not implement them — reconcile-typescript.py excludes them as retired.
  // ========================================================================

  @route("/api/solir/status")
  @get
  @doc("[RETIRED 2026-09-12 — superseded by GET /api/keychain-snapshot/status (rename landed in PR #111, 2026-09-01). Do not implement: kept only as the tombstone for the retired solir name.]")
  @tag("retired")
  op solirStatus(): SolIrStatus;

  @route("/api/solir/snapshot")
  @post
  @doc("[RETIRED 2026-09-12 — superseded by POST /api/keychain-snapshot/snapshot (rename landed in PR #111, 2026-09-01). Do not implement: kept only as the tombstone for the retired solir name.]")
  @tag("retired")
  op solirSnapshot(@body body: SolIrSnapshotRequest): SolIrSnapshotResult;

  // ========================================================================
  // Keychains — agent-record contextual layer (state vector + rewind)
  // ========================================================================

  @route("/api/keychain-snapshot/status")
  @get
  @doc("Legacy observations/assessments keychain projection status.")
  op keychainStatus(): KeychainStatus;

  @route("/api/keychain-snapshot/snapshot")
  @post
  @doc("Trigger the legacy observations/assessments keychain snapshot (timer-based; running stack, not state vector).")
  op keychainSnapshot(@body body: SolIrSnapshotRequest): SolIrSnapshotResult;

  @route("/api/keychain-snapshot/agent-records/status")
  @get
  @doc("Agent-records keychain status: latest state-vector snapshot, entry count.")
  op keychainAgentRecordsStatus(): KeychainAgentRecordsStatus;

  @route("/api/keychain-snapshot/agent-records/snapshot")
  @post
  @doc("Project agent records -> keychain entries (current issuance per instance). Trigger events are normalized to the shared contract and deduplicated on (kind, id).")
  op keychainAgentRecordsSnapshot(@body body: KeychainSnapshotRequest): KeychainSnapshotResult;

  @route("/api/keychain-snapshot/agent-records/transitions")
  @get
  @doc("List recorded snapshot-trigger events (decision points).")
  op keychainAgentRecordsTransitions(): KeychainTransitionsResult;

  @route("/api/keychain-snapshot/agent-records/rewind")
  @get
  @doc("Rewind: reconstruct the per-record-type state vector as of a past decision-point snapshot version.")
  op keychainAgentRecordsRewind(@query at: int32): KeychainRewindResult;

  // ========================================================================
  // Worker tier (Wave 4 — harness/pty/execution as Moleculer workers)
  // ========================================================================

  @route("/api/workers/execution")
  @get
  @doc("Execution worker health: DB connectivity + schema counts (read-only observability).")
  op executionHealth(): WorkerHealth;

  @route("/api/workers/execution/health")
  @get
  @doc("Legacy GET /api/execution/health: scanned_at + per-status request counts (READY/COMPLETED), stale active leases, running attempts. Missing from the initial catalog; added in post-deploy live parity (2026-09-12).")
  op executionRichHealth(): Record<unknown>;

  // ── Execution read catalog (M2): the full legacy execution-srv surface
  // (/api/execution/*), mapped 1:1 under /api/workers/execution. Read-only:
  // SELECTs only, no write path. Payloads mirror the legacy routes' JSON
  // shapes (DB-native column names); see execution-srv `REST API.md`.

  @route("/api/workers/execution/requests")
  @get
  @doc("Paginated request list. Filters: status, search (ILIKE across business_key/title/objective/ids); limit 1-100, offset. Shape: {total, limit, offset, items}.")
  op executionListRequests(@query status?: string, @query search?: string, @query limit?: int32, @query offset?: int32): Record<unknown>;

  @route("/api/workers/execution/leases")
  @get
  @doc("Paginated lease list. Filters: status, search (executor_id/request ids). Shape: {total, limit, offset, items}.")
  op executionListLeases(@query status?: string, @query search?: string, @query limit?: int32, @query offset?: int32): Record<unknown>;

  @route("/api/workers/execution/attempts")
  @get
  @doc("Paginated attempt list. Filters: status, search. Shape: {total, limit, offset, items}.")
  op executionListAttempts(@query status?: string, @query search?: string, @query limit?: int32, @query offset?: int32): Record<unknown>;

  @route("/api/workers/execution/receipts")
  @get
  @doc("Paginated receipt list. Filters: type, search (agent_role/summary/ids), ordered by issued_at DESC. Shape: {total, limit, offset, items}.")
  op executionListReceipts(@query type?: string, @query search?: string, @query limit?: int32, @query offset?: int32): Record<unknown>;

  @route("/api/workers/execution/requests/{id}/state")
  @get
  @doc("Lifecycle state for one request: request + current lease + latest attempt + all receipts (the aggregate-root view). 404 if unknown id; 400 if id is not a UUID.")
  op executionRequestState(@path id: string): Record<unknown>;

  @route("/api/workers/execution/leases/stale")
  @get
  @doc("Active leases whose expires_at < now(), joined to their requests — the lease-expiry enforcement gap made queryable.")
  op executionStaleLeases(): Record<unknown>;

  @route("/api/workers/execution/leases/{id}/lifecycle")
  @get
  @doc("Lease lifecycle: promised vs actually-held TTL, overdue seconds, derived lifecycle_state.")
  op executionLeaseLifecycle(@path id: string): Record<unknown>;

  @route("/api/workers/execution/health/integrity-scan")
  @get
  @doc("Cross-table consistency scan: named pathology kinds, each {kind, count, samples}; totals summarize.")
  op executionIntegrityScan(): Record<unknown>;

  @route("/api/workers/execution/requests/{id}/attempts")
  @get
  @doc("Attempt/lease tree for one request, chronological, each attempt joined to its lease.")
  op executionRequestAttempts(@path id: string): Record<unknown>;

  @route("/api/workers/execution/requests/{id}/receipts/lineage")
  @get
  @doc("Receipt lineage buckets for one request: native / backfilled (vision.receipts) / unknown.")
  op executionReceiptsLineage(@path id: string): Record<unknown>;

  @route("/api/workers/execution/health/by-executor")
  @get
  @doc("Fleet view: lease counts per executor; with executor_id, that executor's active leases and in-progress attempts.")
  op executionByExecutor(@query executor_id?: string): Record<unknown>;

  @route("/api/workers/execution/health/status-distribution")
  @get
  @doc("Status distributions across requests/leases/attempts + receipts-by-type + stale-active-lease count.")
  op executionStatusDistribution(): Record<unknown>;

  @route("/api/workers/execution/receipts/{id}/pipeline-origin")
  @get
  @doc("Follows lineage_original_id to vision.receipts and returns both records side by side, labeled by audit trail.")
  op executionPipelineOrigin(@path id: string): Record<unknown>;

  @route("/api/workers/execution/witnessed-runs")
  @get
  @doc("Witnessed-run provenance projection for (workflow_instance_id, node_id): envelope/manifest/law/assessment/evidence/replay identities + authoritative classifier status. Nullable lineage fields are deliberate.")
  op executionWitnessedRuns(@query workflow_instance_id: string, @query node_id: string): Record<unknown>;

  @route("/api/workers/execution/witnessed-runs/diagnostics")
  @get
  @doc("Per-run health: authoritative status, enumerated missing lineage elements, receipt-correlation validity (W3.05).")
  op executionWitnessedRunDiagnostics(@query workflow_instance_id: string, @query node_id: string): Record<unknown>;

  @route("/api/workers/execution/projections/witnessed-runs")
  @get
  @doc("W3.08 versioned governed projection (projectionVersion pinned by consumers, fail-closed). Identity correlation only; no governance payloads.")
  op executionWitnessedRunProjection(@query workflow_instance_id: string, @query node_id: string): Record<unknown>;

  @route("/api/workers/execution/metrics")
  @get
  @doc("Governance metric families snapshot (counters + latency aggregates; worker-local registry, not shared with the legacy process).")
  op executionGovernanceMetrics(): Record<unknown>;

  @route("/api/workers/pty")
  @get
  @doc("List live pty sessions.")
  op ptyList(): PtyList;

  @route("/api/workers/pty")
  @post
  @doc("Spawn a pty session.")
  op ptySpawn(@body body: PtySpawnRequest): PtySession;

  @route("/api/workers/pty/{id}")
  @delete
  @doc("Terminate a pty session.")
  op ptyKill(@path id: string): Record<unknown>;

  @route("/api/workers/harness")
  @get
  @doc("Harness worker health: DB connectivity + active session count.")
  op harnessHealth(): WorkerHealth;

  @route("/api/workers/harness/run")
  @post
  @doc("Resolve context and execute an agent run for a wind task.")
  op harnessRun(@body body: HarnessRunRequest): Record<unknown>;

  @route("/api/workers/harness/resolve-context")
  @post
  @doc("Resolve the agent context (role, prompt, task, harness) for a wind task without executing.")
  op harnessResolveContext(@body body: HarnessResolveRequest): Record<unknown>;

  @route("/api/workers/harness/sessions")
  @get
  @doc("List active harness sessions.")
  op harnessSessions(): HarnessSessions;
}
