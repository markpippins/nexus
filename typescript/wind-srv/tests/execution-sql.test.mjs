import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const sql = readFileSync(new URL('../../../sql/V151__wind_execution_request_receipt_seam.sql', import.meta.url), 'utf8');

test('Phase B migration contains the revision-pinned request contract', () => {
  for (const field of ['artifact_fingerprint', 'artifact_revision', 'read_set_digest', 'evaluator_contract_digest', 'causation_id', 'correlation_id', 'idempotency_key', 'provider_contract', 'invocation_contract', 'failure_policy']) {
    assert.match(sql, new RegExp(`\\b${field}\\b`));
  }
  assert.match(sql, /authority_level\s+text\s+NOT NULL\s+DEFAULT 'advisory'/);
});

test('Phase B migration is append-only and enumerates truthful outcomes', () => {
  for (const table of ['execution_requests', 'execution_attempts', 'execution_receipts']) {
    assert.match(sql, new RegExp(`trg_wind_${table}_immutable`));
  }
  for (const status of ['SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID']) assert.match(sql, new RegExp(status));
  assert.match(sql, /USING ERRCODE = 'restrict_violation'/);
});
