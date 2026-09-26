/**
 * TesterAttestationRequest — the type definition for the architect's
 * "Design: Shrapnel-typed MongoDB tester attestation dispatcher (no new
 * table)" proposal (Discussions thread 8d431a5d, 2026-09-24), authored in
 * TypeSpec and compiled by tsp-eav-emitter into a shrapnel
 * stereotype_create_revision migration.
 *
 * Field table = the design's own table, one TypeSpec property per design
 * row. Type mapping through the 0001 registry:
 *   Long -> int64 (code 1)      String -> string (code 2)
 *   JSONB -> string[] (code 6)  Timestamp -> utcDateTime (code 5)
 *   UUID -> uuid (code 7)
 *
 * Required/optional mirrors the design's lifecycle semantics: fields that
 * exist from request creation are required; claim/lease/terminal/completion
 * fields arrive later and are optional.
 */
@doc(
  "A durable request asking the tester to attest a specific head of work. Shrapnel is the type/contract authority; MongoDB holds the request instances (dispatch state) per the dispatcher design. Mongo documents carry the stereotype name, schema_version, and schema_fingerprint of this revision."
)
namespace ShrapnelDispatch;

// This @typespec/compiler version ships no uuid scalar. Declared here so the
// design's UUID typing is faithful; the emitter maps a scalar NAMED `uuid`
// to field_type_code 7 by name (mapping.ts).
scalar uuid;

model TesterAttestationRequest {
  @doc("Immutable request identity; doubles as the Mongo _id.")
  request_id: uuid;

  @doc(
    "Idempotency key, derived from work ref + head SHA (unique per request; a new head gets a new request)."
  )
  request_key: string;

  @doc("PR, agent record, or artifact reference being verified.")
  work_ref: string;

  @doc("Exact code head being verified.")
  head_sha: string;

  @doc("Authoring role; input to the G1 self-attestation gate.")
  requested_by: string;

  @doc("Pre-staged evidence offered to the tester (CI runs, logs).")
  evidence: string[];

  @doc("Dispatch state: pending | claimed | completed | rejected | failed.")
  state: string;

  @doc("Bounded invocation count.")
  attempts: int64;

  @doc("Request creation time.")
  created_at: utcDateTime;

  @doc("Last state-transition time.")
  updated_at: utcDateTime;

  @doc("Fencing token for the current worker; null while pending.")
  claim_token?: string;

  @doc("Crash/retry lease expiry; null while not claimed.")
  lease_until?: utcDateTime;

  @doc("Rejection or failure explanation; present only on terminal states.")
  terminal_reason?: string;

  @doc("V179 attestation event identity, set on completion.")
  attestation_id?: uuid;
}
