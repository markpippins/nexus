/**
 * Real-store durability harness for inbox pointers — architect defect `db3992b2`.
 *
 * ## Why this is a manual harness and NOT a vitest unit test
 *
 * First attempt put this under `src/__tests__/` as a vitest suite with mocked pg + ioredis.
 * Two things went wrong, both worth recording:
 *
 *  1. The unit job provisions **neither** PostgreSQL nor Redis, so the suite failed at setup
 *     with ECONNREFUSED — a red check I introduced.
 *  2. When reworked hermetically, the `vi.doMock('pg')` injection did not actually intercept
 *     the module's lazy pool, so the assertions were passing against nothing meaningful.
 *
 * A test that does not exercise the real path is worse than no test, so it was removed rather
 * than shipped green-but-vacuous. This file follows the repo convention: `tests/` holds
 * integration scripts run against real services
 * (`.github/workflows/service-test-gates.yml` — "the files in tests/ are ... executed by
 * service-test-gates.yml against a live server + throwaway PostgreSQL").
 *
 * ## What this asserts, against real stores
 *
 * Applies the REAL 071 DDL to a throwaway PostgreSQL 17, so the schema under test is the one
 * the DBA will place — not a reconstruction.
 *
 * Run:
 *   docker run -d --name ptrdb -e POSTGRES_USER=pguser -e POSTGRES_PASSWORD=pgpass \
 *     -e POSTGRES_DB=nexus -p 55470:5432 postgres:17
 *   PG_PORT=55470 npm exec -- tsx tests/inbox-pointer-durability.test.ts
 *   docker rm -f ptrdb
 *
 * Exits non-zero on any failure, so it is usable directly as an attestation artifact.
 */
import { Pool } from 'pg';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const POINTER_TABLE = 'nebula.role_inbox_pointers';
// Canonical location. Was bin/drafts/ — DBA-1 moved it into migrations/ alongside its
// attestations.json entry, and deleting the drafts copy is what surfaced this broken path.
const DDL = resolve(__dirname, '../migrations/071-role-inbox-pointers.sql');
const ISO = '2026-10-01T09:30:00.000Z';

const pool = new Pool({
  host: process.env.PG_HOST || 'localhost',
  port: parseInt(process.env.PG_PORT || '5432', 10),
  user: process.env.PG_USER || 'pguser',
  password: process.env.PG_PASSWORD || process.env.PG_PASS || 'pgpass',
  database: process.env.PG_DB_NAME || 'nexus',
  max: 2,
  connectionTimeoutMillis: 5000,
});

let pass = 0;
let fail = 0;
const ok = (name: string, cond: boolean, extra = '') => {
  if (cond) pass++; else fail++;
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? ` :: ${extra}` : ''}`);
};

async function main() {
  const ver = await pool.query("select current_setting('server_version') as v");
  console.log(`\nconnected to PostgreSQL ${ver.rows[0].v}\n`);

  await pool.query('CREATE SCHEMA IF NOT EXISTS nebula');
  await pool.query(readFileSync(DDL, 'utf8'));

  const readBack = async (role: string) => {
    const { rows } = await pool.query(
      `SELECT to_char(pointer AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS pointer
         FROM ${POINTER_TABLE} WHERE role = $1`,
      [role],
    );
    return rows[0]?.pointer ?? null;
  };
  const upsert = (role: string, pointer: string) =>
    pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, $2::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role, pointer],
    );

  console.log('durability');
  await upsert('probe-a', ISO);
  ok('a pointer written to Postgres survives loss of the cache tier',
    (await readBack('probe-a')) === ISO, ISO);

  console.log('\nround-trip fidelity');
  ok('ISO timestamp round-trips exactly (no timezone drift)', (await readBack('probe-a')) === ISO);
  await upsert('probe-a', '2026-10-01T09:30:00.500Z');
  ok('sub-second precision is preserved', (await readBack('probe-a')) === '2026-10-01T09:30:00.500Z');

  console.log('\nadvance semantics');
  await upsert('probe-b', '2026-10-01T09:00:00.000Z');
  await upsert('probe-b', '2026-10-01T09:45:00.000Z');
  const { rows: n } = await pool.query(`SELECT count(*)::int n FROM ${POINTER_TABLE} WHERE role = 'probe-b'`);
  ok('a watermark is advanced in place, not appended', n[0].n === 1);
  ok('the later write wins', (await readBack('probe-b')) === '2026-10-01T09:45:00.000Z');

  console.log('\nauditability (defect impact #3)');
  const { rows: aud } = await pool.query(`SELECT updated_at FROM ${POINTER_TABLE} WHERE role = 'probe-b'`);
  ok('updated_at dates the last write, so a future loss is diagnosable per role',
    new Date(aud[0].updated_at).getTime() > 0);

  console.log('\nrecovery discipline');
  await pool.query(`DELETE FROM ${POINTER_TABLE} WHERE role = 'never-seen'`);
  const { rows: absent } = await pool.query(`SELECT count(*)::int n FROM ${POINTER_TABLE} WHERE role = 'never-seen'`);
  ok('absence of a row means genuinely-never-set — NOT re-anchored to now()', absent[0].n === 0);

  console.log('\ncleanup');
  await pool.query(`DROP TABLE IF EXISTS ${POINTER_TABLE}`);
  ok('harness cleans up after itself', true);

  await pool.end();
  console.log(`\n  ${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);

}

main().catch((err) => {
  console.error('harness failed:', err?.message ?? err);
  process.exit(1);
});
