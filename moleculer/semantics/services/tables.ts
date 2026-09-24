/**
 * tables.ts — registry of the 12 semantics.* tables after convergence to the
 *   standalone sol.semantics shape (2026-09-01, cutover reconciliation
 *   deliverable 3 / record f3320458).
 *
 * Removed (retired — owned by resolution.* as the evaluation model):
 *   owning_subsystem, concept, concept_relationship, representation,
 *   representation_identity, representation_relationship, identity_strategy,
 *   consumer_operation, execution_claim.
 *
 * Retained pending the canonical_asset ownership decision (handoff 5e2884db):
 *   canonical_asset — the only asset spine in the nexus DB (47,632 rows,
 *   21 FKs from 8 external schemas). If the decision places assets in
 *   resolution.* instead, this entry and the canonical_asset routes must be
 *   removed in a follow-up.
 *
 * The stored-procedure write surface is generated from this registry:
 *   add_<table>(p_id?, p_<writable>...)           INSERT ... RETURNING *
 *   update_<table>(p_id, p_<writable>...)          append-only replace (expire + insert new version)
 *   soft_delete_<table>(p_id) -> integer           expire-not-delete (idempotent)
 *   resolve_drift_finding(p_id, p_resolved_at)     drift lifecycle (drift_finding only)
 *
 * `writable` = column names accepted as p_* params by the add_/update_ procs,
 * in proc parameter order (auto columns like created_at / effective_at /
 * detected_at are not writable — the DB fills them).
 */

export interface TableMeta {
  /** Table name — also the REST path segment and the MCP tool suffix. */
  table: string;
  /** Schema the table lives in. Default "semantics"; the 4 ontology tables
   * (concept, concept_relationship, representation, representation_relationship)
   * live in "resolution" after V134 moved them out of semantics.*. */
  schema?: string;
  /** Human-readable label. */
  label: string;
  /** Primary key type. */
  idType: "uuid" | "smallint";
  /** true when the add_ proc auto-generates the id (uuid); false → caller must supply it. */
  idAuto: boolean;
  /** Columns that must be coerced to numeric (smallint). */
  smallintCols: string[];
  /** Columns that are jsonb. */
  jsonbCols: string[];
  /** Writable columns (p_* params), in proc parameter order. */
  writable: string[];
  /** NOT NULL columns (enforced by the DB; informational here). */
  required: string[];
  /** Extra note surfaced in tool descriptions. */
  note?: string;
  /** Column used for single-row lookups (default "id"); relationship_type uses "name". */
  idCol?: string;
  /** Proc param name for the id (default "p_id"); relationship_type uses "p_name". */
  idParam?: string;
}

