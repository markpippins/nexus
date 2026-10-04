/**
 * Hermetic tests for Phase C write-side alias expansion (Ruling 26 §§1-§5,
 * ratified Ruling 27). Every mode test injects a registry: no file I/O, no
 * network, no database.
 *
 * Service-tier behaviour (HTTP wiring, storage of the expanded tag array) is
 * covered separately in tests/, per the Ruling 10 split already established
 * for the normalizer and census.
 */
import { describe, it, expect } from 'vitest';
import {
  expandTags,
  loadRegistry,
  modeRetainsTag,
  EXPANSION_MODES,
  type Registry,
  type ExpansionMode,
} from './tagExpansion';
import { normalizeTags } from './tagNormalizer';
import * as fs from 'fs';
import * as path from 'path';

const ROLES = ['architect', 'devops', 'dba', 'engineer', 'lead-engineer'];

/** Build a registry from a compact alias spec. Everything else is derived. */
function registry(roles: string[] = ROLES, aliases: Record<string, { mode: string; targets?: string[] }> = {}): Registry {
  const aliasMap = new Map<string, { mode: ExpansionMode; targets: string[] }>();
  for (const [name, spec] of Object.entries(aliases)) {
    aliasMap.set(name, { mode: spec.mode as ExpansionMode, targets: spec.targets ?? [] });
  }
  return {
    aliases: aliasMap,
    roleKeys: [...roles].sort(),
    roleKeySet: new Set(roles),
    aliasNames: new Set(aliasMap.keys()),
  };
}

describe('tagExpansion — Ruling 26 mode vocabulary', () => {
  it('declares exactly the six ratified modes, in ruling order', () => {
    expect([...EXPANSION_MODES]).toEqual([
      'fan-out-all-roles',
      'forum-broadcast',
      'operator-notification',
      'writer-role',
      'targets-expand',
      'none',
    ]);
  });

  it('agrees with bin/check_address_expansion_modes.py on the closed set', () => {
    // These two implement ONE vocabulary. The guard rejects an undeclared mode
    // at lint time; this module refuses to expand one at write time. If either
    // set drifts, the other silently under- or over-covers.
    const guardSrc = fs.readFileSync(
      path.resolve(__dirname, '..', '..', '..', 'bin', 'check_address_expansion_modes.py'),
      'utf8',
    );
    for (const mode of EXPANSION_MODES) {
      expect(guardSrc, `guard is missing mode ${mode}`).toContain(`"${mode}"`);
    }
    // ...and the guard declares no mode this module does not know.
    const guardModes = [...guardSrc.matchAll(/^\s{4}"([a-z-]+)",$/gm)].map((m) => m[1]);
    for (const gm of guardModes) {
      expect(EXPANSION_MODES as readonly string[]).toContain(gm);
    }
  });

  it('classifies which modes retain their tag', () => {
    // §2/§3 keep the tag as provenance; §4 keeps it when unresolvable;
    // R19 `none` keeps it always. §1/§5 replace it with recipients.
    expect(modeRetainsTag('forum-broadcast')).toBe(true);
    expect(modeRetainsTag('operator-notification')).toBe(true);
    expect(modeRetainsTag('writer-role')).toBe(true);
    expect(modeRetainsTag('none')).toBe(true);
    expect(modeRetainsTag('fan-out-all-roles')).toBe(false);
    expect(modeRetainsTag('targets-expand')).toBe(false);
  });
});

