import { createHash } from 'node:crypto';

export const OUTCOMES = new Set(['SUCCEEDED', 'FAILED', 'UNAVAILABLE', 'STALE', 'INVALID']);
export const DIGEST = /^sha256:[0-9a-f]{64}$/;

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function digest(value) {
  return `sha256:${createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex')}`;
}

export function assertOutcome(status) {
  const normalized = String(status || '').toUpperCase();
  if (!OUTCOMES.has(normalized)) throw new Error(`outcome_status must be one of: ${[...OUTCOMES].join(', ')}`);
  return normalized;
}

export function requestMaterial(body) {
  return {
    artifact_type: body.artifact_type,
    artifact_ref: body.artifact_ref,
    artifact_revision: body.artifact_revision,
    workflow_version_id: body.workflow_version_id || null,
    node_id: body.node_id || null,
    artifact_fingerprint: body.artifact_fingerprint,
    read_set_digest: body.read_set_digest,
    evaluator_contract_digest: body.evaluator_contract_digest,
    causation_id: body.causation_id || null,
    correlation_id: body.correlation_id,
    provider_contract: body.provider_contract || {},
    invocation_contract: body.invocation_contract || {},
    failure_policy: body.failure_policy || {},
  };
}
