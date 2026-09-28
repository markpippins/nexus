/**
 * Voyager-srv API contract tests: the router's behaviour end-to-end against a
 * FAKE pool — no database, no network. Express invocations go through a
 * supertest-style in-process request shim.
 *
 * Pinned behaviours:
 *   * /health shape and its 503 path when the pool fails
 *   * list endpoints return { items, total, page, pageSize } with camelCased
 *     rows, honouring page/pageSize bounds
 *   * single-resource endpoints 404 with the exact error strings
 *   * /stats reports per-table counts and nulls out failing probes
 *   * pool failures surface as 500 { error }
 */
import { describe, it, expect, vi } from 'vitest';
import express from 'express';
import { createRoutes } from './routes.js';

// ── Fake pool ────────────────────────────────────────────────────────
type QueryHandler = (sql: string, params: any[]) => any;

function fakePool(handler: QueryHandler) {
  return { query: vi.fn(handler) } as any;
}

// ── In-process request shim (supertest-free) ─────────────────────────
function makeApp(pool: any) {
  const app = express();
  app.use(express.json());
  app.use('/api', createRoutes(pool));
  return app;
}

function req(app: express.Express, method: string, url: string): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const parts = new URL(url, 'http://test');
    const req: any = {
      method,
      url: parts.pathname + parts.search,
      headers: {},
    };
    const res: any = {
      statusCode: 200,
      body: undefined as any,
      status(code: number) { this.statusCode = code; return this; },
      json(payload: any) { this.body = payload; resolve({ status: this.statusCode, body: this.body }); },
      send(payload: any) { this.body = payload; resolve({ status: this.statusCode, body: this.body }); },
      end() { resolve({ status: this.statusCode, body: this.body }); },
      on() { return this; },
      once() { return this; },
      emit() { return true; },
      set() { return this; },
      setHeader() {},
      get() { return undefined; },
      write() { return true; },
      headersSent: false,
      app,
      req: {} as any,
      locals: {},
    };
    req.res = res;
    res.req = req;
    (app as any).handle(req, res, (err?: any) => {
      if (err) reject(err);
      else resolve({ status: res.statusCode, body: res.body });
    });
  });
}

const UUID = '00000000-0000-0000-0000-000000000001';

describe('GET /api/health', () => {
  it('returns ok with db=true when the pool answers', async () => {
    const app = makeApp(fakePool(() => ({ rows: [{ ok: 1 }] })));
    const r = await req(app, 'GET', '/api/health');
    expect(r.status).toBe(200);
    expect(r.body).toEqual({ status: 'ok', db: true, service: 'voyager-srv' });
  });

  it('returns 503 with db=false when the pool fails', async () => {
    const app = makeApp(fakePool(() => { throw new Error('connection refused'); }));
    const r = await req(app, 'GET', '/api/health');
    expect(r.status).toBe(503);
    expect(r.body.status).toBe('error');
  });
});

describe('list endpoints (pagination + camelCase contract)', () => {
  it('GET /scan-epochs returns { items, total, page, pageSize }', async () => {
    const epoch = { id: UUID, started_at: new Date('2026-09-28T00:00:00Z'), status: 'done' };
    const app = makeApp(fakePool((sql) => {
      if (sql.includes('COUNT(*)')) return { rows: [{ total: 1 }] };
      return { rows: [epoch] };
    }));
    const r = await req(app, 'GET', '/api/scan-epochs?page=2&pageSize=10');
    expect(r.status).toBe(200);
    expect(r.body.total).toBe(1);
    expect(r.body.page).toBe(2);
    expect(r.body.pageSize).toBe(10);
    expect(r.body.items[0].startedAt).toBe(epoch.started_at.getTime());
    expect(r.body.items[0].started_at).toBeUndefined();
  });

  it('GET /observations/files clamps pageSize to the 1..100 window', async () => {
    let seenLimit: number | undefined;
    const app = makeApp(fakePool((sql, params = []) => {
      if (sql.includes('COUNT(*)')) return { rows: [{ total: 0 }] };
      seenLimit = params[params.length - 2];
      return { rows: [] };
    }));
    await req(app, 'GET', '/api/observations/files?pageSize=5000');
    expect(seenLimit).toBe(100);
    await req(app, 'GET', '/api/observations/files?pageSize=0');
    expect(seenLimit).toBe(1);
  });

  it('GET /spans forwards filters as parameterised predicates', async () => {
    const app = makeApp(fakePool((sql) => {
      if (sql.includes('COUNT(*)')) return { rows: [{ total: 0 }] };
      return { rows: [] };
    }));
    const r = await req(app, 'GET', '/api/spans?spanType=heading&minConfidence=0.7');
    expect(r.status).toBe(200);
    expect(r.body.items).toEqual([]);
  });

  it('a pool failure surfaces as 500 { error }', async () => {
    const app = makeApp(fakePool(() => { throw new Error('relation does not exist'); }));
    const r = await req(app, 'GET', '/api/scan-epochs');
    expect(r.status).toBe(500);
    expect(r.body.error).toContain('relation does not exist');
  });
});

