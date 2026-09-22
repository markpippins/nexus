/**
 * shrapnel — TypeSpec Operations
 *
 * Dual-runtime route surface (see main.tsp): the TS service mounts at /api,
 * the JVM port mounts the same routes at /api/shrapnel inside the monolith.
 *
 * Conventions (typespec/v1/typescript-conventions.md):
 *   - flat full-path @route; one op per route
 *   - path params {id} map from Express :id
 *   - LAN-bound, no authentication (repo convention)
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.shrapnel {

  // ── Health ───────────────────────────────────────────────────────────────
  @route("/api/health") @get op health(): HealthResponse | ErrorDetail;

  // ── Field types ──────────────────────────────────────────────────────────
  @route("/api/field-types")            @get op listFieldTypes(): { field_types: FieldType[] } | ErrorDetail;
  @route("/api/field-types/{code}")     @get op getFieldType(@path code: string): { field_type: FieldType } | ErrorDetail;

  // ── Fields ───────────────────────────────────────────────────────────────
  @route("/api/fields")                 @get  op listFields(limit?: int32, offset?: int32, type_code?: int32): { fields: Field[] } | ErrorDetail;
  @route("/api/fields/{id}")            @get  op getField(@path id: string): { field: Field } | ErrorDetail;
  @route("/api/fields")                 @post op createField(@body body: FieldSpec): { field: Field } | ErrorDetail;

  // ── Objects ──────────────────────────────────────────────────────────────
  @route("/api/objects")                @get    op listObjects(limit?: int32, offset?: int32, decode?: boolean): { objects: ObjectRow[] } | ErrorDetail;
  @route("/api/objects/{id}")           @get    op getObject(@path id: string): ObjectEnvelope | ErrorDetail;
  @route("/api/objects")                @post   op createObject(@body body: ObjectCreate): ObjectCreated | ErrorDetail;
  @route("/api/objects/{id}")           @delete op deleteObject(@path id: string): { deleted: int64 } | ErrorDetail;
  @route("/api/objects/{id}/conformance") @get  op objectConformance(@path id: string): Record<unknown> | ErrorDetail;
  @route("/api/objects/{id}/classify")  @post   op classifyObject(@path id: string, @body body: ClassifyRequest): Record<unknown> | ErrorDetail;
  @route("/api/objects/{id}/values")    @get    op objectValues(@path id: string): ObjectValuesEnvelope | ErrorDetail;

  // ── Encode (generic encode/decode round-trip) ────────────────────────────
  @route("/api/encode") @post op encode(@body body: ObjectCreate): EncodeResponse | ErrorDetail;

  // ── Stereotypes ──────────────────────────────────────────────────────────
  @route("/api/stereotypes")                        @get  op listStereotypes(): { stereotypes: StereotypeSummary[] } | ErrorDetail;
  @route("/api/stereotypes/{name}")                 @get  op getStereotype(@path name: string): { revision: StereotypeRevision } | ErrorDetail;
  @route("/api/stereotypes/{name}/chain")           @get  op getStereotypeChain(@path name: string): { stereotype: string; head_revision_id: int64; chain: StereotypeChainEntry[] } | ErrorDetail;
  @route("/api/stereotypes/{name}/contract")        @get  op getStereotypeContract(@path name: string): { stereotype: string; head_revision_id: int64; contract: ContractField[] } | ErrorDetail;
  @route("/api/stereotypes/revisions")              @post op createStereotypeRevision(@body body: StereotypeRevisionCreate): StereotypeRevisionCreated | ErrorDetail;
}