describe('§1 fan-out-all-roles', () => {
  const reg = registry(ROLES, { all: { mode: 'fan-out-all-roles' } });

  it('expands to every roles.json kind:role key, sorted', () => {
    const r = expandTags(['to:all'], { registry: reg });
    expect(r.tags).toEqual(['to:architect', 'to:dba', 'to:devops', 'to:engineer', 'to:lead-engineer']);
    expect(r.warnings).toEqual([]);
  });

  it('REPLACES the alias tag rather than retaining it', () => {
    // expand-and-replace: leaving `to:all` in place would make a consumer
    // that re-derives recipients double-count the audience.
    expect(expandTags(['to:all'], { registry: reg }).tags).not.toContain('to:all');
  });

  it('dedupes against an explicit tag for a role already in the fan-out', () => {
    const r = expandTags(['to:all', 'to:dba'], { registry: reg });
    expect(r.tags.filter((t) => t === 'to:dba')).toHaveLength(1);
    expect(r.tags).toContain('to:dba');
  });

  it('preserves Class V tags untouched', () => {
    const r = expandTags(['to:all', 'type:change', 'status:open'], { registry: reg });
    expect(r.tags).toEqual(
      expect.arrayContaining(['type:change', 'status:open']),
    );
    expect(r.tags).not.toContain('type:CHANGE');
  });

  it('emits no delivery intent — the tags ARE the delivery', () => {
    expect(expandTags(['to:all'], { registry: reg }).intents).toEqual([]);
  });

  it('is snapshot-at-write: later role registration cannot widen a stored record', () => {
    const first = expandTags(['to:all'], { registry: reg });
    const widened = registry([...ROLES, 'sysadmin'], { all: { mode: 'fan-out-all-roles' } });
    const second = expandTags(['to:all'], { registry: widened });
    // The SECOND write sees the new role; the FIRST record's stored tags do
    // not retroactively change, because recipients were frozen at write time.
    expect(first.tags).not.toContain('to:sysadmin');
    expect(second.tags).toContain('to:sysadmin');
  });
});

describe('§1 single-hop', () => {
  it('all-roles expands to the SAME roles as all, not to `all` plus 28 roles', () => {
    const reg = registry(ROLES, {
      all: { mode: 'fan-out-all-roles' },
      'all-roles': { mode: 'fan-out-all-roles' },
    });
    const a = expandTags(['to:all'], { registry: reg }).tags;
    const b = expandTags(['to:all-roles'], { registry: reg }).tags;
    expect(b).toEqual(a);
    // Critically: no alias survives, so no second hop was attempted.
    expect(b).not.toContain('to:all');
  });

  it('never recurses: an alias target is refused, not expanded', () => {
    const reg = registry(ROLES, {
      all: { mode: 'fan-out-all-roles' },
      leader: { mode: 'targets-expand', targets: ['lead-engineer', 'all'] },
    });
    const r = expandTags(['to:leader'], { registry: reg });
    expect(r.tags).toContain('to:lead-engineer');
    expect(r.tags).not.toContain('to:all'); // refused, not followed
    expect(r.warnings.map((w) => w.reason)).toContain('alias-as-target');
  });

  it('produces no alias→alias expansion even for a self-referential registry', () => {
    const reg = registry(ROLES, { loop: { mode: 'targets-expand', targets: ['loop'] } });
    const r = expandTags(['to:loop'], { registry: reg });
    // Terminates, warns, and does not loop. Single-hop makes cycles
    // inexpressible in a well-formed registry; this proves the CODE survives
    // one that is malformed.
    expect(r.tags).toEqual([]);
    expect(r.warnings[0].reason).toBe('alias-as-target');
  });
});

describe('§2 forum-broadcast', () => {
  const reg = registry(ROLES, { assembly: { mode: 'forum-broadcast' } });

  it('retains the tag as provenance and fans out to nothing', () => {
    const r = expandTags(['to:assembly'], { registry: reg });
    expect(r.tags).toEqual(['to:assembly']);
    expect(r.tags).not.toContain('to:architect');
  });

  it('emits exactly one intent, and no per-role intent', () => {
    const r = expandTags(['to:assembly'], { registry: reg });
    expect(r.intents).toEqual([{ kind: 'forum-broadcast', address: 'assembly' }]);
  });

  it('warns nothing — this is a decided, healthy state', () => {
    expect(expandTags(['to:assembly'], { registry: reg }).warnings).toEqual([]);
  });
});

describe('§3 operator-notification', () => {
  const reg = registry(ROLES, { user: { mode: 'operator-notification' } });

  it('retains the tag and delivers to a single recipient', () => {
    const r = expandTags(['to:user'], { registry: reg });
    expect(r.tags).toEqual(['to:user']);
    expect(r.intents).toEqual([{ kind: 'operator-notification', address: 'user' }]);
  });

  it('does NOT fan out to roles the operator never addressed', () => {
    const r = expandTags(['to:user'], { registry: reg });
    expect(r.tags.filter((t) => ROLES.some((role) => t === `to:${role}`))).toEqual([]);
  });
});

