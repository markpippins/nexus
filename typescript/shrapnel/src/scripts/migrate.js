#!/usr/bin/env node
// Idempotently applies every SQL file under ./migrations/*.sql in sorted order.
//
// MIGRATIONS ARE APPLIED WITH psql, NOT THE pg DRIVER. Three files in this
// directory cannot go through pool.query, and two of them are on the main chain:
//
//   1. 0007_work_request_stereotypes.sql uses `\gset` three times. That is a psql
//      meta-command, not SQL; the driver cannot parse it at all ("syntax error at
//      or near \"\\"). \gset is how that file threads the revision id returned by
//      one stereotype_create_revision() call into the next, so the file has no
//      driver-executable rewrite that preserves its semantics.
//   2. 0003_candidate_state_model.sql issues TOP-LEVEL SAVEPOINT /
//      ROLLBACK TO SAVEPOINT, which is illegal outside a transaction block
//      ("SAVEPOINT can only be used in transaction blocks"). The old runner
//      wrapped each file in BEGIN/COMMIT via separate pool.query calls, which
//      works only because the driver happens to hold one session -- but it is
//      fragile, and it breaks the moment a file is applied on its own.
//   3. 0004/0005/0007 wrap themselves in an explicit BEGIN/COMMIT. Under the
//      old runner the inner COMMIT committed the outer transaction early, so
//      the ROLLBACK-on-error path had nothing to roll back (it was swallowed
//      with .catch(() => {})). A failed migration could leave partial DDL
//      applied with no ledger row.
//
// psql is already a dependency of this service: package.json's `dbcheck`
// script shells out to it, and the hermetic tests in test/ build schemas with
// it. This is the same tool, now used by the runner.
//
// ATOMICITY. Each file is applied together with its ledger row in ONE psql
// session under a single transaction (`-1` / --single-transaction,
// ON_ERROR_STOP=1). To make that true, top-level transaction-control lines in
// the file are stripped before it is sent: see stripOuterTransactionControl().
// A migration therefore either applies completely and gets ledgered, or leaves
// no trace at all.
//
// LIVE-OPS GUARDRAILS (2026-09-29; the manual 0007-0009 titanium apply made
// this layer's absence expensive). The atomic core above is unchanged; what is
// new is fail-closed armor around it:
//
//   --dry-run      read-only plan (no bootstrap writes, no lock, no psql);
//                  exits nonzero via the ahead-refusal when the database is
//                  ahead of the checkout.
//   ahead-refusal  if shrapnel._migration_ledger names files absent from
//                  migrations/, REFUSE (the refresh.sh doctrine: applying an
//                  older chain under a newer catalog re-issues stale bodies —
//                  the 0005 freeze-defect resurrection, demonstrated live).
//   advisory lock  pg_try_advisory_lock keyed per-database, so two operators
//                  (or a timer and a human) cannot interleave applies.
//   pre-apply dump the CLI takes an automatic pg_dump -Fc --schema=shrapnel
//                  before the first mutation (--no-dump to skip; skipped
//                  when nothing would apply or the schema does not exist yet).
//
// Default behavior is unchanged: bare invocation still applies, exactly as the
// attested hermetic suite pins it.
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import dotenv from 'dotenv';
import pg from 'pg';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_MIGRATIONS_DIR = join(__dirname, '..', '..', 'migrations');

const DEFAULT_DSN =
  process.env.SHRAPNEL_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/postgres';

/**
 * Top-level transaction-control lines, as a whole line.
 *
 * The trailing semicolon is load-bearing. Every plpgsql body in this directory
 * opens with a bare `BEGIN` on its own line (0001 has one, 0004 has nine), and
 * those must survive untouched -- dropping them would change a DO block's
 * control flow, not just its wrapping. plpgsql `BEGIN` never carries a
 * semicolon, so requiring one separates the two unambiguously. `COMMIT;` and
 * `ROLLBACK;` are not legal inside a plpgsql body at all, so they are safe to
 * match. `END` is deliberately NOT in this pattern: `END;` is how every DO
 * block closes, and stripping it would delete the block.
 */
const TXN_CONTROL_LINE =
  /^[ \t]*(?:BEGIN|COMMIT|ROLLBACK|START[ \t]+TRANSACTION)[ \t]*;[ \t]*$/i;

