/**
 * aegis-srv — TypeSpec Models
 *
 * Domain entities for the `aegis` schema: a TLA+ state-machine registry
 * that bridges formal methods (TLA+ modules) to the `resolution` schema.
 *
 * Canonical source of field names/types is `schemas/aegis.sql`. The Python
 * domain model (python/aegis/aegis.py) holds the equivalent dataclasses.
 *
 * Tables (all children of `registry` cascade on registry_id):
 *   registry, constant, variable, state, transition, invariant,
 *   property, temporal_property, concept_mapping, attribute_mapping,
 *   relationship_mapping, model_check_result, validation_result,
 *   execution_log.
 */
namespace org.nexus.aegissrv {

  // ========================================================================
  // Registry — root of a formal state-machine definition
  // ========================================================================

  model Registry {
    /** UUID primary key (gen_random_uuid()) */
    @format("uuid") id: string;
    /** Registry name — unique among active registries */
    name: string;
    /** Human-readable description */
    description?: string;
    /** Semantic version of the TLA+ module */
    version: string;
    /** Raw TLA+ source text */
    tla_plus_source?: string;
    /** TLA+ module name (e.g. "TrafficLight") */
    tla_plus_module?: string;
    /** Server-assigned creation timestamp */
    createdAt: utcDateTime;
    /** Server-assigned last-update timestamp */
    updatedAt: utcDateTime;
    /** Arbitrary metadata (JSONB) */
    metadata?: Record<unknown>;
    /** Free-form tags */
    tags?: string[];
    /** Soft-delete flag (unique name index is partial on is_active) */
    is_active: boolean;
    /** Optional expiration timestamp */
    expires_at?: utcDateTime;
    /** Resolution schema concept representing this registry's main concept */
    @format("uuid") main_concept_id?: string;
  }

  model RegistryCreate {
    /** Registry name — unique among active registries */
    name: string;
    description?: string;
    version?: string;
    tla_plus_source?: string;
    tla_plus_module?: string;
    metadata?: Record<unknown>;
    tags?: string[];
    is_active?: boolean;
    expires_at?: utcDateTime;
    @format("uuid") main_concept_id?: string;
  }

  model RegistryUpdate {
    name?: string;
    description?: string;
    version?: string;
    tla_plus_source?: string;
    tla_plus_module?: string;
    metadata?: Record<unknown>;
    tags?: string[];
    is_active?: boolean;
    expires_at?: utcDateTime;
    @format("uuid") main_concept_id?: string;
  }

  // ========================================================================
  // TLA+ constants
  // ========================================================================

  model Constant {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Constant name — unique within its registry */
    name: string;
    /** TLA+ type: Set, Tuple, Function, etc. */
    type: string;
    /** Constant value (JSONB) */
    value?: Record<unknown>;
    description?: string;
    constraints?: string[];
    createdAt: utcDateTime;
  }

  model ConstantCreate {
    name: string;
    type: string;
    value?: Record<unknown>;
    description?: string;
    constraints?: string[];
  }

  model ConstantUpdate {
    name?: string;
    type?: string;
    value?: Record<unknown>;
    description?: string;
    constraints?: string[];
  }

  // ========================================================================
  // TLA+ variables
  // ========================================================================

  model Variable {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Variable name — unique within its registry */
    name: string;
    type: string;
    initial_value?: Record<unknown>;
    domain?: Record<unknown>;
    description?: string;
    constraints?: string[];
    createdAt: utcDateTime;
    /** Resolution schema concept attribute backing this variable */
    @format("uuid") attribute_id?: string;
  }

  model VariableCreate {
    name: string;
    type: string;
    initial_value?: Record<unknown>;
    domain?: Record<unknown>;
    description?: string;
    constraints?: string[];
    @format("uuid") attribute_id?: string;
  }

  model VariableUpdate {
    name?: string;
    type?: string;
    initial_value?: Record<unknown>;
    domain?: Record<unknown>;
    description?: string;
    constraints?: string[];
    @format("uuid") attribute_id?: string;
  }

  // ========================================================================
  // TLA+ states
  // ========================================================================

