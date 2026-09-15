/**
 * SOLScript TypeScript core — storage port (cutover 05 contract).
 *
 * Ported from python/SOLScript/solscript/adapters/contract.py. The
 * interpreter consumes ONLY these shapes; adapters convert their source
 * rows into them, and source schema/table names never leak past an
 * adapter. DatabaseLoader (asyncpg) is replaced by this port in TS.
 */

import type { JsonValue } from "./models.js";

export interface ContractConcept {
  id: string;
  name: string;
  description?: string;
}

export interface ContractAttribute {
  id: string;
  conceptId: string;
  name: string;
  /** 'text'|'integer'|'numeric'|'boolean'|'timestamptz'|'jsonb'|'uuid' */
  valueType: string;
  allowedValues: string[];
  isStateAttribute: boolean;
}

export interface ContractRelationship {
  id: string;
  fromConceptId: string;
  toConceptId: string;
  relationshipType: string;
  bindingFromColumn?: string;
  bindingToColumn?: string;
}

/** An instance of a concept with its attribute values + identity. */
export interface ContractSubject {
  id: string;
  conceptId: string;
  externalId?: string;
  canonicalAssetId?: string;
  attributes: Record<string, JsonValue>;
  validFrom?: string;
  validUntil?: string;
}

/** A dense per-object attribute set (EAV-sourced). */
export interface ContractShrapnelFact {
  objectId: string;
  attributes: Record<string, JsonValue>;
  validFrom?: string;
  validUntil?: string;
}

export interface ContractRevision {
  subjectId: string;
  parentRevisionId?: string;
  validFrom?: string;
  validUntil?: string;
  recordedUntilDt?: string;
}

export interface ContractEvidence {
  id: string;
  subjectId?: string;
  source: string;
  content?: string;
  capturedAt?: string;
}

/**
 * Storage port SOLScript requires. No Nexus/resolution-table dependency.
 * The underlying store (sol.semantics, a nexus datasource, an in-memory
 * fixture, ...) is invisible to the interpreter.
 */
export interface SolStoragePort {
  listConcepts(): Promise<ContractConcept[]>;
  listAttributes(): Promise<ContractAttribute[]>;
  listRelationships(): Promise<ContractRelationship[]>;
  listSubjects(conceptId: string): Promise<ContractSubject[]>;
  listShrapnelFacts(): Promise<ContractShrapnelFact[]>;
  listRevisions(subjectId: string): Promise<ContractRevision[]>;
  listEvidence(): Promise<ContractEvidence[]>;
}

/** In-memory adapter (fixtures, tests, embedded use). */
export class InMemorySolStorage implements SolStoragePort {
  concepts: ContractConcept[] = [];
  attributes: ContractAttribute[] = [];
  relationships: ContractRelationship[] = [];
  subjects: ContractSubject[] = [];
  shrapnelFacts: ContractShrapnelFact[] = [];
  revisions: ContractRevision[] = [];
  evidence: ContractEvidence[] = [];

  listConcepts(): Promise<ContractConcept[]> {
    return Promise.resolve([...this.concepts]);
  }
  listAttributes(): Promise<ContractAttribute[]> {
    return Promise.resolve([...this.attributes]);
  }
  listRelationships(): Promise<ContractRelationship[]> {
    return Promise.resolve([...this.relationships]);
  }
  listSubjects(conceptId: string): Promise<ContractSubject[]> {
    return Promise.resolve(this.subjects.filter((s) => s.conceptId === conceptId));
  }
  listShrapnelFacts(): Promise<ContractShrapnelFact[]> {
    return Promise.resolve([...this.shrapnelFacts]);
  }
  listRevisions(subjectId: string): Promise<ContractRevision[]> {
    return Promise.resolve(this.revisions.filter((r) => r.subjectId === subjectId));
  }
  listEvidence(): Promise<ContractEvidence[]> {
    return Promise.resolve([...this.evidence]);
  }
}
