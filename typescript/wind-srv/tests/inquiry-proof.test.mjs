import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import express from 'express';

vi.mock('../src/db.js', () => ({ query: vi.fn(), pool: { connect: vi.fn() } }));
import { query, pool } from '../src/db.js';
import { executionRouter } from '../src/routes/execution.js';
import { errorHandler } from '../src/error-handler.js';
import { digest } from '../src/execution-contract.js';

// Real HTTP routes/validation, NOT a PostgreSQL transaction/constraint proof.
// The double accepts only the SQL used by external recording. Any dispatch,
// registry, lifecycle or unexpected query fails rather than contacting a DB.
let tables;
let failReceipt;
let sqlLog;
async function memoryQuery(sql, params = []) {
  const text = sql.replace(/\s+/g, ' ').trim();
  sqlLog.push(text);
  if (['BEGIN', 'COMMIT', 'ROLLBACK'].includes(text)) return { rows: [] };
  const insertion = text.match(/^INSERT INTO wind\.(execution_\w+) \(([^)]+)\)/);
  if (insertion) {
    const [, table, columns] = insertion;
    if (!tables[table]) throw new Error(`Unexpected table: ${table}`);
    if (table === 'execution_receipts' && failReceipt) {
      failReceipt = false;
      throw new Error('injected receipt persistence outage');
    }
    const row = Object.fromEntries(columns.split(',').map((name, index) => [name.trim(), params[index]]));
    if (table === 'execution_requests' && tables[table].some((old) => old.idempotency_key === row.idempotency_key)) {
      return { rows: [] };
    }
    if (table === 'execution_receipts') {
      row.evidence_refs = JSON.parse(row.evidence_refs);
      row.lineage = JSON.parse(row.lineage);
    }
    row.id = `00000000-0000-4000-8000-${String(1 + Object.values(tables).flat().length).padStart(12, '0')}`;
    tables[table].push(structuredClone(row));
    return { rows: [structuredClone(row)] };
  }
  const selection = text.match(/^SELECT (?:\*|id) FROM wind\.(execution_\w+) WHERE (.+)$/);
  if (!selection || !tables[selection[1]]) throw new Error(`Unexpected SQL: ${text}`);
  const conditions = selection[2].replace(/ (ORDER BY .*|FOR SHARE)$/, '').split(' AND ');
  const rows = tables[selection[1]].filter((row) => conditions.every((condition) => {
    const match = condition.match(/^(\w+) = \$(\d+)$/);
    if (!match) throw new Error(`Unexpected condition: ${condition}`);
    return row[match[1]] === params[Number(match[2]) - 1];
  }));
  return { rows: structuredClone(rows) };
}

beforeEach(() => {
  tables = { execution_requests: [], execution_attempts: [], execution_receipts: [] };
  sqlLog = [];
  failReceipt = false;
  query.mockReset().mockImplementation(memoryQuery);
  pool.connect.mockReset().mockResolvedValue({ query: memoryQuery, release: vi.fn() });
});

