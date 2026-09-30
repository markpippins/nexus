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
 * ## Content binding (Decision 23, ruling `7f2b377a`)
 *
 * The target-identity gate guards WHICH DATABASE; the stale-tree incident of
 * 2026-09-29 (an applied 068 was missing the uuid guard because the runner read
 * `MIGRATIONS_DIR` from a 4-commits-behind working tree) showed a second
 * dimension: WHICH REVISION OF THE FILE. Decision 23: **the gate binds migration
 * CONTENT, not git revision** — before applying, each pending file's hash must
 * match the hash recorded for it in the committed attestation manifest
 * (`migrations/attestations.json`); a pending file with NO recorded hash is the
 * "unknown" state that produced the incident and therefore blocks.
 *
 * The content check sits BEFORE the target-allowlist check on purpose: content
 * binds *what*, the allowlist binds *where*. An allowlisted deploy of the wrong
 * bytes must still block. `NEBULA_MIGRATE_UNSAFE=1` remains the single
 * documented escape and still overrides this check — but the runner is required
 * to log the override loudly (see `migrate.ts`).
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
 * Target-identity portion ratified (Decision 16). Content binding added under
 * Decision 23 (DBA sketch `7c5fe000`, items B/C of the DBA input).
 */

export interface PoolTargetConfig {
  host?: string;
  port?: number;
  database?: string;
}

export interface ContentBinding {
  /** Migration filename as the runner will apply it (`NNN-*.sql`). */
  file: string;
  /** sha256 hex from the attestation manifest; absent = unknown = blocks. */
  expected?: string;
  /** sha256 hex of the bytes actually read from this tree. */
  found: string;
}

export interface MigrationGateInput {
  /** `host:port:database` resolved from the pool's effective config. */
  identity: string;
  /** Migration filenames above the ledger version, ascending. */
  pending: string[];
  dryRun?: string | undefined;
  unsafe?: string | undefined;
  allowlist?: string | undefined;
  /**
   * One binding per pending file. When omitted entirely (older callers /
   * fixtures) every pending file is treated as unknown and the content check
   * blocks — Decision 23 property 3, fail-closed on unknown.
   */
  content?: ContentBinding[];
  /** Display name of the manifest, for error messages. */
  manifestPath?: string;
}

export type MigrationGateDecision =
  | { action: 'proceed'; /** Set when unsafe=1 overrode a failed content check. */ contentOverridden?: boolean }
  | { action: 'dry-run'; pending: string[]; contentProblems: string[] }
  | { action: 'block'; message: string };

/**
 * Resolve the dialed target from the pool's effective config.
 *
 * Defaults mirror `src/index.ts` exactly (PG_DB_NAME || 'nexus'): host
 * 'localhost', port 5432, database 'nexus'. If index.ts ever changes a
 * default, this must be updated in step — a divergence here would mean the
 * gate checks a target the service is not actually using. (The `''`
 * database fallback only fires for a Pool constructed without a database;
 * the index.ts wiring always sets one — Decision 16 condition 2 nit.)
 */
export function resolveMigrateTarget(config: PoolTargetConfig): string {
  const host = config.host ?? 'localhost';
  const port = config.port ?? 5432;
  const database = config.database ?? 'nexus';
  return `${host}:${port}:${database}`;
}

export const DEFAULT_MANIFEST_PATH = 'migrations/attestations.json';

/**
 * Content problems across the pending set: files whose manifest hash is
 * missing (unknown) or disagrees with the bytes read from this tree.
 */
export function contentProblems(
  content: ContentBinding[] | undefined,
  manifestPath: string = DEFAULT_MANIFEST_PATH
): { unknown: string[]; mismatched: { file: string; expected: string; found: string }[] } {
  const unknown: string[] = [];
  const mismatched: { file: string; expected: string; found: string }[] = [];
  for (const binding of content ?? []) {
    if (!binding.expected) unknown.push(binding.file);
    else if (binding.expected !== binding.found) {
      mismatched.push({ file: binding.file, expected: binding.expected, found: binding.found });
    }
  }
  return { unknown, mismatched };
}

