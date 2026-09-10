import { assertOutcome, DIGEST } from './execution-contract.js';

export const INVOCATION_MODES = new Set(['CLI', 'HTTP', 'SDK', 'MCP']);
export const CANCELLATION_MODES = new Set(['COOPERATIVE', 'HARD_TIMEOUT', 'NONE']);
export const EVIDENCE_REF_MODES = new Set(['REQUIRED', 'OPTIONAL']);

function requiredString(value, name) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function digestField(value, name) {
  const result = requiredString(value, name);
  if (!DIGEST.test(result)) throw new Error(`${name} must be a sha256 digest`);
  return result;
}

function enumField(value, name, allowed) {
  const result = requiredString(value, name).toUpperCase();
  if (!allowed.has(result)) throw new Error(`${name} must be one of: ${[...allowed].join(', ')}`);
  return result;
}

function boundedInteger(value, name, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function rejectAuthorityFields(value, name) {
  if (value.authority_level !== undefined && value.authority_level !== 'advisory') {
    throw new Error(`${name}.authority_level must be advisory`);
  }
  if (value.admit_lifecycle === true || value.mutate_lifecycle === true || value.lifecycle_transition !== undefined) {
    throw new Error(`${name} cannot request lifecycle admission or mutation`);
  }
}

/**
 * Validate the provider/adapter plan carried by an advisory execution request.
 *
 * This is deliberately a contract validator, not a dispatcher: it performs no
 * network or process I/O. The returned objects are the only normalized values
 * that should be persisted or passed to an adapter in a later slice.
 */
export function validateProviderInvocation({ provider_contract, invocation_contract, failure_policy }) {
  if (!provider_contract || typeof provider_contract !== 'object' || Array.isArray(provider_contract)) {
    throw new Error('provider_contract is required and must be a JSON object');
  }
  if (!invocation_contract || typeof invocation_contract !== 'object' || Array.isArray(invocation_contract)) {
    throw new Error('invocation_contract is required and must be a JSON object');
  }
  if (!failure_policy || typeof failure_policy !== 'object' || Array.isArray(failure_policy)) {
    throw new Error('failure_policy is required and must be a JSON object');
  }

  rejectAuthorityFields(provider_contract, 'provider_contract');
  rejectAuthorityFields(invocation_contract, 'invocation_contract');
  rejectAuthorityFields(failure_policy, 'failure_policy');

  const provider = {
    contract_version: boundedInteger(provider_contract.contract_version, 'provider_contract.contract_version', 1, 1),
    adapter_id: requiredString(provider_contract.adapter_id, 'provider_contract.adapter_id'),
    adapter_version: requiredString(provider_contract.adapter_version, 'provider_contract.adapter_version'),
    provider_id: requiredString(provider_contract.provider_id, 'provider_contract.provider_id'),
    provider_version: requiredString(provider_contract.provider_version, 'provider_contract.provider_version'),
    input_schema_digest: digestField(provider_contract.input_schema_digest, 'provider_contract.input_schema_digest'),
    output_schema_digest: digestField(provider_contract.output_schema_digest, 'provider_contract.output_schema_digest'),
  };

  const invocation = {
    contract_version: boundedInteger(invocation_contract.contract_version, 'invocation_contract.contract_version', 1, 1),
    mode: enumField(invocation_contract.mode, 'invocation_contract.mode', INVOCATION_MODES),
    timeout_ms: boundedInteger(invocation_contract.timeout_ms, 'invocation_contract.timeout_ms', 1, 900000),
    cancellation_mode: enumField(invocation_contract.cancellation_mode, 'invocation_contract.cancellation_mode', CANCELLATION_MODES),
    retry_budget: boundedInteger(invocation_contract.retry_budget, 'invocation_contract.retry_budget', 0, 10),
    evidence_ref_mode: enumField(invocation_contract.evidence_ref_mode, 'invocation_contract.evidence_ref_mode', EVIDENCE_REF_MODES),
  };

  const failure = {
    unavailable_outcome: assertOutcome(requiredString(failure_policy.unavailable_outcome, 'failure_policy.unavailable_outcome')),
    timeout_outcome: assertOutcome(requiredString(failure_policy.timeout_outcome, 'failure_policy.timeout_outcome')),
    malformed_result_outcome: assertOutcome(requiredString(failure_policy.malformed_result_outcome, 'failure_policy.malformed_result_outcome')),
    stale_outcome: assertOutcome(requiredString(failure_policy.stale_outcome, 'failure_policy.stale_outcome')),
  };

  for (const [name, outcome] of Object.entries(failure)) {
    if (outcome === 'SUCCEEDED') throw new Error(`failure_policy.${name} cannot map failure to SUCCEEDED`);
  }

  return { provider_contract: provider, invocation_contract: invocation, failure_policy: failure };
}
