/**
 * Target-identity gate for the nebula-srv startup migration runner.
 *
 * Implements DBA proposal `5ab78e30`, responding to SEV3 `9a70c8c5`: a worktree
 * nebula-srv was booted on a scratch HTTP port with env pointed at the LIVE
 * `nexus` database, and the startup runner applied migrations 056 and 057 to
 * production. Self-reported, SEV3.
 *
 * ## Why a gate is needed at all
 *
 * The root cause is not carelessness, it is a default. `src/index.ts` builds the
 * Pool with defaults `localhost:5432/nexus`, and on this host **the default IS
 * production**. The runner had no target-identity awareness whatsoever: no
 * allowlist, no dry run, no opt-in, and no environment distinction. The scratch
 * `PORT` in the incident was the HTTP port and had no bearing on the PG target,
 * so the usual "just use a different port" instinct protects nothing here.
 *
 * Every safeguard therefore lived in human discipline, and that discipline has
 * now failed in production once.
 *
 * ## Design
 *
 * Fail-closed, but only when it matters. The gate engages **only when migrations
 * are pending**: pending migrations mean the running code is ahead of the
 * schema, which is the state worth stopping for. When the ledger is current the
 * boot proceeds exactly as today with no new environment variable, because zero
 * steady-state friction is what makes this adoptable at all.
 *
 * The alternative — limping past a pending migration with a warning — is
 * deliberately not offered. It re-creates the exact shape this system keeps
 * hitting: a check that reports healthy while the guarded thing is broken.
 *
 * ## Identity resolution
 *
 * Resolved from the **pool's effective configuration**, never from raw
 * environment variables. If the check read `process.env` while the pool dialed
 * something else, an env-rewrite path could desynchronise the check from the
 * target actually being modified, and the gate would be theatre.
 *
 * ## Status
 *
 * DRAFT, pending Architect ratification — the DBA marked `5ab78e30`
 * "architect ratification pending". The engineer's own migration (DRAFT-068,
 * PR #639) is the thing this gate protects, which is a conflict of interest
 * declared in record `23bd49f9` rather than resolved quietly.
 */

export interface PoolTargetConfig {
  host?: string;
  port?: number;
  database?: string;
}

export interface MigrationGateInput {
  /** `host:port:database` resolved from the pool's effective config. */
  identity: string;
  /** Migration filenames above the ledger version, ascending. */
  pending: string[];
  dryRun?: string | undefined;
  unsafe?: string | undefined;
  allowlist?: string | undefined;
}

export type MigrationGateDecision =
  | { action: 'proceed' }
  | { action: 'dry-run'; pending: string[] }
  | { action: 'block'; message: string };

/**
 * Resolve the dialed target from the pool's effective config.
 *
 * Defaults mirror `src/index.ts` exactly. If the Pool is ever given a different
 * default, this must be updated in step — a divergence here would mean the gate
 * checks a target the service is not actually using.
 */
export function resolveMigrateTarget(config: PoolTargetConfig): string {
  const host = config.host ?? 'localhost';
  const port = config.port ?? 5432;
  const database = config.database ?? '';
  return `${host}:${port}:${database}`;
}

/**
 * Pure gate decision. Extracted so the DBA's four acceptance cases are
 * testable with no database at all.
 */
export function decideMigrationGate(input: MigrationGateInput): MigrationGateDecision {
  const pending = input.pending;

  // Nothing pending: boot exactly as today. This is the zero-friction path and
  // the easiest thing to break, so it is checked first and unconditionally.
  if (pending.length === 0) return { action: 'proceed' };

  if (input.dryRun === '1') return { action: 'dry-run', pending };

  if (input.unsafe === '1') return { action: 'proceed' };

  if (input.allowlist === input.identity) return { action: 'proceed' };

  return {
    action: 'block',
    message:
      `[nebula-migrations] REFUSING to apply ${pending.length} pending migration(s) ` +
      `(${pending.join(', ')}): the resolved target '${input.identity}' does not match ` +
      `NEBULA_MIGRATE_TARGET ('${input.allowlist ?? '<unset>'}'). ` +
      `Pending migrations mean this build is ahead of the schema, so it will not start ` +
      `against a target it was not commissioned for. Either set ` +
      `NEBULA_MIGRATE_TARGET=${input.identity} if this IS the commissioned deploy, or use ` +
      `NEBULA_MIGRATE_DRY_RUN=1 to list what would run and apply nothing. ` +
      `NEBULA_MIGRATE_UNSAFE=1 bypasses this gate and is for scratch targets only.`,
  };
}

/**
 * Extract the version from a `NNN-*.sql` filename, or null if it is not a
 * numbered migration. Mirrors `FILE_RE` in `migrate.ts`; the two must agree,
 * or the gate would judge a different pending set than the runner applies.
 */
export function migrationVersion(filename: string): number | null {
  const match = /^(\d{3})-.*\.sql$/.exec(filename);
  return match ? Number(match[1]) : null;
}
