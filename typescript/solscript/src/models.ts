/**
 * SOLScript TypeScript core — data models.
 *
 * Ported from python/SOLScript/solscript/models.py (deterministic core).
 * Field casing is camelCase per the TypeSpec wire contract
 * (typespec/v1/solscript/python/models.tsp); enum string values are
 * IDENTICAL to the Python reference (parity requirement). The hybrid
 * techniques reasoning lane is excluded by operator directive.
 */

// ── Enums (values identical to Python) ───────────────────────────────

export enum ExpressionKind {
  Literal = "literal",
  AttributeRef = "attribute_ref",
  Operator = "operator",
  FunctionCall = "function_call",
  RelationshipRef = "relationship_ref",
  PropositionRef = "proposition_ref",
}

export enum SolOperator {
  Eq = "=",
  Neq = "<>",
  Gt = ">",
  Lt = "<",
  Gte = ">=",
  Lte = "<=",
  And = "AND",
  Or = "OR",
  Not = "NOT",
}

export enum Quantifier {
  Exists = "EXISTS",
  All = "ALL",
  Count = "COUNT",
}

export enum RuleType {
  Invariant = "invariant",
  Guard = "guard",
  Conditional = "conditional",
  Derivation = "derivation",
}

export enum Severity {
  Hard = "hard",
  Soft = "soft",
}

export enum Disposition {
  Asserted = "Asserted",
  Disputed = "Disputed",
  Rejected = "Rejected",
  Pending = "Pending",
  Proposed = "Proposed",
  Stale = "Stale",
  Retracted = "Retracted",
}

// ── JSON scalar ──────────────────────────────────────────────────────

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue };

// ── Core ontology ────────────────────────────────────────────────────

export interface AttributeBinding {
  schemaName: string;
  tableName: string;
  columnName: string;
}

export interface ConceptAttribute {
  id: string;
  conceptId: string;
  name: string;
  description?: string;
  valueType: string;
  isStateAttribute: boolean;
  binding?: AttributeBinding;
  allowedValues: string[];
  defaultValue?: JsonValue;
}

export interface RelationshipBinding {
  fromSchema: string;
  fromTable: string;
  fromColumn: string;
  toSchema: string;
  toTable: string;
  toColumn: string;
}

export interface Expression {
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

export interface Rule {
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

export interface ConceptRelationship {
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

export interface ConceptStateTransition {
  id: string;
  conceptId: string;
  fromValue?: string;
  toValue: string;
  name: string;
  notes?: string;
  guards: Rule[];
}

export interface Concept {
  id: string;
  name: string;
  description?: string;
  attributes: Record<string, ConceptAttribute>;
  relationships: Record<string, ConceptRelationship>;
  invariants: Rule[];
  derivations: Rule[];
  stateTransitions: ConceptStateTransition[];
  rules: Rule[];
}

export interface Entity {
  id: string;
  conceptId: string;
  attributes: Record<string, JsonValue>;
  externalId?: string;
  assetId?: string;
}

// ── Representations ──────────────────────────────────────────────────

export interface RepresentationIdentity {
  id: string;
  representationId: string;
  identityStrategyId: string;
  identityExpression: string;
}

export interface RepresentationComparison {
  id: string;
  representationRelationshipId: string;
  fromColumn: string;
  toColumn: string;
  notes?: string;
}

export interface Representation {
  id: string;
  conceptId: string;
  label: string;
  schemaName?: string;
  tableName?: string;
  owningSubsystemId?: number;
  owner?: string;
  rawMetadata: Record<string, JsonValue>;
  identity?: RepresentationIdentity;
  rules: Rule[];
}

// ── Frame discipline (v31/v35) ───────────────────────────────────────

export interface FrameDimension {
  id: string;
  name: string;
  description?: string;
  /** 'governed_reference' | 'typed_scalar' */
  valueKind: string;
  /** 'text'|'integer'|'boolean'|'timestamp'|'numeric' for typed_scalar */
  scalarType?: string;
}

export interface FrameDimensionValue {
  id: string;
  dimensionId: string;
  value: string;
  description?: string;
}

export interface PropositionFrameValue {
  id: string;
  propositionId: string;
  dimensionId: string;
  /** for governed_reference */
  referenceValueId?: string;
  /** for typed_scalar */
  scalarValue?: string;
}

/** v35: a proposition describing the meaning of a dimension or one of its values. */
export interface FrameDimensionMeaning {
  id: string;
  propositionId: string;
  dimensionId?: string;
  frameDimensionValueId?: string;
}

export interface Proposition {
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
  lastEvaluatedAt?: string;
  // v31: frame discipline
  semanticTypeId?: string;
  frameValues: PropositionFrameValue[];
}

/** FunctionBinding: executable binding (python_func replaced by the interpreter's function registry). */
export interface FunctionBinding {
  functionName: string;
  sqlTemplate: string;
  argCount: number;
  returnType: string;
  notes?: string;
}
