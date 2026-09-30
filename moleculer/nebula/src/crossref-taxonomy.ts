/**
 * Cross-Reference Relation Type Taxonomy v0.1
 *
 * Formal enumerated taxonomy of all valid rel_type values for
 * the nebula.cross_references table. Replaces ad-hoc free-text strings.
 *
 * Spec: audit/SPECS/CROSSREF_TAXONOMY.md
 * Ontology: schemas/ontology/relationships/wrp-crossref-taxonomy.jsonld
 * Plan reference: #0175
 */

/* ── Enum ─────────────────────────────────────────────────────────── */

export enum CrossReferenceType {
  /* WRP domain */
  WRP_DEPENDS_ON     = "wrp:depends_on",
  WRP_IMPLEMENTS     = "wrp:implements",
  WRP_TRACKED_BY     = "wrp:tracked_by",
  WRP_IMPACTS_SYSTEM = "wrp:impacts_system",
  WRP_SUPERSEDES     = "wrp:supersedes",

  /* Agent domain */
  AG_REFERENCES_PLAN      = "ag:references_plan",
  AG_SAME_THREAD_AS       = "ag:same_thread_as",
  AG_PROMPTED_BY          = "ag:prompted_by",
  AG_SPAWNS_PLAN          = "ag:spawns_plan",
  AG_EVIDENCES_CANDIDATE  = "ag:evidences_candidate",

  /* Knowledge domain */
  KV_SOURCED_FROM         = "kv:sourced_from",
  KV_INFORMS              = "kv:informs",
  KV_CROSS_SCHEMA         = "kv:cross_schema",
  KV_NAME_OVERLAP         = "kv:name_overlap",
  KV_DESCRIPTION_OVERLAP  = "kv:description_overlap",

  /* Requirement domain */
  REQ_BLOCKS     = "req:blocks",
  REQ_DEPENDS_ON = "req:depends_on",

  /* Specification domain */
  SPEC_DEFINES_REQ = "spec:defines_req",
}

/* ── All valid string values ─────────────────────────────────────── */

export const ALL_CROSSREF_TYPES: readonly string[] = Object.values(CrossReferenceType);

/* ── Legacy ───────────────────────────────────────────────────────── */

/** Legacy rel_type values that should not be used for new writes. */
export const LEGACY_CROSSREF_TYPES = new Set<string>(["depends_on"]);

/** Map legacy → replacement. */
export const LEGACY_REPLACEMENT: Record<string, string> = {
  "depends_on": CrossReferenceType.WRP_DEPENDS_ON,
};

/* ── Type guard ────────────────────────────────────────────────────── */

export function isValidCrossReferenceType(value: string): value is CrossReferenceType {
  return ALL_CROSSREF_TYPES.includes(value);
}

/* ── Source/target type constraints ──────────────────────────────── */

interface TypeConstraint {
  sourceType: string;
  targetType: string;
}

const TYPE_CONSTRAINTS: Record<string, TypeConstraint> = {
  [CrossReferenceType.WRP_DEPENDS_ON]:     { sourceType: "plan",       targetType: "plan" },
  [CrossReferenceType.WRP_IMPLEMENTS]:     { sourceType: "plan",       targetType: "work_request" },
  [CrossReferenceType.WRP_TRACKED_BY]:     { sourceType: "work_request", targetType: "plan" },
  [CrossReferenceType.WRP_IMPACTS_SYSTEM]: { sourceType: "plan",       targetType: "system" },
  [CrossReferenceType.WRP_SUPERSEDES]:     { sourceType: "plan",       targetType: "plan" },
  [CrossReferenceType.AG_REFERENCES_PLAN]: { sourceType: "agent_record", targetType: "plan" },
  [CrossReferenceType.AG_SAME_THREAD_AS]:  { sourceType: "agent_record", targetType: "agent_record" },
  [CrossReferenceType.AG_PROMPTED_BY]:     { sourceType: "agent_record", targetType: "prompt" },
  [CrossReferenceType.AG_SPAWNS_PLAN]:          { sourceType: "harvest_candidate", targetType: "plan" },
  [CrossReferenceType.AG_EVIDENCES_CANDIDATE]:  { sourceType: "agent_record", targetType: "harvest_candidate" },
  [CrossReferenceType.KV_SOURCED_FROM]:    { sourceType: "knowledge_entity", targetType: "harvest" },
  [CrossReferenceType.KV_INFORMS]:         { sourceType: "harvest", targetType: "knowledge_entity" },
  [CrossReferenceType.KV_CROSS_SCHEMA]:    { sourceType: "embedding",  targetType: "embedding" },
  [CrossReferenceType.KV_NAME_OVERLAP]:    { sourceType: "knowledge_entity", targetType: "knowledge_entity" },
  [CrossReferenceType.KV_DESCRIPTION_OVERLAP]: { sourceType: "knowledge_entity", targetType: "knowledge_entity" },
  [CrossReferenceType.REQ_BLOCKS]:     { sourceType: "requirement", targetType: "requirement" },
  [CrossReferenceType.REQ_DEPENDS_ON]: { sourceType: "requirement", targetType: "requirement" },
  [CrossReferenceType.SPEC_DEFINES_REQ]: { sourceType: "specification", targetType: "requirement" },
};

/**
 * Validate that a cross-reference's rel_type, source_type, and target_type
 * satisfy the taxonomy constraints.
 *
 * Returns `{ valid: true }` or `{ valid: false, error: string }`.
 */
export function validateCrossRefConstraint(
  relType: string,
  sourceType: string,
  targetType: string,
): { valid: true } | { valid: false; error: string } {
  if (!isValidCrossReferenceType(relType)) {
    const allowed = ALL_CROSSREF_TYPES.join(", ");
    return {
      valid: false,
      error: `Invalid rel_type "${relType}". Allowed values: ${allowed}`,
    };
  }

  const constraint = TYPE_CONSTRAINTS[relType];
  if (!constraint) {
    return { valid: true };
  }

  if (sourceType !== constraint.sourceType) {
    return {
      valid: false,
      error: `rel_type "${relType}" requires source_type="${constraint.sourceType}", got "${sourceType}"`,
    };
  }

  if (targetType !== constraint.targetType) {
    return {
      valid: false,
      error: `rel_type "${relType}" requires target_type="${constraint.targetType}", got "${targetType}"`,
    };
  }

  return { valid: true };
}
