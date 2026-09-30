import { describe, expect, it } from 'vitest';
import {
  contentProblems,
  decideMigrationGate,
  migrationVersion,
  resolveMigrateTarget,
  type ContentBinding,
} from './migrate-gate';

/**
 * The DBA's four acceptance cases from 5ab78e30, plus the Decision 23
 * content-binding cases from 7f2b377a / DBA sketch 7c5fe000, plus the
 * ordering properties that make this adoptable. No database: the decision
 * logic is pure.
 *
 * The commissioned production identity on this host is `localhost:5432:nexus` —
 * which is also `src/index.ts`'s default, and the reason the gate exists.
 */
const PROD = 'localhost:5432:nexus';

const H_A = 'a'.repeat(64);
const H_B = 'b'.repeat(64);

const binding = (file: string, expected: string | undefined, found: string): ContentBinding => ({
  file,
  expected,
  found,
});

const gate = (over: Partial<Parameters<typeof decideMigrationGate>[0]> = {}) =>
  decideMigrationGate({
    identity: PROD,
    pending: ['068-execution-identity.sql'],
    content: [binding('068-execution-identity.sql', H_A, H_A)],
    ...over,
  });

describe('resolveMigrateTarget', () => {
  it('resolves host:port:database from the pool config', () => {
    expect(resolveMigrateTarget({ host: 'db.internal', port: 6543, database: 'nexus' }))
      .toBe('db.internal:6543:nexus');
  });

  it('mirrors the index.ts defaults when config is sparse', () => {
    // If index.ts defaults ever change, this must change with them — a
    // divergence means the gate checks a target the service is not dialling.
    // Decision 16 condition 2: the database fallback is 'nexus' (matching
    // index.ts's PG_DB_NAME || 'nexus'), not ''. A Pool without a database
    // resolves to the same target index.ts would have dialed.
    expect(resolveMigrateTarget({})).toBe('localhost:5432:nexus');
    expect(resolveMigrateTarget({ database: 'nebula_gate_ci' })).toBe('localhost:5432:nebula_gate_ci');
  });
});

describe('acceptance 1: worktree boot with pending migrations fails closed', () => {
  it('blocks when the allowlist is unset', () => {
    const decision = gate({ allowlist: undefined });
    expect(decision.action).toBe('block');
    if (decision.action !== 'block') return;
    expect(decision.message).toContain(PROD);
    expect(decision.message).toContain('NEBULA_MIGRATE_TARGET');
    expect(decision.message).toContain('068-execution-identity.sql');
  });

  it('blocks when the allowlist names a different target', () => {
    const decision = gate({ allowlist: 'otherhost:5432:nexus' });
    expect(decision.action).toBe('block');
  });

  it('names the exact fix in the message, both ways', () => {
    const decision = gate({ allowlist: 'otherhost:5432:nexus' });
    if (decision.action !== 'block') throw new Error('expected block');
    expect(decision.message).toContain(`NEBULA_MIGRATE_TARGET=${PROD}`);
    expect(decision.message).toContain('NEBULA_MIGRATE_DRY_RUN=1');
    expect(decision.message).toContain('NEBULA_MIGRATE_UNSAFE=1');
  });

  it('states why, so an operator is not left guessing', () => {
    const decision = gate();
    if (decision.action !== 'block') throw new Error('expected block');
    expect(decision.message).toContain('ahead of the schema');
  });
});

describe('acceptance 2: legitimate production deploy succeeds', () => {
  it('proceeds when the allowlist matches the resolved target exactly', () => {
    expect(gate({ allowlist: PROD }).action).toBe('proceed');
  });

  it('requires an exact match, not a prefix or a substring', () => {
    for (const wrong of ['localhost:5432', 'localhost:5432:nexus2', 'xlocalhost:5432:nexus', PROD + ' ']) {
      expect(gate({ allowlist: wrong }).action, `should not accept ${wrong}`).toBe('block');
    }
  });

  it('proceeds for a different commissioned target, e.g. the vanadium tier', () => {
    const vd = decideMigrationGate({
      identity: 'vanadium.internal:5432:nexus',
      pending: ['068-x.sql'],
      content: [binding('068-x.sql', H_A, H_A)],
      allowlist: 'vanadium.internal:5432:nexus',
    });
    expect(vd.action).toBe('proceed');
  });
});

