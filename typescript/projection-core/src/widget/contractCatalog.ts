import type { CapabilityId } from "../types/designIR";
import type { WidgetDefinition } from "./catalog";

export interface ConfirmedContractIdentity {
  contractId: string;
  operationId: string;
  artifactId: string;
  artifactVersion: number;
  artifactDigest: `sha256:${string}`;
  source: "user-confirmed" | "typespec" | "openapi";
  revision: number;
}

export interface ContractCatalogEntry extends WidgetDefinition {
  identity: ConfirmedContractIdentity;
  compatibleCapabilities: readonly CapabilityId[];
}

export interface ContractSelectionRequest {
  capability: CapabilityId;
  contractId: string;
  operationId: string;
  artifactDigest: `sha256:${string}`;
  variant?: string;
  density?: string;
}

export class ContractCatalog {
  constructor(private readonly entries: readonly ContractCatalogEntry[]) {}

  select(request: ContractSelectionRequest): ContractCatalogEntry | undefined {
    const candidates = this.entries
      .filter((entry) => entry.identity.contractId === request.contractId)
      .filter((entry) => entry.identity.operationId === request.operationId)
      .filter((entry) => entry.identity.artifactDigest === request.artifactDigest)
      .filter((entry) => entry.compatibleCapabilities.includes(request.capability))
      .filter((entry) => !request.variant || entry.variants?.includes(request.variant))
      .sort((left, right) => score(right, request) - score(left, request) || left.identity.revision - right.identity.revision || left.id.localeCompare(right.id));
    return candidates[0];
  }
}

function score(entry: ContractCatalogEntry, request: ContractSelectionRequest): number {
  return (request.variant && entry.variants?.includes(request.variant) ? 4 : 0)
    + (request.density && entry.defaultDensity === request.density ? 2 : 0)
    + (entry.identity.source === "user-confirmed" ? 1 : 0);
}

export function isConfirmedIdentity(value: unknown): value is ConfirmedContractIdentity {
  if (!value || typeof value !== "object") return false;
  const identity = value as Partial<ConfirmedContractIdentity>;
  return typeof identity.contractId === "string"
    && typeof identity.operationId === "string"
    && typeof identity.artifactId === "string"
    && Number.isInteger(identity.artifactVersion)
    && typeof identity.artifactDigest === "string"
    && /^sha256:[0-9a-f]{64}$/.test(identity.artifactDigest)
    && ["user-confirmed", "typespec", "openapi"].includes(identity.source ?? "")
    && Number.isInteger(identity.revision);
}
