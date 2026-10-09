import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  enforceTagAddressPolicy,
  loadAddressRegistry,
  resolveAddressPolicyMode,
  ADDRESS_POLICY_ENV,
  DEFAULT_ADDRESS_POLICY,
} from './tagAddressPolicy';

/**
 * Hermetic tests for the kind-aware tag address policy (PR #693 draft,
 * Ruling 13 step 5 + Ruling 11 scope). Service-tier two-phase behavior
 * (warn accepts / reject 422) is covered by
 * tests/tag-address-policy-http.test.ts.
 */

function tmpTree(specs: { roles?: any; kinds?: any }): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'addr-policy-'));
  const cfg = path.join(root, 'config', 'roles');
  fs.mkdirSync(cfg, { recursive: true });
  if (specs.roles !== null) {
    fs.writeFileSync(path.join(cfg, 'roles.json'), JSON.stringify(specs.roles ?? { roles: { engineer: {}, dba: {} } }));
  }
  if (specs.kinds !== null) {
    fs.writeFileSync(
      path.join(cfg, 'address-kinds.json'),
      JSON.stringify(specs.kinds ?? { addresses: { all: { kind: 'alias' }, 'wr-conf-observer': { kind: 'telemetry' } } }),
    );
  }
  return root;
}

describe('loadAddressRegistry', () => {
  it('merges both registries, lowercased', () => {
    const root = tmpTree({
      roles: { roles: { engineer: {}, DBA: {}, 'design-synthesist': {} } },
      kinds: { addresses: { ALL: { kind: 'alias' }, 'wr-conf-observer': { kind: 'telemetry' } } },
    });
    const reg = loadAddressRegistry(root);
    expect(reg.degraded).toEqual([]);
    for (const a of ['engineer', 'dba', 'design-synthesist', 'all', 'wr-conf-observer']) {
      expect(reg.knownAddresses.has(a)).toBe(true);
    }
  });

  it('degrades fail-open when a registry file is missing or unreadable', () => {
    const root = tmpTree({ roles: null, kinds: null });
    const reg = loadAddressRegistry(root);
    expect(reg.knownAddresses.size).toBe(0);
    expect(reg.degraded).toHaveLength(2);
    // An unbuildable allowlist must never reject.
    const result = enforceTagAddressPolicy(['to:anything'], reg, 'reject');
    expect(result.violations).toEqual([]);
    expect(result.degraded).toHaveLength(2);
  });
});

describe('classifyAddress (Class R only, kind-blind)', () => {
  const known = new Set(['dba', 'engineer', 'all', 'wr-conf-observer']);

  it('flags unregistered to: addresses regardless of case', () => {
    expect(classify('to:nobody', known)).toMatchObject({ tag: 'to:nobody' });
    expect(classify('to:NOBODY', known)).toMatchObject({ tag: 'to:NOBODY' });
    expect(classify('TO:Nobody', known)).toMatchObject({ tag: 'TO:Nobody' });
  });

  it('NEVER flags case variants of registered addresses (Ruling 11)', () => {
    expect(classify('to:DBA', known)).toBeNull();
    expect(classify('TO:dba', known)).toBeNull();
  });

  it('NEVER flags registered aliases or telemetry (kind-aware)', () => {
    expect(classify('to:all', known)).toBeNull();
    expect(classify('to:wr-conf-observer', known)).toBeNull();
  });

  it('NEVER flags Class V value tags — including uppercase ones (Ruling 11: 1,374 legitimate)', () => {
    for (const t of ['type:change', 'ADR-006', 'blocks:PR-580', 'fix', '2026-W40']) {
      expect(classify(t, known)).toBeNull();
    }
  });

  it('does not flag empty/whitespace address residue (normalizer domain)', () => {
    expect(classify('to:', known)).toBeNull();
    expect(classify('to:  ', known)).toBeNull();
  });

  function classify(tag: string, k: Set<string>) {
    const reg = { knownAddresses: k, degraded: [] as string[] };
    return enforceTagAddressPolicy([tag], reg, 'reject').violations[0] ?? null;
  }
});

describe('enforceTagAddressPolicy', () => {
  const root = tmpTree({});
  const reg = loadAddressRegistry(root);

  it('warn mode returns violations without implying rejection', () => {
    const result = enforceTagAddressPolicy(['to:nobody'], reg, 'warn');
    expect(result.mode).toBe('warn');
    expect(result.violations).toHaveLength(1);
  });

  it('reports every offender, not only the first', () => {
    const result = enforceTagAddressPolicy(['to:ghost-a', 'to:dba', 'to:ghost-b'], reg, 'reject');
    expect(result.violations.map((v) => v.tag)).toEqual(['to:ghost-a', 'to:ghost-b']);
  });

  it('mixed payload: registered + alias + telemetry + Class V pass; unregistered flag', () => {
    const result = enforceTagAddressPolicy(
      ['to:dba', 'to:all', 'to:wr-conf-observer', 'type:change', 'ADR-006', 'to:nobody-here'],
      reg,
      'reject',
    );
    expect(result.violations.map((v) => v.tag)).toEqual(['to:nobody-here']);
  });

  it('handles non-array and non-string tags without throwing', () => {
    expect(enforceTagAddressPolicy(null, reg, 'reject').violations).toEqual([]);
    expect(enforceTagAddressPolicy(undefined, reg, 'reject').violations).toEqual([]);
    expect(enforceTagAddressPolicy([42], reg, 'reject').violations).toEqual([]);
  });
});

describe('resolveAddressPolicyMode', () => {
  it('defaults to warn (this PR arms nothing by itself)', () => {
    expect(resolveAddressPolicyMode({})).toBe(DEFAULT_ADDRESS_POLICY);
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: '' })).toBe('warn');
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: 'WARN' })).toBe('warn');
  });

  it('accepts the reject mode and ignores unknown values', () => {
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: 'reject' })).toBe('reject');
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: 'REJECT' })).toBe('reject');
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: 'enforce' })).toBe('warn');
    expect(resolveAddressPolicyMode({ [ADDRESS_POLICY_ENV]: 'yes' })).toBe('warn');
  });
});
