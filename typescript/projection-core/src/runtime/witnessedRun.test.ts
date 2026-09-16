import {
  classifyWitnessedRun,
  emptyProjection,
  normalizeProjection,
  ReadOnlyWitnessedRunAdapter,
  type WitnessedRunProjection,
} from "./witnessedRun.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function completeProjection(): WitnessedRunProjection {
  return {
    workflow: { instanceId: "wrong", nodeId: "wrong" },
    envelope: { id: "env-1", evaluationFingerprint: "sha256:fp", contractId: "contract-1", contractVersion: 1, contractDigest: "sha256:contract" },
    manifest: { id: "manifest-1", version: 1, digest: "sha256:manifest" },
    law: { propositionIds: ["prop-1"], doctrineIds: ["doc-1"], evaluatorId: "eval-1" },
    assessment: { disposition: "allow", status: "admitted", reason: null },
    receipts: { pebAdmission: "peb-1", conduitTransition: "conduit-1" },
    evidence: { ids: ["evidence-1"], fingerprint: "sha256:evidence" },
    replay: { fixtureId: "F01", status: "replay_ok" },
    status: "unknown", // the server is authoritative and reported this
  };
}

export async function runWitnessedRunConformance(): Promise<void> {
  const query = { workflowInstanceId: "wf-1", nodeId: "node-1" };
  const preserved = normalizeProjection(completeProjection(), query);
  // AC4: browser must consume the server-derived status verbatim, never
  // re-derive the join from raw metadata. completeProjection reports
  // "unknown", so normalizeProjection must preserve exactly that.
  assert(preserved.status === "unknown", "server status preserved verbatim (AC4, no browser-owned reconstruction)");
  assert(preserved.workflow.instanceId === "wf-1" && preserved.workflow.nodeId === "node-1", "query identity must be authoritative");

  // Every server-emitted status passes through untouched.
  for (const status of ["missing_lineage", "refusal", "stale", "drift", "duplicate_retry", "complete"] as const) {
    const p = normalizeProjection({ ...completeProjection(), status }, query);
    assert(p.status === status, `server status ${status} preserved verbatim (AC4)`);
  }

  // Local classifier recomputes from shape (refusal disposition present).
  assert(classifyWitnessedRun({ ...completeProjection(), assessment: { disposition: "refuse", status: "refused", reason: null } }).status === "refusal", "refusal classified from shape");

  assert(emptyProjection(query).status === "missing_lineage", "empty projection should be missing lineage");

  const adapter = new ReadOnlyWitnessedRunAdapter({ query: async () => null });
  assert((await adapter.get(query)).status === "missing_lineage", "null source should produce read-only empty projection");
}