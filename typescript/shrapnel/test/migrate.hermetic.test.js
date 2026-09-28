// Hermetic tests for the psql-backed migration runner.
//
// The defect these exist to prevent: the runner used to apply migration files
// with pool.query, which cannot execute two files that ARE on the main chain --
//
//   * 0007_work_request_stereotypes.sql uses `\gset`, a psql meta-command. The
//     driver cannot parse it, and \gset is how that file threads one revision
//     id into the next, so there is no faithful driver-only rewrite.
//   * 0003_candidate_state_model.sql issues top-level SAVEPOINT, illegal
//     outside a transaction block.
//   * 0004/0005/0007 each carry their own BEGIN/COMMIT, which under the old
//     runner committed the transaction early and left the ROLLBACK-on-error
//     path with nothing to roll back.
//
// So: build a throwaway database, run the real runner against it, and assert
// the chain applies, is idempotent, and is atomic when a migration fails. The
// live `nexus` database is never touched. Order-independent: each test owns its
// own database.
//
// Run: SHRAPNEL_TEST_ADMIN_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
//        node --test test/migrate.hermetic.test.js
//
// Skips itself (rather than failing) when the admin DSN cannot create a
// database, so `npm test` stays useful on a host without that privilege.

import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import pg from 'pg';

import {
  applyMigrationFile,
  buildApplyScript,
  runMigrations,
  stripOuterTransactionControl,
} from '../src/scripts/migrate.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVICE_ROOT = join(__dirname, '..');
const MIGRATIONS_DIR = join(SERVICE_ROOT, 'migrations');

const ADMIN_DSN =
  process.env.SHRAPNEL_TEST_ADMIN_DSN ||
  process.env.SHRAPNEL_PG_DSN ||
  'postgresql://pguser:pgpass@localhost:5432/postgres';

const dsnFor = (dbname) => ADMIN_DSN.replace(/\/[^/]*$/, `/${dbname}`);
const quiet = () => {};

/** Files the real chain is expected to end up ledgered, in order. */
const REAL_CHAIN = readdirSync(MIGRATIONS_DIR)
  .filter((f) => f.endsWith('.sql'))
  .sort();

/** Can we create a throwaway database? If not, every hermetic test skips. */
let canCreateDb = false;
{
  const probe = new pg.Client({ connectionString: ADMIN_DSN });
  try {
    await probe.connect();
    const r = await probe.query('SELECT 1');
    canCreateDb = r.rowCount === 1;
  } catch {
    canCreateDb = false;
  } finally {
    await probe.end().catch(() => {});
  }
}

const databases = [];
const clients = [];
async function freshDb() {
  const dbname = `shrapnel_migrate_test_${randomUUID().replace(/-/g, '').slice(0, 12)}`;
  const admin = new pg.Client({ connectionString: ADMIN_DSN });
  await admin.connect();
  await admin.query(`CREATE DATABASE ${dbname}`);
  await admin.end();
  databases.push(dbname);
  return dsnFor(dbname);
}

after(async () => {
  // Close every client first: a client still attached to a database blocks the
  // DROP, and node:test surfaces that as a stray uncaughtException.
  for (const c of clients.splice(0)) await c.end();
  for (const dbname of databases.splice(0)) {
    const admin = new pg.Client({ connectionString: ADMIN_DSN });
    try {
      await admin.connect();
      await admin.query(`DROP DATABASE IF EXISTS ${dbname} WITH (FORCE)`);
    } catch {
      // Best-effort: a leaked throwaway database is noise, not a test failure.
    } finally {
      await admin.end().catch(() => {});
    }
  }
});

const connect = (dsn) => {
  const client = new pg.Client({ connectionString: dsn });
  clients.push(client);
  return {
    client,
    query: async (...args) => {
      if (!client._connected) await client.connect();
      return client.query(...args);
    },
    end: () => client.end().catch(() => {}),
  };
};

// ---------------------------------------------------------------------------
// Unit: transaction-control stripping.
//
// The regex has one job and one trap. plpgsql bodies in this directory open
// with a bare `BEGIN` on its own line (0004 has nine of them) and close with
// `END;`. Stripping either would silently change a DO block rather than the
// migration's wrapping.
// ---------------------------------------------------------------------------

