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
const OTHER_SPEC_ID = '11111111-2222-4333-8444-555555555556';
const UNKNOWN_ID = '99999999-8888-4777-8666-555555555555';

class FakePool {
  requirements = new Set<string>([REQUIREMENT_ID, OTHER_REQUIREMENT_ID]);
  specifications = new Map<string, string | null>([
    [SPEC_ID, REQUIREMENT_ID],
    [OTHER_SPEC_ID, OTHER_REQUIREMENT_ID],
  ]);
  insertedPayloads: any[] = [];
  queries: string[] = [];

  async query(sql: string, values: any[] = []) {
    this.queries.push(sql);

    if (/SELECT MAX\(NULLIF\(regexp_replace\(plan_number/.test(sql)) {
      return { rows: [{ max_id: 88 }] };
    }
    // The route resolves every id the request names in one lookup per relation,
    // so entity type comes from the database rather than from the field name.
    if (/FROM nebula\.requirements WHERE id = ANY/.test(sql)) {
      const ids = values[0] as string[];
      return { rows: ids.filter((id) => this.requirements.has(id)).map((id) => ({ id })) };
    }
    if (/FROM resolution\.specification WHERE id = ANY/.test(sql)) {
      const ids = values[0] as string[];
      return {
        rows: ids
          .filter((id) => this.specifications.has(id))
          .map((id) => ({ id, requirement_id: this.specifications.get(id) ?? null })),
      };
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

  it('carries requirement_ids and specification_ids into the payload and response', async () => {
    const { response, body } = await createPlan({
      title: 'Blueprint with sets',
      requirement_ids: [REQUIREMENT_ID, OTHER_REQUIREMENT_ID],
      specification_ids: [SPEC_ID],
    });

    expect(response.status).toBe(201);
    expect(body.requirementIds).toEqual([REQUIREMENT_ID, OTHER_REQUIREMENT_ID]);
    expect(body.specificationIds).toEqual([SPEC_ID]);
    expect(pool.insertedPayloads[0]).toMatchObject({
      requirement_ids: [REQUIREMENT_ID, OTHER_REQUIREMENT_ID],
      specification_ids: [SPEC_ID],
    });
  });

  it('accepts the camelCase spelling and reports absent lists as empty', async () => {
    const camel = await createPlan({
      title: 'Camel lists',
      requirementIds: [OTHER_REQUIREMENT_ID],
      specificationIds: [OTHER_SPEC_ID],
    });
    expect(camel.response.status).toBe(201);
    expect(camel.body.requirementIds).toEqual([OTHER_REQUIREMENT_ID]);
    expect(camel.body.specificationIds).toEqual([OTHER_SPEC_ID]);

    const bare = await createPlan({ title: 'No lists' });
    expect(bare.response.status).toBe(201);
    expect(bare.body.requirementIds).toEqual([]);
    expect(bare.body.specificationIds).toEqual([]);
    // Nothing is added to a payload the caller did not name.
    expect(pool.insertedPayloads[1]).not.toHaveProperty('requirement_ids');
    expect(pool.insertedPayloads[1]).not.toHaveProperty('specification_ids');
  });

  it('rejects an unknown id in a list by index and writes nothing', async () => {
    const { response, body } = await createPlan({
      title: 'Unknown list member',
      requirement_ids: [REQUIREMENT_ID, UNKNOWN_ID],
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('requirement_ids[1]');
    expect(body.code).toBe('unknown_requirement_id');
    expect(body.error).toContain('requirement_ids[1] does not exist');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a specification that governs a requirement outside the named set', async () => {
    const { response, body } = await createPlan({
      title: 'Out-of-scope specification',
      requirement_ids: [REQUIREMENT_ID],
      specification_ids: [OTHER_SPEC_ID],
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('specification_ids[0]');
    expect(body.code).toBe('specification_outside_blueprint');
    expect(body.error).toContain(OTHER_REQUIREMENT_ID);
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('names the wrong entity type instead of reporting the id as missing', async () => {
    const { response, body } = await createPlan({
      title: 'Specification in the requirement list',
      requirement_ids: [SPEC_ID],
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('requirement_ids[0]');
    expect(body.code).toBe('wrong_entity_type');
    expect(body.error).toContain('not a Requirement');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a governed specification named with no requirement (orphan list)', async () => {
    const { response, body } = await createPlan({
      title: 'Orphan specification',
      specification_ids: [SPEC_ID],
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('specification_ids[0]');
    expect(body.code).toBe('specification_outside_blueprint');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a governed specRef named with no requirement (orphan primary)', async () => {
    const { response, body } = await createPlan({
      title: 'Orphan primary spec',
      specRef: SPEC_ID,
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('specRef');
    expect(body.code).toBe('specification_outside_blueprint');
    expect(pool.insertedPayloads).toHaveLength(0);
  });

  it('rejects a specRef left out of specification_ids', async () => {
    const { response, body } = await createPlan({
      title: 'Incoherent spec refs',
      requirement_ids: [REQUIREMENT_ID, OTHER_REQUIREMENT_ID],
      specification_ids: [SPEC_ID],
      specRef: OTHER_SPEC_ID,
    });

    expect(response.status).toBe(400);
    expect(body.field).toBe('specification_ids');
    expect(body.code).toBe('incoherent_blueprint_refs');
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
