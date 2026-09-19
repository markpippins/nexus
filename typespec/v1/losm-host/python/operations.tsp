/**
 * losm-host — TypeSpec Operations
 *
 * Rewritten for the vision-srv consolidation (architect decision 5d8e10fd).
 * Extracted from python/vision/losm-host/losm (FastAPI), including the
 * absorbed /api compatibility surface (compat.py). Router prefixes folded
 * into full paths:
 *   native: /receipts, /work-requests, /artifacts, /branches, /kernel
 *   compat: /api/... (vision-srv wire contract; /health stays outside /api)
 *
 * Consumer compat rule (decision point 6): the /api surface is byte-identical
 * to the retired vision-srv (:8003) — vision-ui, vision-mcp and slash
 * commands pass unchanged after the VISION_SRV_URL repoint to :8006.
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.losmhost {

  @route("/health")
  @get
  @doc("Liveness probe (losm-host native shape: status + feature flags).")
  op health(): unknown;

  // ==========================================================================
  // Native surface — receipts
  // ==========================================================================

  @route("/receipts/ingest")
  @post
  @doc("Ingest a receipt (hash-deduplicated).")
  op ingestReceipt(@body body: unknown): ReceiptIngestResponse;

  // ==========================================================================
  // Native surface — work requests (typed responses, `context` key)
  // ==========================================================================

  @route("/work-requests")
  @post
  @doc("Create a work request (CCNF entity_key stamped at birth).")
  op createWorkRequest(@body body: unknown): WorkRequestResponse;

  @route("/work-requests/{wr_id}")
  @get
  @doc("Get a work request by business-key UUID (decision 5d8e10fd: string wr_id addressing everywhere).")
  op getWorkRequest(@path wr_id: string): WorkRequestResponse;

  @route("/work-requests")
  @get
  @doc("List work requests (skip/limit pagination).")
  op listWorkRequests(@query skip?: int32, @query limit?: int32): WorkRequestResponse[];

  @route("/work-requests/{wr_id}/orchestrate")
  @post
  @doc("Start background pipeline orchestration for a work request.")
  op orchestrateWorkRequest(@path wr_id: string): unknown;

  @route("/work-requests/{wr_id}/transition")
  @post
  @doc("Request a lifecycle transition (governed; refusals are durable TRANSITION_REFUSED governance events).")
  op transitionWorkRequest(@path wr_id: string, @body body: unknown): WorkRequestResponse;

  // ==========================================================================
  // Native surface — artifacts / branches (wr-scoped reads, branch lifecycle)
  // ==========================================================================

  @route("/artifacts/wr/{wr_id}")
  @get
  @doc("List artifacts for a work request.")
  op listArtifactsForWr(@path wr_id: string): unknown;

  @route("/artifacts/{artifact_id}/lineage")
  @get
  @doc("Get an artifact's lineage chain.")
  op artifactLineage(@path artifact_id: string): unknown;

  @route("/branches")
  @post
  @doc("Create (fork) a branch for a work request.")
  op createBranch(@body body: unknown): unknown;

  @route("/branches/{branch_id}/fork")
  @post
  @doc("Fork from an existing branch.")
  op forkBranch(@path branch_id: string, @query label: string): unknown;

  @route("/branches/{branch_id}")
  @get
  @doc("Get branch info.")
  op getBranch(@path branch_id: string): unknown;

  @route("/branches/wr/{wr_id}")
  @get
  @doc("List branches for a work request.")
  op listBranchesForWr(@path wr_id: string): unknown;

  @route("/branches/{branch_id}/score")
  @post
  @doc("Score a branch.")
  op scoreBranch(@path branch_id: string, @query score: float64): unknown;

  @route("/branches/{branch_id}/merge")
  @post
  @doc("Merge a branch (strategy: select_best | ...).")
  op mergeBranch(@path branch_id: string, @query strategy?: string): unknown;

  @route("/branches/{branch_id}/discard")
  @post
  @doc("Discard a branch.")
  op discardBranch(@path branch_id: string): unknown;

  @route("/branches/wr/{wr_id}/best")
  @get
  @doc("Get the best-scored branch for a work request.")
  op bestBranch(@path wr_id: string): unknown;

  // ==========================================================================
  // Native surface — kernel
  // ==========================================================================

  @route("/kernel/validate-graph")
  @post
  @doc("Validate a kernel graph (LOSKernel check — different from the /api DAG structural validation).")
  op validateGraph(@body body: unknown): unknown;

  // ==========================================================================
  // /api — absorbed vision-srv surface (wire-identical; string wr_id)
  // ==========================================================================

  // ── Work requests (raw ORM rows; the exact key set is WR_ROW_KEYS) ────────

  @route("/api/work-requests")
  @get
  @doc("List work requests (raw rows; vision-srv-compatible).")
  op apiListWorkRequests(@query limit?: int32, @query skip?: int32): WorkRequestRow[];

  @route("/api/work-requests/{wr_id}")
  @get
  @doc("Get a work request by string wr_id (404 detail: 'Work request not found').")
  op apiGetWorkRequest(@path wr_id: string): WorkRequestRow;

  @route("/api/work-requests")
  @post
  @doc("Create a work request (201; body key context_data; CCNF entity_key stamped).")
  op apiCreateWorkRequest(@body body: unknown): WorkRequestRow;

  @route("/api/work-requests/{wr_id}")
  @patch
  @doc("Partially update a work request (intent/constraints/priority/context_data).")
  op apiPatchWorkRequest(@path wr_id: string, @body body: unknown): WorkRequestRow;

  @route("/api/work-requests/{wr_id}")
  @delete
  @doc("Delete a work request (200 {detail}; 404 if missing).")
  op apiDeleteWorkRequest(@path wr_id: string): unknown;

  // ── DAG API (6-pass compilation pipeline) ─────────────────────────────────

  @route("/api/work-requests/{wr_id}/dag")
  @get
  @doc("Compile and return the full WorkRequestDAG rooted at this WR (+ _metadata block).")
  op apiGetWorkRequestDag(@path wr_id: string): unknown;

  @route("/api/work-requests/{wr_id}/dag/path/{target_wr_id}")
  @get
  @doc("Shortest path between two WRs in the compiled DAG.")
  op apiGetDagPath(@path wr_id: string, @path target_wr_id: string): unknown;

  @route("/api/work-requests/{wr_id}/dag/validate")
  @get
  @doc("Structural validation of the WR's DAG without full compilation.")
  op apiValidateWorkRequestDag(@path wr_id: string): DagValidateResponse;

  // ── Branches ───────────────────────────────────────────────────────────────

  @route("/api/branches")
  @get
  @doc("List all branches (optional wr_id filter + skip/limit pagination).")
  op apiListBranches(@query wr_id?: string, @query limit?: int32, @query skip?: int32): BranchRow[];

  @route("/api/branches")
  @post
  @doc("Create a branch (201).")
  op apiCreateBranch(@body body: unknown): BranchRow;

  // ── Artifacts ──────────────────────────────────────────────────────────────

  @route("/api/artifacts")
  @get
  @doc("List all artifacts (optional wr_id filter + skip/limit pagination).")
  op apiListArtifacts(@query wr_id?: string, @query limit?: int32, @query skip?: int32): ArtifactRow[];

  @route("/api/artifacts")
  @post
  @doc("Create an artifact (201; type must be an ArtifactType enum value).")
  op apiCreateArtifact(@body body: unknown): ArtifactRow;
}
