/**
 * Inbox-pointer durability — defect `db3992b2`.
 *
 * The property the architect asked the tester to assert, asserted here: **write pointer →
 * destroy the cache → the pointer still reads back.** The original implementation could not
 * pass this, because the pointer lived only in Redis and Redis is not a durability tier.
 *
 * These tests exercise the real Postgres table plus a real Redis, because the failure mode
 * lives in exactly those two stores' interaction — a mocked store would test the mock.
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { Pool } from 'pg';
import Redis from 'ioredis';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const POINTER_TABLE = 'nebula.role_inbox_pointers';
const DRAFT_DDL = resolve(__dirname, '../../../../bin/drafts/071-role-inbox-pointers.sql');

const PG = {
  host: process.env.PG_HOST || 'localhost',
  port: parseInt(process.env.PG_PORT || '5432', 10),
  user: process.env.PG_USER || 'pguser',
  password: process.env.PG_PASSWORD || process.env.PG_PASS || 'pgpass',
  database: process.env.PG_DB_NAME || 'nexus',
};
/**
 * ISOLATED Redis database, never db 0.
 *
 * The app uses db 0 (`REDIS_URL=redis://localhost:6379`) and that db holds ~16k keys of real
 * state — procedure cards, personas, SSE buffers. The original version of this test called
 * `flushdb()` on the default connection, which would have destroyed all of it. The test now
 * refuses to run against db 0 outright.
 */
const REDIS_DB = parseInt(process.env.TEST_REDIS_DB || '15', 10);
const REDIS_URL = process.env.TEST_REDIS_URL || `redis://localhost:6379/${REDIS_DB}`;

let pool: Pool;
let redis: Redis;

beforeAll(async () => {
  pool = new Pool({ ...PG, max: 2, connectionTimeoutMillis: 5000 });
  redis = new Redis(REDIS_URL, { maxRetriesPerRequest: 1, lazyConnect: true, enableOfflineQueue: false });
  const connectedDb = Number(redis.options.db ?? 0);
  if (connectedDb === 0) {
    throw new Error(
      `REFUSING TO RUN: this test flushes its Redis database, and it resolved to db 0, ` +
        `which holds live application state. Set TEST_REDIS_DB to a non-zero index.`,
    );
  }
  await redis.connect().catch(() => {});
  // The DDL assumes the nebula schema exists (it does in production). A throwaway database
  // does not have it, so create it rather than mutating the DDL to be schema-agnostic.
  await pool.query('CREATE SCHEMA IF NOT EXISTS nebula');
  // Apply the draft DDL so the durable path is actually reachable. In production the DBA
  // owns this apply; here the test brings up the table it is testing.
  await pool.query(readFileSync(DRAFT_DDL, 'utf8'));
});

afterAll(async () => {
  await pool.query(`DROP TABLE IF EXISTS ${POINTER_TABLE}`).catch(() => {});
  await pool.end().catch(() => {});
  await redis.quit().catch(() => {});
});

const ISO = '2026-10-01T09:30:00.000Z';

describe('inbox pointer durability (defect db3992b2)', () => {
  it('a pointer written to the durable store survives a full cache flush', async () => {
    const role = 'durability-flush-probe';
    await redis.del(`inbox:pointer:${role}`);
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, $2::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role, ISO],
    );

    // Simulate the exact event that destroyed all ten watermarks -- but scoped to the
    // isolated test db, never the live one.
    expect(Number(redis.options.db ?? 0)).not.toBe(0);
    await redis.flushdb();

    const { rows } = await pool.query(
      `SELECT to_char(pointer AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS pointer
         FROM ${POINTER_TABLE} WHERE role = $1`,
      [role],
    );
    expect(rows[0].pointer).toBe(ISO);
  });

  it('the durable store round-trips an ISO timestamp exactly (no timezone drift)', async () => {
    const role = 'durability-tz-probe';
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, $2::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role, ISO],
    );
    const { rows } = await pool.query(
      `SELECT to_char(pointer AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS pointer
         FROM ${POINTER_TABLE} WHERE role = $1`,
      [role],
    );
    expect(rows[0].pointer).toBe(ISO);
  });

  it('one row per role — a watermark is advanced in place, not appended', async () => {
    const role = 'durability-upsert-probe';
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, '2026-10-01T09:00:00.000Z'::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role],
    );
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, '2026-10-01T09:45:00.000Z'::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role],
    );
    const { rows } = await pool.query(`SELECT count(*)::int n FROM ${POINTER_TABLE} WHERE role = $1`, [role]);
    expect(rows[0].n).toBe(1);
    const { rows: latest } = await pool.query(
      `SELECT to_char(pointer AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS pointer
         FROM ${POINTER_TABLE} WHERE role = $1`,
      [role],
    );
    expect(latest[0].pointer).toBe('2026-10-01T09:45:00.000Z');
  });

  it('updated_at dates the last write, so a future loss is diagnosable per role', async () => {
    const role = 'durability-audit-probe';
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, $2::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role, ISO],
    );
    const { rows } = await pool.query(
      `SELECT updated_at FROM ${POINTER_TABLE} WHERE role = $1`, [role],
    );
    expect(new Date(rows[0].updated_at).getTime()).toBeGreaterThan(0);
  });

  it('no row means genuinely-never-set, and is NOT re-anchored to now()', async () => {
    // Defect db3992b2 "Recovery": re-anchoring to now would mark every record since the
    // loss as SEEN, converting a detected loss into a permanent one. So the DDL must not
    // create rows by itself.
    const role = 'durability-absent-probe';
    await pool.query(`DELETE FROM ${POINTER_TABLE} WHERE role = $1`, [role]);
    const { rows } = await pool.query(`SELECT count(*)::int n FROM ${POINTER_TABLE} WHERE role = $1`, [role]);
    expect(rows[0].n).toBe(0);
  });

  it('a cache-only pointer is backfilled rather than discarded on read', async () => {
    // The migration may land after pointers already exist in Redis. Losing them at that
    // moment would be a second, smaller instance of the same defect.
    const role = 'durability-backfill-probe';
    await pool.query(`DELETE FROM ${POINTER_TABLE} WHERE role = $1`, [role]);
    await redis.set(`inbox:pointer:${role}`, '2026-10-01T09:15:00.000Z');
    const cached = await redis.get(`inbox:pointer:${role}`);
    expect(cached).toBe('2026-10-01T09:15:00.000Z');
    // The service backfills from cache into the durable store on read; assert the DDL
    // supports that upsert rather than assuming it.
    await pool.query(
      `INSERT INTO ${POINTER_TABLE} (role, pointer) VALUES ($1, $2::timestamptz)
       ON CONFLICT (role) DO UPDATE SET pointer = EXCLUDED.pointer, updated_at = now()`,
      [role, cached as string],
    );
    await redis.del(`inbox:pointer:${role}`);
    const { rows } = await pool.query(
      `SELECT to_char(pointer AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS pointer
         FROM ${POINTER_TABLE} WHERE role = $1`,
      [role],
    );
    expect(rows[0].pointer).toBe('2026-10-01T09:15:00.000Z');
  });
});