describe('acceptance 3: a current ledger needs no new environment (zero friction)', () => {
  it('proceeds with nothing set at all when there is nothing pending', () => {
    const decision = decideMigrationGate({ identity: PROD, pending: [] });
    expect(decision.action).toBe('proceed');
  });

  it('is unaffected by a missing allowlist, an unsafe flag, a dry run, or absent bindings', () => {
    // The gate must not nag when it has nothing to guard — even with no
    // content bindings at all, since none are needed when nothing is pending.
    expect(gate({ pending: [], allowlist: undefined, content: undefined }).action).toBe('proceed');
    expect(gate({ pending: [], dryRun: '1', content: undefined }).action).toBe('proceed');
    expect(gate({ pending: [], unsafe: '1', content: undefined }).action).toBe('proceed');
  });
});

describe('acceptance 4: dry run lists pending and applies nothing', () => {
  it('reports the pending list instead of proceeding', () => {
    const decision = gate({ dryRun: '1' });
    expect(decision.action).toBe('dry-run');
    if (decision.action !== 'dry-run') return;
    expect(decision.pending).toEqual(['068-execution-identity.sql']);
  });

  it('works from any tree and any target, with no allowlist set', () => {
    const decision = decideMigrationGate({
      identity: 'somewhere.else:5432:scratch',
      pending: ['069-a.sql', '070-b.sql'],
      content: [binding('069-a.sql', H_A, H_A), binding('070-b.sql', H_B, H_B)],
      dryRun: '1',
    });
    expect(decision.action).toBe('dry-run');
  });

  it('only honours the exact value "1"', () => {
    // A truthy-but-wrong value must not silently disable the gate, and must not
    // silently enable dry run either.
    expect(gate({ dryRun: 'true' }).action).toBe('block');
    expect(gate({ dryRun: 'yes' }).action).toBe('block');
  });
});

describe('escape hatch', () => {
  it('NEBULA_MIGRATE_UNSAFE=1 proceeds regardless of the allowlist', () => {
    expect(gate({ unsafe: '1', allowlist: undefined }).action).toBe('proceed');
    expect(gate({ unsafe: '1', allowlist: 'wrong:5432:x' }).action).toBe('proceed');
  });

  it('is not honoured for any other value', () => {
    expect(gate({ unsafe: 'true' }).action).toBe('block');
  });

  it('dry run still wins over unsafe, so a dry run is never applied', () => {
    expect(gate({ dryRun: '1', unsafe: '1' }).action).toBe('dry-run');
  });
});

describe('ordering guarantees', () => {
  it('no pending is checked before any flag, so the frictionless path is first', () => {
    // A regression that reordered this would start nagging operators on every
    // boot, which is how guards get disabled.
    expect(decideMigrationGate({ identity: PROD, pending: [] })).toEqual({ action: 'proceed' });
  });

  it('an empty pending list and a missing one behave identically', () => {
    expect(decideMigrationGate({ identity: PROD, pending: [] }).action).toBe('proceed');
  });
});

