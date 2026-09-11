import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  assertRegisteredContract,
  classifyDispatchFailure,
  dispatchInputDigest,
  invokeRegisteredAdapter,
  resolveEnvironmentReference,
} from '../src/provider-dispatch.js';

const digest = (letter) => `sha256:${letter.repeat(64)}`;
const registered = {
  adapter_id: 'wind.echo.v1',
  adapter_version: '1.0.0',
  provider_id: 'wind-local',
  provider_version: '1',
  invocation_mode: 'SDK',
  input_schema_digest: digest('1'),
  output_schema_digest: digest('2'),
  schema_verification: 'verified',
  is_active: true,
};
const request = {
  artifact_type: 'wind.task',
  artifact_ref: 'task-1',
  artifact_revision: 'r1',
  artifact_fingerprint: digest('a'),
  provider_contract: { ...registered },
};
const invocation = { mode: 'SDK', timeout_ms: 1000 };

test('accepts only an active schema-verified registered adapter', () => {
  assert.equal(assertRegisteredContract(request.provider_contract, invocation, registered), registered);
  assert.throws(
    () => assertRegisteredContract(request.provider_contract, invocation, { ...registered, is_active: false }),
    /not registered/,
  );
  assert.throws(
    () => assertRegisteredContract(request.provider_contract, invocation, { ...registered, output_schema_digest: digest('9') }),
    /stale/,
  );
});

test('resolves environment references without accepting material in the contract', () => {
  assert.equal(resolveEnvironmentReference('TEST_PROVIDER_SECRET', 'credential_env_ref', { TEST_PROVIDER_SECRET: 'secret-value' }), 'secret-value');
  assert.throws(
    () => resolveEnvironmentReference('TEST_PROVIDER_SECRET', 'credential_env_ref', {}),
    /not configured/,
  );
  assert.throws(
    () => resolveEnvironmentReference('not-an-env-ref', 'credential_env_ref', {}),
    /valid environment reference/,
  );
});

test('dispatches the built-in echo adapter and returns a digestable observed result', async () => {
  const observed = await invokeRegisteredAdapter({
    registered,
    request,
    invocation,
    input: { hello: 'world' },
  });
  assert.equal(observed.result.observed, true);
  assert.equal(observed.result.input.hello, 'world');
  assert.match(observed.result_digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(observed.provider_invocation_ref, /^provider:wind\.echo\.v1:/);
});

test('maps stale, timeout, malformed, and unavailable failures truthfully', () => {
  const policy = {
    stale_outcome: 'STALE',
    timeout_outcome: 'FAILED',
    malformed_result_outcome: 'INVALID',
    unavailable_outcome: 'UNAVAILABLE',
  };
  assert.equal(classifyDispatchFailure(Object.assign(new Error('stale'), { kind: 'STALE' }), policy).outcome, 'STALE');
  assert.equal(classifyDispatchFailure(Object.assign(new Error('provider timed out'), { kind: 'FAILED' }), policy).outcome, 'FAILED');
  assert.equal(classifyDispatchFailure(Object.assign(new Error('bad JSON'), { kind: 'INVALID' }), policy).outcome, 'INVALID');
  assert.equal(classifyDispatchFailure(Object.assign(new Error('offline'), { kind: 'UNAVAILABLE' }), policy).outcome, 'UNAVAILABLE');
  assert.match(dispatchInputDigest({ hello: 'world' }), /^sha256:[0-9a-f]{64}$/);
});