const app = express();
app.use(express.json());
app.use('/api/execution-requests', executionRouter);
app.use(errorHandler);
const server = http.createServer(app);
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/api/execution-requests`;
afterAll(() => new Promise((resolve, reject) => server.close((err) => err ? reject(err) : resolve())));

async function api(path, body) {
  const response = await fetch(base + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

// One persistent worker process: prepare happens before Wind recording,
// evaluation happens once, receipt recovery keeps that very same result.
async function startWorker() {
  const worker = spawn('python3', [fileURLToPath(new URL('./fixtures/inquiry_worker.py', import.meta.url))], {
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  worker.stderr.on('data', (chunk) => { stderr += chunk; });
  const lines = createInterface({ input: worker.stdout });
  const iterator = lines[Symbol.asyncIterator]();
  const exited = new Promise((resolve, reject) => {
    worker.once('error', reject);
    worker.once('exit', (code) => code === 0 ? resolve() : reject(new Error(`worker exit ${code}: ${stderr}`)));
  });
  // Attach a handler immediately; callers still observe the original failure.
  exited.catch(() => {});
  return {
    async call(command) {
      worker.stdin.write(JSON.stringify(command) + '\n');
      const next = await iterator.next();
      if (next.done) { await exited; throw new Error(`worker produced no output: ${stderr}`); }
      const output = JSON.parse(next.value);
      if (output.error) throw new Error(`${output.error}: ${output.message}`);
      return output;
    },
    async close() { worker.stdin.end(); await exited; lines.close(); },
  };
}

function executionRequest(prepared, key) {
  return {
    idempotency_key: key,
    artifact_type: 'solscript.proposition', artifact_ref: 'prop-expression-001', artifact_revision: 'fixture-v1',
    artifact_fingerprint: prepared.artifact_fingerprint,
    read_set_digest: prepared.read_set_digest,
    evaluator_contract_digest: prepared.evaluator_contract_digest,
    correlation_id: prepared.work_request.id,
    // Truthful external test evaluator, NOT the registered echo adapter.
    // External recording validates this declaration; it does not verify registry binding.
    provider_contract: {
      contract_version: 1, registry_revision_number: 1,
      adapter_id: 'test.solscript-inquiry-worker', adapter_version: '1',
      provider_id: 'test.local-python', provider_version: '1',
      input_schema_digest: digest({ schema: prepared.manifest.schema }),
      output_schema_digest: digest({ schema: 'test.EvaluationDisposition.v1' }),
    },
    invocation_contract: {
      contract_version: 1, mode: 'SDK', timeout_ms: 30000,
      cancellation_mode: 'NONE', retry_budget: 0, evidence_ref_mode: 'REQUIRED',
    },
    failure_policy: {
      unavailable_outcome: 'UNAVAILABLE', timeout_outcome: 'FAILED',
      malformed_result_outcome: 'INVALID', stale_outcome: 'STALE',
    },
  };
}

function retain(prepared) {
  const store = new Map();
  const evidence = [
    ['read-set', prepared.manifest, prepared.read_set_digest],
    ['evaluator', prepared.evaluator, prepared.evaluator_contract_digest],
  ].map(([type, value, fingerprint]) => {
    const ref = `test-memory:${fingerprint}`;
    store.set(ref, structuredClone(value));
    return { type, ref, digest: fingerprint };
  });
  return { store, evidence };
}

describe('single-proposition inquiry across SOLScript, Wind HTTP and Vision receipt', () => {
  it.each([['open', 'asserted', true], ['closed', 'rejected', false]])(
    '%s evaluates to %s without admission; immutable recording replays and recovers',
    async (status, disposition, allPassed) => {
      const worker = await startWorker();
      try {
        const prepared = await worker.call({ operation: 'prepare', status });
        expect(prepared.work_request.constraints.mutation_policy).toBe('forbidden');
        expect(prepared.work_request.context.inquiry.expected_outcome_type).toBe('EvaluationDisposition');
        expect(digest(prepared.manifest)).toBe(prepared.read_set_digest);
        expect(digest(prepared.evaluator)).toBe(prepared.evaluator_contract_digest);
        expect(digest(prepared.manifest.stores.propositions['prop-expression-001'])).toBe(prepared.artifact_fingerprint);
        expect(prepared.source_after_pin_digest).not.toBe(prepared.read_set_digest);
        // Retain resolvable manifest/evaluator BEFORE creating a Wind request.
        const retained = retain(prepared);
        const requestBody = executionRequest(prepared, `inquiry:${status}`);
        const created = await api('', requestBody);
        expect(created.status).toBe(201);
        const requestId = created.body.id;
        expect(created.body.workflow_version_id).toBeNull();
        expect(created.body.node_id).toBeNull();
        expect((await api('', requestBody)).status).toBe(200);
        expect((await api('', { ...requestBody, read_set_digest: digest({ changed: true }) })).status).toBe(409);

        const observed = await worker.call({ operation: 'evaluate' });
        expect(observed.state_unchanged).toBe(true);
        expect(observed.evaluation_calls).toBe(1);
        expect(observed.result).toEqual({
          proposition_id: 'prop-expression-001', disposition, all_passed: allPassed,
          // Current interpreter treats the empty required-dimension set as
          // scoped (empty-set subset branch). Preserve observed evidence;
          // do not normalize it to the earlier design's not_scoped label.
          context_status: 'scoped', read_set_digest: prepared.read_set_digest,
          evaluator_contract_digest: prepared.evaluator_contract_digest,
          authority_status: 'evaluation_only', mutation_policy: 'forbidden',
        });
        expect(digest(observed.result)).toBe(observed.result_digest);
        const attemptBody = {
          attempt_number: 1, attempt_idempotency_key: 'evaluation:1',
          executor_id: 'test.solscript-inquiry-worker', outcome_status: 'SUCCEEDED',
          result: observed.result, result_digest: observed.result_digest,
        };
        expect((await api(`/${requestId}/attempts`, { ...attemptBody, result_digest: digest({ forged: true }) })).status).toBe(400);
        const attempt = await api(`/${requestId}/attempts`, attemptBody);
        expect(attempt.status).toBe(201);
        expect((await api(`/${requestId}/attempts`, attemptBody)).status).toBe(200);
        const changedResult = { ...observed.result, disposition: 'disputed' };
        expect((await api(`/${requestId}/attempts`, {
          ...attemptBody, result: changedResult, result_digest: digest(changedResult),
        })).status).toBe(409);

        const resultRef = `test-memory:${observed.result_digest}`;
        retained.store.set(resultRef, structuredClone(observed.result));
        retained.evidence.push({ type: 'result', ref: resultRef, digest: observed.result_digest });
        const receiptBody = {
          attempt_id: attempt.body.id, outcome_status: 'SUCCEEDED', result_digest: observed.result_digest,
          evidence_refs: retained.evidence,
          lineage: { work_request_id: prepared.work_request.id, execution_id: '82651675-6060-4205-a243-ccae4bd9b36f', advisory: true },
        };
        expect((await api(`/${requestId}/receipts`, { ...receiptBody, outcome_status: 'FAILED' })).status).toBe(400);
        expect((await api(`/${requestId}/receipts`, { ...receiptBody, result_digest: digest({ forged: true }) })).status).toBe(400);
        failReceipt = true;
        expect((await api(`/${requestId}/receipts`, receiptBody)).status).toBe(500);
        expect(tables.execution_attempts).toHaveLength(1);
        expect(tables.execution_receipts).toHaveLength(0);
        // Retry evidence recording, NOT worker evaluation.
        const windReceipt = await api(`/${requestId}/receipts`, receiptBody);
        expect(windReceipt.status).toBe(201);
        expect((await api(`/${requestId}/receipts`, receiptBody)).status).toBe(200);
        expect((await api(`/${requestId}/receipts`, { ...receiptBody, evidence_refs: [] })).status).toBe(409);
        const evidence = await api(`/${requestId}`);
        expect(evidence.status).toBe(200);
        expect(evidence.body.attempts).toHaveLength(1);
        expect(evidence.body.receipts).toHaveLength(1);
        expect(evidence.body.attempts[0].status).toBe('SUCCEEDED'); // rejected != execution failure
        for (const ref of evidence.body.receipts[0].evidence_refs) {
          expect(retained.store.has(ref.ref)).toBe(true);
          expect(digest(retained.store.get(ref.ref))).toBe(ref.digest);
        }
        const vision = await worker.call({ operation: 'receipt', wind_refs: {
          request_id: requestId, attempt_id: attempt.body.id, receipt_id: windReceipt.body.id,
        } });
        expect(vision.evaluation_calls).toBe(1);
        expect(vision.receipt.result).toBe('SUCCESS');
        expect(vision.receipt.mutations).toEqual([]);
        expect(vision.receipt.work_request_id).toBe(prepared.work_request.id);
        expect(vision.receipt.inquiry_outcome).toEqual(observed.result);
        expect(vision.receipt.result_digest).toBe(observed.result_digest);
        expect(vision.receipt.wind_refs.receipt_id).toBe(windReceipt.body.id);
        await expect(worker.call({ operation: 'evaluate' })).rejects.toThrow('evaluation already completed');
        expect(sqlLog.every((sql) => !/registry|resolution\.|lifecycle|planning_task/.test(sql))).toBe(true);
      } finally { await worker.close(); }
    },
  );

  it('fresh-worker replay is deterministic; entity values change R but not A or E', async () => {
    const samples = [];
    for (const status of ['open', 'open', 'closed']) {
      const worker = await startWorker();
      try {
        const prepared = await worker.call({ operation: 'prepare', status });
        const observed = await worker.call({ operation: 'evaluate' });
        samples.push({ prepared, observed });
      } finally { await worker.close(); }
    }
    expect(samples[0]).toEqual(samples[1]);
    expect(samples[2].prepared.artifact_fingerprint).toBe(samples[0].prepared.artifact_fingerprint);
    expect(samples[2].prepared.evaluator_contract_digest).toBe(samples[0].prepared.evaluator_contract_digest);
    expect(samples[2].prepared.read_set_digest).not.toBe(samples[0].prepared.read_set_digest);
    expect(samples[2].observed.result_digest).not.toBe(samples[0].observed.result_digest);
  });
});
