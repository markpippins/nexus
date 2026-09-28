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
import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
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
 * Apply every unapplied migration, in filename order.
 *
 * The schema and ledger bootstrap stay on the pg driver -- they are two plain
 * statements with no psql semantics, and the pool is already open for the
 * already-applied check.
 */
export async function runMigrations({
  dsn = DEFAULT_DSN,
  migrationsDir = DEFAULT_MIGRATIONS_DIR,
  log = console.log,
  spawn = spawnSync,
} = {}) {
  const files = readdirSync(migrationsDir)
    .filter((f) => f.endsWith('.sql'))
    .sort();
  log(`[shrapnel migrate] found ${files.length} migration file(s): ${files.join(', ')}`);

  const { Pool } = pg;
  const pool = new Pool({ connectionString: dsn });
  const applied = [];
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

    for (const file of files) {
      const led = await pool.query(
        `SELECT filename FROM shrapnel._migration_ledger WHERE filename = $1`,
        [file]
      );
      if (led.rowCount > 0) {
        log(`[shrapnel migrate] skip ${file} (already applied)`);
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
    await pool.end();
  }
  log('[shrapnel migrate] done');
  return applied;
}

export async function main() {
  dotenv.config({ path: '../../.env' });
  dotenv.config({ path: '.env' });
  const applied = await runMigrations();
  if (applied.length === 0) log('[shrapnel migrate] nothing to do');
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