/**
 * Pure gate decision. Extracted so the DBA's acceptance cases are
 * testable with no database at all.
 */
export function decideMigrationGate(input: MigrationGateInput): MigrationGateDecision {
  const pending = input.pending;

  // Nothing pending: boot exactly as today. This is the zero-friction path and
  // the easiest thing to break, so it is checked first and unconditionally.
  if (pending.length === 0) return { action: 'proceed' };

  // Decision 23 property 3, enforced HERE rather than delegated to the caller:
  // a pending migration with no binding at all is "unknown" and unknown fails
  // closed. If the caller passed no bindings (a missing manifest upstream, an
  // older caller, a future runner edit that forgets), every pending file is
  // treated as having no attested hash. The runner normally passes one binding
  // per pending file, so this synthesis only fires when something upstream is
  // already wrong — which is exactly when the gate must not quietly pass.
  const bindings: ContentBinding[] =
    input.content ?? pending.map((file) => ({ file, expected: undefined, found: '' }));

  if (input.dryRun === '1') {
    // Dry run applies nothing, so it never blocks — but it must REPORT content
    // problems, otherwise the cheapest tool for diagnosing a stale tree lies
    // by omission.
    const { unknown, mismatched } = contentProblems(bindings, input.manifestPath);
    const problems = [
      ...unknown.map((f) => `${f}: no attested hash recorded (${input.manifestPath ?? DEFAULT_MANIFEST_PATH})`),
      ...mismatched.map(
        (m) => `${m.file}: attested ${m.expected} != found ${m.found}`
      ),
    ];
    return { action: 'dry-run', pending, contentProblems: problems };
  }

  if (input.unsafe === '1') {
    const { unknown, mismatched } = contentProblems(bindings, input.manifestPath);
    // Single documented escape; the RUNNER logs the override loudly. The
    // decision carries the fact so the runner does not have to recompute it.
    if (unknown.length > 0 || mismatched.length > 0) {
      return { action: 'proceed', contentOverridden: true };
    }
    return { action: 'proceed' };
  }

  // ── Content binding (Decision 23) — BEFORE the target allowlist ─────────
  // Content binds WHAT, the allowlist binds WHERE. An allowlisted deploy of
  // the wrong bytes must still block.
  const { unknown, mismatched } = contentProblems(bindings, input.manifestPath);
  const manifest = input.manifestPath ?? DEFAULT_MANIFEST_PATH;

  if (unknown.length > 0) {
    return {
      action: 'block',
      message:
        `[nebula-migrations] REFUSING to apply ${unknown.length} pending migration(s) with ` +
        `no attested content hash (unknown provenance — this is the state that produced the ` +
        `2026-09-29 stale-tree 068 apply): ${unknown.join(', ')}. ` +
        `The attestation manifest is ${manifest}; every pending NNN-*.sql must have an entry. ` +
        `Add the entries in the PR that introduces the migrations, or use ` +
        `NEBULA_MIGRATE_UNSAFE=1 for a scratch target only.`,
    };
  }

  if (mismatched.length > 0) {
    const first = mismatched[0];
    return {
      action: 'block',
      message:
        `[nebula-migrations] REFUSING to apply ${mismatched.length} pending migration(s) whose ` +
        `bytes do not match their attested hash: ` +
        `${mismatched.map((m) => m.file).join(', ')}. ` +
        `First offender: ${first.file} — expected ${first.expected}, found ${first.found}. ` +
        `The file on disk changed after its hash was recorded; regenerate the manifest entry ` +
        `in the same PR that changes the migration, or use NEBULA_MIGRATE_UNSAFE=1 ` +
        `for a scratch target only.`,
    };
  }

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
