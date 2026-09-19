/**
 * losm-host — TypeSpec Models
 *
 * Rewritten for the vision-srv consolidation (architect decision 5d8e10fd).
 * Shapes are taken from the shared store models (python/vision/losm-store/
 * src/losm_store/models.py) that BOTH losm-host and vision-srv serialize,
 * and locked by tests (python/vision/losm-host/tests/test_compat_surface.py).
 *
 * Wire keys are snake_case to match FastAPI's attribute-based JSON encoding.
 */
namespace org.nexus.losmhost {

  /** Raw ORM row of a work request — the /api (vision-srv-compatible) surface. */
  model WorkRequestRow {
    @doc("Internal integer primary key. Never an addressable key on the wire.")
    id: int32;
    @doc("Business-key UUID. The addressable identifier on every surface.")
    wr_id: string;
    parent_request_id?: string;
    intent: string;
    constraints?: Record<unknown>;
    priority: int32;
    @doc("Context payload. DB column `context`; ORM attribute `context_data`.")
    context_data?: Record<unknown>;
    @doc("WorkStatus state-machine value (NEW, INTAKE, ..., COMPLETION, BLOCKED, FAILED).")
    status: string;
    created_at: string;
    recorded_on_dt?: string;
    recorded_until_dt?: string;
  }

  /** Typed response of the native (non-/api) work-request surface. */
  model WorkRequestResponse {
    id: int32;
    wr_id: string;
    intent: string;
    constraints?: Record<unknown>;
    priority: int32;
    @doc("Context payload under the native key (the /api surface serves `context_data`).")
    context?: Record<unknown>;
    status: string;
    created_at: string;
    updated_at: string;
  }

  /** Raw ORM row of a branch. */
  model BranchRow {
    id?: int32;
    branch_id: string;
    wr_id: string;
    parent_branch_id?: string;
    fork_point?: string;
    label?: string;
    score?: float64;
    status: string;
    created_at: string;
    recorded_on_dt?: string;
    recorded_until_dt?: string;
  }

  /** Raw ORM row of an artifact. */
  model ArtifactRow {
    id?: int32;
    artifact_id: string;
    @doc("ArtifactType enum value (PLAN, CRITIQUE, SPEC, ..., SUMMARY).")
    type: string;
    content: Record<unknown>;
    confidence?: float64;
    provenance?: Record<unknown>;
    wr_id?: string;
    parent_artifact_id?: string;
    template_metadata?: Record<unknown>;
    created_at: string;
    recorded_on_dt?: string;
    recorded_until_dt?: string;
  }

  /** Response of POST /receipts/ingest. */
  model ReceiptIngestResponse {
    status: string;
    receipt_id: string;
    work_request_id: string;
    event_type: string;
  }

  /** Response of GET /api/work-requests/{wr_id}/dag/validate. */
  model DagValidateResponse {
    wr_id: string;
    valid: boolean;
    issues: unknown[];
    warnings: unknown[];
    node_count: int32;
    edge_count: int32;
  }

  /** _metadata block appended to the full DAG compilation result. */
  model DagMetadata {
    node_count: int32;
    edge_count: int32;
    compilation_time_ms: float64;
  }
}
