/**
 * conduit-kernel -- TypeSpec Operations
 *
 * Reverse-engineered from python/conduit/app/main.py + app/api/routes_*.py
 * (FastAPI). Router mounts (include_router prefix) are folded into full paths:
 *   delta -> /delta/, state -> /state/, replay -> /replay/, admin -> /admin/,
 *   sessions -> /api, breaker -> /api, receipts -> /api.
 *
 * Wire-compat rules:
 * - Request/response shapes match FastAPI jsonable_encoder output exactly
 * - UUID fields use @format("uuid") where applicable
 * - Error envelopes: FastAPI standard {"detail": ...}
 * - No trailing slashes on collection paths (except where FastAPI adds them)
 * - String plan_id/identity_id/session_id addressing everywhere
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.conduitkernel {

  // ========================================================================
  // Root / Health Probes
  // ========================================================================

  @route("/metrics")
  @get
  @doc("Prometheus metrics endpoint.")
  op metrics(): MetricsResponse;

  @route("/healthz")
  @get
  @doc("Liveness probe -- always returns 200 if process is running.")
  op liveness(): LivenessResponse;

  @route("/readyz")
  @get
  @doc("Readiness probe -- checks DB connectivity and engine state.")
  op readiness(): ReadinessResponse;

  @route("/")
  @get
  @doc("Service root.")
  op root(): RootResponse;

  // ========================================================================
  // Receipts (/api) -- C1 Single Persistence Path
  // Router: receipts_router (prefix="/api")
  // ========================================================================

  @route("/api/{plan_id}")
  @get
  @doc("Get formatted receipts for a plan. Equivalent to db.ts getPlanReceipts().")
  op getPlanReceipts(@path plan_id: string): PlanReceiptsResponse | unknown;

  @route("/api/{plan_id}/raw")
  @get
  @doc("Get raw receipt rows for a plan. Equivalent to db.ts getReceiptsForPlan().")
  op getReceiptsRaw(@path plan_id: string): PlanRawReceiptsResponse | unknown;

  @route("/api/{plan_id}/latest-type")
  @get
  @doc("Get the latest receipt type for a plan. Equivalent to db.ts getLatestReceiptType().")
  op getLatestReceiptType(@path plan_id: string): LatestReceiptTypeResponse | unknown;

  @route("/api")
  @post
  @doc("Insert a receipt. C1 single persistence path for HTTP channel. Delegates to DBAdapter.insert_receipt with idempotency key (caller's id) and provenance stamping.")
  op insertReceipt(@body body: ReceiptInsertRequest): ReceiptInsertResponse | unknown;

  @route("/api/{plan_id}")
  @delete
  @doc("Delete receipts by plan and type (unblock family only: BLOCK, PLAN_BLOCK, CANCELLED, ABANDONED). Constrained to unblock_plan workflow per architect ruling fcec95a2. Query param: types (comma-separated).")
  op deleteReceipts(@path plan_id: string, @query types: string): DeleteReceiptsResponse | unknown;

  // ========================================================================
  // State Inspection (/state/)
  // Router: state_router (prefix="/state")
  // ========================================================================

  @route("/state/")
  @get
  @doc("Get kernel state summary or full view. Query param 'view': 'summary' (default) or 'full'.")
  op getState(@query view?: string): SystemInfoResponse | unknown;

  @route("/state/identity/{identity_id}")
  @get
  @doc("Resolve an identity by ID, node_id, or bare plan number. Returns identity details plus connected graph edges.")
  op getIdentity(@path identity_id: string): IdentityResolutionResponse | unknown;

  @route("/state/receipt/{receipt_id}")
  @get
  @doc("Look up a single receipt by its receipt UUID.")
  op getReceipt(@path receipt_id: string): ReceiptByIdResponse | unknown;

  @route("/state/receipts-by-plan/{plan_num}")
  @get
  @doc("List receipts whose plan_id matches the given plan number.")
  op getReceiptsByPlan(@path plan_num: string): ReceiptsByPlanResponse | unknown;

  @route("/state/graph")
  @get
  @doc("Get the cross-plan graph with cursor-based pagination. Query params: cursor (opaque), limit (1-5000, default 200).")
  op getGraph(@query cursor?: string, @query limit?: int32): GraphResponse | unknown;

  @route("/state/plan/{plan_num}")
  @get
  @doc("Get detailed profile of a plan: identity, receipt timeline, WRP state machine position, valid transitions, graph edges.")
  op getPlanDetail(@path plan_num: string): PlanDetailResponse | unknown;

  @route("/state/health")
  @get
  @doc("State subsystem health check.")
  op stateHealth(): StateHealthResponse | unknown;

  @route("/state/lineage")
  @get
  @doc("Get lineage events from database. Query params: version (optional filter), limit (default 100).")
  op getLineage(@query version?: int32, @query limit?: int32): LineageResponse | unknown;

  // ========================================================================
  // Replay (/replay/)
  // Router: replay_router (prefix="/replay")
  // ========================================================================

  @route("/replay/")
  @get
  @doc("List replay runs.")
  op listReplay(): ReplayResponse | unknown;

  @route("/replay/compare")
  @get
  @doc("Compare replay runs.")
  op compareReplay(): CompareResponse | unknown;

  // ========================================================================
  // Sessions (/api)
  // Router: sessions_router (prefix="/api")
  // ========================================================================

  @route("/api")
  @get
  @doc("List all sessions. Query param: running_only (boolean).")
  op listSessions(@query running_only?: boolean): SessionListResponse | unknown;

  @route("/api/running")
  @get
  @doc("Get currently running sessions.")
  op getRunningSessions(): SessionListResponse | unknown;

  @route("/api/stale")
  @get
  @doc("Detect stale sessions (no heartbeat within threshold). Query param: threshold_seconds (default 3600).")
  op getStaleSessions(@query threshold_seconds?: int32): SessionListResponse | unknown;

  @route("/api/{session_id}")
  @get
  @doc("Get a single session by ID.")
  op getSession(@path session_id: string): SessionResponse | unknown;

  @route("/api/{session_id}/cost")
  @patch
  @doc("Update session token cost.")
  op updateSessionCost(@path session_id: string, @body body: SessionCostUpdateRequest): SessionCostUpdateResponse | unknown;

  @route("/api/{session_id}/heartbeat")
  @post
  @doc("Update session heartbeat timestamp and activity.")
  op updateSessionHeartbeat(@path session_id: string, @body body?: SessionHeartbeatRequest): SessionHeartbeatResponse | unknown;

  @route("/api/{session_id}/kill")
  @post
  @doc("Kill a running session (SIGKILL process, close session, release tickets).")
  op killSession(@path session_id: string): SessionKillResult | unknown;

  // ========================================================================
  // Circuit Breaker (/api)
  // Router: breaker_router (prefix="/api")
  // ========================================================================

  @route("/api/breaker")
  @get
  @doc("Get circuit breaker state.")
  op getBreaker(): BreakerStateResponse | unknown;

  @route("/api/trip")
  @post
  @doc("Trip the circuit breaker.")
  op tripBreaker(@body body: BreakerTripRequest): BreakerStateResponse | unknown;

  @route("/api/reset")
  @post
  @doc("Reset the circuit breaker.")
  op resetBreaker(): BreakerStateResponse | unknown;

  @route("/api/pause")
  @post
  @doc("Pause the circuit breaker.")
  op pauseBreaker(): BreakerStateResponse | unknown;

  @route("/api/resume")
  @post
  @doc("Resume the circuit breaker.")
  op resumeBreaker(): BreakerStateResponse | unknown;

  @route("/api/failure-recovery")
  @get
  @doc("Get failure-recovery configuration.")
  op getFailureRecovery(): BreakerFailureRecoveryConfig | unknown;

  @route("/api/failure-recovery")
  @post
  @doc("Save failure-recovery configuration.")
  op saveFailureRecovery(@body body: BreakerFailureRecoveryConfig): BreakerFailureRecoveryConfig | unknown;

  // ========================================================================
  // Admin (/admin/)
  // Router: admin_router (prefix="/admin")
  // ========================================================================

  @route("/admin/identities")
  @get
  @doc("List identities.")
  op listIdentities(): IdentityListResponse | unknown;

  @route("/admin/identities/{identity_id}")
  @patch
  @doc("Update an identity.")
  op updateIdentity(@path identity_id: string, @body body: unknown): IdentityUpdateResponse | unknown;

  @route("/admin/identities/{identity_id}")
  @delete
  @doc("Delete an identity.")
  op deleteIdentity(@path identity_id: string): unknown;

  @route("/admin/consistency")
  @get
  @doc("Run a consistency check.")
  op checkConsistency(): ConsistencyCheckResponse | unknown;

  // ========================================================================
  // Delta (/delta/)
  // Router: delta_router (prefix="/delta")
  // ========================================================================

  @route("/delta/")
  @post
  @doc("Apply a delta.")
  op applyDelta(@body body: DeltaApplyRequest): DeltaResponse | unknown;

  @route("/delta/state")
  @get
  @doc("Get delta-derived state summary.")
  op getDeltaState(): StateSummaryResponse | unknown;

}
