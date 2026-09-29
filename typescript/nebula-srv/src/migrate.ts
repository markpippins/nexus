import { readdirSync, readFileSync } from 'fs';
import path from 'path';
import { Pool } from 'pg';
import {
  decideMigrationGate,
  migrationVersion,
  resolveMigrateTarget,
} from './migrate-gate';

/**
 * nebula-srv file-based migration runner (W3.2).
 *
 * nebula-srv migrations live as numbered SQL files in `migrations/` (e.g.
 * `047-allow-dba-and-epistemic-roles.sql`). Historically these were applied
 * by hand via psql, so the schema could drift from the checked-in files with
 * no live record. `nebula.schema_version` (added in migration 043) is the
 * forward ledger: version 41 is a baseline for migrations 001-041, and 042+
 * are recorded individually.
 *
 * This runner is wired into startup: it reads the highest applied version,
 * applies every numbered `NNN-*.sql` file above it in ascending order, and
 * stamps the ledger after each. It is idempotent and forward-only — on a DB
 * already at the current version it is a no-op.
 *
 * Files may carry their own BEGIN/COMMIT (some do); executing the raw file
 * text via the simple query protocol honours that, and PG otherwise wraps a
 * multi-statement string in an implicit transaction, so each migration is
 * all-or-nothing before its ledger stamp.
 *
 * ## Target-identity gate
 *
 * Guarded by `migrate-gate.ts` (DBA proposal `5ab78e30`, after SEV3 `9a70c8c5`
 * in which this runner applied migrations to the LIVE database from a worktree
 * boot). When migrations are pending, the resolved `host:port:database` must
 * match `NEBULA_MIGRATE_TARGET` or startup aborts.
 *
 * The gate engages ONLY when something is pending. On a current ledger the boot
 * is byte-for-byte the pre-gate behaviour with no new environment variable —
 * that zero-friction property is what makes the guard adoptable, and it is the
 * property most easily broken by a later edit.
 *
 * Two ordering choices differ from the reference sketch in `5ab78e30`, both
 * deliberate and both flagged in the gate module: the ledger is read without
 * being created first, so `NEBULA_MIGRATE_DRY_RUN=1` writes nothing at all; and
 * the pending set is computed once and used for both the gate decision and the
 * apply loop, so the two can never disagree.
 */

const MIGRATIONS_DIR = path.resolve(__dirname, '..', 'migrations');

// Advisory lock key — distinct from tackle-srv (873492874) so the two
// services never contend on the same lock.
const NEBULA_MIGRATION_LOCK_KEY = 873492875;

const FILE_RE = /^(\d{3})-.*\.sql$/;

function descriptionFromFilename(filename: string): string {
  return filename
    .replace(/^\d{3}-/, '')
    .replace(/\.sql$/, '')
    .replace(/-/g, ' ');
}

export async function runMigrations(pool: Pool): Promise<void> {
  const client = await pool.connect();
  try {
    await client.query('SET search_path TO nebula');
    await client.query(`SELECT pg_advisory_lock(${NEBULA_MIGRATION_LOCK_KEY})`);
    try {
      // Read the ledger WITHOUT creating it first.
      //
      // The reference sketch in 5ab78e30 creates the ledger before reading the
      // version, which would mean NEBULA_MIGRATE_DRY_RUN=1 still writes — it
      // would create the table on a target that has none. A flag whose entire
      // purpose is "apply nothing" must write nothing, so the read tolerates a
      // missing table and the CREATE happens only once we know we are applying.
      // Undefined table is SQLSTATE 42P01.
      let currentVersion = 0;
      try {
        const current = await client.query(
          'SELECT COALESCE(MAX(version), 0) AS v FROM nebula.schema_version'
        );
        currentVersion = Number(current.rows[0].v);
      } catch (err: any) {
        if (err?.code !== '42P01') throw err;
        // No ledger yet: treat as 0 so every numbered file is pending.
      }

      const files = readdirSync(MIGRATIONS_DIR)
        .filter((f) => FILE_RE.test(f))
        .sort();

      const pending = files.filter((f) => {
        const version = migrationVersion(f);
        return version !== null && version > currentVersion;
      });

      // ── Target-identity gate (DBA 5ab78e30) ──────────────────────────
      // Resolved from the pool's effective config, not process.env, so no
      // env-rewrite path can desynchronise the check from the target actually
      // being dialed. Engages only when migrations are pending.
      const identity = resolveMigrateTarget(pool.options ?? {});
      const decision = decideMigrationGate({
        identity,
        pending,
        dryRun: process.env.NEBULA_MIGRATE_DRY_RUN,
        unsafe: process.env.NEBULA_MIGRATE_UNSAFE,
        allowlist: process.env.NEBULA_MIGRATE_TARGET,
      });

      if (decision.action === 'block') {
        // The call site in index.ts already fails closed on throw, so this
        // aborts startup rather than starting against a target this build was
        // not commissioned for.
        throw new Error(decision.message);
      }
      if (decision.action === 'dry-run') {
        console.log(
          `[nebula-migrations] DRY RUN — target ${identity}, ledger at v${currentVersion}. ` +
            `Would apply ${decision.pending.length} file(s), applying nothing: ` +
            `${decision.pending.join(', ')}`
        );
        return;
      }

      // Ensure the ledger exists even if baseline 41 was never applied. Only
      // reached when we are actually going to apply something.
      await client.query(`
        CREATE TABLE IF NOT EXISTS nebula.schema_version (
          version     INTEGER PRIMARY KEY,
          description TEXT NOT NULL,
          applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
      `);

      let applied = 0;
      for (const file of files) {
        const version = migrationVersion(file);
        if (version === null || version <= currentVersion) continue;

        const sql = readFileSync(path.join(MIGRATIONS_DIR, file), 'utf8');
        const description = descriptionFromFilename(file);
        console.log(`[nebula-migrations] applying v${version}: ${description}`);
        await client.query(sql);
        await client.query(
          `INSERT INTO nebula.schema_version (version, description)
           VALUES ($1, $2)
           ON CONFLICT (version) DO NOTHING`,
          [version, description]
        );
        applied++;
        console.log(`[nebula-migrations] v${version} applied`);
      }

      if (applied === 0) {
        console.log(
          `[nebula-migrations] up to date (ledger at v${currentVersion}, no pending files)`
        );
      }
    } finally {
      await client.query(`SELECT pg_advisory_unlock(${NEBULA_MIGRATION_LOCK_KEY})`);
    }
  } finally {
    client.release();
  }
}
