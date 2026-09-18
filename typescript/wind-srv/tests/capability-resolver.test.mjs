import { describe, it, expect, vi, beforeEach, afterAll } from 'vitest';
import http from 'node:http';
import express from 'express';

// ── Mock the DB layer ─────────────────────────────────────────────────
vi.mock('../src/db.js', () => ({
  query: vi.fn(),
  pool: { connect: vi.fn() },
}));

import { errorHandler } from '../src/error-handler.js';
import { nodeRequirementsRouter } from '../src/routes/node-requirements.js';
import {
  classifyVerdict, SATISFACTION_STATES, buildBundle,
} from '../src/capability-resolver.js';
import { query } from '../src/db.js';

// Ephemeral app server — no supertest dependency (pitfall #15: a test that
// needs an optional dependency is not hermetic).
const app = express();
app.use(express.json());
app.use('/api/nodes', nodeRequirementsRouter);
app.use(errorHandler);
const server = http.createServer(app);
await new Promise((res) => server.listen(0, '127.0.0.1', res));
const base = `http://127.0.0.1:${server.address().port}/api/nodes`;
afterAll(() => server.close());

const get = async (path) => {
  const r = await fetch(base + path);
  return { status: r.status, body: await r.json().catch(() => null) };
};

beforeEach(() => { query.mockReset(); });

const NODE_ID = '11111111-1111-1111-1111-111111111111';

function mockNodeFound() {
  query.mockImplementationOnce(() => Promise.resolve({ rows: [
    { id: NODE_ID, name: 'verify-gate', workflow_version_id: 'v-1' },
  ] }));
}

describe('classifyVerdict (V174 vocabulary, never collapsed)', () => {
  it('passes through all six V174 states', () => {
    for (const s of SATISFACTION_STATES) expect(classifyVerdict(s)).toBe(s);
  });

  it('maps absent/unknown states to unknown without guessing', () => {
    expect(classifyVerdict(undefined)).toBe('unknown');
    expect(classifyVerdict(null)).toBe('unknown');
    expect(classifyVerdict('excellent')).toBe('unknown');
  });

  it('vocabulary is exactly the V174 six', () => {
    expect(SATISFACTION_STATES).toEqual([
      'satisfied', 'satisfied-stale', 'unsatisfied', 'unreachable', 'refused', 'unknown',
    ]);
  });
});

describe('GET /:id/requirements', () => {
  it('returns per-requirement verdicts from the live view shape', async () => {
    mockNodeFound();
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { capability_key: 'has-active-shrapnel-protocol', role_credential: 'tester', last_verdict: null },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { name: 'architect' }, { name: 'engineer' }, { name: 'tester' },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { id: 'cap-1', capability: 'has-active-shrapnel-protocol', description: 'shrapnel' },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { satisfaction_state: 'satisfied', satisfying_providers: ['postgresql'],
        active_adapters: 1, degraded_adapters: 0, declared_adapters: 1,
        last_observed_at: new Date('2026-09-17T04:20:00Z'), evidence_age: '18:09',
        concept_id: 'c-90b5' },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { can_verify_work_requests: true, can_greenlight: false, owns_domains: ['test-verification'] },
    ] }));

    const res = await get(`/${NODE_ID}/requirements`);
    expect(res.status).toBe(200);
    expect(res.body.mode).toBe('warn');
    const r = res.body.requirements[0];
    expect(r.effective_verdict).toBe('satisfied');
    expect(r.capability.satisfying_providers).toEqual(['postgresql']);
    expect(r.verify_holder_class).toEqual(['architect', 'engineer', 'tester']);
    expect(r.role_credential.can_verify_work_requests).toBe(true);
  });

  it('preserves satisfied-stale instead of collapsing to satisfied', async () => {
    mockNodeFound();
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { capability_key: 'k', role_credential: null, last_verdict: null },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [{ name: 'tester' }] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { id: 'c1', capability: 'k', description: '' },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { satisfaction_state: 'satisfied-stale', satisfying_providers: [],
        active_adapters: 0, degraded_adapters: 1, declared_adapters: 0,
        last_observed_at: new Date('2026-09-10T00:00:00Z'), evidence_age: '7d',
        concept_id: null },
    ] }));
    const res = await get(`/${NODE_ID}/requirements`);
    expect(res.body.requirements[0].effective_verdict).toBe('satisfied-stale');
  });

  it('unknown capability resolves to unknown, exists=false', async () => {
    mockNodeFound();
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { capability_key: 'no-such-capability', role_credential: null, last_verdict: null },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
    const res = await get(`/${NODE_ID}/requirements`);
    const r = res.body.requirements[0];
    expect(r.capability.exists).toBe(false);
    expect(r.capability.verdict).toBe('unknown');
  });

  it('404s for unknown node', async () => {
    query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
    const res = await get(`/${NODE_ID}`);
    expect(res.status).toBe(404);
  });

  it('400s on non-UUID node id', async () => {
    const res = await get('/not-a-uuid/requirements');
    expect(res.status).toBe(400);
  });
});

