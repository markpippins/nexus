/**
 * Shrapnel polymorphism + storage-adapter conformance — TypeScript mirror.
 *
 * STAGED-INERT (blueprint plan 8261656): this module is the TypeScript
 * counterpart of `python/SOLScript/solscript/adapters/shrapnel_conformance.py`
 * and of the Expression bridge. It is not wired into any production loader,
 * collection, or schema migration. It exists so the semantic contract has a
 * second-language statement that CI can drift-check against the Python source.
 *
 * Invariants mirrored from the Python reference:
 *  - type identity is storage-independent (`shrapnel:stereotype:<name>`);
 *  - one normalizer, no backend-specific semantic branch;
 *  - typed `governed | unknown | unavailable | absent` values (never null
 *    conflation);
 *  - PostgreSQL attestation/lineage authoritative, Mongo dispatch
 *    reconstructable;
 *  - no authority store => fail closed; neither store => marked minimal
 *    fallback with declared durability/authority loss.
 */

import { createHash } from "node:crypto";

export const CONFORMANCE_REVISION = "shrapnel-storage-conformance-v1";
export const STAGED_INERT = true;
export const PRODUCTION_WIRING = false;

export const WORK_REQUEST_LIFECYCLE_FIELDS = [
  "request_id",
  "status",
  "created_at",
  "updated_at",
  "dispatch_state",
  "worker_id",
  "dispatch_lease_until",
  "attestation_id",
  "attested_at",
  "attestation_verdict",
] as const;

export const DISPATCH_FIELDS = [
  "dispatch_state",
  "worker_id",
  "dispatch_lease_until",
] as const;

export type FieldStatus = "governed" | "unknown" | "unavailable" | "absent";

export interface TypedValue {
  propertyName: string;
  status: FieldStatus;
  valueType: string;
  value?: unknown;
  reason?: string;
}

export interface ContractField {
  propertyName: string;
  required: boolean;
  valueType: string;
  originRevision: number;
  originStereotype: string;
  originVersion: number;
}

export interface EffectiveContract {
  stereotypeName: string;
  revisionId: number;
  version: number;
  depth: number;
  parentRevisionId: number | null;
  contractFingerprint: string;
  fields: ContractField[];
}

export interface TypeIdentity {
  typeId: string;
  stereotypeName: string;
  revisionId: number;
  version: number;
  contractFingerprint: string;
}

export interface LineageRef {
  relation: string;
  ref: string;
  authority: string;
  reconstructable: boolean;
}

export type AuthorityMode =
  | "authority_separated"
  | "degraded_reconstructable"
  | "minimal_fallback"
  | "fail_closed";

export interface AuthorityState {
  mode: AuthorityMode;
  attestationAuthority: string;
  lineageAuthority: string;
  dispatchAuthority: string;
  dispatchReconstructable: boolean;
  durability: string;
  authorityLoss: string[];
  markedFallback: boolean;
  reasonCode: string | null;
}

export interface AuthorityPolicy {
  postgresAvailable: boolean;
  mongoAvailable: boolean;
}

export interface FieldSpec {
  propertyName: string;
  valueType: string;
}

export interface RevisionDef {
  stereotypeName: string;
  revisionId: number;
  version: number;
  parentRevisionId: number | null;
  depth: number;
  required: FieldSpec[];
  optional: FieldSpec[];
}

export interface RawInstance {
  objectId: string;
  stereotypeName: string;
  revisionId: number;
  values: Record<string, unknown>;
  backend: string;
  materialization: string;
  lineage: LineageRef[];
  timestamps: Record<string, string | null>;
  authority: AuthorityState;
}

export interface SemanticDescriptor {
  identity: TypeIdentity;
  contract: EffectiveContract;
  fields: Record<string, TypedValue>;
  lineage: LineageRef[];
  timestamps: Record<string, string | null>;
  authority: AuthorityState;
  objectId: string;
  materialization: string;
}

function canonical(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortValue);
  }
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      out[key] = sortValue(source[key]);
    }
    return out;
  }
  return value;
}

export function sha256(value: unknown): string {
  return "sha256:" + createHash("sha256").update(canonical(value)).digest("hex");
}

