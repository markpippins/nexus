import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  addressKindOf,
  canonicalDeliverabilityRoles,
  nonDeliverableAddresses,
  DEFAULT_ADDRESS_KIND,
} from './deliverabilityRegistry';

/**
 * Hermetic tests for the kind-aware registry derivation (deliverability
 * guard amendment, architect ratification 2abf68c7 + Ruling 13 de5d538f).
 * Service-tier behavior (marker delivery through the live API) lives in
 * tests/deliverability.test.ts.
 *
 * The live-registry tests read the real config files so the guard's
 * contract is pinned to the tree it ships in; the synthetic tests prove
 * the kind-exclusion semantics without depending on registry content.
 */

const REPO = path.resolve(fs.realpathSync(__dirname), '..', '..', '..');
const ROLES_JSON = path.join(REPO, 'config', 'roles', 'roles.json');
const KINDS_JSON = path.join(REPO, 'config', 'roles', 'address-kinds.json');

function loadSpec(): any {
  return JSON.parse(fs.readFileSync(ROLES_JSON, 'utf-8'));
}

describe('addressKindOf', () => {
  it('defaults to role when the kind field is absent (pre-R13 registries)', () => {
    expect(addressKindOf(undefined)).toBe(DEFAULT_ADDRESS_KIND);
    expect(addressKindOf({})).toBe(DEFAULT_ADDRESS_KIND);
    expect(addressKindOf({ nebulaCheck: true })).toBe(DEFAULT_ADDRESS_KIND);
  });

  it('accepts every R13 kind explicitly', () => {
    expect(addressKindOf({ kind: 'role' })).toBe('role');
    expect(addressKindOf({ kind: 'alias' })).toBe('alias');
    expect(addressKindOf({ kind: 'telemetry' })).toBe('telemetry');
  });

  it('falls back to the default on an unrecognized kind string', () => {
    // A typo must never silently shrink the deliverability set.
    expect(addressKindOf({ kind: 'Role' })).toBe('role');
    expect(addressKindOf({ kind: 'telemitry' })).toBe('role');
    expect(addressKindOf({ kind: 42 })).toBe('role');
  });
});

describe('canonicalDeliverabilityRoles (synthetic specs)', () => {
  const base = { nebulaCheck: true };

  it('excludes kind=telemetry from the assertion set even with nebulaCheck=true', () => {
    const roles = canonicalDeliverabilityRoles({
      roleDefaults: base,
      roles: {
        engineer: { kind: 'role' },
        analyst: {},
        architect: {},
        'wr-conf-observer': { kind: 'telemetry', nebulaCheck: true },
      },
    });
    expect(roles).toEqual(['analyst', 'architect', 'engineer']);
  });

  it('excludes kind=alias from the assertion set even with nebulaCheck=true', () => {
    const roles = canonicalDeliverabilityRoles({
      roleDefaults: base,
      roles: {
        engineer: {},
        analyst: {},
        architect: {},
        all: { kind: 'alias', nebulaCheck: true },
      },
    });
    expect(roles).toEqual(['analyst', 'architect', 'engineer']);
  });

  it('defaults entries without a kind field into the set (unchanged legacy behavior)', () => {
    const roles = canonicalDeliverabilityRoles({
      roleDefaults: base,
      roles: { engineer: {}, analyst: {}, architect: {} },
    });
    expect(roles).toEqual(['analyst', 'architect', 'engineer']);
  });

  it('refuses to run vacuously when fewer than 3 deliverable roles derive', () => {
    expect(() =>
      canonicalDeliverabilityRoles({
        roleDefaults: base,
        roles: { engineer: { kind: 'telemetry' }, analyst: { kind: 'alias' } },
      }),
    ).toThrow(/vacuously/);
  });
});

describe('live registry contract (config/roles/roles.json)', () => {
  const spec = loadSpec();

  it('kind-aware derivation equals the legacy nebulaCheck-only derivation', () => {
    // The ratified amendment contract: defaulting kind to role must not
    // change the current assertion set. The legacy set is computed
    // independently here and equality is required — this holds on any tree
    // (pre-#712, post-#712, post-#715) as long as no roles entry carries a
    // non-role kind.
    const defaults = spec.roleDefaults ?? {};
    const legacy = Object.entries<any>(spec.roles ?? {})
      .filter(([, o]) => (o?.nebulaCheck ?? defaults.nebulaCheck ?? false) === true)
      .map(([name]) => name)
      .sort();
    expect(canonicalDeliverabilityRoles(spec)).toEqual(legacy);
  });

  it('derives a substantive set; pins 25 on the post-#712 vocabulary', () => {
    const roles = canonicalDeliverabilityRoles(spec);
    expect(roles.length).toBeGreaterThanOrEqual(20);
    if (roles.includes('dba')) {
      // Post-#712 registries carry lowercase dba; 29 entries minus the 4
      // helper entries (nebulaCheck=false) = exactly 25 deliverable.
      expect(roles).toHaveLength(25);
    }
  });

  it('keeps model/test helper entries out of the deliverability set', () => {
    const roles = canonicalDeliverabilityRoles(spec);
    for (const absent of ['big-pickle', 'test', 'builder-fallback', 'leased-builder']) {
      expect(roles).not.toContain(absent);
    }
  });
});

describe('nonDeliverableAddresses (config/roles/address-kinds.json)', () => {
  it('returns [] when the registry file is absent (pre-#715 trees degrade gracefully)', () => {
    expect(nonDeliverableAddresses(null)).toEqual([]);
    expect(nonDeliverableAddresses('/nonexistent/path/address-kinds.json')).toEqual([]);
  });

  const hasKinds = fs.existsSync(KINDS_JSON);

  it('classifies the #715 registry as non-deliverable with its kind', () => {
    if (!hasKinds) return; // #715 not present on this branch — skip structurally
    const entries = nonDeliverableAddresses(KINDS_JSON);
    const byAddress = new Map(entries.map((e) => [e.address, e.kind]));
    expect(byAddress.get('wr-conf-observer')).toBe('telemetry');
    expect(byAddress.get('all')).toBe('alias');
    expect(byAddress.get('user')).toBe('alias');
    expect(entries.length).toBeGreaterThanOrEqual(10);
  });

  it('never overlaps the deliverability role set (category separation)', () => {
    if (!hasKinds) return;
    const deliverable = canonicalDeliverabilityRoles(loadSpec());
    const nonDeliverable = nonDeliverableAddresses(KINDS_JSON).map((e) => e.address);
    const overlap = nonDeliverable.filter((a) => deliverable.includes(a));
    expect(overlap).toEqual([]);
  });
});