/**
 * Remove top-level transaction-control statements from a migration file,
 * returning the rewritten SQL and the number of lines removed.
 *
 * The runner owns the transaction (psql -1); a migration file that also opens
 * and closes one would end the runner's transaction early and take the ledger
 * insert with it. The rewrite is reported to the caller so the log says which
 * files were adjusted rather than transforming them silently.
 */
export function stripOuterTransactionControl(sql) {
  const kept = [];
  let stripped = 0;
  for (const line of sql.split('\n')) {
    if (TXN_CONTROL_LINE.test(line)) {
      stripped += 1;
      continue;
    }
    kept.push(line);
  }
  return { sql: kept.join('\n'), stripped };
}

/**
 * Make a filename safe to mention in a SQL comment. Filenames come from
 * readdirSync, so they cannot contain a path separator, but a newline would let
 * one escape the `--` comment and become SQL. The value that actually reaches
 * the database is the psql variable, not this; this is only so the log header
 * on stderr cannot be turned into a statement.
 */
function safeForComment(text) {
  return String(text).replace(/[\r\n]+/g, ' ');
}

/**
 * Build the single script psql executes for one migration: the migration body
 * followed by its ledger row, so both commit or neither does.
 *
 * The filename is passed as a psql variable and interpolated with :'mig_filename'
 * rather than pasted into the SQL, so quoting is psql's problem and not ours.
 */
export function buildApplyScript(filename, sql) {
  const { sql: body, stripped } = stripOuterTransactionControl(sql);
  const header = [
    `-- shrapnel migrate: applying ${safeForComment(filename)} (transaction owned by psql -1)`,
    stripped > 0
      ? `-- stripped ${stripped} top-level transaction-control line(s) from this file`
      : null,
  ]
    .filter(Boolean)
    .join('\n');

  return {
    script: [
      header,
      body,
      `INSERT INTO shrapnel._migration_ledger (filename) VALUES (:'mig_filename');`,
    ].join('\n'),
    stripped,
  };
}

/**
 * Apply one migration file plus its ledger row in a single psql transaction.
 * Throws on any failure, with psql's own stderr attached -- psql's diagnostics
 * (line numbers in the file) are the useful part and must not be swallowed.
 */
export function applyMigrationFile({ dsn, filename, sql, spawn = spawnSync }) {
  const { script, stripped } = buildApplyScript(filename, sql);
  const result = spawn(
    'psql',
    [
      dsn,
      '-X', // never read ~/.psqlrc: a local alias or setting must not change what a migration means
      '-q', // quiet: no command tags, but errors still go to stderr
      '-v',
      'ON_ERROR_STOP=1', // abort and exit non-zero instead of continuing after an error
      '-1', // wrap the whole session in one transaction
      '-v',
      `mig_filename=${filename}`,
      '-f',
      '-', // read the script from stdin, so the file on disk is never modified
    ],
    { input: script, encoding: 'utf8' }
  );

  if (result.error) {
    const err = new Error(
      `could not run psql (is the PostgreSQL client installed and on PATH?): ${result.error.message}`
    );
    err.cause = result.error;
    throw err;
  }
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || '').trim();
    throw new Error(
      `psql exited ${result.status} applying ${filename}${detail ? `:\n${detail}` : ''}`
    );
  }
  return { stripped };
}

/**
 * Read-only diff of the migrations directory against the database's ledger.
 *
 * Writes NOTHING: on a database with no shrapnel ledger it reports fresh and
 * does not bootstrap (a plan that creates objects is not a plan). This is the
 * function both --dry-run and the CLI's pre-apply dump decision are built on.
 */
