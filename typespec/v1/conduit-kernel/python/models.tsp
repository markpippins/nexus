/**
 * conduit-kernel — TypeSpec Models
 *
 * Reverse-engineered from python/conduit/app (FastAPI WRP kernel).
 * No route or verb decorators live here; the reconciler scans this file and
 * must find none.
 *
 * Wire-compat rules (per conduit consolidation):
 * - Request/response shapes match FastAPI jsonable_encoder output exactly
 * - UUID fields use @format("uuid") where applicable
 * - Error envelopes: FastAPI standard {"detail": ...}
 * - No trailing slashes on collection paths
 * - String plan_id/identity_id/session_id addressing everywhere
 */
namespace org.nexus.conduitkernel {

  // ============================================================================
  // Core / Root
  // ============================================================================

  /** Liveness probe response. */
  model LivenessResponse {
    status: "alive";
  }

  /** Readiness probe response. */
  model ReadinessResponse {
    status: "ready" | "unready";
    kernel_version?: int32;
  }

  /** Root service info. */
  model RootResponse {
    service: "WRP Kernel Runtime";
    version: string;
    docs: string;
  }

  // ============================================================================
  // Identities (Admin)
  // ============================================================================

  /** A work-request identity record. */
  model IdentityResponse {
    @format("uuid")
    identityId: string;
    /** Human-facing identity label (name/email/role). */
    label?: string;
    /** Opaque identity attributes. */
    attributes?: Record<unknown>;
    /** Aliases for this identity. */
    aliases?: string[];
    /** Outgoing graph edges. */
    edges_outgoing?: Record<unknown>[];
    /** Incoming graph edges. */
    edges_incoming?: Record<unknown>[];
  }

  model IdentityListResponse {
    identities: IdentityResponse[];
  }

  model IdentityUpdateResponse {
    @format("uuid")
    identityId: string;
    updated: boolean;
  }

  // ============================================================================
  // Receipts (/api/receipts)
  // ============================================================================

  /** Receipt record (unified view from nebula.receipts_unified). */
  model ReceiptResponse {
    @format("uuid")
    receiptId: string;
    planId: string;
    type?: string;
    agentRole?: string;
    sessionId?: string;
    artifactPath?: string;
    summary?: string;
    metadata?: Record<unknown>;
    createdAt?: string;
  }

  /** Formatted receipts list for a plan. */
  model PlanReceiptsResponse {
    planId: string;
    count: int32;
    receipts: ReceiptResponse[];
  }

  /** Raw receipt rows for a plan. */
  model PlanRawReceiptsResponse {
    planId: string;
    count: int32;
    receipts: Record<unknown>[];
  }

  /** Latest receipt type for a plan. */
  model LatestReceiptTypeResponse {
    planId: string;
    latestType?: string;
  }

  /** Receipt insert request (C1 single persistence path). */
  model ReceiptInsertRequest {
    @format("uuid")
    id: string;
    planId: string;
    type: string;
    agentRole: string;
    sessionId?: string;
    ticketId?: string;
    artifactPath?: string;
    summary?: string;
    metadataJson?: string;
    tokensUsed?: int32;
    createdAt: string;
    /** C1 gate 1 (Lilac): declaring-producer identity for TS front-door channel. */
    producerId?: string;
    sourceChannel?: string;
    correlationId?: string;
  }

  /** Receipt insert response. */
  model ReceiptInsertResponse {
    ok: boolean;
    id: string;
    planId: string;
  }

  /** Delete receipts request (unblock family only). */
  model DeleteReceiptsRequest {
    types: string[];
  }

  /** Delete receipts response. */
  model DeleteReceiptsResponse {
    deleted: int32;
    planId: string;
    types: string[];
  }

  // ============================================================================
  // State Inspection (/state)
  // ============================================================================

  /** System info summary. */
  model SystemInfoResponse {
    kernelVersion: int32;
    deltaCount: int32;
    planCount: int32;
    receiptCount: int32;
    identityCount: int32;
    graphEdgeCount: int32;
    lineageEventCount: int32;
  }

  /** Identity resolution response. */
  model IdentityResolutionResponse {
    id: string;
    aliases: string[];
    label?: string;
    edgesOutgoing: Record<unknown>[];
    edgesIncoming: Record<unknown>[];
  }

