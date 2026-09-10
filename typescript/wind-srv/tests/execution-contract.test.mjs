import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertOutcome, canonicalJson, digest, requestMaterial } from '../src/execution-contract.js';

const material = (overrides = {}) => requestMaterial({
  artifact_type: 'wind.task',
  artifact_ref: 'task-1',
  artifact_revision: 'r1',
  artifact_fingerprint: `sha256:${'a'.repeat(64)}`,
  read_set_digest: `sha256:${'b'.repeat(64)}`,
  evaluator_contract_digest: `sha256:${'c'.repeat(64)}`,
  correlation_id: 'corr',
  provider_contract: { region: 'local' },
  invocation_contract: {},
  failure_policy: {},
  ...overrides,
});

test('canonical JSON makes request material key-order independent', () => {
  assert.equal(canonicalJson({ b: 2, a: 1 }), canonicalJson({ a: 1, b: 2 }));
  assert.equal(digest(material()), digest(material()));
  assert.notEqual(digest(material({ artifact_revision: 'r2' })), digest(material()));
});

test('accepts every truthful terminal outcome and rejects claims outside the vocabulary', () => {
  for (const status of ['SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID']) assert.equal(assertOutcome(status.toLowerCase()), status);
  assert.throws(() => assertOutcome('VERIFIED'), /outcome_status/);
});
