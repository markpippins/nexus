/**
 * solscript — TypeSpec Models
 *
 * Extracted from the reference implementation at python/SOLScript/solscript/
 * (models.py, events.py, adapters/contract.py). SOLScript is an in-memory
 * interpreter for the resolution schema language — a library, not an HTTP
 * service — so this contract is model-only (nats-envelope precedent): no
 * routes, no verbs. The Moleculer REST facade is a future consumer that
 * will layer its own operations contract on these models.
 *
 * Excluded by operator directive (2026-09-15): the hybrid techniques
 * reasoning lane (reasoning/pattern_library.py, LLMIntegrationLayer,
 * HybridReasoner). The deterministic core is the porting target.
 */
namespace org.nexus.solscript {

  // ── Scalar unions (models.py value types) ───────────────────────────

  /** JSON-safe scalar; models.py uses typing.Any for these positions. */
  scalar JsonValue;

  // ── Enums (models.py) ───────────────────────────────────────────────

  union ExpressionKind {
    literal: "literal",
    attributeRef: "attribute_ref",
    operator: "operator",
    functionCall: "function_call",
    relationshipRef: "relationship_ref",
    propositionRef: "proposition_ref",
  }

  union SolOperator {
    eq: "=",
    neq: "<>",
    gt: ">",
    lt: "<",
    gte: ">=",
    lte: "<=",
    and: "AND",
    or: "OR",
    not: "NOT",
  }

  union Quantifier {
    exists: "EXISTS",
    all: "ALL",
    count: "COUNT",
  }

  union RuleType {
    invariant: "invariant",
    guard: "guard",
    conditional: "conditional",
    derivation: "derivation",
  }

  union Severity {
    hard: "hard",
    soft: "soft",
  }

  /** Note: serialized values are capitalized (Disposition.Asserted -> "Asserted"). */
  union Disposition {
    asserted: "Asserted",
    disputed: "Disputed",
    rejected: "Rejected",
    pending: "Pending",
    proposed: "Proposed",
    stale: "Stale",
    retracted: "Retracted",
  }

  /** contract.py ContractAttribute.value_type vocabulary. */
  union AttributeValueType {
    text: "text",
    integer: "integer",
    numeric: "numeric",
    boolean: "boolean",
    timestamptz: "timestamptz",
    jsonb: "jsonb",
    uuid: "uuid",
  }

  /** models.py FrameDimension.value_kind vocabulary (v31). */
  union FrameValueKind {
    governedReference: "governed_reference",
    typedScalar: "typed_scalar",
  }

  // ── Core ontology (models.py) ───────────────────────────────────────

  model AttributeBinding {
    schemaName: string;
    tableName: string;
    columnName: string;
  }

  model ConceptAttribute {
    id: string;
    conceptId: string;
    name: string;
    description?: string;
    /** Free-form in models.py; adapters normalize to AttributeValueType. */
    valueType: string;
    isStateAttribute: boolean;
    binding?: AttributeBinding;
    allowedValues: string[];
    defaultValue?: JsonValue;
  }

  model RelationshipBinding {
    fromSchema: string;
    fromTable: string;
    fromColumn: string;
    toSchema: string;
    toTable: string;
    toColumn: string;
  }

  /** Recursive expression tree; operands nests sub-expressions. */
  model Expression {
    id: string;
    kind: ExpressionKind;
    returnType: string;
    operator?: SolOperator;
    literalValue?: JsonValue;
    attributeId?: string;
    functionName?: string;
    conceptRelationshipId?: string;
    quantifier?: Quantifier;
    referencedPropositionId?: string;
    propositionRefField?: string;
    operands: Expression[];
    label?: string;
  }

  model Rule {
    id: string;
    name: string;
    ruleType: RuleType;
    expression?: Expression;
    severity: Severity;
    conceptId?: string;
    conceptRelationshipId?: string;
    representationId?: string;
    stateTransitionId?: string;
    notes?: string;
    isRelationalCheck: boolean;
    // Extended fields used by the deterministic reasoner
    conceptAttributeId?: string;
    conclusionAttributeId?: string;
    conclusionValue?: JsonValue;
    conditions: Expression[];
  }

  model ConceptRelationship {
    id: string;
    fromConceptId: string;
    toConceptId: string;
    relationshipType: string;
    path?: string;
    notes?: string;
    binding?: RelationshipBinding;
    conditionals: Rule[];
    name: string;
  }

