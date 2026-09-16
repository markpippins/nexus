export type DoctrineLookupStatus = "resolved" | "unknown" | "refusal" | "stale";

export interface DoctrineRecord {
  kind: "doctrine" | "proposition" | "posture";
  id: string;
  version: number;
  digest: `sha256:${string}`;
  effectiveFrom: string;
  supersededAt?: string | null;
  sourceDecisionId: string;
}

export interface DoctrineLookupRequest {
  kind: DoctrineRecord["kind"];
  stableId: string;
  asOf: string;
}

export interface DoctrineLookupResult {
  status: DoctrineLookupStatus;
  consulted: boolean;
  latencyMs: number;
  record?: DoctrineRecord;
  reason?: string;
}

export interface DoctrineLookup {
  lookup(request: DoctrineLookupRequest, signal?: AbortSignal): Promise<DoctrineLookupResult>;
}

export class InMemoryDoctrineLookup implements DoctrineLookup {
  constructor(private readonly records: readonly DoctrineRecord[]) {}

  async lookup(request: DoctrineLookupRequest): Promise<DoctrineLookupResult> {
    const started = performance.now();
    if (!request.stableId || !request.asOf) {
      return { status: "refusal", consulted: false, latencyMs: elapsed(started), reason: "stable_id_and_as_of_required" };
    }
    const candidates = this.records.filter((record) => record.kind === request.kind && record.id === request.stableId);
    if (candidates.length === 0) {
      return { status: "unknown", consulted: true, latencyMs: elapsed(started), reason: "stable_id_not_found" };
    }
    const active = candidates
      .filter((record) => record.effectiveFrom <= request.asOf && (record.supersededAt == null || record.supersededAt > request.asOf))
      .sort((left, right) => right.effectiveFrom.localeCompare(left.effectiveFrom) || right.version - left.version)[0];
    if (!active) {
      return { status: "stale", consulted: true, latencyMs: elapsed(started), reason: "stable_id_not_effective_at_as_of" };
    }
    return { status: "resolved", consulted: true, latencyMs: elapsed(started), record: structuredClone(active) };
  }
}

function elapsed(started: number): number {
  return Math.max(0, Math.round((performance.now() - started) * 1000) / 1000);
}

export function assertBlockingLookup(result: DoctrineLookupResult): DoctrineRecord {
  if (result.status !== "resolved" || !result.record) {
    throw new Error(`doctrine lookup ${result.status}: ${result.reason ?? "unresolved"}`);
  }
  return result.record;
}
