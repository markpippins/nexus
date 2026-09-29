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
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import pg from 'pg';

import {
  applyMigrationFile,
  buildApplyScript,
  buildDumpCommand,
  planMigrations,
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

/**
 * A migration number that sorts AFTER every real migration.
 *
 * The rollback test below used to hard-code '0008_broken.sql' and the boundary
 * `filename < '0008'`, which silently meant "whatever happens to be numbered
 * below 8 today". The moment a real 0008 landed, the deliberately-broken file
 * sorted *before* it, the real 0008 was never applied, and the "the migrations
 * before it are still applied" count came out one short of REAL_CHAIN — a
 * failure caused by the test's own hard-coding rather than by any regression.
 *
 * Deriving the number from the chain makes the test mean what it says no matter
 * how many migrations exist later.
 */
const NEXT_NUM = String(
  Math.max(...REAL_CHAIN.map((f) => parseInt(f.slice(0, 4), 10) || 0)) + 1
).padStart(4, '0');
const BROKEN_NAME = `${NEXT_NUM}_broken.sql`;

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

// Can this host's pg_dump talk to the test PostgreSQL? pg_dump refuses to dump
// a NEWER server, and CI runners may carry an older client than the throwaway
// server (pg_dump 16 vs PostgreSQL 17 was hit live on 2026-09-29). That is a
// property of the environment, not of the runner, so the dump test branches on
// it instead of skipping: compatible client -> assert the archive is written;
// incompatible client -> assert the fail-closed refusal. Both legs pin real
// guardrail behavior; neither is allowed to silently skip.
let dumpRailSkipReason = null;
{
  try {
    const dumpOut = spawnSync('pg_dump', ['--version'], { encoding: 'utf8' }).stdout || '';
    const dumpMajor = parseInt(dumpOut.match(/\d+/)?.[0] ?? '0', 10);
    const probe = new pg.Client({ connectionString: ADMIN_DSN });
    await probe.connect();
    const { rows } = await probe.query('SHOW server_version');
    await probe.end().catch(() => {});
    const serverMajor = parseInt(String(rows[0].server_version).split('.')[0], 10);
    if (dumpMajor < serverMajor) {
      dumpRailSkipReason = `pg_dump ${dumpMajor} cannot dump server ${serverMajor} (client older than the test server)`;
    }
  } catch (err) {
    dumpRailSkipReason = `pg_dump compatibility probe failed: ${err.message}`;
  }
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
    // BROKEN_NAME sorts after every real migration (see NEXT_NUM).
    const dir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-'));
    cpSync(MIGRATIONS_DIR, dir, { recursive: true });
    writeFileSync(
      join(dir, BROKEN_NAME),
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
        new RegExp(`${NEXT_NUM}_broken\\.sql|division by zero`)
      );

      const c = connect(dsn);
      // The property the old runner lost: the inner COMMIT ended the outer
      // transaction, so its ROLLBACK had nothing to roll back.
      const table = await c.query(
        "SELECT to_regclass('shrapnel.must_not_survive') AS reg"
      );
      assert.equal(table.rows[0].reg, null, 'partial DDL from a failed migration must not survive');

      const led = await c.query(
        `SELECT count(*)::int AS n FROM shrapnel._migration_ledger WHERE filename = '${BROKEN_NAME}'`
      );
      assert.equal(led.rows[0].n, 0, 'a failed migration must not be ledgered');

      // ...and every real migration before it is still applied and still ledgered.
      const ok = await c.query(
        `SELECT count(*)::int AS n FROM shrapnel._migration_ledger
          WHERE filename >= '0001' AND filename < '${NEXT_NUM}'`
      );
      assert.equal(
        ok.rows[0].n,
        REAL_CHAIN.length,
        `expected all ${REAL_CHAIN.length} real migrations applied before ${BROKEN_NAME}`
      );
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

// ---------------------------------------------------------------------------
// Guardrails: plan mode, ahead-of-checkout refusal, advisory lock, dump shape.
// These exist because the first LIVE use of the runner was the 2026-09-29
// hand-driven titanium apply, whose safety rails lived only in the operator's
// head. Each test pins one of those rails so they outlive the operator.
// ---------------------------------------------------------------------------

describe('buildDumpCommand', () => {
  it('dumps only the shrapnel schema, custom format, to a deterministic path', () => {
    const { file, args } = buildDumpCommand({
      dsn: 'postgresql://u:p@h:5432/db',
      dumpDir: '/spool',
      timestamp: '2026-09-29T00-00-00',
    });
    assert.equal(file, '/spool/shrapnel-pre-migrate_2026-09-29T00-00-00.dump');
    assert.equal(args[0], 'postgresql://u:p@h:5432/db');
    assert.ok(args.includes('--format=custom'), 'custom format so pg_restore can list/verify it');
    assert.ok(args.includes('--schema=shrapnel'), 'the dump is scoped to the schema being migrated');
    assert.ok(args.includes(`--file=${file}`));
  });
});

describe('planMigrations', { skip: canCreateDb ? false : 'cannot create a database' }, () => {
  it('reports a fresh database read-only — and creates nothing', async () => {
    const dsn = await freshDb();
    const plan = await planMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    assert.equal(plan.fresh, true);
    assert.deepEqual(plan.pending, REAL_CHAIN);
    assert.deepEqual(plan.ahead, []);

    const c = connect(dsn);
    const reg = await c.query("SELECT to_regclass('shrapnel._migration_ledger') AS reg");
    assert.equal(reg.rows[0].reg, null, 'a plan must not bootstrap the ledger it is planning');
    await c.end();
  });

  it('goes from full-pending to in-sync around a real apply', async () => {
    const dsn = await freshDb();
    const before = await planMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    assert.equal(before.pending.length, REAL_CHAIN.length);
    await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    const after = await planMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet });
    assert.deepEqual(after.pending, []);
    assert.deepEqual(after.ahead, []);
  });
});

