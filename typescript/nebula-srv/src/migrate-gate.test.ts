import { describe, expect, it } from 'vitest';
import {
  decideMigrationGate,
  migrationVersion,
  resolveMigrateTarget,
} from './migrate-gate';

/**
 * The DBA's four acceptance cases from 5ab78e30, plus the ordering property
 * that makes this adoptable. No database: the decision logic is pure.
 *
 * The commissioned production identity on this host is `localhost:5432:nexus` —
 * which is also `src/index.ts`'s default, and the reason the gate exists.
 */
const PROD = 'localhost:5432:nexus';

const gate = (over: Partial<Parameters<typeof decideMigrationGate>[0]> = {}) =>
  decideMigrationGate({ identity: PROD, pending: ['068-execution-identity.sql'], ...over });

describe('resolveMigrateTarget', () => {
  it('resolves host:port:database from the pool config', () => {
    expect(resolveMigrateTarget({ host: 'db.internal', port: 6543, database: 'nexus' }))
      .toBe('db.internal:6543:nexus');
  });

  it('mirrors the index.ts defaults when config is sparse', () => {
    // If index.ts defaults ever change, this must change with them — a
    // divergence means the gate checks a target the service is not dialling.
    expect(resolveMigrateTarget({})).toBe('localhost:5432:');
    expect(resolveMigrateTarget({ database: 'nexus' })).toBe('localhost:5432:nexus');
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

  it('is unaffected by a missing allowlist, an unsafe flag, or a dry run', () => {
    // The gate must not nag when it has nothing to guard.
    expect(gate({ pending: [], allowlist: undefined }).action).toBe('proceed');
    expect(gate({ pending: [], dryRun: '1' }).action).toBe('proceed');
    expect(gate({ pending: [], unsafe: '1' }).action).toBe('proceed');
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
