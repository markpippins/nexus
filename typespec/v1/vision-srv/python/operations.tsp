/**
 * vision-srv — TypeSpec Operations
 *
 * Reverse-engineered from python/vision/losm-host (FastAPI).
 * Includes vision-srv wire-compat surface under /api (compat.py).
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.visionsrv {

  @route("/health")
  @get
  @doc("Liveness probe.")
  op health(): unknown;

  // ========================================================================
  // Receipts (/api/receipts) — Deliverable 1
  // ========================================================================

  @route("/api/receipts/ingest")
  @post
  @doc("Ingest an execution receipt.")
  op ingestReceipt(@body body: ReceiptIngestRequest): ReceiptIngestResponse | unknown;

  // ========================================================================
  // Work requests (/api/work-requests)
  // ========================================================================

  @route("/api/work-requests")
  @get
  @doc("List work requests.")
  op listWorkRequests(): WorkRequestResponse[] | unknown;

  @route("/api/work-requests/{wr_id}")
  @get
  @doc("Get a work request.")
  op getWorkRequest(@path wr_id: string): WorkRequestResponse | unknown;

  @route("/api/work-requests")
  @post
  @doc("Create a work request.")
  op createWorkRequest(@body body: unknown): WorkRequestResponse | unknown;

  @route("/api/work-requests/{wr_id}")
  @patch
  @doc("Update a work request.")
  op updateWorkRequest(@path wr_id: string, @body body: unknown): WorkRequestResponse | unknown;

  @route("/api/work-requests/{wr_id}")
  @delete
  @doc("Delete a work request.")
  op deleteWorkRequest(@path wr_id: string): unknown;

  @route("/api/work-requests/{wr_id}/dag")
  @get
  @doc("Get a work request's DAG.")
  op getWorkRequestDag(@path wr_id: string): unknown;

  @route("/api/work-requests/{wr_id}/dag/path/{target_wr_id}")
  @get
  @doc("Get a DAG path between two work requests.")
  op getDagPath(@path wr_id: string, @path target_wr_id: string): unknown;

  @route("/api/work-requests/{wr_id}/dag/validate")
  @get
  @doc("Validate a work request's DAG.")
  op validateDag(@path wr_id: string): unknown;

  // ========================================================================
  // Branches (/api/branches)
  // ========================================================================

  @route("/api/branches")
  @get
  @doc("List branches.")
  op listBranches(): BranchResponse[] | unknown;

  @route("/api/branches")
  @post
  @doc("Create a branch.")
  op createBranch(@body body: unknown): BranchResponse | unknown;

  // ========================================================================
  // Artifacts (/api/artifacts)
  // ========================================================================

  @route("/api/artifacts")
  @get
  @doc("List artifacts.")
  op listArtifacts(): ArtifactResponse[] | unknown;

  @route("/api/artifacts")
  @post
  @doc("Create an artifact.")
  op createArtifact(@body body: unknown): ArtifactResponse | unknown;

  // ========================================================================
  // Kernel (/kernel) — Deliverable 1
  // ========================================================================

  @route("/kernel/validate-graph")
  @post
  @doc("Validate a graph using the kernel constraint system.")
  op validateGraph(@body body: KernelGraph): KernelValidateResponse | unknown;

  // ========================================================================
  // Receipt-writer contract documentation — Deliverable 2
  // ========================================================================

  /**
   * RECEIPT-WRITER CONTRACT (portable across Python/JVM)
   *
   * External emitters MUST use conduit POST /api/receipts (C1 single persistence path).
   * The ported JVM vision kernel writes idempotently:
   *   - unique constraint on vision.receipts.id
   *   - ON CONFLICT DO NOTHING semantics
   *   - no "one process exists" assumptions (multi-instance safe)
   *
   * DELETE /api/receipts/{plan_id} hardening (ruling fcec95a2):
   *   - ruled target shape: types allowlist or dedicated /api/receipts/unblock route
   *   - capture the ruled target shape in TypeSpec marked as such
   *
   * Receipt type families:
   *   - vision.receipts: 18-type CHECK enum (chk_vision_receipts_type)
   *   - execution.receipts: 19-type CHECK enum (chk_execution_receipts_type, superset with EXECUTION_COMPLETE)
   *   - JVM writer MUST target the correct table
   */
}