describe('runMigrations guardrails', { skip: canCreateDb ? false : 'cannot create a database' }, () => {
  it('dry-run writes nothing: no ledger, no schema, no locks held afterwards', async () => {
    const dsn = await freshDb();
    const pending = await runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet, dryRun: true });
    assert.deepEqual(pending, REAL_CHAIN, 'the plan names every file');

    const c = connect(dsn);
    const reg = await c.query("SELECT to_regclass('shrapnel._migration_ledger') AS reg");
    assert.equal(reg.rows[0].reg, null, 'dry-run must not create the ledger');
    await c.end();
  });

  it('refuses to apply when the ledger knows migrations this checkout has never heard of', async () => {
    // Apply a ONE-file chain, then take that file away: the database is now
    // ahead of the checkout, which is exactly the state that resurrected the
    // 0005 freeze defect when an older chain was replayed under a newer DB.
    const dsn = await freshDb();
    const dir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-ahead-'));
    cpSync(join(MIGRATIONS_DIR, '0001_init.sql'), join(dir, '0001_init.sql'));
    try {
      await runMigrations({ dsn, migrationsDir: dir, log: quiet });
      rmSync(join(dir, '0001_init.sql'));
      await assert.rejects(
        runMigrations({ dsn, migrationsDir: dir, log: quiet }),
        /AHEAD of this checkout.*0001_init\.sql/s
      );
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('refuses to apply while another session holds the per-database advisory lock', async () => {
    const dsn = await freshDb();
    const blocker = connect(dsn);
    await blocker.query("SELECT pg_try_advisory_lock(hashtext(current_database() || ':shrapnel-migrate')) AS ok");
    try {
      await assert.rejects(
        runMigrations({ dsn, migrationsDir: MIGRATIONS_DIR, log: quiet }),
        /advisory lock/
      );
    } finally {
      await blocker.query("SELECT pg_advisory_unlock(hashtext(current_database() || ':shrapnel-migrate'))");
      await blocker.end();
    }
  });

  it('surfaces the ahead-refusal through the CLI, exit 1, before any psql runs', async () => {
    const dsn = await freshDb();
    // Ledger the whole chain PLUS a file the repo dir has never had: the CLI
    // always plans against the repo migrations dir, so only a file that is
    // genuinely absent from it can trigger the ahead-refusal end to end.
    const dir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-cli-'));
    cpSync(MIGRATIONS_DIR, dir, { recursive: true });
    writeFileSync(join(dir, '0000_extra.sql'), 'SELECT 1;\n');
    try {
      await runMigrations({ dsn, migrationsDir: dir, log: quiet });
      const res = spawnSync('node', [join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js'), '--dry-run'], {
        encoding: 'utf8',
        env: { ...process.env, SHRAPNEL_PG_DSN: dsn },
      });
      assert.equal(res.status, 1, `expected exit 1: ${res.stderr || res.stdout}`);
      assert.match(res.stderr, /AHEAD of this checkout/);
      assert.match(res.stderr, /0000_extra\.sql/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('exits 0 on an in-sync no-op apply — the exit code must not contradict the ledger', async () => {
    // Regression: main() called an undefined `log` in the applied.length === 0
    // branch, so a fully successful no-op apply exited 1 AFTER doing its (zero)
    // work — caught live in the 2026-09-29 throwaway-DB rehearsal (record
    // 3f5fed68). The suite missed it because every prior CLI-path test applies
    // something; this one drives the real binary through the no-op branch.
    const dsn = await freshDb();
    const run = () =>
      spawnSync('node', [join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js')], {
        encoding: 'utf8',
        env: { ...process.env, SHRAPNEL_PG_DSN: dsn },
      });

    const first = run();
    assert.equal(first.status, 0, `first apply failed: ${first.stderr || first.stdout}`);

    const second = run();
    assert.equal(
      second.status,
      0,
      `no-op apply exited ${second.status}: ${second.stderr || second.stdout}`
    );
    assert.match(second.stdout, /nothing to do/);
    assert.ok(
      !/log is not defined/.test(`${second.stderr}${second.stdout}`),
      'the ReferenceError must be gone from the CLI path'
    );

    const c = connect(dsn);
    const { rows } = await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger');
    assert.equal(rows[0].n, REAL_CHAIN.length, 'the no-op run changed nothing');
    await c.end();
  });

  it('applies to an existing database through the CLI with --no-dump: exit 0, ledger complete', async () => {
    // The dump rail's success leg is exercised below, gated on client/server
    // compatibility; this pins the existing-database apply path end to end
    // without depending on the runner's pg_dump build.
    const dsn = await freshDb();
    const root = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-cli-root-'));
    mkdirSync(join(root, 'src', 'scripts'), { recursive: true });
    cpSync(MIGRATIONS_DIR, join(root, 'migrations'), { recursive: true });
    cpSync(join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js'), join(root, 'src', 'scripts', 'migrate.js'));
    symlinkSync(join(SERVICE_ROOT, 'node_modules'), join(root, 'node_modules'), 'dir');
    const last = REAL_CHAIN[REAL_CHAIN.length - 1];
    const run = (extraEnv = {}, extraArgs = []) =>
      spawnSync('node', [join(root, 'src', 'scripts', 'migrate.js'), ...extraArgs], {
        encoding: 'utf8',
        cwd: root,
        env: { ...process.env, SHRAPNEL_PG_DSN: dsn, ...extraEnv },
      });
    try {
      // Seed: fresh database, chain minus the last file.
      rmSync(join(root, 'migrations', last));
      const seeded = run();
      assert.equal(seeded.status, 0, `seed apply failed: ${seeded.stderr || seeded.stdout}`);

      // Restore the last file: one real pending migration on an existing DB.
      cpSync(join(MIGRATIONS_DIR, last), join(root, 'migrations', last));
      const res = run({}, ['--no-dump']);
      assert.equal(res.status, 0, `no-dump apply failed: ${res.stderr || res.stdout}`);
      assert.match(res.stdout, new RegExp(`applied ${last.replace(/\./g, '\\.')}`));
      assert.ok(!/pre-apply dump/.test(res.stdout), '--no-dump writes no archive');

      const c = connect(dsn);
      const { rows } = await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger');
      assert.equal(rows[0].n, REAL_CHAIN.length, 'the whole chain is ledgered');
      await c.end();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('refuses to apply when the pre-apply dump cannot be written (fail-closed, end to end)', async () => {
    // Deterministic in every environment: the dump directory is occupied by a
    // regular file, so the rail fails before pg_dump is even considered -- and
    // the apply must refuse with the ledger untouched.
    const dsn = await freshDb();
    const root = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-cli-root-'));
    mkdirSync(join(root, 'src', 'scripts'), { recursive: true });
    cpSync(MIGRATIONS_DIR, join(root, 'migrations'), { recursive: true });
    cpSync(join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js'), join(root, 'src', 'scripts', 'migrate.js'));
    symlinkSync(join(SERVICE_ROOT, 'node_modules'), join(root, 'node_modules'), 'dir');
    const blocker = join(tmpdir(), `shrapnel-dump-blocker-${randomUUID().slice(0, 8)}`);
    const pending = '9999_rehearsal_pending.sql';
    writeFileSync(blocker, 'not a directory');
    const run = (extraEnv = {}) =>
      spawnSync('node', [join(root, 'src', 'scripts', 'migrate.js')], {
        encoding: 'utf8',
        cwd: root,
        env: { ...process.env, SHRAPNEL_PG_DSN: dsn, ...extraEnv },
      });
    try {
      // Seed the full real chain on a fresh database (no dump on fresh).
      const seeded = run();
      assert.equal(seeded.status, 0, `seed apply failed: ${seeded.stderr || seeded.stdout}`);

      // Only NOW introduce pending work, so the seed really is the real chain.
      writeFileSync(join(root, 'migrations', pending), 'SELECT 1;\n');

      const res = run({ SHRAPNEL_MIGRATE_DUMP_DIR: blocker });
      assert.equal(res.status, 1, `expected the dump rail to refuse: ${res.stdout}`);
      assert.match(
        `${res.stderr}${res.stdout}`,
        /pre-apply pg_dump failed|EEXIST/,
        'the operator sees a refusal, not a silent apply'
      );

      const c = connect(dsn);
      const { rows } = await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger');
      assert.equal(rows[0].n, REAL_CHAIN.length, 'the refused run changed nothing');
      const pend = await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger WHERE filename = $1', [pending]);
      assert.equal(pend.rows[0].n, 0, 'the pending migration was not applied behind the refusal');
      await c.end();
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(blocker, { force: true });
    }
  });

  it(
    'dump rail on an existing DB with pending work: dumps when the client can, refuses when it cannot',
    async () => {
      // The dump rail lives in main(), which the unit tests never reach, and
      // it only fires on an EXISTING database with pending work. The CLI always
      // plans against the migrations dir next to migrate.js itself, so the test
      // runs a copied service root: seed the DB minus the last migration,
      // restore the file, then apply. Where the host's pg_dump can read the
      // test server, the archive must be written and the apply commits; where
      // it cannot (CI: pg_dump 16 vs server 17), the rail must refuse the apply
      // outright — the fail-closed property itself. No branch is allowed to
      // skip: both pin guardrail behavior and both end at the same ledger
      // invariant (the refused run changed nothing; the successful one ledgered
      // the whole chain).
      const dsn = await freshDb();
      const root = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-cli-root-'));
      const dumpDir = mkdtempSync(join(tmpdir(), 'shrapnel-migrate-dumps-'));
      mkdirSync(join(root, 'src', 'scripts'), { recursive: true });
      cpSync(MIGRATIONS_DIR, join(root, 'migrations'), { recursive: true });
      cpSync(join(SERVICE_ROOT, 'src', 'scripts', 'migrate.js'), join(root, 'src', 'scripts', 'migrate.js'));
      symlinkSync(join(SERVICE_ROOT, 'node_modules'), join(root, 'node_modules'), 'dir');
      const last = REAL_CHAIN[REAL_CHAIN.length - 1];
      const run = (extraEnv = {}) =>
        spawnSync('node', [join(root, 'src', 'scripts', 'migrate.js')], {
          encoding: 'utf8',
          cwd: root,
          env: { ...process.env, SHRAPNEL_PG_DSN: dsn, SHRAPNEL_MIGRATE_DUMP_DIR: dumpDir, ...extraEnv },
        });
      try {
        // Seed: fresh database, chain minus the last file -> no dump (fresh).
        rmSync(join(root, 'migrations', last));
        const seeded = run();
        assert.equal(seeded.status, 0, `seed apply failed: ${seeded.stderr || seeded.stdout}`);
        assert.ok(!/pre-apply dump/.test(seeded.stdout), 'a fresh database gets no dump');
        assert.equal(readdirSync(dumpDir).length, 0, 'no dump artifacts after the fresh apply');

        // Restore the last file: one real pending migration on an existing DB.
        cpSync(join(MIGRATIONS_DIR, last), join(root, 'migrations', last));
        const res = run();
        const dumps = readdirSync(dumpDir).filter((f) => f.endsWith('.dump'));

        const c = connect(dsn);
        const ledgered = async () =>
          (await c.query('SELECT count(*)::int AS n FROM shrapnel._migration_ledger')).rows[0].n;

        if (dumpRailSkipReason) {
          // Incompatible client: the rail must fail CLOSED — refuse the apply,
          // write no archive, change no ledger row. (CI's pg_dump 16 vs the
          // throwaway server 17 lands here, and that is a real assertion.)
          assert.equal(res.status, 1, `expected the dump rail to refuse: ${res.stdout}`);
          assert.match(
            `${res.stderr}${res.stdout}`,
            /pre-apply pg_dump failed[\s\S]*override with --no-dump/,
            'the operator sees the wrapped refusal, not a silent apply'
          );
          assert.match(`${res.stderr}${res.stdout}`, /pg_dump/);
          assert.equal(dumps.length, 0, 'no archive is written by a failed dump');
          assert.equal(await ledgered(), REAL_CHAIN.length - 1, 'the refused run changed nothing');
        } else {
          // Compatible client: the archive is written, then the apply commits.
          assert.equal(res.status, 0, `dump-rail apply failed: ${res.stderr || res.stdout}`);
          assert.match(res.stdout, /pre-apply dump: /, 'the rail announces the archive');
          assert.match(res.stdout, new RegExp(`applied ${last.replace(/\./g, '\\.')}`));
          assert.equal(dumps.length, 1, 'exactly one pre-apply dump was written');
          assert.ok(statSync(join(dumpDir, dumps[0])).size > 0, 'the archive is non-empty');
          assert.equal(await ledgered(), REAL_CHAIN.length, 'the whole chain is ledgered');
        }
        await c.end();
      } finally {
        rmSync(root, { recursive: true, force: true });
        rmSync(dumpDir, { recursive: true, force: true });
      }
    }
  );
});
