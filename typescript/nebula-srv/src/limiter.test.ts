/**
 * Rate-limiter behaviour for the governed requirement-compilation API (#749).
 *
 * WHY THIS EXISTS
 * ---------------
 * CodeQL `js/missing-rate-limiting` is satisfied by the limiter merely being
 * PRESENT on the route. That is a static property — it says nothing about
 * whether the limiter is actually wired to behave. A limiter that is
 * misconfigured (wrong key, ceiling never reached, middleware mounted but
 * shadowed) satisfies CodeQL and fails in production.
 *
 * For an API that EVALUATES requirements and MINTS / READS durable receipts,
 * that is the wrong place to be satisfied by presence alone. These assertions
 * cover the two behaviours that matter:
 *
 *   1. a normal request still reaches the route handler (400 from its own first
 *      validation branch, NOT 429) — catches a limiter that blocks all traffic;
 *   2. exceeding the ceiling returns 429 with the documented envelope
 *      — catches a ceiling that is never reached, or a wrong key.
 *
 * HERMETIC
 * --------
 * The probe POSTs an empty body, which the route rejects with
 * `400 INVALID_REQUIREMENT_ID` at its FIRST validation branch — before any
 * `pool.query`. The limiter sits ahead of that as route middleware, so the
 * request exercises the limiter without touching the database. `/health` is
 * deliberately not used: it queries pg and answers 503 with no database
 * reachable, which fails in CI while passing locally. (Same reasoning as
 * typescript/execution-srv/src/limiter.test.ts.)
 *
 * The pool is a local stub and the app is built from `createRoutes` — the
 * convention already used by src/__tests__/requirement-compile-contract.test.ts,
 * rather than importing src/index.ts (which constructs a real pg.Pool at module
 * scope and would try to reach a database at import).
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'http';
import { createRoutes } from './routes';

const COMPILE_PATH = '/v1/capabilities/requirement-compile/compile';

/** Never queried: the probe is rejected before any pool access. */
const pool = { async query() { return { rows: [] }; } } as never;

let server: Server;
let base = '';

beforeAll(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api', createRoutes(pool));
  server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Test server has no TCP address');
  base = `http://127.0.0.1:${address.port}/api`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

/** A request the route rejects on validation, before any DB access. */
function probe(): Promise<Response> {
  return fetch(`${base}${COMPILE_PATH}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
}

describe('requirement-compile rate limiter (#749)', () => {
  it('lets a normal request through to the handler (not throttled)', async () => {
    const res = await probe();
    // The limiter is ahead of the route's first validation branch, so a first
    // request must reach the handler and be rejected on its own terms.
    expect(res.status).toBe(400);
    expect(res.status).not.toBe(429);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe('INVALID_REQUIREMENT_ID');
  });

  it('stamps the draft-7 RateLimit headers the limiter advertises', async () => {
    const res = await probe();
    // Presence-only CodeQL would pass even if these were absent; asserting the
    // advertised policy catches a limiter mounted with a different config.
    expect(res.headers.get('ratelimit')).toMatch(/limit=\d+/);
    expect(res.headers.get('ratelimit-policy')).toMatch(/\d+/);
  });

  it('returns the documented 429 envelope once the ceiling is exceeded', async () => {
    let saw429 = false;
    let attempts = 0;
    // The ceiling is 300 req/min/IP; drive past it rather than hardcoding 300,
    // so a changed ceiling is still proven to bite rather than silently passing.
    for (; attempts < 400; attempts++) {
      const res = await probe();
      if (res.status === 429) {
        saw429 = true;
        const body = (await res.json()) as { error?: string };
        expect(body.error).toBe('nebula-srv requirement-compile rate limit exceeded');
        break;
      }
    }
    expect(saw429, `no 429 after ${attempts} requests — the ceiling is not being enforced`).toBe(true);
  });

  it('throttles only the guarded routes, not a pre-existing one', async () => {
    // The limiter is scoped to the two requirement-compile routes, matching
    // limiter.ts's stated convention. A global limiter would be a behavioural
    // change to every pre-existing route, so assert the scope rather than let
    // it pass silently.
    //
    // NOTE: the ceiling was already consumed by the previous test, and
    // express-rate-limit keys both guarded routes off the same client IP, so
    // this must run against a route OUTSIDE the guarded set. A guarded route
    // would correctly answer 429 here and prove nothing about scope.
    const res = await fetch(`${base}/v1/agent-records?limit=1`);
    expect(res.status).not.toBe(429);
  });
});