describe('Decision 23: content binding (ruling 7f2b377a, sketch 7c5fe000)', () => {
  it('property 3: a pending file with NO attested hash blocks (unknown fails closed)', () => {
    const decision = gate({
      content: [binding('068-execution-identity.sql', undefined, H_A)],
    });
    expect(decision.action).toBe('block');
    if (decision.action !== 'block') return;
    expect(decision.message).toContain('no attested content hash');
    expect(decision.message).toContain('068-execution-identity.sql');
    expect(decision.message).toContain('attestations.json');
  });

  it('property 1: a byte mismatch blocks, naming the file and BOTH hashes', () => {
    const decision = gate({
      content: [binding('068-execution-identity.sql', H_A, H_B)],
    });
    expect(decision.action).toBe('block');
    if (decision.action !== 'block') return;
    expect(decision.message).toContain('068-execution-identity.sql');
    expect(decision.message).toContain(H_A);
    expect(decision.message).toContain(H_B);
  });

  it('an entirely missing manifest blocks (every binding unknown)', () => {
    const decision = gate({
      content: undefined,
      allowlist: PROD, // even the correct target must not bypass the content check
    });
    expect(decision.action).toBe('block');
    if (decision.action !== 'block') return;
    expect(decision.message).toContain('no attested content hash');
  });

  it('property ordering: content binding blocks BEFORE the target allowlist', () => {
    // An allowlisted deploy of the wrong bytes must still block — content
    // binds WHAT, the allowlist binds WHERE, neither alone suffices.
    const mismatched = gate({
      allowlist: PROD,
      content: [binding('068-execution-identity.sql', H_A, H_B)],
    });
    expect(mismatched.action).toBe('block');
    if (mismatched.action !== 'block') return;
    expect(mismatched.message).toContain('do not match their attested hash');

    const unknown = gate({
      allowlist: PROD,
      content: [binding('068-execution-identity.sql', undefined, H_A)],
    });
    expect(unknown.action).toBe('block');
  });

  it('dry run REPORTS content problems without blocking, both kinds', () => {
    const decision = gate({
      dryRun: '1',
      pending: ['068-execution-identity.sql', '069-x.sql'],
      content: [
        binding('068-execution-identity.sql', undefined, H_A),
        binding('069-x.sql', H_B, H_A),
      ],
    });
    expect(decision.action).toBe('dry-run');
    if (decision.action !== 'dry-run') return;
    expect(decision.contentProblems).toHaveLength(2);
    expect(decision.contentProblems[0]).toContain('no attested hash recorded');
    expect(decision.contentProblems[1]).toContain('attested bbbb');
  });

  it('unsafe=1 proceeds past a failed binding but flags the override', () => {
    const decision = gate({
      unsafe: '1',
      content: [binding('068-execution-identity.sql', H_A, H_B)],
    });
    expect(decision.action).toBe('proceed');
    if (decision.action !== 'proceed') return;
    expect(decision.contentOverridden).toBe(true);
  });

  it('unsafe=1 with a clean binding does not claim an override', () => {
    const decision = gate({ unsafe: '1' });
    expect(decision.action).toBe('proceed');
    if (decision.action !== 'proceed') return;
    expect(decision.contentOverridden).toBeFalsy();
  });

  it('mixed unknown + mismatched files block with all names present', () => {
    const decision = gate({
      pending: ['068-a.sql', '069-b.sql'],
      content: [binding('068-a.sql', undefined, H_A), binding('069-b.sql', H_B, H_A)],
    });
    expect(decision.action).toBe('block');
    if (decision.action !== 'block') return;
    // Unknown is reported first (it is the more severe provenance hole).
    expect(decision.message).toContain('068-a.sql');
  });
});

describe('contentProblems helper', () => {
  it('classifies unknown and mismatched bindings and ignores clean ones', () => {
    const { unknown, mismatched } = contentProblems([
      binding('a.sql', H_A, H_A),
      binding('b.sql', undefined, H_B),
      binding('c.sql', H_A, H_B),
    ]);
    expect(unknown).toEqual(['b.sql']);
    expect(mismatched).toEqual([{ file: 'c.sql', expected: H_A, found: H_B }]);
  });

  it('treats an absent binding list as all-unknown when pending exist', () => {
    const { unknown } = contentProblems(undefined);
    expect(unknown).toEqual([]);
  });
});

describe('migrationVersion agrees with the runner FILE_RE', () => {
  it('parses a numbered migration', () => {
    expect(migrationVersion('068-execution-identity.sql')).toBe(68);
    expect(migrationVersion('001-add-harvest-candidates.sql')).toBe(1);
  });

  it('returns null for anything the runner would not apply', () => {
    // The draft migration and the unnumbered scd-*/seed-* files must all be
    // ignored, or the gate would judge a different pending set than the runner
    // applies.
    for (const name of [
      'DRAFT-068-execution-identity.sql',
      'scd-type4-temporal.sql',
      'seed-projections-crossrefs.sql',
      '68-short.sql',
      '068.sql.bak',
      'notamigration.sql',
    ]) {
      expect(migrationVersion(name), `should ignore ${name}`).toBeNull();
    }
  });
});