  model ConceptStateTransition {
    id: string;
    conceptId: string;
    fromValue?: string;
    toValue: string;
    name: string;
    notes?: string;
    guards: Rule[];
  }

  model Concept {
    id: string;
    name: string;
    description?: string;
    attributes: Record<ConceptAttribute>;
    relationships: Record<ConceptRelationship>;
    invariants: Rule[];
    derivations: Rule[];
    stateTransitions: ConceptStateTransition[];
    rules: Rule[];
  }

  model Entity {
    id: string;
    conceptId: string;
    attributes: Record<JsonValue>;
    externalId?: string;
    assetId?: string;
  }

  // ── Representations (models.py) ─────────────────────────────────────

  model RepresentationIdentity {
    id: string;
    representationId: string;
    identityStrategyId: string;
    identityExpression: string;
  }

  model RepresentationComparison {
    id: string;
    representationRelationshipId: string;
    fromColumn: string;
    toColumn: string;
    notes?: string;
  }

  model Representation {
    id: string;
    conceptId: string;
    label: string;
    schemaName?: string;
    tableName?: string;
    owningSubsystemId?: int32;
    owner?: string;
    rawMetadata: Record<JsonValue>;
    identity?: RepresentationIdentity;
    rules: Rule[];
  }

  // ── Frame discipline (models.py v31/v35) ────────────────────────────

  model FrameDimension {
    id: string;
    name: string;
    description?: string;
    /** 'governed_reference' | 'typed_scalar' */
    valueKind: string;
    /** 'text'|'integer'|'boolean'|'timestamp'|'numeric' for typed_scalar */
    scalarType?: string;
  }

  model FrameDimensionValue {
    id: string;
    dimensionId: string;
    value: string;
    description?: string;
  }

  model PropositionFrameValue {
    id: string;
    propositionId: string;
    dimensionId: string;
    /** for governed_reference */
    referenceValueId?: string;
    /** for typed_scalar */
    scalarValue?: string;
  }

  /** v35: a proposition describing the meaning of a dimension or one of its values. */
  model FrameDimensionMeaning {
    id: string;
    propositionId: string;
    dimensionId?: string;
    frameDimensionValueId?: string;
  }

  model Proposition {
    id: string;
    title: string;
    description?: string;
    assetConceptId: string;
    subjectEntityId: string;
    disposition: Disposition;
    value?: boolean;
    groundingStatus?: string;
    assertions: Rule[];
    comparisons: RepresentationComparison[];
    lastEvaluatedAt?: utcDateTime;
    // v31: frame discipline
    semanticTypeId?: string;
    frameValues: PropositionFrameValue[];
  }

  /** models.py FunctionBinding: executable binding (python_func excluded from wire). */
  model FunctionBinding {
    functionName: string;
    sqlTemplate: string;
    argCount: int32;
    returnType: string;
    notes?: string;
  }

  // ── Storage contract (adapters/contract.py, cutover 05) ─────────────

  model ContractConcept {
    id: string;
    name: string;
    description?: string;
  }

  model ContractAttribute {
    id: string;
    conceptId: string;
    name: string;
    /** 'text'|'integer'|'numeric'|'boolean'|'timestamptz'|'jsonb'|'uuid' */
    valueType: AttributeValueType;
    allowedValues: string[];
    isStateAttribute: boolean;
  }

  model ContractRelationship {
    id: string;
    fromConceptId: string;
    toConceptId: string;
    relationshipType: string;
    bindingFromColumn?: string;
    bindingToColumn?: string;
  }

  /** An instance of a concept with its attribute values + identity. */
  model ContractSubject {
    id: string;
    conceptId: string;
    externalId?: string;
    canonicalAssetId?: string;
    attributes: Record<JsonValue>;
    validFrom?: utcDateTime;
    validUntil?: utcDateTime;
  }

  /** A dense per-object attribute set (EAV-sourced). */
  model ContractShrapnelFact {
    objectId: string;
    attributes: Record<JsonValue>;
    validFrom?: utcDateTime;
    validUntil?: utcDateTime;
  }

  model ContractRevision {
    subjectId: string;
    parentRevisionId?: string;
    validFrom?: utcDateTime;
    validUntil?: utcDateTime;
    recordedUntilDt?: utcDateTime;
  }

  model ContractEvidence {
    id: string;
    subjectId?: string;
    source: string;
    content?: string;
    capturedAt?: utcDateTime;
  }

  // ── Keychains events (events.py) ────────────────────────────────────