export function assessAuthority(policy: AuthorityPolicy): AuthorityState {
  if (!policy.postgresAvailable && !policy.mongoAvailable) {
    return {
      mode: "minimal_fallback",
      attestationAuthority: "none",
      lineageAuthority: "none",
      dispatchAuthority: "none",
      dispatchReconstructable: true,
      durability: "process_only",
      authorityLoss: ["attestation", "lineage", "dispatch"],
      markedFallback: true,
      reasonCode: "minimal_fallback_no_authority_store",
    };
  }
  if (!policy.postgresAvailable) {
    return {
      mode: "fail_closed",
      attestationAuthority: "none",
      lineageAuthority: "none",
      dispatchAuthority: "mongo_reconstructable",
      dispatchReconstructable: true,
      durability: "unavailable",
      authorityLoss: ["attestation", "lineage"],
      markedFallback: false,
      reasonCode: "authority_backend_unavailable",
    };
  }
  if (!policy.mongoAvailable) {
    return {
      mode: "degraded_reconstructable",
      attestationAuthority: "postgres",
      lineageAuthority: "postgres",
      dispatchAuthority: "unavailable",
      dispatchReconstructable: true,
      durability: "postgres_only",
      authorityLoss: ["dispatch_freshness"],
      markedFallback: false,
      reasonCode: "dispatch_backend_unavailable",
    };
  }
  return {
    mode: "authority_separated",
    attestationAuthority: "postgres",
    lineageAuthority: "postgres",
    dispatchAuthority: "mongo_reconstructable",
    dispatchReconstructable: true,
    durability: "postgres+mongo",
    authorityLoss: [],
    markedFallback: false,
    reasonCode: null,
  };
}

function revisionIndex(revisions: RevisionDef[]): Map<number, RevisionDef> {
  const index = new Map<number, RevisionDef>();
  for (const revision of revisions) {
    if (index.has(revision.revisionId)) {
      throw new Error(`duplicate revision id ${revision.revisionId}`);
    }
    index.set(revision.revisionId, revision);
  }
  return index;
}

export function resolveEffectiveContract(
  revisions: RevisionDef[],
  stereotypeName: string,
  revisionId: number,
  cache = new Map<string, EffectiveContract>(),
): EffectiveContract {
  const key = `${stereotypeName}@${revisionId}`;
  const cached = cache.get(key);
  if (cached !== undefined) {
    return cached;
  }
  const index = revisionIndex(revisions);
  const revision = index.get(revisionId);
  if (revision === undefined || revision.stereotypeName !== stereotypeName) {
    throw new Error(`unknown revision ${revisionId} for ${stereotypeName}`);
  }

  const merged = new Map<string, ContractField>();
  if (revision.parentRevisionId !== null) {
    const parentDef = index.get(revision.parentRevisionId);
    if (parentDef === undefined) {
      throw new Error(`revision ${revisionId} pins missing parent`);
    }
    const parent = resolveEffectiveContract(
      revisions,
      parentDef.stereotypeName,
      parentDef.revisionId,
      cache,
    );
    for (const field of parent.fields) {
      merged.set(field.propertyName, field);
    }
    const parentRequired = new Set(
      parent.fields.filter((f) => f.required).map((f) => f.propertyName),
    );
    for (const spec of revision.required) {
      if (!parentRequired.has(spec.propertyName)) {
        // A child may add required members, never drop the parent's.
        continue;
      }
    }
  }

  const declared: Array<[FieldSpec, boolean]> = [
    ...revision.required.map((spec): [FieldSpec, boolean] => [spec, true]),
    ...revision.optional.map((spec): [FieldSpec, boolean] => [spec, false]),
  ];
  for (const [spec, required] of declared) {
    merged.set(spec.propertyName, {
      propertyName: spec.propertyName,
      required,
      valueType: spec.valueType,
      originRevision: revision.revisionId,
      originStereotype: revision.stereotypeName,
      originVersion: revision.version,
    });
  }

  const fields = Array.from(merged.values()).sort((a, b) => {
    if (a.required !== b.required) {
      return a.required ? -1 : 1;
    }
    return a.propertyName.localeCompare(b.propertyName);
  });
  const contractFingerprint = sha256({
    stereotypeName: revision.stereotypeName,
    revisionId: revision.revisionId,
    version: revision.version,
    parentRevisionId: revision.parentRevisionId,
    fields: [...fields]
      .sort((a, b) => a.propertyName.localeCompare(b.propertyName))
      .map((f) => ({ propertyName: f.propertyName, required: f.required })),
  });
  const contract: EffectiveContract = {
    stereotypeName: revision.stereotypeName,
    revisionId: revision.revisionId,
    version: revision.version,
    depth: revision.depth,
    parentRevisionId: revision.parentRevisionId,
    contractFingerprint,
    fields,
  };
  cache.set(key, contract);
  return contract;
}

export function typeIdentity(contract: EffectiveContract): TypeIdentity {
  return {
    typeId: `shrapnel:stereotype:${contract.stereotypeName}`,
    stereotypeName: contract.stereotypeName,
    revisionId: contract.revisionId,
    version: contract.version,
    contractFingerprint: contract.contractFingerprint,
  };
}