describe('single-resource endpoints', () => {
  it('GET /scan-epochs/:id returns a camelCased row when found', async () => {
    const app = makeApp(fakePool(() => ({ rows: [{ id: UUID, started_at: new Date(0) }] })));
    const r = await req(app, 'GET', `/api/scan-epochs/${UUID}`);
    expect(r.status).toBe(200);
    expect(r.body.id).toBe(UUID);
    expect(r.body.startedAt).toBe(0);
  });

  it('GET /scan-epochs/:id 404s with the exact error string when missing', async () => {
    const app = makeApp(fakePool(() => ({ rows: [] })));
    const r = await req(app, 'GET', `/api/scan-epochs/${UUID}`);
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: 'Scan epoch not found' });
  });

  it('GET /observations/files/by-id/:observationId 404s on miss', async () => {
    const app = makeApp(fakePool(() => ({ rows: [] })));
    const r = await req(app, 'GET', `/api/observations/files/by-id/${UUID}`);
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: 'File observation not found' });
  });

  it('GET /topology/signals/:id 404s on miss', async () => {
    const app = makeApp(fakePool(() => ({ rows: [] })));
    const r = await req(app, 'GET', `/api/topology/signals/${UUID}`);
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: 'Topology signal not found' });
  });

  it('GET /entities/:id embeds drift history (camelCased) when found', async () => {
    const app = makeApp(fakePool((sql) => {
      if (sql.includes('entity_drift')) return { rows: [{ discovered_at: new Date(5), drift_kind: 'moved' }] };
      return { rows: [{ id: UUID, entity_id: 'e-uuid', state: { canonical_path: '/x' } }] };
    }));
    const r = await req(app, 'GET', `/api/entities/${UUID}`);
    expect(r.status).toBe(200);
    expect(r.body.canonicalPath).toBeUndefined(); // state passes through nested
    expect(r.body.drifts[0].discoveredAt).toBe(5);
    expect(r.body.drifts[0].driftKind).toBe('moved');
  });

  it('GET /entities/:id 404s with the exact error string when missing', async () => {
    const app = makeApp(fakePool(() => ({ rows: [] })));
    const r = await req(app, 'GET', `/api/entities/${UUID}`);
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: 'Entity not found' });
  });

  it('GET /spans/:id 404s with the exact error string when missing', async () => {
    const app = makeApp(fakePool(() => ({ rows: [] })));
    const r = await req(app, 'GET', `/api/spans/${UUID}`);
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: 'Metadata span not found' });
  });
});

describe('GET /api/stats', () => {
  it('reports per-table counts and the latest epoch', async () => {
    const app = makeApp(fakePool((sql) => {
      if (sql.includes('span_type')) return { rows: [{ span_type: 'heading', count: 2 }] };
      if (sql.includes('ORDER BY started_at')) return { rows: [{ id: UUID, status: 'done', started_at: new Date(9) }] };
      return { rows: [{ count: 4 }] };
    }));
    const r = await req(app, 'GET', '/api/stats');
    expect(r.status).toBe(200);
    expect(r.body.file_observations).toBe(4);
    expect(r.body.scan_epochs).toBe(4);
    expect(r.body.span_types).toEqual([{ span_type: 'heading', count: 2 }]);
    expect(r.body.latest_epoch.id).toBe(UUID);
    expect(r.body.latest_epoch.startedAt).toBe(9);
  });

  it('nulls out individual probes that fail (per-table try/catch)', async () => {
    let call = 0;
    const app = makeApp(fakePool((sql) => {
      call++;
      if (sql.includes('span_type')) throw new Error('boom');
      if (sql.includes('ORDER BY started_at')) return { rows: [] };
      return { rows: [{ count: 1 }] };
    }));
    const r = await req(app, 'GET', '/api/stats');
    expect(r.status).toBe(200);
    expect(r.body.span_types).toBeNull();
    expect(r.body.latest_epoch).toBeNull();
  });
});