  /** Single receipt by ID. */
  model ReceiptByIdResponse {
    id: string;
    receipt: Record<unknown>;
  }

  /** Receipts by plan number. */
  model ReceiptsByPlanResponse {
    planNum: string;
    receipts: Record<unknown>[];
    count: int32;
  }

  /** State graph with pagination. */
  model GraphResponse {
    nodes: Record<unknown>[];
    edges: Record<unknown>[];
    totalEdges: int32;
    cursor: string;
    limit: int32;
  }

  /** Plan detail with WRP state machine position. */
  model PlanReceiptItem {
    id?: string;
    type?: string;
    agentRole?: string;
    createdAt?: string;
    summary?: string;
    ticketId?: string;
  }

  model PlanDetailResponse {
    planNum: string;
    identityId: string;
    aliases: string[];
    label?: string;
    receiptCount: int32;
    currentWrpState: string;
    validTransitions: string[];
    receipts: PlanReceiptItem[];
    edgesOutgoing: Record<unknown>[];
    edgesIncoming: Record<unknown>[];
  }

  /** State health check. */
  model StateHealthResponse {
    status: "ok";
    kernelVersion: int32;
  }

  /** Lineage events. */
  model LineageResponse {
    events: Record<unknown>[];
    count: int32;
  }

  // ============================================================================
  // Replay (/replay)
  // ============================================================================

  model ReplayResponse {
    replayed: int32;
    events?: Record<unknown>[];
  }

  model CompareResponse {
    differences: Record<unknown>[];
    equal: boolean;
  }

  // ============================================================================
  // Sessions (/api/sessions)
  // ============================================================================

  model SessionResponse {
    @format("uuid")
    sessionId: string;
    status?: string;
    running?: boolean;
    cost?: float64;
    createdAt?: string;
    lastActivity?: string;
    lastHeartbeatAt?: string;
    pid?: int32;
    isRunning?: boolean;
    role?: string;
    state?: string;
    detail?: string;
  }

  model SessionListResponse {
    sessions: SessionResponse[];
    count: int32;
  }

  model SessionKillResult {
    killed: boolean;
    sessionId: string;
    pids: int32[];
    errors?: string[];
    timestamp: string;
  }

  model SessionCostUpdateRequest {
    costUsd: float64;
  }

  model SessionCostUpdateResponse {
    updated: boolean;
    sessionId: string;
    costUsd: float64;
  }

  model SessionHeartbeatRequest {
    role?: string;
    state?: string;
    detail?: string;
    pid?: int32;
  }

  model SessionHeartbeatResponse {
    updated: boolean;
    sessionId: string;
    timestamp: string;
  }

  // ============================================================================
  // Circuit Breaker (/api/breaker)
  // ============================================================================

  model BreakerStateResponse {
    state: string;
    failures?: int32;
    open?: boolean;
    paused?: boolean;
    retryAfter?: int32;
    source?: string;
    detail?: string;
    error?: string;
    maxRetriesPerModel?: int32;
    retryDelaySeconds?: int32;
    maxFallbacks?: int32;
    pushBackToPending?: boolean;
    circuitBreakerRetryAfter?: int32;
  }

  model BreakerTripRequest {
    error: string;
    detail?: string;
    source?: string;
    retryAfter?: int32;
  }

  model BreakerFailureRecoveryConfig {
    maxRetriesPerModel?: int32;
    retryDelaySeconds?: int32;
    maxFallbacks?: int32;
    pushBackToPending?: boolean;
    circuitBreakerRetryAfter?: int32;
  }

  // ============================================================================
  // Admin (/admin)
  // ============================================================================

  model ConsistencyCheckResponse {
    consistent: boolean;
    issues: string[];
  }

  // ============================================================================
  // Delta (/delta)
  // ============================================================================

  model DeltaApplyRequest {
    // Delta payload (opaque)
  }

  model DeltaResponse {
    applied: boolean;
    delta?: Record<unknown>;
  }

  model StateSummaryResponse {
    state?: string;
    revision?: string;
  }

  // ============================================================================
  // Prometheus Metrics
  // ============================================================================

  model MetricsResponse {
    metrics: string;
  }
}
