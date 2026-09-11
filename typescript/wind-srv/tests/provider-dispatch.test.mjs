import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  assertRegisteredContract,
  classifyDispatchFailure,
  dispatchInputDigest,
  dispatchReplay,
  invokeRegisteredAdapter,
  resolveEnvironmentReference,
  resolveEnvironmentReferenceMetadata,
} from '../src/provider-dispatch.js';

const digest = (letter) => `sha256:${letter.repeat(64)}`;
const registered = {
  revision_number: 1,
  adapter_id: 'wind.echo.v1',
  adapter_version: '1.0.0',
  provider_id: 'wind-local',
  provider_version: '1',
  invocation_mode: 'SDK',
  input_schema_digest: digest('1'),
  output_schema_digest: digest('2'),
  schema_verification: 'verified',
  lifecycle_state: 'ACTIVE',
};
const request = {
  artifact_type: 'wind.task',
  artifact_ref: 'task-1',
  artifact_revision: 'r1',
  artifact_fingerprint: digest('a'),
  provider_contract: {
    registry_revision_number: 1,
    adapter_id: registered.adapter_id,
    adapter_version: registered.adapter_version,
    provider_id: registered.provider_id,
    provider_version: registered.provider_version,
    input_schema_digest: registered.input_schema_digest,
    output_schema_digest: registered.output_schema_digest,
  },
};
const invocation = { mode: 'SDK', timeout_ms: 1000 };

test('rejects inactive, stale, and misconfigured registered contracts', () => {
  assert.equal(assertRegisteredContract(request.provider_contract, invocation, registered), registered);
  assert.throws(
    () => assertRegisteredContract(request.provider_contract, invocation, { ...registered, lifecycle_state: 'DEACTIVATED' }),
    /not registered/,
  );
  assert.throws(
    () => assertRegisteredContract({ ...request.provider_contract, registry_revision_number: 2 }, invocation, registered),
    /stale/,
  );
  assert.throws(
    () => assertRegisteredContract(request.provider_contract, { mode: 'HTTP' }, {
      ...registered,
      invocation_mode: 'HTTP',
      endpoint_env_ref: null,
    }),
    /endpoint environment reference/,
  );
});

test('replay selects the terminal child and matching receipt without creating evidence', () => {
  const reservation = { id: 'reservation-1', attempt_idempotency_key: 'dispatch:replay-1' };
  const terminal = {
    id: 'terminal-1',
    parent_attempt_id: reservation.id,
    attempt_idempotency_key: 'dispatch:replay-1:terminal',
    status: 'SUCCEEDED',
  };
  const receipt = { id: 'receipt-1', attempt_id: terminal.id, outcome_status: 'SUCCEEDED' };
  const replay = dispatchReplay([reservation, terminal], [receipt], 'replay-1');
  assert.equal(replay.inProgress, false);
  assert.equal(replay.reservation, reservation);
  assert.equal(replay.attempt, terminal);
  assert.equal(replay.receipt, receipt);
});

test('replay reports an unfinished reservation without fabricating a receipt', () => {
  const reservation = { id: 'reservation-2', attempt_idempotency_key: 'dispatch:replay-2' };
  const replay = dispatchReplay([reservation], [], 'replay-2');
  assert.equal(replay.inProgress, true);
  assert.equal(replay.attempt, null);
  assert.equal(replay.receipt, null);
});

test('credential metadata returns only a reference and fingerprint', () => {
  const metadata = resolveEnvironmentReferenceMetadata('TEST_PROVIDER_SECRET', 'credential_env_ref', {
    TEST_PROVIDER_SECRET: 'never-persist-this-value',
  });
  assert.deepEqual(Object.keys(metadata).sort(), ['fingerprint', 'reference']);
  assert.equal(metadata.reference, 'TEST_PROVIDER_SECRET');
  assert.match(metadata.fingerprint, /^sha256:[0-9a-f]{64}$/);
  assert.doesNotMatch(JSON.stringify(metadata), /never-persist-this-value/);
});

test('secret and endpoint references fail closed when the environment boundary is empty', () => {
  assert.throws(
    () => resolveEnvironmentReference('TEST_PROVIDER_SECRET', 'credential_env_ref', {}),
    /not configured in the secret boundary/,
  );
  assert.throws(
    () => resolveEnvironmentReference('not-an-env-ref', 'endpoint_env_ref', {}),
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
