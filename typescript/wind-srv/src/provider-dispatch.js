import { createHash, randomUUID } from 'node:crypto';
import { canonicalJson, digest } from './execution-contract.js';

export const TERMINAL_OUTCOMES = new Set(['SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID']);

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function envRef(value, name) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'string' || !/^[A-Z][A-Z0-9_]{0,127}$/.test(value)) {
    throw new Error(`${name} is not a valid environment reference`);
  }
  return value;
}

/** Resolve a secret/reference from the process environment without exposing its value. */
export function resolveEnvironmentReference(reference, name, environment = process.env) {
  const key = envRef(reference, name);
  if (!key) return null;
  const value = environment[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name} ${key} is not configured in the secret boundary`);
  }
  return value;
}

function sameContract(request, registered) {
  return request?.adapter_id === registered.adapter_id
    && request?.adapter_version === registered.adapter_version
    && request?.provider_id === registered.provider_id
    && request?.provider_version === registered.provider_version
    && request?.input_schema_digest === registered.input_schema_digest
    && request?.output_schema_digest === registered.output_schema_digest;
}

function sameInvocation(request, registered) {
  return request?.mode === registered.invocation_mode;
}

/**
 * Confirm that the persisted request is still bound to the persisted adapter.
 * The registry is authoritative for dispatchability; callers cannot inject a
 * module, URL, provider, or credential value at dispatch time.
 */
export function assertRegisteredContract(request, invocation, registered) {
  if (!registered || registered.is_active !== true || registered.schema_verification !== 'verified') {
    const error = new Error('provider adapter is not registered, active, and schema-verified');
    error.kind = 'UNAVAILABLE';
    throw error;
  }
  if (!sameContract(request, registered) || !sameInvocation(invocation, registered)) {
    const error = new Error('request provider contract is stale relative to the registered adapter contract');
    error.kind = 'STALE';
    throw error;
  }
  if (registered.invocation_mode === 'HTTP' && !registered.endpoint_env_ref) {
    const error = new Error('HTTP adapter has no registered endpoint environment reference');
    error.kind = 'INVALID';
    throw error;
  }
  return registered;
}

function boundedTimeout(value) {
  return Math.min(Math.max(Number(value) || 1, 1), 900000);
}

function resultEnvelope(value) {
  if (!isObject(value)) throw new Error('provider result must be a JSON object');
  return value;
}

async function invokeEcho({ request, input }) {
  return {
    adapter_id: 'wind.echo.v1',
    observed: true,
    input: input ?? {
      artifact_type: request.artifact_type,
      artifact_ref: request.artifact_ref,
      artifact_revision: request.artifact_revision,
      artifact_fingerprint: request.artifact_fingerprint,
    },
  };
}

async function invokeHttp({ registered, request, input, timeoutMs, environment }) {
  const endpoint = resolveEnvironmentReference(registered.endpoint_env_ref, 'endpoint_env_ref', environment);
  const credential = resolveEnvironmentReference(registered.credential_env_ref, 'credential_env_ref', environment);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), boundedTimeout(timeoutMs));
  try {
    const headers = { 'content-type': 'application/json', accept: 'application/json' };
    if (credential) headers.authorization = `Bearer ${credential}`;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        artifact_type: request.artifact_type,
        artifact_ref: request.artifact_ref,
        artifact_revision: request.artifact_revision,
        artifact_fingerprint: request.artifact_fingerprint,
        input: input ?? null,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const error = new Error(`provider returned HTTP ${response.status}`);
      error.kind = 'UNAVAILABLE';
      throw error;
    }
    let body;
    try { body = await response.json(); } catch (_) {
      const error = new Error('provider returned malformed JSON');
      error.kind = 'INVALID';
      throw error;
    }
    return resultEnvelope(body);
  } catch (error) {
    if (error.name === 'AbortError') {
      error.kind = 'FAILED';
      error.message = `provider timed out after ${boundedTimeout(timeoutMs)}ms`;
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Dispatch one registered adapter. This function performs no lifecycle writes. */
export async function invokeRegisteredAdapter({ registered, request, invocation, input, environment = process.env }) {
  const startedAt = Date.now();
  let result;
  if (registered.adapter_id === 'wind.echo.v1' && registered.invocation_mode === 'SDK') {
    result = await invokeEcho({ request, input });
  } else if (registered.adapter_id === 'wind.http-json.v1' && registered.invocation_mode === 'HTTP') {
    result = await invokeHttp({
      registered,
      request,
      input,
      timeoutMs: invocation.timeout_ms,
      environment,
    });
  } else {
    const error = new Error(`no implementation for registered adapter ${registered.adapter_id}`);
    error.kind = 'UNAVAILABLE';
    throw error;
  }
  const normalized = resultEnvelope(result);
  return {
    result: normalized,
    result_digest: digest(normalized),
    provider_invocation_ref: `provider:${registered.adapter_id}:${randomUUID()}`,
    duration_ms: Date.now() - startedAt,
  };
}

export function classifyDispatchFailure(error, failurePolicy) {
  const kind = error?.kind || 'UNAVAILABLE';
  if (kind === 'STALE') return { outcome: failurePolicy.stale_outcome || 'STALE', reason: error.message };
  if (kind === 'INVALID') return { outcome: failurePolicy.malformed_result_outcome || 'INVALID', reason: error.message };
  if (kind === 'FAILED' && /timed out/i.test(error.message)) {
    return { outcome: failurePolicy.timeout_outcome || 'FAILED', reason: error.message };
  }
  return { outcome: failurePolicy.unavailable_outcome || 'UNAVAILABLE', reason: error.message };
}

export function dispatchInputDigest(input) {
  return `sha256:${createHash('sha256').update(canonicalJson(input ?? null), 'utf8').digest('hex')}`;
}