describe('§4 writer-role', () => {
  const reg = registry(ROLES, { self: { mode: 'writer-role' } });

  it('resolves to the creating record\'s own role', () => {
    const r = expandTags(['to:self'], { registry: reg, writerRole: 'dba' });
    expect(r.tags).toEqual(['to:dba']);
    expect(r.intents).toEqual([{ kind: 'writer-role', address: 'self', role: 'dba' }]);
  });

  it('lowercases the writer role, matching Class R normalization', () => {
    expect(expandTags(['to:self'], { registry: reg, writerRole: 'DBA' }).tags).toEqual(['to:dba']);
  });

  it('warns unresolvable-self and NEVER falls back when the writer has no role', () => {
    // The critical negative. Falling back to to:all or to:user would redirect
    // a self-addressed record to an unrelated audience.
    for (const writerRole of [undefined, null, '', '   ']) {
      const r = expandTags(['to:self'], { registry: reg, writerRole });
      expect(r.tags).toEqual(['to:self']);
      expect(r.tags).not.toContain('to:all');
      expect(r.tags).not.toContain('to:user');
      expect(r.warnings.map((w) => w.reason)).toContain('unresolvable-self');
    }
  });

  it('does not emit a writer-role intent when unresolvable', () => {
    expect(expandTags(['to:self'], { registry: reg }).intents).toEqual([]);
  });
});

describe('§5 targets-expand', () => {
  it('expands to its declared role targets', () => {
    const reg = registry([...ROLES, 'design-synthesist', 'layout-mechanic'], {
      designer: { mode: 'targets-expand', targets: ['design-synthesist', 'layout-mechanic'] },
    });
    const r = expandTags(['to:designer'], { registry: reg });
    expect(r.tags).toContain('to:design-synthesist');
    expect(r.tags).not.toContain('to:designer');
  });

  it('RETAINS the tag and warns alias-unmapped when targets are empty', () => {
    // The DECLARED unmapped state (Ruling 26 §5): visible, never a drop,
    // never a reject. The `admin` address ships in exactly this state.
    const reg = registry(ROLES, { admin: { mode: 'targets-expand', targets: [] } });
    const r = expandTags(['to:admin'], { registry: reg });
    expect(r.tags).toEqual(['to:admin']);
    expect(r.warnings).toHaveLength(1);
    expect(r.warnings[0].reason).toBe('alias-unmapped');
  });

  it('treats a MISSING targets key the same as an empty one', () => {
    // The naive `not targets` test and the correct empty-list test must agree
    // here; a registry that omits `targets` is the `admin` shape.
    const reg = registry(ROLES, { admin: { mode: 'targets-expand' } });
    expect(expandTags(['to:admin'], { registry: reg }).warnings[0].reason).toBe('alias-unmapped');
  });

  it('does NOT report alias-unmapped for modes that legitimately have no targets', () => {
    // The exact false-positive the guard was written to avoid: a
    // targets-presence test fires on all five DECIDED aliases.
    const reg = registry(ROLES, {
      all: { mode: 'fan-out-all-roles' },
      assembly: { mode: 'forum-broadcast' },
      user: { mode: 'operator-notification' },
      self: { mode: 'writer-role' },
      'big-pickle': { mode: 'none' },
    });
    for (const tag of ['to:all', 'to:assembly', 'to:user', 'to:self', 'to:big-pickle']) {
      const reasons = expandTags([tag], { registry: reg, writerRole: 'dba' }).warnings.map((w) => w.reason);
      expect(reasons, `${tag} must not be flagged alias-unmapped`).not.toContain('alias-unmapped');
    }
  });

  it('warns target-not-a-role when the registry names a non-role target', () => {
    const reg = registry(ROLES, { leader: { mode: 'targets-expand', targets: ['supervisor', 'nonexistent'] } });
    const r = expandTags(['to:leader'], { registry: reg });
    expect(r.tags).toEqual([]); // both refused rather than delivered to nobody silently
    expect(r.warnings.map((w) => w.reason)).toEqual(['target-not-a-role', 'target-not-a-role']);
  });
});

