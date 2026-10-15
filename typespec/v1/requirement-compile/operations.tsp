import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.requirementcompile {
  /**
   * Governed requirement compilation capability implemented by nebula-srv.
   */
  @route("/api/v1/capabilities/requirement-compile")
  @tag("Requirement Compilation Capability")
  interface RequirementCompilationEndpoint {
    /**
     * Compile a requirement into a versioned IR and optionally request
     * creation of a pending implementation plan. This does not approve a plan,
     * dispatch opcode execution, or authorize effects downstream.
     *
     * A caller idempotencyKey is required when createPlan=true; matching retries return the original result.
     * dryRun=true MUST create neither a plan nor execution effects; it suppresses createPlan when both are requested.
     * A plan-creation failure after successful compilation returns a partial
     * outcome with the compiled IR and an explicit effect.planCreated=false.
     * An unmatched registry may produce a fallback only when the result marks
     * selection.kind=fallback and includes fallbackReason.
     */
    @post
    @route("/compile")
    compile(@body request: CompileRequirementRequest): CompileHttpResponse;

    /** Retrieve the immutable receipt by the receipt ID returned by compile. */
    @get
    @route("/receipts/{receiptId}")
    getReceipt(@path receiptId: string): ReceiptHttpResponse;
  }
}
