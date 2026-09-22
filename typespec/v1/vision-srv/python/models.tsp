/**
 * vision-srv — TypeSpec Models
 *
 * Reverse-engineered from python/vision/losm-host (FastAPI).
 * No route or verb decorators live here.
 *
 * Wire-compat rules (per architect decision 5d8e10fd):
 * - request/response shapes are byte-identical to losm-host /api compat surface
 * - `context` key (not `context_data`) on WorkRequestResponse
 * - string `wr_id` addressing everywhere (int PK stays internal)
 * - error bodies are FastAPI `{"detail":...}` envelopes
 * - no trailing slashes on collection paths
 */
namespace org.nexus.visionsrv {

  /** A work request. */
  model WorkRequestResponse {
    wrId: string;
    state?: string;
    spec?: Record<unknown>;
    context?: Record<unknown>;
    parentRequestId?: string;
    priority?: int32;
    createdAt?: string;
    recordedOnDt?: string;
    recordedUntilDt?: string;
  }

  /** A branch. */
  model BranchResponse {
    branchId: string;
    wrId?: string;
    state?: string;
    parentBranchId?: string;
    forkPoint?: string;
    label?: string;
    score?: float64;
    createdAt?: string;
  }

  /** An artifact. */
  model ArtifactResponse {
    artifactId: string;
    wrId?: string;
    kind?: string;
    type?: string;
    content?: Record<unknown>;
    confidence?: float64;
    provenance?: Record<unknown>;
    parentArtifactId?: string;
    templateMetadata?: Record<unknown>;
    createdAt?: string;
    title?: string;
    summary?: string;
    stage?: string;
  }

  // ============================================================================
  // Receipt types (vision.receipts) — Deliverable 2
  // ============================================================================

  /** Receipt type enum from live CHECK constraint `chk_vision_receipts_type`. */
  enum VisionReceiptType {
    ABANDONED,
    API_LIMIT,
    BLOCK,
    CANCELLED,
    CCNF_EXECUTION,
    CRITIQUE,
    CRITIQUE_PASS,
    CRITIQUE_REJECT,
    HOLD,
    IMPLEMENTATION,
    PLANNING,
    PLAN_BLOCK,
    PLAN_CREATE,
    PROPOSED,
    REQUEUED,
    REVIEW,
    REVIEW_PASS,
    REVIEW_REJECT,
  }

  /** Vision receipt model mirroring vision.receipts table. */
  model VisionReceipt {
    id: string;
    planId: string;
    type: VisionReceiptType;
    agentRole: string;
    sessionId?: string;
    artifactPath?: string;
    summary: string;
    metadataJson: string;
    createdAt: string;
    ticketId?: string;
    tokensUsed: int32;
    recordedOnDt: string;
    recordedUntilDt?: string;
    sequence: int32;
  }

  /** Receipt ingest request/response. */
  model ReceiptIngestRequest {
    // Arbitrary receipt payload - validated by ingester
  }

  model ReceiptIngestResponse {
    status: string;
    receiptId: string;
    workRequestId: string;
    eventType: string;
  }

  // ============================================================================
  // Kernel types
  // ============================================================================

  /** Graph node for kernel validation. */
  model KernelGraphNode {
    id: string;
    label?: string;
    metadata?: Record<unknown>;
  }

  /** Graph edge for kernel validation. */
  model KernelGraphEdge {
    source: string;
    target: string;
    type?: string;
    metadata?: Record<unknown>;
  }

  /** Graph structure for kernel validation. */
  model KernelGraph {
    nodes: KernelGraphNode[];
    edges: KernelGraphEdge[];
  }

  /** Kernel validation response. */
  model KernelValidateResponse {
    valid: boolean;
    graph?: Record<unknown>;
    error?: string;
  }
}
