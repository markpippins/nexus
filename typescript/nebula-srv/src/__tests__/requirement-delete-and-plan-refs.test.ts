/**
 * P4a: what a requirement delete means once `resolution.requirement` is the
 * canonical table (V222), and what a Blueprint read exposes about its refs.
 *
 * The database owns the delete ruling (`resolution.requirement_delete_blockers`
 * plus a BEFORE DELETE trigger). These tests pin the HTTP contract on top of
 * it: a referenced requirement is a 409 naming the blocking surfaces, a
 * system/feature cascade is atomic (nothing deleted, no parent removed), and a
 * clean delete still goes through the ordered leaf-first helper. They also pin
 * that plan reads surface the referenced Requirement and Specification ids the
 * write path now validates.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'http';
import { createRoutes } from '../routes';

const REQUIREMENT_ID = '804b3f87-fe84-4167-b5a7-15eaa35c2d4c';
const OTHER_REQUIREMENT_ID = '5fcf5826-b7ad-48bd-900a-c8ed54b95970';
const BLOCKED_REQUIREMENT_ID = '11111111-2222-4333-8444-555555555555';
const SYSTEM_ID = '99999999-9999-4999-8999-999999999991';
const FEATURE_ID = '77777777-6666-4555-8444-333333333333';
const SPEC_ID = '22222222-3333-4444-8555-666666666666';

type Scope = 'system_id' | 'subsystem_id' | 'feature_id';

class FakeDb {
  /** V222 is operator-gated; flipping this models the pre-apply window. */
  guardInstalled = true;
  requirements = new Map<string, Record<Scope, string | null>>([
    [REQUIREMENT_ID, { system_id: SYSTEM_ID, subsystem_id: null, feature_id: FEATURE_ID }],
    [OTHER_REQUIREMENT_ID, { system_id: SYSTEM_ID, subsystem_id: null, feature_id: FEATURE_ID }],
    [BLOCKED_REQUIREMENT_ID, { system_id: SYSTEM_ID, subsystem_id: null, feature_id: FEATURE_ID }],
  ]);
  blockers = new Map<string, Record<string, string[]>>([
    [BLOCKED_REQUIREMENT_ID, { specifications: [SPEC_ID] }],
  ]);
  deletedSets: string[][] = [];
  parentDeletes: { table: string; id: string }[] = [];
  committed = false;
  rolledBack = false;

  async query(sql: string, values: any[] = []): Promise<any> {
    const s = sql.replace(/\s+/g, ' ').trim();

    if (s === 'BEGIN' || s === 'COMMIT' || s === 'ROLLBACK') {
      if (s === 'COMMIT') this.committed = true;
      if (s === 'ROLLBACK') this.rolledBack = true;
      return { rows: [], rowCount: null };
    }

    if (s.includes('to_regprocedure')) {
      return { rows: [{ installed: this.guardInstalled }] };
    }

    if (s.startsWith('SELECT id FROM nebula.requirements WHERE')) {
      const scope = s.match(/WHERE (system_id|subsystem_id|feature_id) = \$1/)![1] as Scope;
      const rows = [...this.requirements.entries()]
        .filter(([, row]) => row[scope] === values[0])
        .map(([id]) => ({ id }));
      return { rows };
    }

    if (s.includes('FROM unnest($1::uuid[])')) {
      const rows = (values[0] as string[])
        .filter((id) => this.blockers.has(id))
        .map((id) => ({ id, blockers: this.blockers.get(id) }));
      return { rows };
    }

    if (s.includes('SELECT resolution.delete_requirements($1::uuid[])')) {
      const ids = values[0] as string[];
      this.deletedSets.push(ids);
      const deleted = ids.filter((id) => this.requirements.delete(id)).length;
      return { rows: [{ deleted }] };
    }

    if (s.includes('DELETE FROM requirements WHERE id = ANY($1::uuid[]) RETURNING id')) {
      const ids = values[0] as string[];
      this.deletedSets.push(ids);
      const rows = ids.filter((id) => this.requirements.delete(id)).map((id) => ({ id }));
      return { rows };
    }

    if (/DELETE FROM (systems|subsystems|features) WHERE id = \$1/.test(s)) {
      this.parentDeletes.push({ table: s.match(/DELETE FROM (\w+)/)![1], id: values[0] });
      return { rows: [], rowCount: 1 };
    }

    if (s.startsWith('DELETE FROM work_sessions')) {
      return { rows: [], rowCount: 0 };
    }

    if (s.includes('FROM nebula.implementation_plans p') && s.includes('COUNT(*)')) {
      return { rows: [{ total: 1 }] };
    }

    if (s.includes('FROM nebula.implementation_plans p')) {
      // Answer with the projection the SQL asked for, so the alias choice is
      // what the test asserts rather than something the fake made up.
      const quoted = s.includes('AS "requirementRef"');
      const row = quoted
        ? { id: '0089', title: 'A plan', requirementRef: REQUIREMENT_ID, specRef: SPEC_ID }
        : { id: '0089', title: 'A plan', requirement_ref: REQUIREMENT_ID, spec_ref: SPEC_ID };
      return { rows: [row] };
    }

    throw new Error(`Unexpected query: ${s}`);
  }

  async connect() {
    return {
      query: (sql: string, values: any[] = []) => this.query(sql, values),
      release: () => {},
    };
  }
}

