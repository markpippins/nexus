import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'http';
import { createRoutes } from '../routes';

const REQUIREMENT_ID = 'requirement-42';
const PLAN_RESPONSE = { created: true, planNumber: '0088', status: 'pending' };

class FakePool {
  idempotency = new Map<string, { input_digest: string; state: string; response?: unknown; response_status?: number }>();
  receipts = new Map<string, any>();
  requirementReads = 0;
  failReceiptWrite = false;

  async query(sql: string, values: any[] = []) {
    if (/INSERT INTO nebula\.compilation_idempotency/.test(sql)) {
      const [key, digest] = values;
      if (this.idempotency.has(key)) return { rows: [] };
      this.idempotency.set(key, { input_digest: digest, state: 'in_progress' });
      return { rows: [{ idempotency_key: key }] };
    }
    if (/SELECT input_digest, state, response, response_status FROM nebula\.compilation_idempotency/.test(sql)) {
      const row = this.idempotency.get(values[0]);
      return { rows: row ? [row] : [] };
    }
    if (/WITH saved AS/.test(sql)) {
      if (this.failReceiptWrite) throw new Error('receipt storage unavailable');
      const [receiptId, key, digest, requirementId, outcome, receipt, response, responseStatus] = values;
      this.receipts.set(receiptId, JSON.parse(receipt));
      this.idempotency.set(key, {
        input_digest: digest,
        state: 'completed',
        response: JSON.parse(response),
        response_status: responseStatus,
      });
      expect(requirementId).toBe(REQUIREMENT_ID);
      expect(outcome).toBe(this.receipts.get(receiptId).outcome);
      return { rows: [{ response: JSON.parse(response) }] };
    }
    if (/SELECT receipt FROM nebula\.compilation_receipts/.test(sql)) {
      const receipt = this.receipts.get(values[0]);
      return { rows: receipt ? [{ receipt }] : [] };
    }
    if (/FROM nebula\.requirements req/.test(sql)) {
      this.requirementReads++;
      return { rows: [{
        id: REQUIREMENT_ID,
        title: 'Compile a sample service',
        description: 'Build a sample service scaffold',
        status: 'open',
        priority: 'medium',
        req_type: 'feature',
        acceptance_criteria: ['Service compiles', 'Tests pass'],
        system_id: null,
        subsystem_id: null,
        feature_id: null,
        system_name: '',
        system_description: '',
        subsystem_name: '',
        subsystem_description: '',
        feature_name: '',
        feature_description: '',
      }] };
    }
    if (/FROM nebula\.cross_references cr/.test(sql)) return { rows: [] };
    if (/SELECT match_patterns FROM nebula\.op_registry/.test(sql)) return { rows: [] };
    if (/FROM nebula\.op_registry/.test(sql)) return { rows: [] };
    if (/INSERT INTO nebula\.agent_records/.test(sql)) return { rows: [] };
    if (/UPDATE requirements SET conduit_plan_id/.test(sql)) return { rows: [] };
    throw new Error(`Unexpected query: ${sql}`);
  }
}

let pool: FakePool;
let server: Server;
let base: string;
let fetchPlanMock: ReturnType<typeof vi.fn>;
const realFetch = globalThis.fetch.bind(globalThis);

