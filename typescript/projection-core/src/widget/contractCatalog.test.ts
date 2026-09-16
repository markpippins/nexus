function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}
import { ContractCatalog, isConfirmedIdentity, type ContractCatalogEntry } from "./contractCatalog.js";

const digest = "sha256:" + "a".repeat(64) as `sha256:${string}`;
const base: ContractCatalogEntry = {
  id: "DataTable",
  name: "Data Table",
  implements: ["EntityCollection"],
  identity: { contractId: "contract-1", operationId: "list", artifactId: "api-1", artifactVersion: 1, artifactDigest: digest, source: "typespec", revision: 1 },
  compatibleCapabilities: ["EntityCollection"],
  variants: ["compact"],
  defaultDensity: "compact",
};

export function runContractCatalogConformance(): void {
  equal(isConfirmedIdentity(base.identity), true, "identity validation");
  const catalog = new ContractCatalog([
    { ...base, id: "B", identity: { ...base.identity, revision: 2 }, defaultDensity: "normal" },
    { ...base, id: "A", identity: { ...base.identity, revision: 1 }, defaultDensity: "compact" },
  ]);
  equal(catalog.select({ capability: "EntityCollection", contractId: "contract-1", operationId: "list", artifactDigest: digest, density: "compact" })?.id, "A", "deterministic selection");
  equal(catalog.select({ capability: "EntityCollection", contractId: "contract-1", operationId: "list", artifactDigest: "sha256:" + "b".repeat(64) as `sha256:${string}` }), undefined, "digest mismatch");
  equal(catalog.select({ capability: "MetricSeries", contractId: "contract-1", operationId: "list", artifactDigest: digest }), undefined, "capability mismatch");
}
