/**
 * Conformance tests for nexus.requirement.compile-to-plan/1.0 (plan 8261662).
 *
 * Complements requirement-compile-contract.test.ts with the two provider
 * scenarios that file does not exercise:
 *   1. a successful *registry match* (not the criteria fallback), and
 *   2. a requirement that does not exist (404) while still persisting an
 *      immutable failed receipt.
 *
 * Both cases assert the permanent boundary: executionAuthorized is false and
 * no plan is ever requested by a dry run.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'http';
import { createRoutes } from '../routes';

const REQUIREMENT_ID = 'requirement-registry-match';
const REGISTRY_ID = '11111111-1111-4111-8111-111111111111';
const MATCH_PATTERNS = ['^build a sample service', 'sample service'];

function requirementRow(id = REQUIREMENT_ID) {
  return {
    id,
    title: 'Build a sample service',
    description: 'Scaffold a service from the registry template',
    status: 'open',
    priority: 'medium',
    req_type: 'feature',
    acceptance_criteria: ['Service compiles'],
    candidate_id: null,
    system_id: null,
    subsystem_id: null,
    feature_id: null,
    parent_id: null,
    system_name: '',
    system_description: '',
    subsystem_name: '',
    subsystem_description: '',
    feature_name: '',
    feature_description: '',
  };
}

class FakePool {
  idempotency = new Map<string, { input_digest: string; state: string; response?: unknown; response_status?: number }>();
  receipts = new Map<string, any>();
  requirementRows: any[] = [requirementRow()];

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
      const [receiptId, key, digest, , , receipt, response, responseStatus] = values;
      const parsedReceipt = JSON.parse(receipt);
      this.receipts.set(receiptId, parsedReceipt);
      this.idempotency.set(key, {
        input_digest: digest,
        state: 'completed',
        response: JSON.parse(response),
        response_status: responseStatus,
      });
      return { rows: [{ response: JSON.parse(response) }] };
    }
    if (/SELECT receipt FROM nebula\.compilation_receipts/.test(sql)) {
      const receipt = this.receipts.get(values[0]);
      return { rows: receipt ? [{ receipt }] : [] };
    }
    if (/FROM nebula\.requirements req/.test(sql)) return { rows: this.requirementRows };
    if (/FROM nebula\.cross_references cr/.test(sql)) return { rows: [] };
    // Selection-evidence lookup performed by the versioned adapter.
    if (/SELECT match_patterns FROM nebula\.op_registry WHERE id = \$1/.test(sql)) {
      return { rows: [{ match_patterns: MATCH_PATTERNS }] };
    }
    // Active-registry lookup performed by the legacy compiler.
    if (/FROM nebula\.op_registry/.test(sql)) {
      return {
        rows: [{
          id: REGISTRY_ID,
          intent_id: 'INIT_SERVICE_SCAFFOLD',
          version: 'v1',
          label: 'Scaffold service',
          match_patterns: MATCH_PATTERNS,
          opcode_template: [{ op: 'WRITE_FILE', target: 'src/sample/index.ts', params: { template: 'service' } }],
          required_params: [],
          idempotency_key: 'init-service',
        }],
      };
    }
    if (/INSERT INTO nebula\.agent_records/.test(sql)) return { rows: [] };
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
  fetchPlanMock = vi.fn();
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

async function compile(body: unknown, key: string) {
  return realFetch(`${base}/compile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify(body),
  });
}

describe('requirement compilation capability conformance', () => {
  it('reports an explicit registry match, its pattern and template IR without granting execution', async () => {
    const response = await compile({ requirementId: REQUIREMENT_ID, dryRun: true }, 'registry-match-key');
    expect(response.status).toBe(200);
    const { result } = await response.json() as any;

    expect(result.outcome).toBe('compiled');
    expect(result.selection).toEqual({
      kind: 'registry_match',
      entryId: REGISTRY_ID,
      intentId: 'INIT_SERVICE_SCAFFOLD',
      version: 'v1',
      matchedPattern: '^build a sample service',
      selectionMethod: 'longest-regex-substring/v1',
      fallbackReason: null,
    });
    expect(result.ir.opSequence).toEqual([
      {
        step: 1,
        opcode: 'WRITE_FILE',
        target: 'src/sample/index.ts',
        args: { template: 'service' },
        idempotencyKey: 'init-service-requirem',
      },
    ]);
    expect(result.effect).toMatchObject({ dryRun: true, planCreated: false, executionAuthorized: false });
    expect(fetchPlanMock).not.toHaveBeenCalled();

    const receiptResponse = await realFetch(`${base}/receipts/${result.receiptId}`);
    expect(receiptResponse.status).toBe(200);
    const { receipt } = await receiptResponse.json() as any;
    expect(receipt.selection.kind).toBe('registry_match');
    expect(receipt.selection.matchedPattern).toBe('^build a sample service');
    expect(receipt.resultIr).toEqual(result.ir);
    expect(receipt.effect.executionAuthorized).toBe(false);
  });

  it('returns 404 for an unknown requirement and still persists a retrievable failed receipt', async () => {
    pool.requirementRows = [];
    const response = await compile({ requirementId: 'does-not-exist', dryRun: true }, 'missing-requirement-key');
    expect(response.status).toBe(404);
    const body = await response.json() as any;
    expect(body).toMatchObject({ code: 'REQUIREMENT_NOT_FOUND' });
    expect(body.receiptId).toMatch(/^[0-9a-f-]{36}$/);
    expect(fetchPlanMock).not.toHaveBeenCalled();

    const receiptResponse = await realFetch(`${base}/receipts/${body.receiptId}`);
    expect(receiptResponse.status).toBe(200);
    const { receipt } = await receiptResponse.json() as any;
    expect(receipt).toMatchObject({
      receiptId: body.receiptId,
      outcome: 'failed',
      error: { code: 'REQUIREMENT_NOT_FOUND' },
      effect: { dryRun: true, planCreated: false, executionAuthorized: false },
      replayable: false,
    });
  });
});
