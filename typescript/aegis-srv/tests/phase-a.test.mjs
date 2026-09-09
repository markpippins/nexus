import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { canonicalJson, digestJson, mapCheckerOutcome } = require('../dist/phase-a.js');

test('canonicalJson sorts object keys recursively but preserves array order', () => {
  assert.equal(
    canonicalJson({ b: 2, a: { d: 4, c: 3 }, list: [{ z: 1, y: 2 }] }),
    '{"a":{"c":3,"d":4},"b":2,"list":[{"y":2,"z":1}]}',
  );
});

test('digestJson is stable for equivalent object key orderings', () => {
  assert.equal(digestJson({ b: 2, a: 1 }), digestJson({ a: 1, b: 2 }));
  assert.notEqual(digestJson({ a: 1, b: 2 }), digestJson({ a: 1, b: 3 }));
  assert.match(digestJson({ a: 1 }), /^sha256:[0-9a-f]{64}$/);
});

test('real TLC success verifies safety only and remains advisory', () => {
  const result = mapCheckerOutcome('tlc', 'success');
  assert.deepEqual(result, {
    status: 'verified',
    safety_status: 'verified',
    liveness_status: 'unknown',
    authority_level: 'advisory',
    reason: 'TLC completed without a safety violation; liveness remains unknown at the gate',
  });
});

test('TLC counterexample becomes violated and never verified', () => {
  const result = mapCheckerOutcome('tlc', 'failure', 'counterexample');
  assert.equal(result.status, 'violated');
  assert.equal(result.safety_status, 'violated');
  assert.equal(result.liveness_status, 'unknown');
  assert.equal(result.authority_level, 'advisory');
});

test('structural success remains unknown because it is not a formal proof', () => {
  const result = mapCheckerOutcome('structural', 'success');
  assert.equal(result.status, 'unknown');
  assert.equal(result.safety_status, 'unknown');
  assert.equal(result.liveness_status, 'unknown');
  assert.equal(result.authority_level, 'advisory');
});

test('structural failure is invalid, not a formal violation', () => {
  const result = mapCheckerOutcome('structural', 'failure');
  assert.equal(result.status, 'invalid');
  assert.equal(result.safety_status, 'invalid');
  assert.equal(result.liveness_status, 'unknown');
});

test('checker error is unavailable and cannot become verified', () => {
  const result = mapCheckerOutcome('tlc', 'error', 'TLC timed out');
  assert.equal(result.status, 'unavailable');
  assert.equal(result.safety_status, 'unavailable');
  assert.equal(result.liveness_status, 'unknown');
  assert.notEqual(result.status, 'verified');
});