let db: FakeDb;
let server: Server;
let base: string;

beforeEach(async () => {
  db = new FakeDb();
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json());
  app.use('/api', createRoutes(db as never));
  server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Test server has no TCP address');
  base = `http://127.0.0.1:${address.port}/api`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
});

async function remove(path: string) {
  const response = await fetch(`${base}${path}`, { method: 'DELETE' });
  return { response, body: await response.json() as any };
}

describe('DELETE /api/requirements/:id', () => {
  it('deletes an unreferenced requirement through the ordered helper', async () => {
    const { response, body } = await remove(`/requirements/${REQUIREMENT_ID}`);

    expect(response.status).toBe(200);
    expect(body).toEqual({ ok: true, deletedRequirements: 1 });
    expect(db.deletedSets).toEqual([[REQUIREMENT_ID]]);
    expect(db.committed).toBe(true);
  });

  it('refuses a referenced requirement with 409 and names the blockers', async () => {
    const { response, body } = await remove(`/requirements/${BLOCKED_REQUIREMENT_ID}`);

    expect(response.status).toBe(409);
    expect(body.code).toBe('requirement_delete_blocked');
    expect(body.blockers).toEqual([
      { requirementId: BLOCKED_REQUIREMENT_ID, blockers: { specifications: [SPEC_ID] } },
    ]);
    // Nothing was deleted and the transaction was undone.
    expect(db.deletedSets).toEqual([]);
    expect(db.rolledBack).toBe(true);
    expect(db.committed).toBe(false);
    expect(db.requirements.has(BLOCKED_REQUIREMENT_ID)).toBe(true);
  });

  it('404s when the requirement does not exist', async () => {
    const { response } = await remove('/requirements/00000000-0000-4000-8000-000000000000');
    expect(response.status).toBe(404);
    expect(db.committed).toBe(false);
  });

  it('still deletes through the transition view before V222 is applied', async () => {
    db.guardInstalled = false;

    const { response, body } = await remove(`/requirements/${REQUIREMENT_ID}`);

    expect(response.status).toBe(200);
    expect(body.deletedRequirements).toBe(1);
    expect(db.deletedSets).toEqual([[REQUIREMENT_ID]]);
  });
});

