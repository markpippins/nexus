/**
 * Hermetic real-database checks for the stereotype lifecycle verb (0010).
 *
 * Creates a throwaway database, applies the service's own migration chain to
 * it, drives test/lifecycle_0010_stereotype_lifecycle.sql, and drops the
 * database. The live `nexus` database is never touched, and — unlike a suite
 * that reuses a shared DSN and DROPs the `shrapnel` schema — two test files
 * cannot race each other's schema.
 *
 * WHY REAL-DB AND NOT MOCKED
 * Retirement is enforced by database triggers and CHECK constraints, not by
 * application code: the live-parent refusal, the append-only log, the pinned
 * head, and the ownership table only exist inside PostgreSQL. A mocked test
 * would prove that the JavaScript called a function, not that the catalog
 * refused a destructive write. The failure mode that matters is a silent
 * resurrection or a partial retirement — neither is observable off-database.
 *
 * Run:
 *   SHRAPNEL_TEST_ADMIN_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
 *     node --test test/lifecycle.test.js
 *
 * Skips itself (with a reason) only when the admin DSN cannot create a
 * database, so `npm test` stays useful on a host without that privilege.
 */
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync, execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import pg from 'pg';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVICE_ROOT = join(__dirname, '..');
const MIGRATIONS_DIR = join(SERVICE_ROOT, 'migrations');
const CHECK_SCRIPT = join(__dirname, 'lifecycle_0010_stereotype_lifecycle.sql');

const ADMIN_DSN =
  process.env.SHRAPNEL_TEST_ADMIN_DSN ||
  process.env.SHRAPNEL_PG_DSN ||
  'postgresql://pguser:pgpass@localhost:5432/postgres';

const dsnFor = (dbname) => ADMIN_DSN.replace(/\/[^/]*$/, `/${dbname}`);

let admin;
let dbName;
let skipReason = null;
let output = '';
let migrationFiles = [];

/**
 * Apply the service's own migration chain with psql, not the pg driver:
 * 0007 uses `\gset`, a psql meta-command the driver cannot parse. `-1`
 * supplies the outer transaction, so 0003's top-level SAVEPOINT is legal.
 */
function buildSchema(dbname) {
  const script = join(tmpdir(), `lifecycle-build-${dbname}.sql`);
  writeFileSync(
    script,
    [
      'CREATE SCHEMA IF NOT EXISTS shrapnel AUTHORIZATION pguser;',
      ...migrationFiles.map((f) => readFileSync(join(MIGRATIONS_DIR, f), 'utf8')),
    ].join('\n')
  );
  try {
    const psql = spawnSync(
      'psql',
      [dsnFor(dbname), '-q', '-v', 'ON_ERROR_STOP=1', '-1', '-f', script],
      { encoding: 'utf8' }
    );
    if (psql.status !== 0) {
      throw new Error(
        `schema build failed (psql exit ${psql.status}):\n${psql.stderr || psql.stdout}`
      );
    }
  } finally {
    rmSync(script, { force: true });
  }
}

function runChecks(dbname) {
  return execFileSync(
    'psql',
    [dsnFor(dbname), '-v', 'ON_ERROR_STOP=0', '-A', '-t', '-f', CHECK_SCRIPT],
    { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
  );
}

before(async () => {
  migrationFiles = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith('.sql')).sort();
  dbName = `nexus_lifecycle_test_${process.pid}_${randomUUID().slice(0, 6)}`;
  try {
    admin = new pg.Client({ connectionString: dsnFor('postgres') });
    await admin.connect();
    await admin.query(`CREATE DATABASE "${dbName}"`);
  } catch (err) {
    skipReason = `cannot create a throwaway database (${err.message})`;
    if (admin) await admin.end().catch(() => {});
    return;
  }
  // A broken schema build or check run is a real failure, not an
  // environmental skip: swallowing it would let a green run hide a broken
  // harness. Let it reject the hook.
  buildSchema(dbName);
  output = runChecks(dbName);
});

after(async () => {
  if (!admin) return;
  if (dbName) {
    await admin.query(`DROP DATABASE IF EXISTS "${dbName}" WITH (FORCE)`).catch(() => {});
  }
  await admin.end().catch(() => {});
});