describe('stripOuterTransactionControl', () => {
  it('removes top-level BEGIN; and COMMIT; and reports the count', () => {
    const { sql, stripped } = stripOuterTransactionControl(
      ['BEGIN;', 'SELECT 1;', 'COMMIT;', ''].join('\n')
    );
    assert.equal(stripped, 2);
    assert.equal(sql, ['SELECT 1;', ''].join('\n'));
  });

  it('keeps a bare plpgsql BEGIN -- no semicolon, so it is not transaction control', () => {
    const body = ['DO $$', 'DECLARE n int;', 'BEGIN', '  n := 1;', 'END $$;'].join('\n');
    const { sql, stripped } = stripOuterTransactionControl(body);
    assert.equal(stripped, 0);
    assert.equal(sql, body);
  });

  it('keeps END; -- it closes a DO block, it does not end a transaction', () => {
    const body = ['DO $$', 'BEGIN', '  RAISE NOTICE \'hi\';', 'END $$;'].join('\n');
    const { stripped, sql } = stripOuterTransactionControl(body);
    assert.equal(stripped, 0);
    assert.equal(sql, body);
  });

  it('does not touch a transaction-control word mid-line', () => {
    const body = "SELECT 'BEGIN;' AS not_a_command, 'COMMIT' AS also_not;";
    const { sql, stripped } = stripOuterTransactionControl(body);
    assert.equal(stripped, 0);
    assert.equal(sql, body);
  });

  it('handles SAVEPOINT, which is legal only because psql -1 opened the transaction', () => {
    const { sql, stripped } = stripOuterTransactionControl(
      ['SAVEPOINT sp;', 'SELECT 1;', 'ROLLBACK TO SAVEPOINT sp;'].join('\n')
    );
    assert.equal(stripped, 0, 'savepoints are not transaction control and must survive');
    assert.equal(sql, 'SAVEPOINT sp;\nSELECT 1;\nROLLBACK TO SAVEPOINT sp;');
  });

  it('strips the real chain files that wrap themselves', () => {
    // 0004, 0005 and 0007 each open and close their own transaction.
    for (const f of ['0004_stereotype_model.sql', '0005_stereotype_api.sql', '0007_work_request_stereotypes.sql']) {
      const raw = readFileSync(join(MIGRATIONS_DIR, f), 'utf8');
      const { stripped } = stripOuterTransactionControl(raw);
      assert.equal(stripped, 2, `${f} should lose exactly its BEGIN; and COMMIT;`);
    }
  });

  it('leaves the rest of the chain alone -- 0003 needs its savepoints and has no BEGIN/COMMIT', () => {
    const raw = readFileSync(join(MIGRATIONS_DIR, '0003_candidate_state_model.sql'), 'utf8');
    const { sql, stripped } = stripOuterTransactionControl(raw);
    assert.equal(stripped, 0);
    assert.equal(sql, raw, '0003 is passed to psql byte-for-byte');
  });
});

// ---------------------------------------------------------------------------
// Unit: script construction.
// ---------------------------------------------------------------------------

describe('buildApplyScript', () => {
  it('puts the ledger insert in the same session as the migration', () => {
    const { script } = buildApplyScript('0001_x.sql', 'SELECT 1;');
    assert.match(script, /SELECT 1;/);
    assert.match(
      script,
      /INSERT INTO shrapnel\._migration_ledger \(filename\) VALUES \(:'mig_filename'\);/
    );
  });

  it('passes the filename as a psql variable rather than pasting it into SQL', () => {
    const { script } = buildApplyScript('0001_x.sql', 'SELECT 1;');
    assert.ok(!script.includes("'0001_x.sql'"), 'the filename is never a SQL string literal');
    assert.match(script, /VALUES \(:'mig_filename'\);/);
  });

  it('cannot be talked into emitting a statement through the filename', () => {
    // A newline in a filename must not let it escape the -- header comment and
    // become SQL. readdirSync cannot produce this today; the runner should not
    // depend on that staying true.
    const hostile = "x'\nDROP TABLE shrapnel.stereotype; --.sql";
    const { script } = buildApplyScript(hostile, 'SELECT 1;');
    const body = script.split('\n').filter((l) => !l.startsWith('--'));
    assert.deepEqual(
      body,
      ['SELECT 1;', "INSERT INTO shrapnel._migration_ledger (filename) VALUES (:'mig_filename');"],
      'only the migration body and the ledger insert may be executable lines'
    );
  });

  it('says in the header when it stripped lines, rather than transforming silently', () => {
    const { script, stripped } = buildApplyScript('0001_x.sql', 'BEGIN;\nSELECT 1;\nCOMMIT;');
    assert.equal(stripped, 2);
    assert.match(script, /stripped 2 top-level transaction-control line/);
  });
});

describe('applyMigrationFile error handling', () => {
  it('explains a missing psql instead of surfacing a bare ENOENT', () => {
    const spawn = () => ({ error: Object.assign(new Error('spawnSync psql ENOENT'), { code: 'ENOENT' }) });
    assert.throws(
      () => applyMigrationFile({ dsn: 'x', filename: '0001_x.sql', sql: 'SELECT 1;', spawn }),
      /PostgreSQL client installed and on PATH/
    );
  });

  it('keeps psql stderr in the failure message -- the line numbers are the point', () => {
    const spawn = () => ({ status: 3, stdout: '', stderr: 'psql:7: ERROR:  syntax error' });
    assert.throws(
      () => applyMigrationFile({ dsn: 'x', filename: '0007_x.sql', sql: 'SELECT 1;', spawn }),
      /psql exited 3 applying 0007_x\.sql:[\s\S]*syntax error/
    );
  });
});

// ---------------------------------------------------------------------------
// Hermetic: the real chain, on a throwaway database.
// ---------------------------------------------------------------------------