  model State {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** State name — unique within its registry */
    name: string;
    description?: string;
    /** variable -> value map (JSONB) */
    variable_assignments?: Record<unknown>;
    constraints?: string[];
    is_initial: boolean;
    is_terminal: boolean;
    createdAt: utcDateTime;
    @format("uuid") concept_id?: string;
    @format("uuid") attribute_value_id?: string;
  }

  model StateCreate {
    name: string;
    description?: string;
    variable_assignments?: Record<unknown>;
    constraints?: string[];
    is_initial?: boolean;
    is_terminal?: boolean;
    @format("uuid") concept_id?: string;
    @format("uuid") attribute_value_id?: string;
  }

  model StateUpdate {
    name?: string;
    description?: string;
    variable_assignments?: Record<unknown>;
    constraints?: string[];
    is_initial?: boolean;
    is_terminal?: boolean;
    @format("uuid") concept_id?: string;
    @format("uuid") attribute_value_id?: string;
  }

  // ========================================================================
  // TLA+ transitions
  // ========================================================================

  model Transition {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Transition name — unique within its registry */
    name: string;
    description?: string;
    /** Guard condition that gates firing */
    guard_expression?: string;
    /** variable -> new value action map (JSONB) */
    action?: Record<unknown>;
    weak_fairness: boolean;
    strong_fairness: boolean;
    temporal_conditions?: string[];
    priority: int32;
    createdAt: utcDateTime;
    @format("uuid") from_state_id?: string;
    @format("uuid") to_state_id?: string;
    @format("uuid") guard_rule_id?: string;
    @format("uuid") transition_rule_id?: string;
    @format("uuid") state_transition_id?: string;
  }

  model TransitionCreate {
    name: string;
    description?: string;
    guard_expression?: string;
    action?: Record<unknown>;
    weak_fairness?: boolean;
    strong_fairness?: boolean;
    temporal_conditions?: string[];
    priority?: int32;
    @format("uuid") from_state_id?: string;
    @format("uuid") to_state_id?: string;
    @format("uuid") guard_rule_id?: string;
    @format("uuid") transition_rule_id?: string;
    @format("uuid") state_transition_id?: string;
  }

  model TransitionUpdate {
    name?: string;
    description?: string;
    guard_expression?: string;
    action?: Record<unknown>;
    weak_fairness?: boolean;
    strong_fairness?: boolean;
    temporal_conditions?: string[];
    priority?: int32;
    @format("uuid") from_state_id?: string;
    @format("uuid") to_state_id?: string;
    @format("uuid") guard_rule_id?: string;
    @format("uuid") transition_rule_id?: string;
    @format("uuid") state_transition_id?: string;
  }

  // ========================================================================
  // TLA+ invariants
  // ========================================================================

  model Invariant {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Invariant name — unique within its registry */
    name: string;
    /** TLA+ expression */
    expression: string;
    description?: string;
    is_type_invariant: boolean;
    createdAt: utcDateTime;
    @format("uuid") rule_id?: string;
    @format("uuid") expression_id?: string;
  }

  model InvariantCreate {
    name: string;
    expression: string;
    description?: string;
    is_type_invariant?: boolean;
    @format("uuid") rule_id?: string;
    @format("uuid") expression_id?: string;
  }

  model InvariantUpdate {
    name?: string;
    expression?: string;
    description?: string;
    is_type_invariant?: boolean;
    @format("uuid") rule_id?: string;
    @format("uuid") expression_id?: string;
  }

  // ========================================================================
  // TLA+ properties (safety / liveness / fairness)
  // ========================================================================

  model Property {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Property name — unique within its registry */
    name: string;
    /** safety | liveness | fairness */
    type: PropertyType;
    /** TLA+ expression */
    expression: string;
    description?: string;
    is_verified: boolean;
    verified_at?: utcDateTime;
    @format("uuid") verified_by?: string;
    createdAt: utcDateTime;
  }

  model PropertyCreate {
    name: string;
    type: PropertyType;
    expression: string;
    description?: string;
    is_verified?: boolean;
    verified_at?: utcDateTime;
    @format("uuid") verified_by?: string;
  }

  model PropertyUpdate {
    name?: string;
    type?: PropertyType;
    expression?: string;
    description?: string;
    is_verified?: boolean;
    verified_at?: utcDateTime;
    @format("uuid") verified_by?: string;
  }

  enum PropertyType {
    safety,
    liveness,
    fairness,
  }