describe('0010 stereotype lifecycle (hermetic real database)', () => {
  it('applies the whole migration chain including 0010 cleanly', (t) => {
    if (skipReason) return t.skip(skipReason);
    assert.ok(
      migrationFiles.includes('0010_stereotype_lifecycle.sql'),
      '0010_stereotype_lifecycle.sql must be present in migrations/'
    );
    // The retire verb is the last migration today, but later migrations will
    // follow it. Assert presence and ordering, never an exact count.
    assert.ok(
      migrationFiles.length >= 10,
      `expected at least 10 migrations, found ${migrationFiles.length}`
    );
    assert.equal(
      migrationFiles[migrationFiles.length - 1],
      '0010_stereotype_lifecycle.sql',
      'the lifecycle verb must be the highest-numbered migration until another lands'
    );
  });

  it('exposes the lifecycle verb, ownership table and append-only log', (t) => {
    if (skipReason) return t.skip(skipReason);
    const out = execFileSync(
      'psql',
      [dsnFor(dbName), '-A', '-t', '-c',
        `SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')=' ||
                pg_get_function_result(p.oid)
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'shrapnel'
           AND p.proname IN ('stereotype_lifecycle_plan','stereotype_lifecycle_report',
                             'stereotype_lifecycle_apply',
                             'stereotype_current_state','stereotype_claim_ownership',
                             'stereotype_reconcile_owned')
         ORDER BY p.proname;`],
      { encoding: 'utf8' }
    ).trim().split('\n');

    const expected = [
      'stereotype_lifecycle_plan(p_source text, p_declared text[], p_retired text[], ' +
        'p_deprecated text[], p_reasons jsonb)=TABLE(name text, current_state text, ' +
        'action text, reason text, blocked_by text, head_revision_id bigint, plan_token text)',
      'stereotype_lifecycle_report(p_source text, p_declared text[], p_retired text[], ' +
        'p_deprecated text[], p_reasons jsonb)=TABLE(name text, current_state text, ' +
        'action text, reason text, blocked_by text, head_revision_id bigint, plan_token text)',
      'stereotype_lifecycle_apply(p_source text, p_plan_token text, p_declared text[], ' +
        'p_retired text[], p_deprecated text[], p_reasons jsonb)=TABLE(name text, ' +
        'from_state text, to_state text)',
      'stereotype_current_state(p_stereotype_id bigint)=text',
      'stereotype_claim_ownership(p_source text, p_name text)=bigint',
      'stereotype_reconcile_owned(p_source text, p_name text, p_extends_revision bigint, ' +
        'p_rationale text, p_required_fields text[], p_optional_fields text[])=TABLE(revision_id bigint, created boolean)',
    ];
    for (const sig of expected) {
      assert.ok(
        out.includes(sig),
        `missing lifecycle signature:\n  ${sig}\nfound:\n${out.join('\n')}`
      );
    }

    const tables = execFileSync(
      'psql',
      [dsnFor(dbName), '-A', '-t', '-c',
        `SELECT count(*) FROM information_schema.tables
          WHERE table_schema = 'shrapnel'
            AND table_name IN ('stereotype_lifecycle_event','stereotype_source_owner');`],
      { encoding: 'utf8' }
    ).trim();
    assert.equal(tables, '2', 'the lifecycle log and ownership table must exist');
  });

  it('reports zero failing checks', (t) => {
    if (skipReason) return t.skip(skipReason);
    const failures = output.split('\n').filter((l) => l.startsWith('NOT OK - '));
    assert.deepEqual(
      failures,
      [],
      `lifecycle checks failed:\n${failures.join('\n')}\n\nfull output:\n${output}`
    );
  });

  it('actually ran the checks (guards against a vacuous pass)', (t) => {
    if (skipReason) return t.skip(skipReason);
    const passes = output.split('\n').filter((l) => l.startsWith('ok - ')).length;
    assert.ok(
      passes >= 40,
      `expected at least 40 passing checks, saw ${passes}. A vacuous pass here would ` +
      'mean the checks silently stopped running — the exact failure mode this suite exists to catch.'
    );
  });

  it('ends with a summary line showing 0 failures', (t) => {
    if (skipReason) return t.skip(skipReason);
    const summary = output
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => /^\d+\|\d+\|\d+$/.test(l))
      .pop();
    assert.ok(summary, `no "total|passed|failed" summary line found in:\n${output}`);
    const [total, passed, failed] = summary.split('|').map(Number);
    assert.equal(failed, 0, `expected 0 failures, summary said ${summary}`);
    assert.equal(total, passed, `expected passed == total, summary said ${summary}`);
  });
});