describe('cascade deletes', () => {
  it('deletes the system requirements before the system row', async () => {
    db.blockers.clear();
    const { response, body } = await remove(`/systems/${SYSTEM_ID}`);

    expect(response.status).toBe(200);
    // The response reports what it removed, not just that it succeeded.
    expect(body).toEqual({ ok: true, deletedRequirements: 3 });
    // Every requirement in the system, including the feature-scoped ones: the
    // hierarchy views have no FK to cascade from.
    expect(db.deletedSets).toEqual([
      [REQUIREMENT_ID, OTHER_REQUIREMENT_ID, BLOCKED_REQUIREMENT_ID],
    ]);
    expect(db.parentDeletes).toEqual([{ table: 'systems', id: SYSTEM_ID }]);
    expect(db.committed).toBe(true);
  });

  it('refuses the whole feature cascade when one requirement is referenced', async () => {
    const { response, body } = await remove(`/features/${FEATURE_ID}`);

    expect(response.status).toBe(409);
    expect(body.code).toBe('requirement_delete_blocked');
    expect(body.blockers).toHaveLength(1);
    expect(body.blockers[0].requirementId).toBe(BLOCKED_REQUIREMENT_ID);
    // No partial cascade: the feature row survives with its requirements.
    expect(db.parentDeletes).toEqual([]);
    expect(db.deletedSets).toEqual([]);
    expect(db.committed).toBe(false);
  });

  it('maps the database guard refusal (a race) to 409 rather than 500', async () => {
    // Simulate the trigger winning between the pre-check and the delete: the
    // pre-check sees nothing, the delete is refused by the database guard.
    db.blockers.clear();
    const original = db.query.bind(db);
    db.query = async (sql: string, values: any[] = []) => {
      if (sql.includes('SELECT resolution.delete_requirements')) {
        const err: any = new Error('P4A-DELETE-BLOCKED: requirement x still has dependants');
        err.code = 'P4A01';
        err.detail = JSON.stringify({
          requirementId: REQUIREMENT_ID,
          blockers: { specifications: [SPEC_ID] },
        });
        throw err;
      }
      return original(sql, values);
    };

    const { response, body } = await remove(`/requirements/${REQUIREMENT_ID}`);

    expect(response.status).toBe(409);
    expect(body.code).toBe('requirement_delete_blocked');
    expect(body.blockers).toEqual([
      { requirementId: REQUIREMENT_ID, blockers: { specifications: [SPEC_ID] } },
    ]);
    expect(db.rolledBack).toBe(true);
  });

  it('maps a residual FK violation (23503) to 409, not 500', async () => {
    // A reference the guard did not enumerate (e.g. a part-added FK surface)
    // still means "conflict", not "server fault".
    db.blockers.clear();
    const original = db.query.bind(db);
    db.query = async (sql: string, values: any[] = []) => {
      if (sql.includes('SELECT resolution.delete_requirements')) {
        const err: any = new Error('update or delete on table violates foreign key constraint');
        err.code = '23503';
        throw err;
      }
      return original(sql, values);
    };

    const { response, body } = await remove(`/requirements/${REQUIREMENT_ID}`);

    expect(response.status).toBe(409);
    expect(body.code).toBe('requirement_delete_blocked');
    expect(db.rolledBack).toBe(true);
    expect(db.committed).toBe(false);
  });
});

describe('plan reads expose the Blueprint refs', () => {
  it('includes requirementRef and specRef in the plan list', async () => {
    const response = await fetch(`${base}/plans`);
    const body = await response.json() as any;

    expect(response.status).toBe(200);
    expect(response.headers.get('x-powered-by')).toBeNull();
    expect(body.items[0].requirementRef).toBe(REQUIREMENT_ID);
    expect(body.items[0].specRef).toBe(SPEC_ID);
  });

  it('includes requirementRef and specRef in a single plan', async () => {
    const response = await fetch(`${base}/plans/0089`);
    const body = await response.json() as any;

    expect(response.status).toBe(200);
    expect(body.requirementRef).toBe(REQUIREMENT_ID);
    expect(body.specRef).toBe(SPEC_ID);
  });
});