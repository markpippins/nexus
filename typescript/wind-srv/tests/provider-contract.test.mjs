import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateProviderInvocation } from '../src/provider-contract.js';

const digest = (letter) => `sha256:${letter.repeat(64)}`;

const valid = (overrides = {}) => ({
  provider_contract: {
    contract_version: 1,
    registry_revision_number: 1,
    adapter_id: 'adapter.test',
    adapter_version: '1.2.3',
    provider_id: 'provider.test',
    provider_version: '2026-09-10',
    input_schema_digest: digest('a'),
    output_schema_digest: digest('b'),
    ...(overrides.provider_contract || {}),
  },
  invocation_contract: {
    contract_version: 1,
    mode: 'HTTP',
    timeout_ms: 30000,
    cancellation_mode: 'COOPERATIVE',
    retry_budget: 2,
    evidence_ref_mode: 'REQUIRED',
    ...(overrides.invocation_contract || {}),
  },
  failure_policy: {
    unavailable_outcome: 'UNAVAILABLE',
    timeout_outcome: 'FAILED',
    malformed_result_outcome: 'INVALID',
    stale_outcome: 'STALE',
    ...(overrides.failure_policy || {}),
  },
});

test('normalizes a valid advisory provider invocation contract', () => {
  const result = validateProviderInvocation(valid());
  assert.equal(result.provider_contract.contract_version, 1);
  assert.equal(result.invocation_contract.mode, 'HTTP');
  assert.equal(result.invocation_contract.timeout_ms, 30000);
  assert.equal(result.failure_policy.unavailable_outcome, 'UNAVAILABLE');
});

test('rejects incomplete provider identity and schema lineage', () => {
  assert.throws(
    () => validateProviderInvocation(valid({ provider_contract: { input_schema_digest: undefined } })),
    /input_schema_digest/,
  );
  assert.throws(
    () => validateProviderInvocation(valid({ provider_contract: { contract_version: 2 } })),
    /contract_version/,
  );
});

test('rejects unsafe invocation bounds and success-masking failure policy', () => {
  assert.throws(
    () => validateProviderInvocation(valid({ invocation_contract: { timeout_ms: 0 } })),
    /timeout_ms/,
  );
  assert.throws(
    () => validateProviderInvocation(valid({ invocation_contract: { retry_budget: 11 } })),
    /retry_budget/,
  );
  assert.throws(
    () => validateProviderInvocation(valid({ failure_policy: { timeout_outcome: 'SUCCEEDED' } })),
    /cannot map failure to SUCCEEDED/,
  );
});

test('rejects lifecycle authority and unknown invocation modes', () => {
  assert.throws(
    () => validateProviderInvocation(valid({ invocation_contract: { mode: 'PYTHON' } })),
    /mode must be one of/,
  );
  assert.throws(
    () => validateProviderInvocation(valid({ provider_contract: { authority_level: 'binding' } })),
    /authority_level must be advisory/,
  );
  assert.throws(
    () => validateProviderInvocation(valid({ invocation_contract: { admit_lifecycle: true } })),
    /cannot request lifecycle admission/,
  );
});
