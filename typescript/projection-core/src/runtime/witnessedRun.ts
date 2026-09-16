export type WitnessedRunStatus =
  | "complete"
  | "missing_lineage"
  | "unknown"
  | "stale"
  | "refusal"
  | "drift"
  | "duplicate_retry";

import { resolveDispositionViaSolscript } from "./solscriptAdapter.js";
import type { Proposition } from "@nexus/solscript";

/**
 * Apply a solscript-derived disposition to a witnessed-run projection's
 * assessment (F-0 / option A). The disposition label comes from the canonical
 * @nexus/solscript Disposition enum via resolveDispositionViaSolscript — the
 * reference implementation — never re-derived by Projection. When the proposition
 * carries no decisive disposition, the existing assessment disposition is kept.
 */
export function applySolscriptDisposition(
  projection: WitnessedRunProjection,
  proposition: Proposition | undefined,
): WitnessedRunProjection {
  const disposition = resolveDispositionViaSolscript(proposition);
  if (!disposition) return projection;
  return {
    ...projection,
    assessment: { ...projection.assessment, disposition },
  };
}

export interface WitnessedRunQuery {
  workflowInstanceId: string;
  nodeId: string;
}

export interface WitnessedRunProjection {
  workflow: {
    instanceId: string;
    nodeId: string;
  };
  envelope: {
    id: string | null;
    evaluationFingerprint: string | null;
    contractId: string | null;
    contractVersion: number | null;
    contractDigest: string | null;
  };
  manifest: {
    id: string | null;
    version: number | null;
    digest: string | null;
  };
  law: {
    propositionIds: string[];
    doctrineIds: string[];
    evaluatorId: string | null;
  };
  assessment: {
    disposition: string | null;
    status: string | null;
    reason: string | null;
  };
  receipts: {
    pebAdmission: string | null;
    conduitTransition: string | null;
  };
  evidence: {
    ids: string[];
    fingerprint: string | null;
  };
  replay: {
    fixtureId: string | null;
    status: string | null;
  };
  status: WitnessedRunStatus;
}

export interface WitnessedRunSource {
  query(query: WitnessedRunQuery, signal?: AbortSignal): Promise<WitnessedRunProjection | null>;
}

export class WitnessedRunAdapterError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = "WitnessedRunAdapterError";
  }
}

export class ReadOnlyWitnessedRunAdapter {
  constructor(private readonly source: WitnessedRunSource) {}

  async get(query: WitnessedRunQuery, signal?: AbortSignal): Promise<WitnessedRunProjection> {
    if (!query.workflowInstanceId || !query.nodeId) {
      throw new WitnessedRunAdapterError("Workflow instance and node IDs are required", "INVALID_WITNESSED_RUN_QUERY");
    }
    const projection = await this.source.query(query, signal);
    if (!projection) return emptyProjection(query, "missing_lineage");
    return normalizeProjection(projection, query);
  }
}

export function emptyProjection(
  query: WitnessedRunQuery,
  status: WitnessedRunStatus = "missing_lineage",
): WitnessedRunProjection {
  return {
    workflow: { instanceId: query.workflowInstanceId, nodeId: query.nodeId },
    envelope: { id: null, evaluationFingerprint: null, contractId: null, contractVersion: null, contractDigest: null },
    manifest: { id: null, version: null, digest: null },
    law: { propositionIds: [], doctrineIds: [], evaluatorId: null },
    assessment: { disposition: null, status: null, reason: null },
    receipts: { pebAdmission: null, conduitTransition: null },
    evidence: { ids: [], fingerprint: null },
    replay: { fixtureId: null, status: null },
    status,
  };
}

/**
 * States the server-side classifier (execution-srv) may emit. The authoritative
 * join state is derived ON THE SERVER — the projection consumer must never re-derive it
 * from raw metadata in the browser (AC4: no browser-owned reconstruction).
 */
const SERVER_STATUSES: ReadonlySet<WitnessedRunStatus> = new Set<WitnessedRunStatus>([
  'complete',
  'missing_lineage',
  'unknown',
  'stale',
  'refusal',
  'drift',
  'duplicate_retry',
]);

export function normalizeProjection(
  projection: WitnessedRunProjection,
  query: WitnessedRunQuery,
): WitnessedRunProjection {
  const normalized: WitnessedRunProjection = {
    ...projection,
    workflow: { instanceId: query.workflowInstanceId, nodeId: query.nodeId },
    law: {
      ...projection.law,
      propositionIds: [...projection.law.propositionIds],
      doctrineIds: [...projection.law.doctrineIds],
    },
    evidence: { ...projection.evidence, ids: [...projection.evidence.ids] },
  };
  // Consume the server's authoritative status verbatim. Only fall back to
  // deriving locally for a projection the server did not classify (defensive;
  // matches AC4's "no browser-owned reconstruction" by defaulting to the
  // authoritative value and never inventing one).
  if (SERVER_STATUSES.has(normalized.status)) {
    return normalized;
  }
  return { ...normalized, status: classifyWitnessedRun(normalized).status };
}

export function classifyWitnessedRun(projection: WitnessedRunProjection): WitnessedRunProjection {
  if (!projection.envelope.id || !projection.envelope.evaluationFingerprint) return { ...projection, status: "missing_lineage" };
  if (projection.replay.status === "stale" || projection.assessment.status === "stale") return { ...projection, status: "stale" };
  if (projection.replay.status === "drift" || projection.assessment.status === "drift") return { ...projection, status: "drift" };
  if (projection.replay.status === "duplicate_retry") return { ...projection, status: "duplicate_retry" };
  if (projection.assessment.status === "refused" || projection.assessment.disposition === "refuse") return { ...projection, status: "refusal" };
  if (!projection.manifest.id || !projection.receipts.pebAdmission || !projection.receipts.conduitTransition || projection.evidence.ids.length === 0) {
    return { ...projection, status: "missing_lineage" };
  }
  return { ...projection, status: "complete" };
}
