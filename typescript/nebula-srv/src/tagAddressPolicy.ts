/**
 * Tag address policy — Ruling 13 step 5, sequenced draft for PR #693.
 *
 * Ruling 13 (de5d538f) + Ruling 11 (scope: to:-addresses ONLY): the write
 * side REJECTS only `to:`-prefixed addresses that are registered NEITHER in
 * config/roles/roles.json (roles = kind `role`) NOR in
 * config/roles/address-kinds.json (PR #715: aliases + telemetry). Case is
 * NEVER a violation (Ruling 11: 1,374 legitimate uppercase VALUE tags;
 * matching is case-insensitive and #711 delivers case variants) — the
 * registry is the single source of "known address".
 *
 * Mode is env-gated, WARN by default:
 *   NEBULA_TAG_ADDRESS_POLICY unset|'warn'   -> log loudly, accept (default)
 *   NEBULA_TAG_ADDRESS_POLICY='reject'       -> HTTP 422 naming every offender
 *
 * Arming gate (card §6 + R13 binding order): 'reject' may only be enabled
 * AFTER the full chain lands and is attested — explicit kind (#715/#712),
 * #713 kind-aware amendment, #714 write-side normalizer (normalization runs
 * BEFORE this policy in the §6 order, so the checker sees repaired tags).
 * Until then this module ships dark: warn mode on every tree.
 *
 * Fail-open: if neither registry file can be read (pre-#715 tree, partial
 * checkout), the checker degrades to warn-with-reason and NEVER rejects —
 * an unbuildable allowlist must not break the write path.
 */

/** Where the two registry files live relative to the repo root. */
export const ROLES_JSON_PATH = 'config/roles/roles.json';
export const ADDRESS_KINDS_JSON_PATH = 'config/roles/address-kinds.json';

/** Env var selecting the policy mode. */
export const ADDRESS_POLICY_ENV = 'NEBULA_TAG_ADDRESS_POLICY';

/** Default mode: WARN — this PR arms nothing by itself (R13 sequencing). */
export const DEFAULT_ADDRESS_POLICY = 'warn' as const;

const VALID_MODES = ['warn', 'reject'] as const;
export type AddressPolicyMode = (typeof VALID_MODES)[number];

export interface AddressViolation {
  tag: string;
  reason: string;
}

export interface LoadedRegistries {
  /** Lowercase registered Class R addresses across both registries. */
  knownAddresses: Set<string>;
  /** Non-empty when a registry file was missing/unreadable (fail-open). */
  degraded: string[];
}

export interface AddressPolicyResult {
  mode: AddressPolicyMode;
  violations: AddressViolation[];
  degraded: string[];
}

import * as fs from 'fs';
import * as path from 'path';

/**
 * Load + merge the registered-address universe: roles.json `roles` keys
 * (kind=role by construction of that file) plus address-kinds.json
 * `addresses` keys (alias/telemetry). Keys are lowercased on load; lookup
 * is therefore case-insensitive (#711 complement). A missing/unreadable
 * file degrades instead of throwing.
 */
export function loadAddressRegistry(repoRoot: string): LoadedRegistries {
  const known = new Set<string>();
  const degraded: string[] = [];

  const readKeys = (file: string, mapKeys: (spec: any) => string[]): void => {
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(repoRoot, file), 'utf-8'));
      for (const key of mapKeys(raw)) {
        if (typeof key === 'string' && key.trim() !== '') known.add(key.trim().toLowerCase());
      }
    } catch {
      degraded.push(file);
    }
  };

  readKeys(ROLES_JSON_PATH, (spec) => Object.keys(spec?.roles ?? {}));
  readKeys(ADDRESS_KINDS_JSON_PATH, (spec) => Object.keys(spec?.addresses ?? {}));

  return { knownAddresses: known, degraded };
}

/** Resolve the mode from the environment; unknown values fall back to warn. */
export function resolveAddressPolicyMode(env: NodeJS.ProcessEnv | Record<string, string | undefined> = process.env): AddressPolicyMode {
  const raw = (env[ADDRESS_POLICY_ENV] ?? '').trim().toLowerCase();
  return (VALID_MODES as readonly string[]).includes(raw) ? (raw as AddressPolicyMode) : DEFAULT_ADDRESS_POLICY;
}

/**
 * Classify one already-normalized tag. Returns a violation ONLY for Class R
 * (`to:`-prefixed) addresses absent from the registry — value tags are
 * never routing (R11) and registered aliases/telemetry are known addresses.
 */
export function classifyAddress(tag: string, known: Set<string>): AddressViolation | null {
  if (!tag.toLowerCase().startsWith('to:')) return null; // Class V: no routing semantics
  const address = tag.slice(3).trim().toLowerCase();
  if (address === '') return null; // empty repair residue is the normalizer's domain
  if (known.has(address)) return null; // role, alias, or telemetry — all known
  return {
    tag,
    reason: `unregistered routing address "to:${address}" — not in roles.json or address-kinds.json`,
  };
}

/**
 * Full check over a tags array. `registries` may be omitted in warn mode
 * (the checker then cannot find violations and simply reports the mode).
 */
export function enforceTagAddressPolicy(
  tags: unknown,
  registries?: LoadedRegistries,
  mode: AddressPolicyMode = DEFAULT_ADDRESS_POLICY,
): AddressPolicyResult {
  const violations: AddressViolation[] = [];
  const degraded = registries?.degraded ?? [];

  // Fail-open: with ANY registry file unreadable the allowlist is known to
  // be incomplete, so classification is skipped entirely — a partial
  // allowlist must never reject an address it simply could not read.
  if (registries && degraded.length === 0 && Array.isArray(tags)) {
    for (const raw of tags) {
      if (typeof raw !== 'string') continue; // non-string tags: normalizer's domain
      const violation = classifyAddress(raw, registries.knownAddresses);
      if (violation) violations.push(violation);
    }
  }

  return { mode, violations, degraded };
}

/** 422 body for reject mode: names every offender, never truncates silently. */
export function addressPolicyErrorMessage(result: AddressPolicyResult): string | null {
  if (result.violations.length === 0) return null;
  return `unregistered routing address(es): ${result.violations
    .map((v) => `"${v.tag}" (${v.reason})`)
    .join('; ')}. Valid addresses: to:<role> (roles.json) or a registered alias/telemetry address (address-kinds.json). If this address is a new role, register it first — do not bypass the registry.`;
}