  // ========================================================================
  // Temporal logic properties
  // ========================================================================

  model TemporalProperty {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** Property name — unique within its registry */
    name: string;
    /** [] always, <> eventually, -> eventually-leads-to, ~> leads-to, => implies */
    operator: TemporalOperator;
    expression: string;
    description?: string;
    createdAt: utcDateTime;
  }

  model TemporalPropertyCreate {
    name: string;
    operator: TemporalOperator;
    expression: string;
    description?: string;
  }

  model TemporalPropertyUpdate {
    name?: string;
    operator?: TemporalOperator;
    expression?: string;
    description?: string;
  }

  enum TemporalOperator {
    /** [] always */
    always: "[]",
    /** <> eventually */
    eventually: "<>",
    /** -> eventually-leads-to */
    leads_to: "->",
    /** ~> strong leads-to */
    strong_leads_to: "~>",
    /** => implies */
    implies: "=>",
  }

  // ========================================================================
  // Resolution schema bridge — concept / attribute / relationship mappings
  // ========================================================================

  model ConceptMapping {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** TLA+ concept name — unique within its registry */
    tla_name: string;
    /** Resolution schema concept id */
    @format("uuid") concept_id: string;
    /** direct | derived | composite */
    mapping_type: string;
    mapping_expression?: string;
    /** one_to_one | one_to_many | many_to_one */
    cardinality: Cardinality;
    createdAt: utcDateTime;
  }

  model ConceptMappingCreate {
    tla_name: string;
    @format("uuid") concept_id: string;
    mapping_type: string;
    mapping_expression?: string;
    cardinality?: Cardinality;
  }

  model ConceptMappingUpdate {
    tla_name?: string;
    @format("uuid") concept_id?: string;
    mapping_type?: string;
    mapping_expression?: string;
    cardinality?: Cardinality;
  }

  enum Cardinality {
    one_to_one,
    one_to_many,
    many_to_one,
  }

  model AttributeMapping {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** TLA+ variable name — unique within its registry */
    tla_variable: string;
    /** Resolution schema concept attribute id */
    @format("uuid") attribute_id: string;
    conversion_function?: string;
    default_value?: Record<unknown>;
    createdAt: utcDateTime;
  }

  model AttributeMappingCreate {
    tla_variable: string;
    @format("uuid") attribute_id: string;
    conversion_function?: string;
    default_value?: Record<unknown>;
  }

  model AttributeMappingUpdate {
    tla_variable?: string;
    @format("uuid") attribute_id?: string;
    conversion_function?: string;
    default_value?: Record<unknown>;
  }

  model RelationshipMapping {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** TLA+ relationship name — unique within its registry */
    tla_relationship: string;
    /** Resolution schema concept relationship id */
    @format("uuid") relationship_id: string;
    /** direct | inverse | transitive */
    mapping_type: string;
    constraints?: string[];
    createdAt: utcDateTime;
  }

  model RelationshipMappingCreate {
    tla_relationship: string;
    @format("uuid") relationship_id: string;
    mapping_type: string;
    constraints?: string[];
  }

  model RelationshipMappingUpdate {
    tla_relationship?: string;
    @format("uuid") relationship_id?: string;
    mapping_type?: string;
    constraints?: string[];
  }

  // ========================================================================
  // Phase A immutable revisions and truthful verification evidence
  // ========================================================================

  model RegistryRevision {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    revision_number: int64;
    source: string;
    source_digest: string;
    @encodedName("application/json", "model")
    model_snapshot: Record<unknown>;
    model_digest: string;
    createdAt: utcDateTime;
    created_by?: string;
    @format("uuid") supersedes_revision_id?: string;
  }

  model RegistryRevisionCreate {
    created_by?: string;
  }

  enum VerificationStatus {
    verified,
    violated,
    unknown_status: "unknown",
    stale,
    invalid,
    unavailable,
  }

  // ========================================================================
  // Model-check and validation results
  // ========================================================================

