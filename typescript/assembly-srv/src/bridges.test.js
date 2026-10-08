/**
 * Changed-line coverage for the CodeQL js/missing-rate-limiting backfill:
 * the assembly incumbent's bridges router now carries a router-level
 * express-rate-limit guard (mirroring the moleculer twin, PR #686).
 *
 * HERMETIC: the router is mounted on a throwaway Express app and driven over
 * an ephemeral loopback port. Every probe uses a validation-negative body
 * (`{}`), which the handler rejects with 400 *before* it would call
 * `pool.query` — so the only thing that can turn a request into 429 is the
 * limiter itself, and no database is touched. The pg Pool is constructed at
 * import but never queried here.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import http from 'node:http';
import express from 'express';

import { bridgesRouter } from './routes/bridges.js';

let server;
let baseUrl = '';

beforeAll(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bridges', bridgesRouter);
  app.use((_req, res) => res.status(404).json({ error: 'not_found' }));
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    res.status(err.statusCode || 500).json({ error: err.message });
  });
  server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const addr = /** @type {import('node:net').AddressInfo} */ (server.address());
  baseUrl = `http://127.0.0.1:${addr.port}`;
});

afterAll(async () => {
  await new Promise((resolve) => server.close(() => resolve()));
});

describe('bridges rate limit (CodeQL js/missing-rate-limiting backfill)', () => {
  it('allows 120 requests in the window then rejects the 121st with 429', async () => {
    const statuses = [];
    for (let i = 0; i < 121; i++) {
      const res = await fetch(`${baseUrl}/api/bridges/forum-agenda`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      statuses.push(res.status);
      if (res.status === 429) break;
    }

    // The route handler's own response to a validation-negative body is 400;
    // the limiter adds no such behavior, so a 429 can only come from it.
    expect(statuses).toHaveLength(121);
    expect(statuses.slice(0, 120).every((s) => s === 400)).toBe(true);
    expect(statuses[120]).toBe(429);
  }, 30000);
});