describe('R19 mode: none', () => {
  it('carries no delivery obligation and derives nothing', () => {
    const reg = registry(ROLES, { 'big-pickle': { mode: 'none' } });
    const r = expandTags(['to:big-pickle'], { registry: reg });
    expect(r.tags).toEqual(['to:big-pickle']);
    expect(r.intents).toEqual([]);
    expect(r.warnings).toEqual([]);
  });

  it('emits NO delivery intent — a model identity has no recipient', () => {
    // Regression pin. An intent here would have the write path post an
    // Assembly thread or notify the operator about `to:big-pickle`, an
    // address that by definition carries no delivery obligation (R19).
    const reg = registry(ROLES, { 'big-pickle': { mode: 'none' } });
    expect(expandTags(['to:big-pickle'], { registry: reg }).intents).toEqual([]);
  });
});

describe('fail-loud: the registry being unusable', () => {
  it('degrades to pass-through and flags degraded when the registry cannot load', () => {
    const r = expandTags(['to:all', 'to:assembly'], {
      configDir: '/nonexistent/config/roles',
    });
    expect(r.degraded).toBe(true);
    expect(r.warnings[0].reason).toBe('registry-unavailable');
    // CRITICAL: the tags are stored UNEXPANDED. A consumer still sees
    // `to:all`; nobody sees an empty recipient list.
    expect(r.tags).toEqual(['to:all', 'to:assembly']);
  });

  it('REFUSES a zero-role vocabulary rather than fanning out to nobody', () => {
    // The roles.json-on-main shape: 30 keys, no `kind` field. Expanding
    // `to:all` against it yields zero recipients — the silent drop Ruling 26
    // §0 exists to prevent, reached by the most plausible route.
    expect(() => loadRegistry('/nonexistent')).toThrow(/not found|ENOENT|no `/);

    const dir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'tags-exp-'));
    fs.writeFileSync(path.join(dir, 'address-kinds.json'), JSON.stringify({
      addresses: { all: { kind: 'alias', expansion: { mode: 'fan-out-all-roles' } } },
    }));
    fs.writeFileSync(path.join(dir, 'roles.json'), JSON.stringify({
      roles: { architect: { governance: false }, devops: {} }, // no `kind`
    }));
    expect(() => loadRegistry(dir)).toThrow(/kind: "role"/);

    // And the expander, handed that same registry, would keep the tag.
    const r = expandTags(['to:all'], {
      registry: { aliases: new Map(), roleKeys: [], roleKeySet: new Set(), aliasNames: new Set() },
    });
    expect(r.tags).toEqual(['to:all']);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

describe('unknown and unregistered addresses', () => {
  it('passes a role address through untouched — expansion is not normalization', () => {
    const reg = registry(ROLES, { all: { mode: 'fan-out-all-roles' } });
    expect(expandTags(['to:architect'], { registry: reg }).tags).toEqual(['to:architect']);
  });

  it('passes an unregistered alias through untouched (reject is #693\'s scope)', () => {
    const reg = registry(ROLES);
    const r = expandTags(['to:not-registered'], { registry: reg });
    expect(r.tags).toEqual(['to:not-registered']);
    expect(r.warnings).toEqual([]);
  });

  it('matches addresses case-insensitively, since Class R was lowercased', () => {
    const reg = registry(ROLES, { leader: { mode: 'targets-expand', targets: ['lead-engineer'] } });
    expect(expandTags(['to:Leader'], { registry: reg }).tags).toEqual(['to:lead-engineer']);
  });
});

describe('the REAL shipped registry', () => {
  // An expander tested only on fixtures would agree with a broken registry by
  // construction. These load config/roles/ from disk.
  const configDir = path.resolve(__dirname, '..', '..', '..', 'config', 'roles');

  it('loads, and declares a mode for every registered alias', () => {
    const reg = loadRegistry(configDir);
    expect(reg.roleKeys.length).toBeGreaterThan(0);
    // Every alias in address-kinds.json has an expansion mode this module
    // knows — no silent pass-through for a shipped address.
    const kinds = JSON.parse(fs.readFileSync(path.join(configDir, 'address-kinds.json'), 'utf8'));
    const aliases = Object.entries(kinds.addresses)
      .filter(([, e]: [string, any]) => e.kind === 'alias')
      .map(([n]) => n);
    expect(aliases.length).toBeGreaterThan(0);
    for (const name of aliases) {
      expect(reg.aliases.get(name)?.mode, `${name} has no known mode`).toBeDefined();
    }
  });

  it('every declared target is a real roles.json kind:role key', () => {
    const reg = loadRegistry(configDir);
    for (const [name, entry] of reg.aliases) {
      if (entry.mode !== 'targets-expand') continue;
      for (const t of entry.targets) {
        expect(reg.roleKeySet.has(t), `${name} targets non-role ${t}`).toBe(true);
      }
    }
  });

  it('every alias targets-expand entry carries targets EXCEPT declared-unmapped ones', () => {
    // `admin` ships unmapped by design (Ruling 26 §5). Anything else unmapped
    // is a regression, so pin the exception by name rather than by count.
    const reg = loadRegistry(configDir);
    const unmapped = [...reg.aliases]
      .filter(([, e]) => e.mode === 'targets-expand' && e.targets.length === 0)
      .map(([n]) => n);
    expect(unmapped).toEqual(['admin']);
  });

  it('expands the shipped to:all to every role, with no alias left behind', () => {
    const reg = loadRegistry(configDir);
    const r = expandTags(['to:all'], { registry: reg });
    expect(r.tags).toHaveLength(reg.roleKeys.length);
    expect(r.tags).not.toContain('to:all');
    expect(r.warnings).toEqual([]);
  });

  it('leaves telemetry addresses alone — they are archival, never delivered', () => {
    const reg = loadRegistry(configDir);
    const r = expandTags(['to:wr-conf-observer'], { registry: reg });
    expect(r.tags).toEqual(['to:wr-conf-observer']);
    expect(r.intents).toEqual([]);
  });
});

describe('composition with the normalizer and the census', () => {
  // The census guard (tagCensus.ts) defines a write-path bypass as
  // `normalizeTags([t]).tags !== [t]`. So EVERY tag this module stores must
  // be a normalizeTags fixed point — otherwise Phase C would manufacture
  // records that the census then reports as bypasses, poisoning the very
  // guard meant to detect bypasses.
  const configDir = path.resolve(__dirname, '..', '..', '..', 'config', 'roles');
  const ALL_ADDRESSES = [
    'to:all', 'to:all-roles', 'to:assembly', 'to:user', 'to:self',
    'to:big-pickle', 'to:admin', 'to:leader', 'to:designer', 'to:watchdog',
    'to:wr-conf-observer',
  ];

  it('stores only normalizeTags fixed points, for every shipped address', () => {
    const reg = loadRegistry(configDir);
    for (const address of ALL_ADDRESSES) {
      const out = expandTags([address], { registry: reg, writerRole: 'dba' }).tags;
      for (const stored of out) {
        expect(normalizeTags([stored]).tags, `${address} -> ${stored}`).toEqual([stored]);
      }
    }
  });

  it('is idempotent: re-expanding stored tags changes nothing', () => {
    // Snapshot-at-write means a read-modify-write cycle must not widen or
    // re-derive the audience. A PATCH that re-sends stored tags is a no-op.
    const reg = loadRegistry(configDir);
    for (const address of ALL_ADDRESSES) {
      const once = expandTags([address], { registry: reg, writerRole: 'dba' }).tags;
      const twice = expandTags(once, { registry: reg, writerRole: 'dba' }).tags;
      expect(twice, `${address} expansion is not idempotent`).toEqual(once);
    }
  });

  it('composes cleanly: normalize -> expand -> normalize is a fixed point', () => {
    // The realistic write shape, including a Class R repair defect.
    const reg = loadRegistry(configDir);
    const raw = ['to:engineer-to:engineer-ii', 'to:all', 'type:change', '  ', 'area:inbox'];
    const stored = expandTags(normalizeTags(raw).tags, { registry: reg, writerRole: 'dba' }).tags;
    expect(normalizeTags(stored).tags).toEqual(stored);
    expect(stored).toContain('to:engineer-ii'); // the repair survived expansion
    expect(stored).toContain('area:inbox');    // Class V untouched
    expect(stored).not.toContain('to:all');
  });
});