export async function planMigrations({
  dsn = DEFAULT_DSN,
  migrationsDir = DEFAULT_MIGRATIONS_DIR,
  log = console.log,
} = {}) {
  const files = readdirSync(migrationsDir)
    .filter((f) => f.endsWith('.sql'))
    .sort();
  const { Pool } = pg;
  const pool = new Pool({ connectionString: dsn });
  try {
    const probe = await pool.query(
      `SELECT to_regclass('shrapnel._migration_ledger') AS reg`
    );
    if (!probe.rows[0].reg) {
      log(
        `[shrapnel migrate] plan: fresh database (no ledger yet) — all ${files.length} file(s) would apply`
      );
      return { fresh: true, files, pending: [...files], ahead: [] };
    }
    const led = await pool.query(`SELECT filename FROM shrapnel._migration_ledger`);
    const ledgerFiles = led.rows.map((r) => r.filename);
    const ledger = new Set(ledgerFiles);
    const onDisk = new Set(files);
    const pending = files.filter((f) => !ledger.has(f));
    const ahead = ledgerFiles.filter((f) => !onDisk.has(f)).sort();
    log(
      `[shrapnel migrate] plan: ${ledgerFiles.length} applied, ${pending.length} pending, ${ahead.length} ahead-of-checkout`
    );
    return { fresh: false, files, pending, ahead };
  } finally {
    await pool.end();
  }
}

/** Per-database advisory-lock key: cluster-wide locks are shared by every
 * database in the cluster, so the key must embed current_database() to keep
 * concurrent throwaway-database tests and unrelated services from colliding. */
const ADVISORY_LOCK_SQL = "pg_try_advisory_lock(hashtext(current_database() || ':shrapnel-migrate'))";
const ADVISORY_UNLOCK_SQL = "pg_advisory_unlock(hashtext(current_database() || ':shrapnel-migrate'))";

/**
 * Apply every unapplied migration, in filename order.
 *
 * The schema and ledger bootstrap stay on the pg driver -- they are two plain
 * statements with no psql semantics, and the pool is already open for the
 * already-applied check.
 *
 * With dryRun: true nothing is written and no lock is taken -- the plan is
 * computed read-only and returned in place of the applied list. In the apply
 * path the run holds a per-database advisory lock (fail-fast, never blocks)
 * so two concurrent invocations cannot interleave.
 */
