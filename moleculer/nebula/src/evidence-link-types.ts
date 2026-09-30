/**
 * Evidence Link Type Taxonomy v0.1
 *
 * Formal enumerated taxonomy of all valid link_type values for
 * the knowledge.evidence_links table. Mirrors the PG DOMAIN constraint
 * defined in migration 014.
 *
 * These types are specifically for linking harvested evidence to
 * knowledge graph entities — distinct from the nebula.cross_references
 * taxonomy which is for arbitrary entity-to-entity relationships.
 *
 * Spec: audit/SPECS/EVIDENCE_LINK_TAXONOMY.md
 * Plan reference: #0183
 */

/* ── Enum ─────────────────────────────────────────────────────────── */

export enum EvidenceLinkType {
  SUPPORTS      = "supports",
  REFINES       = "refines",
  INSTANTIATES  = "instantiates",
  CONTRADICTS   = "contradicts",
  SUPERSEDES    = "supersedes",
  MENTIONS      = "mentions",
  INFORMS       = "informs",
  VALIDATES     = "validates",
}

/* ── All valid string values ─────────────────────────────────────── */

export const ALL_EVIDENCE_LINK_TYPES: readonly string[] = Object.values(EvidenceLinkType);

/* ─── Human-readable descriptions ───────────────────────────────── */

export const EVIDENCE_LINK_TYPE_DESCRIPTIONS: Record<string, string> = {
  [EvidenceLinkType.SUPPORTS]:
    "Entity is supported by the evidence. The evidence corroborates or confirms the entity's claims.",
  [EvidenceLinkType.REFINES]:
    "Evidence refines/elaborates entity details. Provides richer or more precise information.",
  [EvidenceLinkType.INSTANTIATES]:
    "Evidence is a concrete instance of the entity concept. The entity is abstract; the evidence shows a real-world example.",
  [EvidenceLinkType.CONTRADICTS]:
    "Evidence contradicts the entity. Disagrees with or undermines the entity's claims.",
  [EvidenceLinkType.SUPERSEDES]:
    "This evidence supersedes older evidence for this entity. A newer or more authoritative source.",
  [EvidenceLinkType.MENTIONS]:
    "Evidence mentions the entity (weakest link). The entity is referenced but not elaborated.",
  [EvidenceLinkType.INFORMS]:
    "Evidence informs entity definition without directly supporting or contradicting it. Contextual or background information.",
  [EvidenceLinkType.VALIDATES]:
    "Evidence validates entity correctness. Typically used for verified/factual entities.",
};

/* ── Type guard ──────────────────────────────────────────────────── */

export function isValidEvidenceLinkType(value: string): value is EvidenceLinkType {
  return ALL_EVIDENCE_LINK_TYPES.includes(value);
}

/* ── Provenance constants ───────────────────────────────────────── */

export const EVIDENCE_PROVENANCE_VALUES = [
  "auto_ingestor",    // Created by the automated harvest→knowledge pipeline
  "manual",           // User/agent-created
  "reconciler",       // Created by the reconciliation/steward service
  "llm_extracted",    // Extracted by LLM during knowledge graph build
  "migration",        // Backfilled during system migration
] as const;

export type EvidenceProvenance = typeof EVIDENCE_PROVENANCE_VALUES[number];

export function isValidProvenance(value: string): value is EvidenceProvenance {
  return (EVIDENCE_PROVENANCE_VALUES as readonly string[]).includes(value);
}

/**
 * Default provenance for auto-ingested links.
 */
export const DEFAULT_PROVENANCE: EvidenceProvenance = "auto_ingestor";
