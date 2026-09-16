function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}
import { assertBlockingLookup, InMemoryDoctrineLookup, type DoctrineRecord } from "./doctrineLookup.js";

const records: DoctrineRecord[] = [
  { kind: "doctrine", id: "doc-1", version: 1, digest: "sha256:" + "a".repeat(64) as `sha256:${string}`, effectiveFrom: "2026-01-01T00:00:00Z", sourceDecisionId: "decision-1" },
  { kind: "doctrine", id: "doc-old", version: 1, digest: "sha256:" + "b".repeat(64) as `sha256:${string}`, effectiveFrom: "2025-01-01T00:00:00Z", supersededAt: "2026-02-01T00:00:00Z", sourceDecisionId: "decision-old" },
];

export async function runDoctrineLookupConformance(): Promise<void> {
  const lookup = new InMemoryDoctrineLookup(records);
  equal((await lookup.lookup({ kind: "doctrine", stableId: "doc-1", asOf: "2026-08-28T00:00:00Z" })).status, "resolved", "resolved lookup");
  equal((await lookup.lookup({ kind: "doctrine", stableId: "missing", asOf: "2026-08-28T00:00:00Z" })).status, "unknown", "unknown lookup");
  equal((await lookup.lookup({ kind: "doctrine", stableId: "doc-old", asOf: "2026-08-28T00:00:00Z" })).status, "stale", "stale lookup");
  equal((await lookup.lookup({ kind: "doctrine", stableId: "", asOf: "2026-08-28T00:00:00Z" })).status, "refusal", "refused lookup");
  const resolved = await lookup.lookup({ kind: "doctrine", stableId: "doc-1", asOf: "2026-08-28T00:00:00Z" });
  equal(assertBlockingLookup(resolved).sourceDecisionId, "decision-1", "source decision");
}
