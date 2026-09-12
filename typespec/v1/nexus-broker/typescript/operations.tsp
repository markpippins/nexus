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
  // RETIRED (2026-09-02): SOL IR — renamed to Keychains per D-2026-08-31.
  // sol_ir was the retired name for keychains; the implementation now serves
  // the /api/keychain-snapshot/* surface below. These two ops are RETIRED,
  // not planned: no source route exists and none is planned. Do not
  // implement them — reconcile-typescript.py excludes them as retired.
  // ========================================================================

  @route("/api/solir/status")
  @get
  @doc("[RETIRED 2026-09-02 — superseded by GET /api/keychain-snapshot/status. Do not implement: kept only as the tombstone for the retired solir name.]")
  @tag("retired")
  op solirStatus(): SolIrStatus;

  @route("/api/solir/snapshot")
  @post
  @doc("[RETIRED 2026-09-02 — superseded by POST /api/keychain-snapshot/snapshot. Do not implement: kept only as the tombstone for the retired solir name.]")
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