  /** events.py KEYCHAIN_EVENT_SCHEMA_VERSION / READ_SET_MANIFEST_SCHEMA_VERSION. */
  const KEYCHAIN_EVENT_SCHEMA_VERSION: int32 = 1;
  const READ_SET_MANIFEST_SCHEMA_VERSION: int32 = 1;

  /** The exact evaluator context, by references and digests — not a second source of truth. */
  model ReadSetManifest {
    sourceNamespace: string;
    evaluationId: string;
    evaluationKind: string;
    targetId: string;
    /** Capture-time fact; retained but excluded from manifest_digest identity. */
    asOf: string;
    visibilityScope: string;
    evaluatorId?: string;
    permissions: Record<JsonValue>;
    context: Record<JsonValue>;
    sourceRefs: Record<JsonValue>[];
    manifestId: string;
    schemaVersion: int32;
    recordedAt: string;
    /** SHA-256 over the canonical-JSON descriptor (as_of excluded). */
    manifestDigest: string;
    status: string;
  }

  /** A durable source event consumed by the Keychains projection. */
  model KeychainEvent {
    sourceNamespace: string;
    sourceEventId: string;
    /** e.g. resolution.transition.committed | .refused | .rejected | .failed,
        resolution.evaluation.<kind>.completed */
    kind: string;
    outcome: string;
    aggregateId?: string;
    schemaVersion: int32;
    causationId?: string;
    correlationId?: string;
    actor?: string;
    contractId?: string;
    evaluatorId?: string;
    lawId?: string;
    effectiveAt?: string;
    recordedAt: string;
    readSet: Record<JsonValue>;
    payload: Record<JsonValue>;
    checkpointStatus: string;
  }

  /** Computed on the wire representation: {source_namespace}:{source_event_id}. */
  model KeychainEventEnvelope {
    event: KeychainEvent;
    eventId: string;
    idempotencyKey: string;
  }

  /** events.py build_transition_event outcome vocabulary. */
  union TransitionOutcome {
    committed: "committed",
    refused: "refused",
    rejected: "rejected",
    failed: "failed",
  }

  /** Read-set shape emitted by build_transition_event. */
  model TransitionReadSet {
    entityId: string;
    transitionId: string;
    conceptId?: string;
    guardResults: JsonValue;
    stateBefore?: JsonValue;
    stateAfter?: JsonValue;
  }

  // ── Programmatic API descriptor (documented here; a Moleculer REST facade maps these 1:1) ──

  /**
   * Programmatic interpreter surface, named as the interpreter.py methods a
   * Moleculer facade would expose as service actions. Declared as a union —
   * NOT an interface — so the contract stays route-free (zero synthesized
   * paths in projections; the reference library has zero HTTP routes).
   */
  union SolInterpreterOperation {
    evaluateProposition: "evaluate_proposition",
    checkRule: "check_rule",
    checkTransitionGuard: "check_transition_guard",
    transitionEntity: "transition_entity",
    executeQuery: "execute_query",
  }

  /** Options accepted by SolInterpreterApi.evaluateProposition. */
  model EvaluationOptions {
    /** Re-evaluate even when a terminal disposition is present. */
    force?: boolean;
    /** Optional evaluator identity recorded on the read-set manifest. */
    evaluatorId?: string;
    /** Optional visibility scope override for the read-set manifest. */
    visibilityScope?: string;
  }

  /** Minimal query descriptor mirroring query_builder.Query semantics. */
  model SolQuerySpec {
    conceptName: string;
    filters?: SolQueryFilter[];
    orderBy?: string;
    orderDirection?: string;
    limit?: int32;
    offset?: int32;
    fields?: string[];
  }

  model SolQueryFilter {
    attribute: string;
    operator: SolOperator;
    value: JsonValue;
  }

  /** Shape of evaluate_proposition's result mapping (interpreter.py). */
  model EvaluationResult {
    propositionId: string;
    disposition: Disposition;
    value?: boolean;
    groundingStatus?: string;
    evaluatedAt: utcDateTime;
    reason?: string;
  }

  model RuleCheckResult {
    ruleId: string;
    passed: boolean;
    severity: Severity;
    reason?: string;
  }

  model GuardCheckResult {
    transitionId: string;
    entityId: string;
    passed: boolean;
    failedGuards: RuleCheckResult[];
  }

  model TransitionResult {
    entityId: string;
    transitionId: string;
    outcome: TransitionOutcome;
    stateBefore?: Record<JsonValue>;
    stateAfter?: Record<JsonValue>;
    event?: KeychainEvent;
  }
}
