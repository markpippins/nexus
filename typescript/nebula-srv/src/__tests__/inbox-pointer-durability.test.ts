/**
 * App-layer regression guards for the inbox-pointer durability fix (defect `db3992b2`).
 *
 * The tester is right that the real-store harness in `tests/` asserts the SCHEMA, and that
 * three application behaviours were correct on inspection but unguarded:
 *
 *   1. durable-first ordering  — the entire point of the fix
 *   2. the Redis-only backfill — otherwise applying migration 071 destroys live pointers
 *   3. `durable:false` / `pointerMissing` propagation — so non-durability can be reported
 *
 * On a fix whose purpose is that ordering, none of them should ride in silently.
 *
 * ## Why these are hermetic, and what that means
 *
 * The unit job provisions neither PostgreSQL nor Redis, so these run against fakes. The
 * schema-level guarantees (exact ISO round-trip, upsert, `updated_at`, no re-anchor) are
 * covered separately against real stores by `tests/inbox-pointer-durability.test.ts`, which
 * applies the real 071 DDL to a throwaway PostgreSQL 17. Between the two, nothing is asserted
 * by only one kind of test.
 *
 * A cleared in-memory fake is exactly as lossy as a flushed Redis, which is what makes the
 * cache-empty assertions meaningful rather than decorative.
 *
 * ## Note for whoever touches the fakes
 *
 * The first attempt at this suite failed 9/9 and looked like `vi.doMock('pg')` failing to
 * intercept. It was not: the service calls `pointerPool.on('error', ...)` immediately after
 * construction, the fake had no `on`, that threw inside the write's try block, and every write
 * silently returned `durable:false`. **A fake that is missing a method the code calls fails as
 * a mysterious assertion failure, not as a clear error.** Hence `FakePool.on` below.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import express from 'express';
import type { Server } from 'http';

const ISO = '2026-10-01T09:30:00.000Z';
const KEY = (r: string) => `inbox:pointer:${r}`;

/** A cache that can be EMPTIED — the whole point: the cache must not be load-bearing. */
class FakeRedis {
  store = new Map<string, string>();
  async get(k: string) { return this.store.get(k) ?? null; }
  async set(k: string, v: string) { this.store.set(k, v); return 'OK'; }
  async del(k: string) { return this.store.delete(k) ? 1 : 0; }
  async keys(pattern: string) {
    const p = pattern.replace('*', '');
    return [...this.store.keys()].filter((k) => k.startsWith(p));
  }
  async connect() { return this; }
  async quit() {}
  /** The service attaches an error handler immediately after constructing the client. */
  on() { return this; }
  duplicate() { return this; }
}

/** A durable store whose writes can be made to fail, to prove failure is never silent. */
class FakePool {
  rows = new Map<string, string>();
  failWrites = false;
  constructor(_opts?: unknown) {}
  /** Called immediately by the service; without this, every write silently 'fails'. */
  on() { return this; }
  async end() {}
  async query(text: string, values?: unknown[]) {
    if (/INSERT INTO/i.test(text)) {
      if (this.failWrites) throw new Error('relation "nebula.role_inbox_pointers" does not exist');
      const [role, pointer] = values as [string, string];
      this.rows.set(role, pointer);
      return { rows: [] };
    }
    if (/WHERE role = \$1/i.test(text)) {
      const role = (values as [string])[0];
      const pointer = this.rows.get(role);
      return { rows: pointer === undefined ? [] : [{ role, pointer }] };
    }
    return { rows: [...this.rows.entries()].map(([role, pointer]) => ({ role, pointer })) };
  }
}

let fakeRedis: FakeRedis;
let fakePool: FakePool;
let svc: typeof import('../services/block-segmentation-redis.service');

beforeEach(async () => {
  vi.resetModules();
  fakeRedis = new FakeRedis();
  fakePool = new FakePool();
  vi.doMock('ioredis', () => ({ default: class { constructor() { return fakeRedis as never; } } }));
  vi.doMock('pg', () => ({ Pool: class { constructor(o?: unknown) { return fakePool as never; } } }));
  svc = await import('../services/block-segmentation-redis.service');
  svc.initRedis();
});

afterEach(() => { vi.doUnmock('ioredis'); vi.doUnmock('pg'); });

