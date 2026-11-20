/**
 * P4a: a Blueprint must be able to carry — and must validate — the Requirement
 * and Specification it implements.
 *
 * Before this change `POST /api/plans` hardcoded `spec_ref`/`requirement_ref`
 * to null, so every blueprint was parentless and the
 * Specification → Requirement → Blueprint → WorkRequest chain could not be
 * reconstructed. The values are also cast to uuid by the
 * `nebula.implementation_plans` view on read, so unvalidated text would poison
 * that projection.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'http';
import { createRoutes } from '../routes';

const REQUIREMENT_ID = '804b3f87-fe84-4167-b5a7-15eaa35c2d4c';
const OTHER_REQUIREMENT_ID = '5fcf5826-b7ad-48bd-900a-c8ed54b95970';
const SPEC_ID = '11111111-2222-4333-8444-555555555555';
const UNKNOWN_ID = '99999999-8888-4777-8666-555555555555';

class FakePool {
  requirements = new Set<string>([REQUIREMENT_ID, OTHER_REQUIREMENT_ID]);
  specifications = new Map<string, string | null>([[SPEC_ID, REQUIREMENT_ID]]);
  insertedPayloads: any[] = [];
  queries: string[] = [];

  async query(sql: string, values: any[] = []) {
    this.queries.push(sql);

    if (/SELECT MAX\(NULLIF\(regexp_replace\(plan_number/.test(sql)) {
      return { rows: [{ max_id: 88 }] };
    }
    if (/SELECT id FROM nebula\.requirements WHERE id = \$1/.test(sql)) {
      return { rows: this.requirements.has(values[0]) ? [{ id: values[0] }] : [] };
    }
    if (/SELECT id, requirement_id FROM resolution\.specification WHERE id = \$1/.test(sql)) {
      if (!this.specifications.has(values[0])) return { rows: [] };
      return { rows: [{ id: values[0], requirement_id: this.specifications.get(values[0]) }] };
    }
    if (/INSERT INTO nebula\.blueprints_history/.test(sql)) {
      const [, title, payload, createdAt] = values;
      const parsed = JSON.parse(payload);
      this.insertedPayloads.push(parsed);
      return {
        rows: [{
          plan_number: '0089',
          title,
          payload: parsed,
          status: 'pending',
        }],
      };
    }
    throw new Error(`Unexpected query: ${sql}`);
  }
}

let pool: FakePool;
let server: Server;
let base: string;

beforeEach(async () => {
  pool = new FakePool();
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json());
  app.use('/api', createRoutes(pool as never));
  server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Test server has no TCP address');
  base = `http://127.0.0.1:${address.port}/api/plans`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
});

async function createPlan(body: unknown) {
  const response = await fetch(base, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { response, body: await response.json() as any };
}

describe('POST /api/plans blueprint governance refs', () => {
  it('carries a valid requirementRef into the blueprint payload', async () => {
    const { response, body } = await createPlan({ title: 'Wire the emitter', requirementRef: REQUIREMENT_ID });

    expect(response.status).toBe(201);
    expect(response.headers.get('x-powered-by')).toBeNull();
    expect(body.requirementRef).toBe(REQUIREMENT_ID);
    expect(body.specRef).toBeNull();
    expect(pool.insertedPayloads).toHaveLength(1);
    expect(pool.insertedPayloads[0].requirement_ref).toBe(REQUIREMENT_ID);
    expect(pool.insertedPayloads[0].spec_ref).toBeNull();
  });

  it('accepts the requirementId spelling used by the compile path', async () => {
    const { response, body } = await createPlan({ title: 'Compiled blueprint', requirementId: REQUIREMENT_ID });

    expect(response.status).toBe(201);
    expect(body.requirementRef).toBe(REQUIREMENT_ID);
    expect(pool.insertedPayloads[0].requirement_ref).toBe(REQUIREMENT_ID);
  });

  it('carries a coherent requirement/specification pair', async () => {
    const { response, body } = await createPlan({
      title: 'Specified blueprint',
      requirementRef: REQUIREMENT_ID,
      specRef: SPEC_ID,
    });

    expect(response.status).toBe(201);
    expect(body.requirementRef).toBe(REQUIREMENT_ID);
    expect(body.specRef).toBe(SPEC_ID);
    expect(pool.insertedPayloads[0]).toMatchObject({
      requirement_ref: REQUIREMENT_ID,
      spec_ref: SPEC_ID,
    });
  });

  it('rejects a requirementRef that does not exist and writes nothing', async () => {
    const { response, body } = await createPlan({ title: 'Dangling', requirementRef: UNKNOWN_ID });

    expect(response.status).toBe(400);
    expect(body.error).toContain('requirementRef does not exist');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a specRef that does not exist', async () => {
    const { response, body } = await createPlan({ title: 'Dangling spec', specRef: UNKNOWN_ID });

    expect(response.status).toBe(400);
    expect(body.error).toContain('specRef does not exist');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a specification paired with a different requirement', async () => {
    const { response, body } = await createPlan({
      title: 'Mismatched pair',
      requirementRef: OTHER_REQUIREMENT_ID,
      specRef: SPEC_ID,
    });

    expect(response.status).toBe(400);
    expect(body.error).toContain('governs requirement');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a malformed ref instead of poisoning the uuid projection', async () => {
    const { response, body } = await createPlan({ title: 'Bad ref', requirementRef: 'not-a-uuid' });

    expect(response.status).toBe(400);
    expect(body.error).toContain('must be a uuid');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('still creates a parentless blueprint when no refs are supplied', async () => {
    const { response, body } = await createPlan({ title: 'Unscoped blueprint' });

    expect(response.status).toBe(201);
    expect(body.requirementRef).toBeNull();
    expect(body.specRef).toBeNull();
    expect(pool.insertedPayloads[0].requirement_ref).toBeNull();
  });
});