export function buildLineage(entries: Array<Record<string, string>>): LineageRef[] {
  return entries
    .map((entry) => {
      const relation = String(entry["relation"]);
      const ref = String(entry["ref"]);
      if (relation === "dispatch") {
        return { relation, ref, authority: "mongo_reconstructable", reconstructable: true };
      }
      return { relation, ref, authority: "postgres", reconstructable: false };
    })
    .sort((a, b) =>
      a.relation === b.relation
        ? a.ref.localeCompare(b.ref)
        : a.relation.localeCompare(b.relation),
    );
}

export function normalizeInstance(
  raw: RawInstance,
  contract: EffectiveContract,
  identity: TypeIdentity,
  unavailableFields: readonly string[] = [],
): SemanticDescriptor {
  const declared = new Set(contract.fields.map((f) => f.propertyName));
  const undeclared = Object.keys(raw.values).filter((name) => !declared.has(name));
  if (undeclared.length > 0) {
    throw new Error(
      `instance carries undeclared values for ${contract.stereotypeName}@${contract.revisionId}: ${undeclared.join(", ")}`,
    );
  }
  const unavailable = new Set(unavailableFields);
  const fields: Record<string, TypedValue> = {};
  for (const member of contract.fields) {
    const name = member.propertyName;
    if (unavailable.has(name)) {
      fields[name] = {
        propertyName: name,
        status: "unavailable",
        valueType: member.valueType,
        reason: "authority_backend_unavailable",
      };
    } else if (Object.prototype.hasOwnProperty.call(raw.values, name)) {
      fields[name] = {
        propertyName: name,
        status: "governed",
        valueType: member.valueType,
        value: raw.values[name],
      };
    } else if (member.required) {
      fields[name] = {
        propertyName: name,
        status: "unknown",
        valueType: member.valueType,
        reason: "required_value_not_materialized",
      };
    } else {
      fields[name] = {
        propertyName: name,
        status: "absent",
        valueType: member.valueType,
        reason: "optional_value_not_materialized",
      };
    }
  }
  return {
    identity,
    contract,
    fields,
    lineage: raw.lineage,
    timestamps: raw.timestamps,
    authority: raw.authority,
    objectId: raw.objectId,
    materialization: raw.materialization,
  };
}

/** Canonical semantic surface — excludes backend and object identity. */
export function semanticSurface(descriptor: SemanticDescriptor): unknown {
  return {
    conformanceRevision: CONFORMANCE_REVISION,
    identity: descriptor.identity,
    contract: descriptor.contract,
    fields: Object.keys(descriptor.fields)
      .sort()
      .map((name) => descriptor.fields[name]),
    lineage: [...descriptor.lineage].sort((a, b) =>
      `${a.relation}\u0000${a.ref}\u0000${a.authority}`.localeCompare(
        `${b.relation}\u0000${b.ref}\u0000${b.authority}`,
      ),
    ),
    timestamps: descriptor.timestamps,
    authority: descriptor.authority,
  };
}

export function semanticFingerprint(descriptor: SemanticDescriptor): string {
  return sha256(semanticSurface(descriptor));
}

export interface EvaluationResult {
  mode: AuthorityMode;
  disposition: "asserted" | "unevaluable" | "refused";
  reasonCode: string | null;
  authority: AuthorityState;
  descriptor: SemanticDescriptor | null;
  fingerprint: string | null;
}

/** Evaluate one raw instance through the authority boundary (fail-closed). */
export function evaluateMaterialization(
  rawFactory: () => RawInstance,
  contract: EffectiveContract,
  identity: TypeIdentity,
  policy: AuthorityPolicy,
  unavailableFields: readonly string[] = [],
): EvaluationResult {
  const authority = assessAuthority(policy);
  if (authority.mode === "fail_closed") {
    return {
      mode: authority.mode,
      disposition: "refused",
      reasonCode: authority.reasonCode ?? "authority_backend_unavailable",
      authority,
      descriptor: null,
      fingerprint: null,
    };
  }
  const descriptor = normalizeInstance(
    rawFactory(),
    contract,
    identity,
    authority.mode === "degraded_reconstructable" ? DISPATCH_FIELDS : unavailableFields,
  );
  return {
    mode: authority.mode,
    disposition: authority.mode === "minimal_fallback" ? "unevaluable" : "asserted",
    reasonCode: authority.reasonCode,
    authority,
    descriptor,
    fingerprint: semanticFingerprint(descriptor),
  };
}