export async function runMigrations({
  dsn = DEFAULT_DSN,
  migrationsDir = DEFAULT_MIGRATIONS_DIR,
  log = console.log,
  spawn = spawnSync,
  dryRun = false,
} = {}) {
  const plan = await planMigrations({ dsn, migrationsDir, log });

  if (plan.ahead.length > 0) {
    throw new Error(
      `refusing to apply: shrapnel._migration_ledger records ${plan.ahead.length} migration(s) ` +
        `absent from ${migrationsDir} — the database is AHEAD of this checkout: ${plan.ahead.join(', ')}. ` +
        `Applying here would interleave an older chain under a newer catalog and re-issue stale ` +
        `function bodies (the 0005 freeze-defect resurrection). Pull/merge the missing migrations first.`
    );
  }

  if (dryRun) {
    if (plan.pending.length === 0) log('[shrapnel migrate] plan: in sync — nothing to apply');
    else log(`[shrapnel migrate] plan (dry-run, nothing written): ${plan.pending.join(', ')}`);
    return plan.pending;
  }

  const { Pool } = pg;
  const pool = new Pool({ connectionString: dsn });
  const applied = [];
  let lockClient = null;
  try {
    // Ensure a migrations ledger exists (the shrapnel schema itself is created
    // by 0001_init.sql, so bootstrap it first on brand-new databases).
    await pool.query(`CREATE SCHEMA IF NOT EXISTS shrapnel AUTHORIZATION pguser`);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS shrapnel._migration_ledger (
        filename     text PRIMARY KEY,
        applied_at   timestamptz NOT NULL DEFAULT now()
      )
    `);

    lockClient = await pool.connect();
    const lock = await lockClient.query(`SELECT ${ADVISORY_LOCK_SQL} AS ok`);
    if (!lock.rows[0].ok) {
      throw new Error(
        'another migrate run holds the advisory lock for this database — refusing to apply concurrently'
      );
    }

    // The plan was computed before the lock; re-read the ledger now that we
    // hold it, so a run that applied files in that window is skipped rather
    // than colliding with its ledger INSERT.
    const held = await lockClient.query(`SELECT filename FROM shrapnel._migration_ledger`);
    const heldSet = new Set(held.rows.map((r) => r.filename));

    for (const file of plan.pending) {
      if (heldSet.has(file)) {
        log(`[shrapnel migrate] skip ${file} (applied by a concurrent run)`);
        continue;
      }
      const sql = readFileSync(join(migrationsDir, file), 'utf8');
      const { stripped } = applyMigrationFile({ dsn, filename: file, sql, spawn });
      if (stripped > 0) {
        log(
          `[shrapnel migrate] note ${file}: stripped ${stripped} top-level transaction-control line(s); the runner owns the transaction`
        );
      }
      applied.push(file);
      log(`[shrapnel migrate] applied ${file}`);
    }
  } finally {
    if (lockClient) {
      await lockClient.query(`SELECT ${ADVISORY_UNLOCK_SQL}`).catch(() => {});
      lockClient.release();
    }
    await pool.end();
  }
  log('[shrapnel migrate] done');
  return applied;
}

/**
 * Build the pre-apply safety dump command: one custom-format archive of the
 * shrapnel schema, written to dumpDir. Pure — the caller decides when to run
 * it — so tests can assert the shape without touching pg_dump.
 */
export function buildDumpCommand({ dsn, dumpDir, timestamp }) {
  const file = join(dumpDir, `shrapnel-pre-migrate_${timestamp}.dump`);
  return {
    file,
    args: [dsn, '--format=custom', '--schema=shrapnel', `--file=${file}`],
  };
}

export async function main(argv = process.argv.slice(2)) {
  dotenv.config({ path: '../../.env' });
  dotenv.config({ path: '.env' });

  if (argv.includes('--help') || argv.includes('-h')) {
    console.log(
      [
        'usage: node src/scripts/migrate.js [--dry-run] [--no-dump] [--help]',
        '',
        '  (no flags)   apply pending migrations; each migration + its ledger row commit atomically (psql -1)',
        '  --dry-run    read-only plan: what would apply; refuses (exit 1) if the database is ahead of this checkout',
        '  --no-dump    skip the automatic pre-apply pg_dump (schema shrapnel, custom format, system tmp by default)',
        '  --help       this text',
        '',
        'env: SHRAPNEL_PG_DSN (connection string), SHRAPNEL_MIGRATE_DUMP_DIR (pre-apply dump spool)',
      ].join('\n')
    );
    return;
  }

  const dryRun = argv.includes('--dry-run');
  const noDump = argv.includes('--no-dump');

  if (dryRun) {
    await runMigrations({ dryRun: true });
    return;
  }

  // Pre-apply safety dump. Skipped when nothing would apply or the database is
  // brand-new (no shrapnel schema to dump); a FAILED dump refuses the apply —
  // a safety net that cannot fail silently is worth more than one that is
  // always convenient. --no-dump is the explicit operator escape hatch.
  if (!noDump) {
    const plan = await planMigrations({});
    if (plan.pending.length > 0 && !plan.fresh) {
      const dumpDir =
        process.env.SHRAPNEL_MIGRATE_DUMP_DIR || join(tmpdir(), 'shrapnel-migrate-dumps');
      mkdirSync(dumpDir, { recursive: true });
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const { file, args } = buildDumpCommand({ dsn: DEFAULT_DSN, dumpDir, timestamp });
      const res = spawnSync('pg_dump', args, { encoding: 'utf8' });
      if (res.error || res.status !== 0) {
        const detail = res.stderr || (res.error && res.error.message) || '';
        throw new Error(`pre-apply pg_dump failed — refusing to apply (override with --no-dump): ${detail}`);
      }
      console.log(`[shrapnel migrate] pre-apply dump: ${file}`);
    }
  }

  const applied = await runMigrations({});
  // console.log, not a bare `log`: main() has no logger in scope. A
  // ReferenceError here exited 1 AFTER a fully successful no-op apply — the
  // operator-facing exit code contradicted the ledger (caught live in the
  // 2026-09-29 throwaway-DB rehearsal, record 3f5fed68; hermetic tests never
  // saw it because they call runMigrations() directly, bypassing main()).
  if (applied.length === 0) console.log('[shrapnel migrate] nothing to do');
}

// Only run when invoked as the CLI. Importing this module (the tests do) must
// not open a pool or read .env.
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((err) => {
    console.error('[shrapnel migrate]', err.message);
    process.exit(1);
  });
}
