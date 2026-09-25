import { createHash } from 'node:crypto';

export type VerificationStatus =
  | 'verified'
  | 'violated'
  | 'unknown'
  | 'stale'
  | 'invalid'
  | 'unavailable';

export interface TruthfulOutcome {
  status: VerificationStatus;
  safety_status: VerificationStatus;
  liveness_status: VerificationStatus;
  authority_level: 'advisory';
  reason: string;
}

/**
 * Produce deterministic JSON for content-addressed artifacts.
 * Object keys are sorted recursively; array order is preserved because array
 * order is part of a model/result contract.
 */
export function canonicalJson(value: unknown): string {
  const normalize = (input: unknown): unknown => {
    if (input instanceof Date) return input.toISOString();
    if (Array.isArray(input)) return input.map(normalize);
    if (input && typeof input === 'object') {
      const record = input as Record<string, unknown>;
      return Object.fromEntries(
        Object.keys(record).sort().map((key) => [key, normalize(record[key])]),
      );
    }
    return input;
  };
  return JSON.stringify(normalize(value));
}

export function sha256Digest(value: string): string {
  return `sha256:${createHash('sha256').update(value, 'utf8').digest('hex')}`;
}

export function digestJson(value: unknown): string {
  return sha256Digest(canonicalJson(value));
}

export function mapCheckerOutcome(
  engine: 'tlc' | 'structural',
  status: 'success' | 'failure' | 'error',
  reason?: string,
): TruthfulOutcome {
  // Only real TLC success can establish a verified safety result. The
  // structural checker is deliberately evidence-producing but not a formal
  // proof, so its success remains unknown.
  if (engine === 'tlc' && status === 'success') {
    return {
      status: 'verified',
      safety_status: 'verified',
      liveness_status: 'unknown',
      authority_level: 'advisory',
      reason: reason || 'TLC completed without a safety violation; liveness remains unknown at the gate',
    };
  }

  if (engine === 'tlc' && status === 'failure') {
    return {
      status: 'violated',
      safety_status: 'violated',
      liveness_status: 'unknown',
      authority_level: 'advisory',
      reason: reason || 'TLC found a counterexample or deadlock',
    };
  }

  if (engine === 'structural' && status === 'failure') {
    return {
      status: 'invalid',
      safety_status: 'invalid',
      liveness_status: 'unknown',
      authority_level: 'advisory',
      reason: reason || 'Structured model failed validation; no formal verification was established',
    };
  }

  if (engine === 'structural') {
    return {
      status: 'unknown',
      safety_status: 'unknown',
      liveness_status: 'unknown',
      authority_level: 'advisory',
      reason: reason || 'Structural analysis completed; it is not a formal proof',
    };
  }

  return {
    status: 'unavailable',
    safety_status: 'unavailable',
    liveness_status: 'unknown',
    authority_level: 'advisory',
    reason: reason || 'Formal checker was unavailable or failed to produce a result',
  };
}
