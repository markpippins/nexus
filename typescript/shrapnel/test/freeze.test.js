/**
 * Real-DB checks for the stereotype_field freeze fix (migration 0009).
 *
 * Drives test/freeze_subtransaction_0009.sql against a real PostgreSQL. The SQL
 * is the substance; this file is only the harness, so the checks run under
 * `npm test` rather than by hand.
 *
 * WHY REAL-DB
 * The whole bug is about PostgreSQL transaction and subtransaction semantics.
 * A mock cannot express "a row written in a subtransaction carries the
 * subtransaction's xid", which is the entire mechanism. There is nothing
 * meaningful to assert without a real engine.
 *
 * WHY THE APPEND-ONLY ASSERTIONS MATTER MOST
 * The fix makes a trigger accept more. A trigger that accepted everything would
 * make every "the defect is fixed" assertion pass. The suite is therefore
 * ordered so the freeze-still-holds checks are the ones that would catch an
 * over-correction, and the harness asserts a minimum number of checks ran so a
 * vacuous pass cannot masquerade as a green suite.
 *
 * SKIPS
 * Without SHRAPNEL_PG_DSN the suite skips with an explicit reason rather than
 * passing silently.
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
const checkScript = join(__dirname, 'freeze_subtransaction_0009.sql');

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
  ? 'SHRAPNEL_PG_DSN is not set — the 0009 freeze checks require a real PostgreSQL'
  : !psqlAvailable()
    ? 'psql client is not available on PATH'
    : null;

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

describe('0009 stereotype_field freeze (real database)', { skip: skipReason ?? false }, () => {
  let output = '';
  let migrationFiles = [];

  before(() => {
    migrationFiles = applyMigrations();
    output = runChecks();
  });

  it('applies every migration cleanly, including 0009', () => {
    assert.ok(
      migrationFiles.includes('0009_stereotype_field_freeze_subtransaction.sql'),
      '0009_stereotype_field_freeze_subtransaction.sql must be present in migrations/'
    );
  });

  it('exposes the named freeze rule', () => {
    const out = execFileSync(
      'psql',
      [dsn, '-A', '-t', '-c',
        `SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')=' ||
                pg_get_function_result(p.oid)
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'shrapnel'
           AND p.proname IN ('stereotype_revision_under_construction',
                             'forbid_stereotype_field_mutation')
         ORDER BY p.proname;`],
      { encoding: 'utf8' }
    ).trim().split('\n');

    assert.ok(
      out.includes('stereotype_revision_under_construction(p_revision_id bigint)=boolean'),
      `stereotype_revision_under_construction missing or wrong:\n${out.join('\n')}`
    );
    assert.ok(
      out.includes('forbid_stereotype_field_mutation()=trigger'),
      `forbid_stereotype_field_mutation missing or wrong:\n${out.join('\n')}`
    );
  });

  it('reports zero failing checks', () => {
    const failures = output.split('\n').filter((l) => l.startsWith('NOT OK - '));
    assert.deepEqual(
      failures,
      [],
      `freeze checks failed:\n${failures.join('\n')}\n\nfull output:\n${output}`
    );
  });

  it('actually ran the checks (guards against a vacuous pass)', () => {
    const passes = output.split('\n').filter((l) => l.startsWith('ok - ')).length;
    assert.ok(
      passes >= 14,
      `expected at least 14 passing checks, saw ${passes}. A drop here means assertions ` +
      'are silently vanishing — most likely a NULL verdict hitting the tap.ok NOT NULL ' +
      'constraint, which deletes the row instead of reporting a failure.'
    );
  });

  it('still refuses to mutate a committed revision (the freeze is not weakened)', () => {
    // Asserted by name, not just by the aggregate count, because this is the
    // property an over-eager "fix" would break.
    for (const mustHold of [
      'committed revision: required-flag flip still REJECTED',
      'committed revision: field-row DELETE still REJECTED',
      'committed revision: post-contract-growth INSERT still REJECTED',
      'a COMMITTED subtransaction-made revision is frozen like any other',
    ]) {
      const line = output.split('\n').find((l) => l.includes(mustHold));
      assert.ok(
        line && line.startsWith('ok - '),
        `freeze no longer holds — expected an "ok -" line for: ${mustHold}\n\n${output}`
      );
    }
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
