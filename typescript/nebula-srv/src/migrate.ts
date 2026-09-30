import { readdirSync, readFileSync, existsSync } from 'fs';
import { createHash } from 'crypto';
import path from 'path';
import { Pool } from 'pg';
import {
  contentProblems,
  decideMigrationGate,
  migrationVersion,
  resolveMigrateTarget,
  type ContentBinding,
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
 * ## Content binding (Decision 23, ruling `7f2b377a`)
 *
 * The stale-tree incident of 2026-09-29 applied an 068 that was missing the
 * uuid guard, because `MIGRATIONS_DIR` resolves from `__dirname` — the local
 * working tree, whatever revision it happens to be on. Decision 23: bind the
 * CONTENT of each pending file to the committed attestation manifest
 * (`migrations/attestations.json`, sha256 per file). A pending file with no
 * recorded hash (unknown) or with bytes that disagree with the manifest
 * (stale/mutated tree) fails closed at boot.
 *
 * The bytes are read ONCE during pending detection, hashed there, and the same
 * bytes are what the apply loop executes. The runner previously re-read each
 * file inside the apply loop — a TOCTOU window between gate decision and
 * apply that this rewrite closes.
 *
 * `NEBULA_MIGRATE_UNSAFE=1` remains the single documented escape; when it
 * overrides a failed content binding, the override is logged LOUDLY with the
 * offending files named, so an unsafe apply can never pass silently.
 *
 * The gate engages ONLY when something is pending. On a current ledger the boot
 * is byte-for-byte the pre-gate behaviour with no new environment variable —
 * that zero-friction property is what makes the guard adoptable, and it is the
 * property most easily broken by a later edit.
 */

const MIGRATIONS_DIR = path.resolve(__dirname, '..', 'migrations');
/** Display path used in gate messages; resolved against MIGRATIONS_DIR. */
const MANIFEST_DISPLAY = 'migrations/attestations.json';

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

interface PendingRead {
  filename: string;
  version: number;
  bytes: Buffer;
  sha256: string;
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

      // ── Single read: bytes + hash captured together, before any decision ──
      // The gate judges THESE bytes and the apply loop executes THESE bytes;
      // there is no second read for a mutation to slip into.
      const reads: PendingRead[] = files
        .map((f) => {
          const version = migrationVersion(f);
          if (version === null || version <= currentVersion) return null;
          const bytes = readFileSync(path.join(MIGRATIONS_DIR, f));
          return {
            filename: f,
            version,
            bytes,
            sha256: createHash('sha256').update(bytes).digest('hex'),
          } as PendingRead;
        })
        .filter((r): r is PendingRead => r !== null);

      const pending = reads.map((r) => r.filename);

      // Attested hashes. A MISSING manifest file is not fatal here — the gate
      // treats every binding as unknown and blocks with a message naming the
      // manifest path, which is the Decision 23 property-3 behaviour.
      const manifestPath = path.join(MIGRATIONS_DIR, 'attestations.json');
      const manifest: Record<string, string> = existsSync(manifestPath)
        ? JSON.parse(readFileSync(manifestPath, 'utf8'))
        : {};

      const content: ContentBinding[] = reads.map((r) => ({
        file: r.filename,
        expected: manifest[r.filename],
        found: r.sha256,
      }));

      // ── Gates (target-identity + content binding, Decision 16 + 23) ─────
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
        content,
        manifestPath: MANIFEST_DISPLAY,
      });

      if (decision.action === 'block') {
        // The call site in index.ts already fails closed on throw, so this
        // aborts startup rather than starting against a target this build was
        // not commissioned for, or applying bytes that were never attested.
        throw new Error(decision.message);
      }
      if (decision.action === 'dry-run') {
        console.log(
          `[nebula-migrations] DRY RUN — target ${identity}, ledger at v${currentVersion}. ` +
            `Would apply ${decision.pending.length} file(s), applying nothing: ` +
            `${decision.pending.join(', ')}`
        );
        if (decision.contentProblems.length > 0) {
          console.log(
            `[nebula-migrations] DRY RUN content problems (would BLOCK a real apply):\n  ` +
              decision.contentProblems.join('\n  ')
          );
        }
        return;
      }

      if (decision.contentOverridden) {
        const { unknown, mismatched } = contentProblems(content, MANIFEST_DISPLAY);
        const names = [...unknown, ...mismatched.map((m) => m.file)];
        console.log(
          `[nebula-migrations] WARNING: NEBULA_MIGRATE_UNSAFE=1 overrode the ` +
            `attested-content binding for: ${names.join(', ')}. The bytes about to be ` +
            `applied are NOT the attested bytes. This override is for scratch targets only.`
        );
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
      for (const read of reads) {
        const description = descriptionFromFilename(read.filename);
        console.log(`[nebula-migrations] applying v${read.version}: ${description}`);
        // The exact bytes the gate hashed — no re-read. The Buffer is decoded
        // as UTF-8 for the simple query protocol (migration files are UTF-8
        // SQL text); the hash binds the raw bytes, this sends those bytes.
        await client.query(read.bytes.toString('utf8'));
        await client.query(
          `INSERT INTO nebula.schema_version (version, description)
           VALUES ($1, $2)
           ON CONFLICT (version) DO NOTHING`,
          [read.version, description]
        );
        // Record the applied content hash (Decision 23 property 2).
        //
        // CAUTION: the column-existence guard CANNOT live in the SQL — PG
        // resolves the SET target at parse time, so `SET content_hash` fails
        // with 42703 even when a WHERE clause would filter it out. (Found by
        // CI: seeding at ledger 67 makes 068 AND 069 pending; after applying
        // 068 the stamp crashed and 069 — the migration that CREATES the
        // column — could never be reached.) So: try the stamp, tolerate
        // 42703 as "column not there yet" (069 pending or pre-069 chain),
        // and let the documented backfill cover those rows.
        try {
          await client.query(
            `UPDATE nebula.schema_version SET content_hash = $1 WHERE version = $2`,
            [read.sha256, read.version]
          );
        } catch (err: any) {
          if (err?.code !== '42703') throw err;
          console.log(
            `[nebula-migrations] content_hash column not present yet — ` +
              `v${read.version} hash not stamped (backfill covers pre-069 rows)`
          );
        }
        applied++;
        console.log(`[nebula-migrations] v${read.version} applied`);
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
