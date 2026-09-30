/**
 * tables.ts — read-surface registry of the resolution.* tables exposed by
 * resolution-srv.
 *
 * resolution.* is the canonical store for identity, lineage, disposition,
 * evaluation joins, receipts, and admission (Lilac / Consolidation Wave 1
 * direction). Unlike semantics.*, the resolution schema deliberately has NO
 * generic add_/update_/soft_delete_ stored-proc surface: writes are
 * producer-authorized paths (SOLScript evaluation, receipts, admission
 * receipts) with their own idempotency contracts. This service therefore
 * ships a READ-ONLY surface in v1:
 *
 *   GET /api/meta                      — registry overview + row counts
 *   GET /api/<table>                   — active rows (paginated: ?limit&offset)
 *   GET /api/<table>/:id               — single row by PK
 *   GET /api/health                    — DB connectivity + schema check
 *
 * Write routes (execution_claim, receipt, execution_admission_receipt, ...)
 * are gated on a per-table authorization decision and intentionally absent
 * here — see the To Do thread 6b3ca700 scope discussion.
 */

export interface TableMeta {
  /** Table name — also the REST path segment. */
  table: string;
  /** Human-readable label. */
  label: string;
  /** Governance concern grouping (informational, surfaced in /api/meta). */
  group:
    | "registry"
    | "entity_proposition"
    | "reasoning"
    | "outcome"
    | "lineage";
  /** Primary key column (uuid everywhere in resolution.* v1 surface). */
  idCol: string;
  /** Column that marks a row inactive/retired when present (maturity varies by table). */
  expiryCol?: string;
  /** Timestamp column for default ordering. */
  orderCol?: string;
}

export const TABLES: TableMeta[] = [
  // ── Registry-adjacent: semantic types, concepts, attributes, transitions ──
  {
    table: "semantic_type",
    label: "semantic type (class-level framing requirements vocabulary)",
    group: "registry",
    idCol: "id",
  },
  {
    table: "semantic_type_required_dimension",
    label: "required frame dimension per semantic type (class-level is_well_framed)",
    group: "registry",
    idCol: "id",
  },
  {
    table: "concept",
    label: "concept (governed vocabulary)",
    group: "registry",
    idCol: "id",
    expiryCol: "expired_at",
    orderCol: "created_at",
  },
  {
    table: "concept_attribute",
    label: "concept attribute definition",
    group: "registry",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "concept_attribute_value",
    label: "concept attribute value (disposition vocabulary lives here)",
    group: "registry",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "concept_attribute_binding",
    label: "concept attribute binding",
    group: "registry",
    idCol: "id",
  },
  {
    table: "concept_relationship",
    label: "concept relationship (governed edge)",
    group: "registry",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "concept_relationship_binding",
    label: "concept relationship binding",
    group: "registry",
    idCol: "id",
  },
  {
    table: "concept_state_transition",
    label: "concept state transition definition",
    group: "registry",
    idCol: "id",
  },
  {
    table: "frame_dimension",
    label: "frame dimension definition",
    group: "registry",
    idCol: "id",
  },
  {
    table: "frame_dimension_meaning",
    label: "frame dimension meaning",
    group: "registry",
    idCol: "id",
  },
  {
    table: "frame_dimension_value",
    label: "frame dimension value vocabulary",
    group: "registry",
    idCol: "id",
  },

  // ── Entity / proposition: the evaluated model ─────────────────────────
  {
    table: "entity",
    label: "entity (subject of propositions)",
    group: "entity_proposition",
    idCol: "id",
    expiryCol: "expired_at",
    orderCol: "created_at",
  },
  {
    table: "proposition",
    label: "proposition (evaluatable claim)",
    group: "entity_proposition",
    idCol: "id",
    orderCol: "created_at",
  },
  {
    table: "proposition_assertion",
    label: "proposition assertion (rule binding)",
    group: "entity_proposition",
    idCol: "id",
  },
  {
    table: "proposition_frame_value",
    label: "proposition frame value (committed framing)",
    group: "entity_proposition",
    idCol: "id",
  },
  {
    table: "proposition_comparison",
    label: "proposition comparison",
    group: "entity_proposition",
    idCol: "id",
  },
  {
    table: "assertion_evaluation",
    label: "assertion evaluation result",
    group: "entity_proposition",
    idCol: "id",
  },
  {
    table: "observation",
    label: "observation (recorded evidence)",
    group: "entity_proposition",
    idCol: "id",
  },

  // ── Reasoning: rules, expressions, representations ────────────────────
  {
    table: "rule",
    label: "rule (invariant / transition / derivation)",
    group: "reasoning",
    idCol: "id",
    expiryCol: "expired_at",
    orderCol: "created_at",
  },
  {
    table: "expression",
    label: "expression (compiled predicate)",
    group: "reasoning",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "expression_operand",
    label: "expression operand",
    group: "reasoning",
    idCol: "id",
  },
  {
    table: "function_binding",
    label: "function binding",
    group: "reasoning",
    idCol: "id",
  },
  {
    table: "representation",
    label: "representation (V134-migrated ontology table)",
    group: "reasoning",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "representation_relationship",
    label: "representation relationship (V134-migrated)",
    group: "reasoning",
    idCol: "id",
    expiryCol: "expired_at",
  },
  {
    table: "representation_identity",
    label: "representation identity",
    group: "reasoning",
    idCol: "id",
  },
  {
    table: "representation_comparison",
    label: "representation comparison",
    group: "reasoning",
    idCol: "id",
  },

  // ── Outcomes: claims, evidence, receipts, admission ───────────────────
  {
    table: "execution_claim",
    label: "execution claim (claimed evaluation outcome)",
    group: "outcome",
    idCol: "id",
    orderCol: "created_at",
  },
  {
    table: "execution_claim_evidence",
    label: "execution claim evidence",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "execution_evidence",
    label: "execution evidence",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "execution_admission_receipt",
    label: "execution admission receipt",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "receipt",
    label: "receipt (canonical R4 idem-contract receipt)",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "verified_statement",
    label: "verified statement",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "governance_threshold",
    label: "governance threshold",
    group: "outcome",
    idCol: "id",
  },
  {
    table: "enforcement_posture",
    label: "enforcement posture",
    group: "outcome",
    idCol: "id",
  },

  // ── Lineage: producer registry, tickets, work requests ────────────────
  {
    table: "producer_registry",
    label: "producer registry (authorized write paths)",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "producer_refusals",
    label: "producer refusals",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "identity_strategy",
    label: "identity strategy",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "consumer_operation",
    label: "consumer operation",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "ticket",
    label: "ticket (Lilac-consolidated ticket)",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "ticket_transition",
    label: "ticket transition",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "work_request",
    label: "work request (canonical WR)",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "work_request_edge",
    label: "work request edge",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "contract_version",
    label: "contract version",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "implementation_plan",
    label: "implementation plan",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "requirement",
    label: "requirement",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "specification",
    label: "specification",
    group: "lineage",
    idCol: "id",
  },
  {
    table: "specification_lineage",
    label: "specification lineage",
    group: "lineage",
    idCol: "id",
  },
];

/** Tables that must exist for the service to be considered healthy. */
export const HEALTH_CHECK_TABLES = ["proposition", "receipt", "execution_claim"] as const;
