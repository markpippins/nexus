/**
 * shrapnel — TypeSpec Models
 *
 * Shared shapes for the EAV object store surface. Values are JSON-native
 * (Long/String/Double/Boolean/Timestamp/JSONB/UUID at rest); decoded objects
 * return property_name -> JSON value maps.
 */
import "@typespec/http";

using Http;

namespace org.nexus.shrapnel {

  // ── shared error envelope (mirrors the TS error-handler.js shape) ────────

  @error
  model ErrorDetail {
    @doc("Error payload.")
    error: ErrorBody;
  }

  model ErrorBody {
    @doc("Human-readable message.")
    message: string;
    @doc("Optional structured details (e.g. PG constraint detail).")
    details?: string;
  }

  // ── health ───────────────────────────────────────────────────────────────

  model HealthResponse {
    status: string;
    counts: StoreCounts;
  }

  model StoreCounts {
    field_type_count: int32;
    field_count: int32;
    object_count: int32;
    value_count: int32;
    binding_count: int32;
  }

  // ── field types ──────────────────────────────────────────────────────────

  model FieldType {
    code: int32;
    name: string;
    description?: string;
    pg_type?: string;
  }

  // ── fields ───────────────────────────────────────────────────────────────

  model Field {
    id: int64;
    is_calculated: boolean;
    field_index: int32;
    label?: string;
    name: string;
    property_name: string;
    field_type_code: int32;
    created_at?: string;
    updated_at?: string;
  }

  /** POST /api/fields body — either {type: "String"} or {field_type_code: 2}. */
  model FieldSpec {
    is_calculated?: boolean;
    field_index?: int32;
    label?: string;
    name?: string;
    property_name: string;
    type?: string;
    field_type_code?: int32;
  }

  // ── objects ──────────────────────────────────────────────────────────────

  model ObjectRow {
    id: int64;
    created_at?: string;
  }

  /** Decoded object: property_name -> JSON value. */
  model ObjectValues {
    @doc("Decoded EAV bindings, keyed by property_name.")
    values: Record<unknown>;
  }

  model ObjectEnvelope {
    object: ObjectRow & {
      @doc("Decoded values (property_name -> JSON value).")
      values: Record<unknown>;
    };
  }

  /** POST /api/objects body: { fields?: FieldSpec[], values: Record } —
      values-only form infers fields. */
  model ObjectCreate {
    fields?: FieldSpec[];
    values?: Record<unknown>;
  }

  model ObjectCreated {
    object_id: int64;
    fields: FieldSpec[];
  }

  model ObjectValuesEnvelope {
    object_id: int64;
    values: BindingRow[];
  }

  model BindingRow {
    field_id: int64;
    property_name: string;
    label?: string;
    name?: string;
    field_type_code: int32;
    value_id: int64;
    bound_at?: string;
  }

  // ── encode ───────────────────────────────────────────────────────────────

  model EncodeResponse {
    object_id: int64;
    fields: FieldSpec[];
    decoded: Record<unknown>;
  }

  // ── stereotypes ──────────────────────────────────────────────────────────

  model StereotypeSummary {
    stereotype_id: int64;
    name: string;
    description?: string;
    head_revision_id: int64;
    version: int32;
    depth?: int32;
    contract_fingerprint?: string;
    created_at?: string;
  }

  model StereotypeRevision {
    id: int64;
    stereotype_id: int64;
    version: int32;
    parent_revision_id?: int64;
    parent_stereotype_id?: int64;
    extends_rationale?: string;
    depth?: int32;
    contract_fingerprint?: string;
    created_at?: string;
  }

  model StereotypeChainEntry {
    name: string;
    version: int32;
    hop: int32;
  }

  model ContractField {
    property_name: string;
    required: boolean;
    origin_revision?: int64;
    origin_stereotype?: string;
    origin_version?: int32;
  }

  model StereotypeRevisionCreate {
    name: string;
    extends_revision?: int64;
    rationale?: string;
    required_fields: string[];
    optional_fields?: string[];
  }

  model StereotypeRevisionCreated {
    revision_id: int64;
    name: string;
    version: int32;
    depth: int32;
    contract_fingerprint: string;
  }

  /** Classify request: object into a revision. */
  model ClassifyRequest {
    revision_id: int64;
    disposition?: string;
  }
}
