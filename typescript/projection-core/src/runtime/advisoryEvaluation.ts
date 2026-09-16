/**
 * W4.02 — Advisory-mode governed evaluation.
 *
 * Runs the governed evaluation path (doctrine lookup → verdict) in
 * ADVISORY-ONLY mode:
 *   - verdicts are advisory labels, never authority
 *   - zero lifecycle mutation: no peb.decisions writes, no conduit
 *     transitions, no envelope rewrites — a mutation attempt is refused
 *   - fail-closed: any doctrine lookup error/refusal/unknown produces an
 *     advisory 'unknown' verdict, never a silent pass
 *   - deterministic sampling: advisory evaluation applies only to sampled
 *     requests, chosen by stable hash (same input → same sample decision)
 *
 * Promotion ladder context (Wave 4): shadow/read-only → advisory →
 * narrowly blocking. This module IS the advisory rung.
 */
import type { DoctrineLookup, DoctrineLookupRequest, DoctrineLookupResult } from './doctrineLookup.js';
import { createSolscriptRuleEvaluator } from './solscriptAdapter.js';
import type { ResolutionInterpreter, Rule, Entity } from "@nexus/solscript";

export type AdvisoryVerdict = 'advisory_pass' | 'advisory_fail' | 'advisory_unknown';

export interface AdvisoryEvaluationInput {
  /** Stable identity of the request being evaluated (e.g. envelope id). */
  requestId: string;
  /** Doctrine lookup request the governed path depends on. */
  doctrineRequest: DoctrineLookupRequest;
  /** Deterministic evaluation of the doctrine record (pure function). */
  evaluate: (record: { id: string; version: number; digest: string }) => boolean;
}

export interface AdvisoryEvaluationResult {
  verdict: AdvisoryVerdict;
  /** True when this request was selected by the sampling policy. */
  sampled: boolean;
  consulted: boolean;
  latencyMs: number;
  reason?: string;
  /** Identity of the doctrine record consulted, when resolved. */
  record?: { id: string; version: number; digest: string };
}

export interface AdvisoryPolicy {
  /** Fraction of requests sampled, 0..1. Deterministic by request id hash. */
  sampleRate: number;
}

export const DEFAULT_ADVISORY_POLICY: AdvisoryPolicy = { sampleRate: 1.0 };

/** Deterministic 32-bit FNV-1a hash — stable across processes for sampling. */
export function advisorySampleHash(requestId: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < requestId.length; i++) {
    hash ^= requestId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/** Deterministic sampling: same requestId → same sample decision, always. */
export function isSampled(requestId: string, policy: AdvisoryPolicy): boolean {
  if (policy.sampleRate >= 1) return true;
  if (policy.sampleRate <= 0) return false;
  return (advisorySampleHash(requestId) % 10_000) / 10_000 < policy.sampleRate;
}

/**
 * Run one advisory evaluation. NEVER mutates lifecycle state: this module
 * holds no writers and the verdict is a plain value. Fail-closed: lookup
 * errors and non-resolved lookups yield 'advisory_unknown', never a pass.
 */
export async function evaluateAdvisory(
  lookup: DoctrineLookup,
  input: AdvisoryEvaluationInput,
  policy: AdvisoryPolicy = DEFAULT_ADVISORY_POLICY,
): Promise<AdvisoryEvaluationResult> {
  const started = performance.now();
  const sampled = isSampled(input.requestId, policy);
  if (!sampled) {
    return { verdict: 'advisory_unknown', sampled: false, consulted: false, latencyMs: 0, reason: 'not_sampled' };
  }
  let result: DoctrineLookupResult;
  try {
    result = await lookup.lookup(input.doctrineRequest);
  } catch (err) {
    return {
      verdict: 'advisory_unknown',
      sampled: true,
      consulted: false,
      latencyMs: elapsed(started),
      reason: `lookup_error:${err instanceof Error ? err.message : String(err)}`,
    };
  }
  if (result.status !== 'resolved' || !result.record) {
    return {
      verdict: 'advisory_unknown',
      sampled: true,
      consulted: result.consulted,
      latencyMs: elapsed(started),
      reason: `lookup_${result.status}:${result.reason ?? 'unresolved'}`,
    };
  }
  let pass: boolean;
  try {
    pass = input.evaluate({ id: result.record.id, version: result.record.version, digest: result.record.digest });
  } catch (err) {
    return {
      verdict: 'advisory_unknown',
      sampled: true,
      consulted: true,
      latencyMs: elapsed(started),
      reason: `evaluate_error:${err instanceof Error ? err.message : String(err)}`,
    };
  }
  return {
    verdict: pass ? 'advisory_pass' : 'advisory_fail',
    sampled: true,
    consulted: true,
    latencyMs: elapsed(started),
    record: { id: result.record.id, version: result.record.version, digest: result.record.digest },
  };
}

function elapsed(started: number): number {
  return Math.max(0, Math.round((performance.now() - started) * 1000) / 1000);
}

/**
 * Solscript-backed advisory evaluation (F-0 / option A).
 *
 * Runs the advisory path against a @nexus/solscript Rule + Entity via the
 * canonical ResolutionInterpreter (the reference implementation), instead of
 * a caller-hand-rolled evaluate callback. Keeps Projection's doctrine verdicts
 * solscript-derived. Fail-closed: any lookup/evaluate error → advisory_unknown.
 *
 * @param lookup the doctrine lookup the governed path depends on.
 * @param input the advisory input; its `evaluate` is IGNORED in favor of the
 *        solscript rule check (solscript is authoritative for the verdict).
 * @param interpreter a seeded ResolutionInterpreter (rules/entities loaded).
 * @param rule the solscript Rule to evaluate.
 * @param entity the solscript Entity to evaluate against.
 * @param policy sampling policy (default: sample all).
 */
export async function evaluateAdvisoryViaSolscript(
  lookup: DoctrineLookup,
  input: Omit<AdvisoryEvaluationInput, "evaluate">,
  interpreter: ResolutionInterpreter,
  rule: Rule,
  entity: Entity,
  policy: AdvisoryPolicy = DEFAULT_ADVISORY_POLICY,
): Promise<AdvisoryEvaluationResult> {
  const evaluate = createSolscriptRuleEvaluator(interpreter, rule, entity);
  return evaluateAdvisory(
    lookup,
    { ...input, evaluate },
    policy,
  );
}