beforeEach(async () => {
  pool = new FakePool();
  fetchPlanMock = vi.fn(async () => new Response(JSON.stringify(PLAN_RESPONSE), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchPlanMock);
  const app = express();
  app.use(express.json());
  app.use('/api', createRoutes(pool as never));
  server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Test server has no TCP address');
  base = `http://127.0.0.1:${address.port}/api/v1/capabilities/requirement-compile`;
});

afterEach(async () => {
  vi.unstubAllGlobals();
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
});

async function compile(body: unknown, key = 'compile-test-key') {
  return realFetch(`${base}/compile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify(body),
  });
}

describe('requirement compilation capability HTTP contract', () => {
  it('validates input before reserving an idempotency key', async () => {
    const response = await compile({ requirementId: '', dryRun: true });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ code: 'INVALID_REQUIREMENT_ID' });
    expect(pool.idempotency.size).toBe(0);
    expect(pool.requirementReads).toBe(0);
  });

  it('dry-run returns the mapped IR, persists a retrievable immutable receipt, and makes no plan call', async () => {
    const response = await compile({ requirementId: REQUIREMENT_ID, dryRun: true });
    expect(response.status).toBe(200);
    const { result } = await response.json() as any;
    expect(result.outcome).toBe('compiled');
    expect(result.ir.opSequence.length).toBeGreaterThan(0);
    expect(result.requirement.acceptanceCriteria).toEqual(['Service compiles', 'Tests pass']);
    expect(result.selection).toMatchObject({ kind: 'fallback', selectionMethod: 'longest-regex-substring/v1' });
    expect(result.selection.fallbackReason).toBeTruthy();
    expect(result.effect).toEqual({
      dryRun: true,
      planRequested: false,
      planCreated: false,
      planNumber: null,
      planStatus: null,
      executionAuthorized: false,
    });
    expect(fetchPlanMock).not.toHaveBeenCalled();

    const receiptResponse = await realFetch(`${base}/receipts/${result.receiptId}`);
    expect(receiptResponse.status).toBe(200);
    const { receipt } = await receiptResponse.json() as any;
    expect(receipt).toMatchObject({
      receiptSchema: 'nexus.compilation-receipt/v1',
      receiptId: result.receiptId,
      capabilityName: 'nexus.requirement.compile-to-plan',
      outcome: 'compiled',
      replayable: false,
      effect: { dryRun: true, executionAuthorized: false },
    });
    expect(receipt.resultIr).toEqual(result.ir);
    expect(receipt.canonicalInput).toEqual({ requirementId: REQUIREMENT_ID, createPlan: false, dryRun: true, deliverable: null });
    expect(receipt.normalizedInputDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it('returns the original response for a repeated key without recompiling', async () => {
    const request = { requirementId: REQUIREMENT_ID, dryRun: true };
    const first = await compile(request, 'stable-retry-key');
    const firstBody = await first.json();
    const second = await compile(request, 'stable-retry-key');
    expect(second.status).toBe(first.status);
    expect(await second.json()).toEqual(firstBody);
    expect(pool.requirementReads).toBe(1);
  });

  it('rejects reuse of a key with different canonical input', async () => {
    await compile({ requirementId: REQUIREMENT_ID, dryRun: true }, 'conflicting-key');
    const response = await compile({ requirementId: REQUIREMENT_ID, dryRun: false }, 'conflicting-key');
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ code: 'IDEMPOTENCY_KEY_CONFLICT' });
    expect(pool.requirementReads).toBe(1);
  });

  it('dryRun suppresses createPlan and records that requested effect as suppressed', async () => {
    const response = await compile({ requirementId: REQUIREMENT_ID, createPlan: true, dryRun: true }, 'dry-plan-key');
    expect(response.status).toBe(200);
    const { result } = await response.json() as any;
    expect(result.effect).toMatchObject({ dryRun: true, planRequested: true, planCreated: false, executionAuthorized: false });
    expect(result.warnings).toContainEqual(expect.objectContaining({ code: 'PLAN_SUPPRESSED_DRY_RUN' }));
    expect(fetchPlanMock).not.toHaveBeenCalled();
  });

  it('requires a caller idempotency key for plan creation', async () => {
    const response = await realFetch(`${base}/compile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirementId: REQUIREMENT_ID, createPlan: true }),
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ code: 'IDEMPOTENCY_KEY_REQUIRED' });
    expect(fetchPlanMock).not.toHaveBeenCalled();
  });

  it('reports a confirmed plan only as pending and never grants execution authority', async () => {
    const response = await compile({ requirementId: REQUIREMENT_ID, createPlan: true }, 'plan-create-key');
    expect(response.status).toBe(201);
    const { result } = await response.json() as any;
    expect(result.outcome).toBe('plan_created');
    expect(result.effect).toMatchObject({
      planRequested: true,
      planCreated: true,
      planNumber: '0088',
      planStatus: 'pending',
      executionAuthorized: false,
    });
    expect(fetchPlanMock).toHaveBeenCalledTimes(1);
  });

  it('reports a failed plan side effect as partial and retains the compiled IR', async () => {
    fetchPlanMock.mockResolvedValueOnce(new Response(JSON.stringify({ created: false }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    }));
    const response = await compile({ requirementId: REQUIREMENT_ID, createPlan: true }, 'partial-key');
    expect(response.status).toBe(200);
    const { result } = await response.json() as any;
    expect(result.outcome).toBe('partial');
    expect(result.ir).toBeDefined();
    expect(result.effect).toMatchObject({ planRequested: true, planCreated: false, executionAuthorized: false });
    expect(result.warnings).toContainEqual(expect.objectContaining({ code: 'PLAN_CREATION_FAILED' }));
  });

  it('fails closed when receipt persistence fails after compilation', async () => {
    pool.failReceiptWrite = true;
    const response = await compile({ requirementId: REQUIREMENT_ID, dryRun: true }, 'receipt-error-key');
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ code: 'RECEIPT_PERSISTENCE_FAILED' });
  });

  it('returns 404 for a missing immutable receipt', async () => {
    const response = await realFetch(`${base}/receipts/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`);
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ code: 'RECEIPT_NOT_FOUND' });
  });
});
