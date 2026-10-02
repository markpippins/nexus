/**
 * Registry derivation for the deliverability guard (PR #713), kind-aware
 * amendment per architect ratification 2abf68c7 + Ruling 13 (de5d538f).
 *
 * Ruling 13 gives addresses a KIND: `role` (delivery obligation — the only
 * kind the deliverability guard asserts), `alias`, `telemetry` (recorded,
 * never delivered). The kind DEFAULTS to `role`, so pre-R13 registries
 * (entries without a kind field) keep their exact assertion set — the
 * current 25 canonical roles are unchanged by this amendment.
 *
 * Pure module: no I/O beyond reading the two registry JSON files the caller
 * points it at. Hermetically unit-tested in deliverabilityRegistry.test.ts;
 * the service-tier script (tests/deliverability.test.ts) consumes the same
 * functions so the derivation contract cannot drift between tiers.
 */
import * as fs from 'fs';

export const DEFAULT_ADDRESS_KIND = 'role' as const;

export type AddressKind = 'role' | 'alias' | 'telemetry';

export const KIND_VOCABULARY: readonly AddressKind[] = ['role', 'alias', 'telemetry'] as const;

export interface RolesSpec {
  roleDefaults?: Record<string, unknown>;
  roles: Record<string, Record<string, unknown> | undefined>;
}

export interface AddressKindsSpec {
  addresses: Record<string, { kind?: string } & Record<string, unknown>>;
}

/**
 * The address kind of a registry entry: the explicit `kind` field when it is
 * a recognized kind string, DEFAULT_ADDRESS_KIND otherwise. Unknown kind
 * strings fall back to the default so a typo can never silently shrink the
 * deliverability set (R13: consumers default kind to role when absent).
 */
export function addressKindOf(block: Record<string, unknown> | undefined | null): AddressKind {
  const explicit = block && typeof block === 'object' ? (block as any).kind : undefined;
  if (typeof explicit === 'string' && (KIND_VOCABULARY as readonly string[]).includes(explicit)) {
    return explicit as AddressKind;
  }
  return DEFAULT_ADDRESS_KIND;
}

/**
 * Deliverability assertion set: registry entries whose address kind carries
 * a delivery obligation (kind=role, explicit or defaulted) AND whose
 * nebulaCheck surface is expected (entry flag, else roleDefaults, else
 * false — unchanged from the pre-amendment derivation).
 */
export function canonicalDeliverabilityRoles(spec: RolesSpec): string[] {
  const defaults = spec.roleDefaults ?? {};
  const roles: string[] = [];
  for (const [name, overrides] of Object.entries(spec.roles ?? {})) {
    const kind = addressKindOf(overrides);
    if (kind !== 'role') continue; // aliases/telemetry: never delivery-asserted
    const nebulaCheck = overrides?.nebulaCheck ?? defaults.nebulaCheck ?? false;
    if (nebulaCheck) roles.push(name);
  }
  if (roles.length < 3) {
    throw new Error(
      `registry derived only ${roles.length} deliverable roles — refusing to run vacuously`,
    );
  }
  return roles.sort();
}

/**
 * Registered non-deliverable addresses from config/roles/address-kinds.json
 * (PR #715): telemetry is recorded-never-delivered, aliases are expand-at-
 * write (their delivery is exercised by expansion tests, not per-address
 * assertions). Returns [] when the registry is absent (pre-#715 trees) so
 * the guard degrades gracefully on main until #715 lands.
 */
export function nonDeliverableAddresses(
  kindsPath: string | null,
): Array<{ address: string; kind: AddressKind }> {
  if (!kindsPath || !fs.existsSync(kindsPath)) return [];
  const spec = JSON.parse(fs.readFileSync(kindsPath, 'utf-8')) as AddressKindsSpec;
  const out: Array<{ address: string; kind: AddressKind }> = [];
  for (const [address, entry] of Object.entries(spec.addresses ?? {})) {
    const kind = addressKindOf(entry);
    if (kind !== 'role') out.push({ address, kind });
  }
  return out.sort((a, b) => a.address.localeCompare(b.address));
}
