/**
 * WorkRequest — the shrapnel stereotype for the canonical WorkRequest
 * (nebula.work_requests), authored from the live column set (read from
 * postgres 2026-09-26, information_schema).
 *
 * PREREQUISITE for TesterAttestationRequest: an attestation request IS a
 * kind of work request ("verify this head"), so the type chain is
 *
 *     WorkRequest  <-  TesterAttestationRequest extends WorkRequest
 *
 * Scope note: this contract covers the fields a consumer of a work-request
 * SHAPE needs (identity, intent, attribution, lifecycle). Deliberately NOT
 * carried over: nebula plumbing columns (source_specification_id,
 * source_requirement_id, plan_id, legacy_id, dco_json, step_outputs,
 * asset_id, entity_key, bitemporal recorded_* columns) — those belong to
 * the nebula TABLE, not to the shrapnel TYPE. C5 honesty: this stereotype
 * describes the data shape; it does not claim nebula.work_requests is
 * backed by shrapnel.
 *
 * Type mapping through the 0001 registry:
 *   uuid -> 7, string -> 2, string[] -> 6 (JSONB), utcDateTime -> 5.
 */
@doc(
  "A WorkRequest: a durable, attributed unit of requested work with intent, context, and lifecycle. Canonical storage is nebula.work_requests; this stereotype is the shared TYPE contract for shapes that are kinds of work requests."
)
namespace ShrapnelDispatch;

// This @typespec/compiler version ships no uuid scalar. Declared once in
// the base file; the emitter maps a scalar NAMED `uuid` to field_type_code
// 7 by name (mapping.ts).
scalar uuid;

model WorkRequest {
  @doc("Immutable identity (nebula.work_requests.id).")
  id: uuid;

  @doc("Short title of the requested work.")
  title: string;

  @doc("Full description of what is being asked.")
  description: string;

  @doc("The intent this request serves (cognition over the knowledge graph).")
  intent: string;

  @doc("Structured context payload accompanying the request.")
  context: string[];

  @doc("Structured constraints the work must honor.")
  constraints: string[];

  @doc("Role or account that created the request.")
  created_by: string;

  @doc("Creation time.")
  created_at: utcDateTime;

  @doc("Last update time.")
  updated_at: utcDateTime;
}