  model ModelCheckResult {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    @format("uuid") registry_revision_id: string;
    @format("uuid") property_id?: string;
    /** Truthful outcome; verified requires verified safety from real TLC. */
    status: VerificationStatus;
    /** Counterexample / trace (JSONB) */
    trace?: Record<unknown>;
    checked_properties?: string[];
    execution_time_ms?: int32;
    checked_at: utcDateTime;
    @format("uuid") checked_by?: string;
    engine: string;
    engine_version: string;
    checker_config_digest: string;
    source_digest: string;
    model_digest: string;
    input_snapshot_digest: string;
    result_digest: string;
    safety_status: VerificationStatus;
    /** Never verified at a bounded admission gate. */
    liveness_status: VerificationStatus;
    /** Phase A permits advisory only. */
    authority_level: "advisory";
    reason: string;
  }

  model ModelCheckResultCreate {
    @format("uuid") property_id?: string;
    @format("uuid") revision_id?: string;
    /** Optional content-addressed subject/evidence snapshot. */
    input_snapshot?: Record<unknown>;
    input_snapshot_digest?: string;
    checker_config?: Record<unknown>;
    @format("uuid") checked_by?: string;
  }

  model ValidationResult {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    is_valid: boolean;
    errors?: unknown[];
    warnings?: unknown[];
    suggestions?: unknown[];
    validated_at: utcDateTime;
    @format("uuid") validated_by?: string;
  }

  model ValidationResultCreate {
    is_valid: boolean;
    errors?: unknown[];
    warnings?: unknown[];
    suggestions?: unknown[];
    @format("uuid") validated_by?: string;
  }

  // ========================================================================
  // Execution log — audited state transitions of external entities
  // ========================================================================

  model ExecutionLog {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    /** The external entity id whose transition is recorded */
    @format("uuid") entity_id: string;
    @format("uuid") from_state_id?: string;
    @format("uuid") to_state_id?: string;
    @format("uuid") transition_id?: string;
    trigger_event?: string;
    @format("uuid") trigger_user?: string;
    context?: Record<unknown>;
    createdAt: utcDateTime;
  }

  model ExecutionLogCreate {
    @format("uuid") entity_id: string;
    @format("uuid") from_state_id?: string;
    @format("uuid") to_state_id?: string;
    @format("uuid") transition_id?: string;
    trigger_event?: string;
    @format("uuid") trigger_user?: string;
    context?: Record<unknown>;
  }

  // ========================================================================
  // Aegis revision -> Wind compilation
  // ========================================================================

  model WindCompilation {
    @format("uuid") id: string;
    @format("uuid") registry_id: string;
    @format("uuid") registry_revision_id: string;
    @format("uuid") wind_workflow_id: string;
    @format("uuid") wind_workflow_version_id: string;
    wind_workflow_version_number: int32;
    source_digest: string;
    model_digest: string;
    wind_graph_digest: string;
    compiler_version: string;
    compiler_config_digest: string;
    compiled_at: utcDateTime;
    @format("uuid") compiled_by?: string;
    status: CompilationStatus;
  }

  model CompiledNode {
    @format("uuid") id: string;
    @format("uuid") compilation_id: string;
    @format("uuid") registry_id: string;
    @format("uuid") registry_revision_id: string;
    @format("uuid") state_id: string;
    @format("uuid") wind_workflow_version_id: string;
    @format("uuid") wind_node_id: string;
  }

  model CompiledEdge {
    @format("uuid") id: string;
    @format("uuid") compilation_id: string;
    @format("uuid") registry_id: string;
    @format("uuid") registry_revision_id: string;
    @format("uuid") transition_id: string;
    @format("uuid") wind_workflow_version_id: string;
    @format("uuid") wind_edge_id: string;
  }

  model WindCompilationCreate {
    @format("uuid") revision_id?: string;
    @format("uuid") wind_workflow_id: string;
    compiler_version?: string;
    compiler_config?: Record<unknown>;
    @format("uuid") compiled_by?: string;
  }

  enum CompilationStatus {
    succeeded,
    failed,
    stale,
    invalid,
  }

  model WindCompilationResponse {
    compilation: WindCompilation;
    workflow_version: Record<unknown>;
    nodes: CompiledNode[];
    edges: CompiledEdge[];
  }

  // ========================================================================
  // Shared response / error shapes
  // ========================================================================

  model HealthResponse {
    ok: boolean;
    service?: string;
  }

  model ErrorDetail {
    error: string;
    message?: string;
    details?: unknown[];
  }
}