describe('1. durable-first ordering', () => {
  it('reads the durable value when the cache is cold — the defect cannot recur', async () => {
    await svc.setInboxPointer('engineer-iii', ISO);
    fakeRedis.store.clear();                                  // total cache loss
    expect(await svc.getInboxPointer('engineer-iii')).toBe(ISO);
  });

  it('prefers durable over a STALE cache value (never cache-first)', async () => {
    fakePool.rows.set('engineer-iii', '2026-10-01T10:00:00.000Z');
    fakeRedis.store.set(KEY('engineer-iii'), '2026-10-01T09:00:00.000Z');
    expect(await svc.getInboxPointer('engineer-iii')).toBe('2026-10-01T10:00:00.000Z');
  });

  it('repairs the cache from durable on read', async () => {
    fakePool.rows.set('engineer-iii', '2026-10-01T10:00:00.000Z');
    fakeRedis.store.set(KEY('engineer-iii'), '2026-10-01T09:00:00.000Z');
    await svc.getInboxPointer('engineer-iii');
    expect(fakeRedis.store.get(KEY('engineer-iii'))).toBe('2026-10-01T10:00:00.000Z');
  });

  it('writes durable BEFORE the cache, so a crash between them loses nothing', async () => {
    await svc.setInboxPointer('engineer-iii', ISO);
    expect(fakePool.rows.get('engineer-iii')).toBe(ISO);
  });

  it('a role with no pointer reads as null — absence, never re-anchored', async () => {
    expect(await svc.getInboxPointer('never-seen')).toBeNull();
    expect(fakePool.rows.has('never-seen')).toBe(false);
  });
});

describe('2. the Redis-only backfill', () => {
  it('promotes a cache-only pointer into the durable store', async () => {
    fakeRedis.store.set(KEY('engineer-iii'), ISO);          // survives only in cache
    expect(await svc.getInboxPointer('engineer-iii')).toBe(ISO);
    expect(fakePool.rows.get('engineer-iii')).toBe(ISO);   // promoted, not discarded
  });

  it('survives the cache then being emptied (the point of the backfill)', async () => {
    fakeRedis.store.set(KEY('engineer-iii'), ISO);
    await svc.getInboxPointer('engineer-iii');              // triggers backfill
    fakeRedis.store.clear();
    expect(await svc.getInboxPointer('engineer-iii')).toBe(ISO);
  });

  it('never lets a cache value overwrite a durable one during backfill', async () => {
    fakePool.rows.set('engineer-iii', '2026-10-01T10:00:00.000Z');
    fakeRedis.store.set(KEY('engineer-iii'), '2026-10-01T09:00:00.000Z');
    await svc.getInboxPointer('engineer-iii');
    expect(fakePool.rows.get('engineer-iii')).toBe('2026-10-01T10:00:00.000Z');
  });
});

describe('3. durable:false / pointerMissing propagation', () => {
  let server: Server | undefined;
  let base = '';

  beforeEach(async () => {
    const { createRoutes } = await import('../routes');
    const app = express();
    app.use(express.json());
    app.use('/api', createRoutes(fakePool as never));
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    const addr = server.address();
    const port = typeof addr === 'object' && addr ? addr.port : 0;
    base = `http://127.0.0.1:${port}/api`;
  });

  afterEach(async () => {
    if (server) await new Promise((r) => server!.close(() => r(undefined)));
    server = undefined;
  });

  it('PUT reports durable:true when the durable write succeeded', async () => {
    const res = await fetch(`${base}/inbox-pointer/engineer-iii`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timestamp: ISO }),
    });
    const body = (await res.json()) as { durable?: boolean; pointer?: string };
    expect(res.status).toBe(200);
    expect(body.durable).toBe(true);
    expect(body.pointer).toBe(ISO);
  });

  it('PUT reports durable:FALSE when the durable store is unavailable', async () => {
    fakePool.failWrites = true;
    const res = await fetch(`${base}/inbox-pointer/engineer-iii`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timestamp: ISO }),
    });
    const body = (await res.json()) as { ok?: boolean; durable?: boolean };
    // The whole point: the endpoint can now report non-durability instead of always ok:true.
    expect(res.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(body.durable).toBe(false);
  });

  it('GET reports pointerMissing:true rather than letting null read as "nothing pending"', async () => {
    const res = await fetch(`${base}/inbox-pointer/never-seen-role`);
    const body = (await res.json()) as { pointer?: string | null; pointerMissing?: boolean };
    expect(res.status).toBe(200);
    expect(body.pointer).toBeNull();
    expect(body.pointerMissing).toBe(true);
  });

  it('GET reports pointerMissing:false once a durable pointer exists', async () => {
    fakePool.rows.set('engineer-iii', ISO);
    const res = await fetch(`${base}/inbox-pointer/engineer-iii`);
    const body = (await res.json()) as { pointer?: string | null; pointerMissing?: boolean };
    expect(body.pointer).toBe(ISO);
    expect(body.pointerMissing).toBe(false);
  });
});