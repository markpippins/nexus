/**
 * Real-DB checks for shrapnel.stereotype_reconcile (migration 0008).
 *
 * Drives test/reconcile_0008_stereotype_reconcile.sql against a real
 * PostgreSQL. The SQL is the substance of the test; this file is only the
 * harness, and it exists so the checks run under `npm test` rather than only by
 * hand.
 *
 * WHY REAL-DB AND NOT MOCKED
 * The failure mode that matters for a reconciliation verb is silence. The
 * no-op branch writes no rows, so NONE of the deferred constraint triggers
 * (fingerprint, superset-v2, C1 depth, field integrity) fire to check it — the
 * entire safety apparatus is silent on exactly the branch most likely to be
 * wrong. A hermetic test of that comparison proves very little.
 *
 * SKIPS
 * When SHRAPNEL_PG_DSN is unset the suite skips with an explicit reason
 * rather than silently passing. A skip that says why is information; a skip
 * that looks like a pass is not.
 */
import { describe, it, before } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgRoot = join(__dirname, '..');
const migrationsDir = join(pkgRoot, 'migrations');
const checkScript = join(__dirname, 'reconcile_0008_stereotype_reconcile.sql');

const dsn = process.env.SHRAPNEL_PG_DSN || '';

function psqlAvailable() {
  try {
    execFileSync('psql', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

const skipReason = !dsn
  ? 'SHRAPNEL_PG_DSN is not set — the 0008 reconcile checks require a real PostgreSQL'
  : !psqlAvailable()
    ? 'psql client is not available on PATH'
    : null;

/** Apply every migration in sorted order, each inside its own transaction —
 *  the same per-file transaction src/scripts/migrate.js uses. */
function applyMigrations() {
  const files = readdirSync(migrationsDir).filter((f) => f.endsWith('.sql')).sort();
  execFileSync(
    'psql',
    [dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-c',
      'DROP SCHEMA IF EXISTS shrapnel CASCADE; CREATE SCHEMA shrapnel AUTHORIZATION pguser;'],
    { stdio: 'pipe' }
  );
  for (const file of files) {
    const sql = readFileSync(join(migrationsDir, file), 'utf8');
    execFileSync('psql', [dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-f', '-'], {
      input: `BEGIN;\n${sql}\nCOMMIT;\n`,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  }
  return files;
}

function runChecks() {
  return execFileSync(
    'psql',
    [dsn, '-v', 'ON_ERROR_STOP=0', '-A', '-t', '-f', checkScript],
    { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
  );
}

describe('0008 stereotype_reconcile (real database)', { skip: skipReason ?? false }, () => {
  let output = '';
  let migrationFiles = [];

  before(() => {
    migrationFiles = applyMigrations();
    output = runChecks();
  });

  it('applies every migration 0001-0008 cleanly', () => {
    assert.ok(
      migrationFiles.includes('0008_stereotype_reconcile.sql'),
      '0008_stereotype_reconcile.sql must be present in migrations/'
    );
    assert.equal(migrationFiles.length, 8, `expected 8 migrations, found ${migrationFiles.length}`);
  });

  it('exposes stereotype_reconcile and the shared field-document helper', () => {
    const out = execFileSync(
      'psql',
      [dsn, '-A', '-t', '-c',
        `SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')=' ||
                pg_get_function_result(p.oid)
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'shrapnel'
           AND p.proname IN ('stereotype_reconcile','stereotype_fields_document_for',
                             'stereotype_create_revision','stereotype_canonical_contract')
         ORDER BY p.proname;`],
      { encoding: 'utf8' }
    ).trim().split('\n');

    assert.ok(
      out.includes('stereotype_reconcile(p_name text, p_extends_revision bigint, ' +
        'p_rationale text, p_required_fields text[], p_optional_fields text[])' +
        '=TABLE(revision_id bigint, created boolean)'),
      `stereotype_reconcile has the wrong signature:\n${out.join('\n')}`
    );

    // The reconcile verdict and the create write must resolve fields through
    // the SAME helper, or a no-op can be decided against different inputs than
    // the ones that would have been written.
    assert.ok(
      out.includes('stereotype_fields_document_for(p_required_fields text[], ' +
        'p_optional_fields text[], p_error_prefix text)=jsonb'),
      `stereotype_fields_document_for has the wrong signature:\n${out.join('\n')}`
    );

    // create_revision must keep its exact prior signature — the whole reason
    // reconcile is a separate verb rather than a flag on this one.
    assert.ok(
      out.includes('stereotype_create_revision(p_name text, p_extends_revision bigint, ' +
        'p_rationale text, p_required_fields text[], p_optional_fields text[])=bigint'),
      `stereotype_create_revision signature changed:\n${out.join('\n')}`
    );
  });

  it('reports zero failing checks', () => {
    const failures = output.split('\n').filter((l) => l.startsWith('NOT OK - '));
    assert.deepEqual(
      failures,
      [],
      `reconcile checks failed:\n${failures.join('\n')}\n\nfull output:\n${output}`
    );
  });

  it('actually ran the checks (guards against a vacuous pass)', () => {
    const passes = output.split('\n').filter((l) => l.startsWith('ok - ')).length;
    assert.ok(
      passes >= 30,
      `expected at least 30 passing checks, saw ${passes}. A vacuous pass here would ` +
      'mean the checks silently stopped running — the exact failure mode this suite exists to catch.'
    );
  });

  it('ends with a summary line showing 0 failures', () => {
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