describe('migration runner (hermetic)', { skip: canCreateDb ? false : 'cannot create a database' }, () => {
  it('applies the whole chain, 0007 included, where the driver used to fail', async () => {
    const dsn = await freshDb();
    const applied = await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });

    assert.deepEqual(applied, REAL_CHAIN);
    assert.ok(
      applied.includes('0007_work_request_stereotypes.sql'),
      '0007 is the file the pg driver could not parse at all'
    );

    const c = connect(dsn);
    const ledger = await c.query('SELECT filename FROM shrapnel._migration_ledger ORDER BY filename');
    assert.deepEqual(
      ledger.rows.map((r) => r.filename),
      REAL_CHAIN
    );
    await c.end();
  });

  it('leaves the 0007 work_request family actually resolvable, not merely ledgered', async () => {
    const dsn = await freshDb();
    await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });

    const c = connect(dsn);
    // These mirror 0007's own post-condition block. If \gset had silently failed
    // to thread the revision ids, the chain would not resolve.
    const head = await c.query("SELECT head_revision_id FROM shrapnel.stereotype_resolve('work_request')");
    assert.ok(head.rows[0].head_revision_id, 'work_request must resolve to a head revision');

    const leaf = await c.query(
      "SELECT head_revision_id FROM shrapnel.stereotype_resolve('tester_attestation_request')"
    );
    const leafId = leaf.rows[0].head_revision_id;
    assert.ok(leafId, 'tester_attestation_request must resolve to a head revision');

    const chain = await c.query('SELECT count(*)::int AS hops FROM shrapnel.stereotype_chain($1)', [leafId]);
    assert.equal(chain.rows[0].hops, 3, 'leaf -> dispatch -> base is 3 hops');

    const effective = await c.query(
      'SELECT count(*)::int AS n FROM shrapnel.stereotype_effective_contract($1) WHERE required',
      [leafId]
    );
    assert.equal(effective.rows[0].n, 12, 'the superset doctrine gives the leaf 12 required fields');

    const extendsBase = await c.query(
      'SELECT shrapnel.stereotype_extends($1, $2) AS yes',
      [leafId, 'work_request']
    );
    assert.equal(extendsBase.rows[0].yes, true);
    await c.end();
  });

  it('is idempotent: a second run applies nothing and changes nothing', async () => {
    const dsn = await freshDb();
    await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    const second = await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    assert.deepEqual(second, [], 'every file is already ledgered');

    const c = connect(dsn);
    const { rows } = await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger');
    assert.equal(rows[0].n, REAL_CHAIN.length, 'no duplicate ledger rows');
    await c.end();
  });

  it('rolls a failed migration back completely -- no partial DDL, no ledger row', async () => {
    const dsn = await freshDb();

    // The real chain, plus a migration that creates a table and then fails.
    const dir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-'));
    cpSync(MIGRATIONS_DIR, dir, { recursive: true });
    writeFileSync(
      join(dir, '0008_broken.sql'),
      [
        'BEGIN;',
        'CREATE TABLE shrapnel.must_not_survive (id int);',
        "SELECT 1/0;",
        'COMMIT;',
        '',
      ].join('\n')
    );

    try {
      await assert.rejects(
        runMigrations({ dsn, migrationsDir: dir, log: quiet }),
        /0008_broken\.sql|division by zero/
      );

      const c = connect(dsn);
      // The property the old runner lost: the inner COMMIT ended the outer
      // transaction, so its ROLLBACK had nothing to roll back.
      const table = await c.query(
        "SELECT to_regclass('shrapnel.must_not_survive') AS reg"
      );
      assert.equal(table.rows[0].reg, null, 'partial DDL from a failed migration must not survive');

      const led = await c.query(
        "SELECT count(*)::int AS n FROM shrapnel._migration_ledger WHERE filename = '0008_broken.sql'"
      );
      assert.equal(led.rows[0].n, 0, 'a failed migration must not be ledgered');

      // ...and the migrations before it are still applied and still ledgered.
      const ok = await c.query(
        "SELECT count(*)::int AS n FROM shrapnel._migration_ledger WHERE filename >= '0001' AND filename < '0008'"
      );
      assert.equal(ok.rows[0].n, REAL_CHAIN.length);
      await c.end();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('surfaces psql diagnostics with the filename, so an operator knows where to look', async () => {
    const dsn = await freshDb();
    const dir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-err-'));
    writeFileSync(join(dir, '0001_bad.sql'), 'SELECT definitely_not_a_function_xyz();\n');
    try {
      await assert.rejects(
        runMigrations({ dsn, migrationsDir: dir, log: quiet }),
        (err) => {
          assert.match(err.message, /0001_bad\.sql/);
          assert.match(err.message, /definitely_not_a_function_xyz/, "psql's own text is preserved");
          return true;
        }
      );
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('works through the npm script, which is how an operator runs it', async () => {
    const dsn = await freshDb();
    const res = spawnSync('node', [join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js')], {
      encoding: 'utf8',
      env: { ...process.env, SHRAPNEL_PG_DSN: dsn },
    });
    assert.equal(res.status, 0, `migrate.js exited ${res.status}: ${res.stderr || res.stdout}`);
    assert.match(res.stdout, /applied 0007_work_request_stereotypes\.sql/);
    assert.match(res.stdout, /\[shrapnel migrate] done/);
  });
});