export const TABLES: TableMeta[] = [
  // ── V060: relationship type vocabulary ────────────────────────────
  {
    table: "relationship_type",
    label: "relationship type (vocabulary of legal edge types)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["name", "description", "scope", "notes", "expired_at"],
    required: ["name", "description"],
    idCol: "name",
    idParam: "p_name",
    note: "Vocabulary table — FK-referenced by concept_relationship / representation_relationship (only defined types are legal edges). update takes p_new_name (names are never reused).",
  },
  // ── V072: Evidence spine ──────────────────────────────────────────
  {
    table: "evidence_type",
    label: "evidence type (vocabulary of evidence kinds)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["name", "description", "origin_category", "notes", "expired_at"],
    required: ["name", "description"],
    idCol: "name",
    idParam: "p_name",
    note: "Vocabulary table — FK-referenced by evidence_item. update takes p_new_name.",
  },
  {
    table: "evidence_item",
    label: "evidence item (immutable, hash-deduplicated evidence record)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: ["metadata"],
    writable: ["evidence_type_id", "uri", "excerpt", "note", "origin", "captured_at", "source_hash", "metadata", "valid_from", "valid_to", "expired_at"],
    required: ["evidence_type_id"],
    note: "Immutable after creation (no update_ proc). Soft-close via soft_delete_. Dedup on (evidence_type_id, source_hash).",
  },
  {
    table: "statement_evidence",
    label: "statement evidence (evidence linked to a relationship claim)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["evidence_item_id", "statement_type", "statement_id", "role", "strength", "comment", "expired_at"],
    required: ["evidence_item_id", "statement_type", "statement_id", "role"],
    note: "Polymorphic junction — statement_type includes source_observation, concept_relationship, representation_relationship, execution_claim, resolution_proposition (sol vocabulary). Unique on (evidence_item_id, statement_type, statement_id, role).",
  },
  // ── V057: snapshots & drift ───────────────────────────────────────
  {
    table: "snapshot",
    label: "snapshot (per-baseline judgment record)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["label", "version", "parent_id", "status", "created_by", "notes", "expired_at"],
    required: ["label", "version", "created_by"],
  },
  {
    table: "snapshot_observation",
    label: "snapshot observation (per-baseline judgment on a representation)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: [
      "snapshot_id",
      "representation_id",
      "lifecycle_state",
      "is_completed_fix",
      "completed_fix_ref",
      "audit_reason",
      "safe_to_retire",
      "expired_at",
    ],
    required: ["snapshot_id", "representation_id", "lifecycle_state"],
    note: "representation_id column retained (matches sol.semantics) but its FK to semantics.representation is dropped under convergence — the id is historical.",
  },
  {
    table: "drift_finding",
    label: "drift finding (finding against a snapshot observation)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["observation_id", "description", "severity", "resolved_at", "expired_at"],
    required: ["observation_id", "description", "severity"],
    note: "Lifecycle: detected (resolved_at NULL) → resolved via semantics_resolve_drift_finding.",
  },
  // ── V065: T02 Phase 1 — canonical asset identity spine ──────────────
  // canonical_asset / asset_revision / source_observation. Append-only /
  // expire-not-delete, partial unique indexes on active rows. Phase 2
  // (asset_identity_claim + asset_relation) lands in a later turn.
  {
    table: "canonical_asset",
    label: "canonical asset (enduring identity record: asset:<platform>:<ns>:<key>)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: ["canonical_key"],
    writable: [
      "canonical_asset_id",
      "asset_kind",
      "canonical_key",
      "source_hash",
      "content_hash",
      "validity_start",
      "validity_end",
      "expired_at",
    ],
    required: ["canonical_asset_id", "asset_kind"],
    note: "T02 Phase 1. canonical_asset_id is the durable business key (compound asset:<platform>:<ns>:<key>); partial unique index on active rows. revisions/claims/relations hang off it. Retained pending the asset-ownership decision (handoff 5e2884db); do not remove without that decision.",
  },
  {
    table: "asset_revision",
    label: "asset revision (immutable append-only revision of a canonical asset)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: [
      "revision_id",
      "asset_id",
      "content_hash",
      "source_hash",
      "parent_revision_id",
      "recording_start",
      "recording_end",
      "created_by",
      "expired_at",
    ],
    required: ["revision_id", "asset_id"],
    note: "T02 Phase 1. Same content_hash → same revision (idempotent, invariant #2); different content_hash → NEW revision of the same asset, NOT a new asset.",
  },
  {
    table: "source_observation",
    label: "source observation (provenance: what was observed and from where)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: [
      "revision_id",
      "platform",
      "platform_identifier",
      "namespace",
      "raw_location",
      "observed_at",
      "ingestion_run_id",
      "raw_hash",
      "expired_at",
    ],
    required: ["revision_id", "platform"],
    note: "T02 Phase 1. No created_at — observations are provenance rows (matches snapshot_observation convention).",
  },
  // ── V066: T02 Phase 2 — asset relationship layer ────────────────────
  // asset_identity_claim + asset_relation. The claim NEVER performs a
  // merge — resolution requires an owning-role decision (Architect closes
  // spec/plan, Planner closes candidate/question per contract Q4).
  {
    table: "asset_identity_claim",
    label: "asset identity claim (proposed identity linkage with confidence)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: [
      "asset_id",
      "candidate_asset_id",
      "claim_type",
      "confidence",
      "basis",
      "status",
      "decided_by",
      "decided_at",
      "expired_at",
    ],
    required: ["asset_id", "claim_type", "status"],
    note: "T02 Phase 2. claim_type ∈ identity|supersession|derivation|consolidation|split; status lifecycle open→resolved/rejected; basis ∈ strong|medium|weak. INVARIANT: claim never performs the merge — only owning-role decision resolves it.",
  },
  {
    table: "asset_relation",
    label: "asset relation (directed edge: supersedes / derives_from / contradicts / consolidates_into / split_from)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: [
      "from_asset_id",
      "to_asset_id",
      "relation_type",
      "decided_by",
      "decided_at",
      "effective_at",
      "expired_at",
    ],
    required: ["from_asset_id", "to_asset_id", "relation_type"],
    note: "T02 Phase 2. Self-loops forbidden (from_asset_id <> to_asset_id). Append-only with expired_at soft-delete; active edge uniqueness on (from, to, type).",
  },
  // ── Resolution ontology (graph explorer) — live in resolution.* after V134 ──
  // These 4 tables were retired from semantics.* and consolidated into
  // resolution.* (V134, nexus record 716362c7). The semantics-ui Graph
  // Explorer renders them; the generic CRUD routes serve them from the
  // resolution schema via the `schema` field.
  {
    table: "concept",
    schema: "resolution",
    label: "concept (ontology node — resolution.*)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["name", "description", "expired_at"],
    required: ["name"],
    note: "Resolution ontology concept. resolution.concept has a plain UNIQUE(name). Add via direct INSERT (no add_proc).",
  },
  {
    table: "concept_relationship",
    schema: "resolution",
    label: "concept relationship (typed edge between concepts — resolution.*)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["from_concept_id", "to_concept_id", "relationship_type", "path", "notes", "expired_at"],
    required: ["from_concept_id", "to_concept_id", "relationship_type"],
    note: "Resolution ontology edge. relationship_type references resolution.relationship_type/defined vocabulary.",
  },
  {
    table: "representation",
    schema: "resolution",
    label: "representation (entity table binding for a concept — resolution.*)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: ["raw_metadata"],
    writable: ["concept_id", "label", "schema_name", "table_name", "owning_subsystem_id", "owner", "raw_metadata", "expired_at"],
    required: ["concept_id", "label"],
    note: "Resolution ontology representation. Maps a concept to its physical schema.table.",
  },
  {
    table: "representation_relationship",
    schema: "resolution",
    label: "representation relationship (edge between representations — resolution.*)",
    idType: "uuid",
    idAuto: true,
    smallintCols: [],
    jsonbCols: [],
    writable: ["from_representation_id", "to_representation_id", "relationship_type", "notes", "expired_at"],
    required: ["from_representation_id", "to_representation_id", "relationship_type"],
    note: "Resolution ontology edge between representations.",
  },
];

export function getTable(name: string): TableMeta | undefined {
  return TABLES.find((t) => t.table === name);
}
