/**
 * TesterAttestationRequest — the dispatcher design's type (thread
 * 8d431a5d), now a KIND OF WorkRequest:
 *
 *     WorkRequest  <-  TesterAttestationRequest extends WorkRequest
 *
 * Inheritance semantics (this is the concrete answer to "how do we
 * indicate inheritance, starting from TypeSpec"):
 *   - TypeSpec `extends` is the authoring surface — the same mechanism the
 *     language already enforces (override-property-mismatch blocks
 *     required->optional weakening at compile time).
 *   - The EMITTER compiles it: base properties ride along implicitly in
 *     TypeSpec, but shrapnel's superset-v2 trigger requires child
 *     revisions to DECLARE every parent-required field — so the emitter
 *     materializes inherited required fields transitively into the child
 *     contract (the DB oracle caught exactly this on the first-generation
 *     output; see README).
 *   - The DATABASE enforces it durably: depth<=3 (C1), acyclicity,
 *     fingerprint v2 over the full declared field document, and
 *     stereotype_extends() answering "is X a Y" transitively at query
 *     time.
 *
 * Own additions beyond WorkRequest: attestation-specific identity
 * (request_key idempotency, work_ref/head_sha target, G1 attribution,
 * evidence, dispatch state machine, completion back-reference).
 * id/title/created_by/created_at/updated_at are INHERITED from
 * WorkRequest (title reused, never weakened).
 */
import "./work-request.tsp";
import "tsp-eav-emitter";

namespace ShrapnelDispatch {

@ShrapnelCatalog.instanceStorage("mongodb")
@ShrapnelCatalog.extendsRationale(
  "An attestation request is a work request whose ask is verification of a specific head; it inherits identity/attribution/lifecycle from WorkRequest and adds only the attestation-specific target, evidence, and dispatch-state fields."
)
model TesterAttestationRequest extends WorkRequest {
  @doc("Idempotency key, derived from work ref + head SHA (unique per request).")
  request_key: string;

  @doc("Work reference being verified (PR, agent record, artifact).")
  work_ref: string;

  @doc("Exact code head being verified.")
  head_sha: string;

  @doc("Pre-staged evidence offered to the tester (CI runs, logs).")
  evidence: string[];

  @doc("Dispatch state: pending | claimed | completed | rejected | failed.")
  state: string;

  @doc("Bounded invocation count.")
  attempts: int64;

  @doc("Fencing token for the current worker; null while pending.")
  claim_token?: string;

  @doc("Crash/retry lease expiry; null while not claimed.")
  lease_until?: utcDateTime;

  @doc("Rejection or failure explanation; present only on terminal states.")
  terminal_reason?: string;

  @doc("V179 attestation event identity, set on completion.")
  attestation_id?: uuid;
}
}