describe('ResolvedContextBundle (refs-only law)', () => {
  it('carries only refs in binding_refs — no content blobs', () => {
    const bundle = buildBundle(
      { id: NODE_ID, name: 'n', workflow_version_id: 'v' },
      [{
        capability_key: 'k', effective_verdict: 'satisfied',
        capability: { concept_id: 'c-1' },
      }]);
    expect(bundle.binding_refs.capability_concepts).toEqual(['c-1']);
    for (const v of Object.values(bundle.binding_refs)) {
      for (const ref of v) expect(typeof ref).toBe('string');
    }
    expect(JSON.stringify(bundle)).not.toMatch(/content|body_md|payload/);
  });

  it('route resolves the bundle and reports warn mode', async () => {
    mockNodeFound();
    query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
    const res = await get(`/${NODE_ID}/resolve`);
    expect(res.status).toBe(200);
    expect(res.body.mode).toBe('warn');
    expect(res.body.requirement_verdicts).toEqual([]);
  });
});

describe('credential + capability helper routes', () => {
  it('credential route reports exists=false for unknown role (absence as data)', async () => {
    query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
    const res = await get('/credential/ghost');
    expect(res.status).toBe(200);
    expect(res.body.exists).toBe(false);
    expect(res.body.can_verify_work_requests).toBe(false);
  });

  it('capability resolve route returns verdict for existing key', async () => {
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { id: 'c1', name: 'has-active-shrapnel-protocol', description: '' },
    ] }));
    query.mockImplementationOnce(() => Promise.resolve({ rows: [
      { satisfaction_state: 'satisfied', satisfying_providers: ['postgresql'],
        active_adapters: 1, degraded_adapters: 0, declared_adapters: 0,
        last_observed_at: null, evidence_age: null, concept_id: null },
    ] }));
    const res = await get('/capability/has-active-shrapnel-protocol/resolve');
    expect(res.status).toBe(200);
    expect(res.body.verdict).toBe('satisfied');
    expect(res.body.satisfying_providers).toEqual(['postgresql']);
  });

  // SQL-shape pins: the mocked-db suite above can't see SQL text, which is
  // exactly how the V175 sentinel / column-name drift shipped (alerts on the
  // live restart). These assert the SQL against the LIVE contract.
  describe('SQL shape pins (live-contract regression guards)', () => {
    it('credential SQL uses the V175 house open-sentinel, not infinity', async () => {
      query.mockResolvedValueOnce({ rows: [] });
      await get('/credential/engineer');
      const sql = query.mock.calls[0][0];
      expect(sql).toContain("valid_until = '9999-12-31 00:00:00+00'::timestamptz");
      expect(sql).toContain("recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz");
      expect(sql).not.toContain('infinity');
    });

    it('capability SQL queries nebula.capabilities by name column', async () => {
      query.mockImplementationOnce(() => Promise.resolve({ rows: [] }));
      await get('/capability/anything/resolve');
      const sql = query.mock.calls[0][0];
      expect(sql).toContain('FROM nebula.capabilities');
      expect(sql).toContain('WHERE name = $1');
      expect(sql).not.toMatch(/WHERE capability\s*=/);
    });
  });
});
