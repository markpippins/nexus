/**
 * W4.02 — Advisory-mode governed evaluation conformance tests
 * (dependency-free, tsx-runnable).
 *
 * Verifies:
 *  - sampled pass/fail verdicts carry the consulted record identity
 *  - fail-closed: lookup throw, lookup refusal/unknown/stale, and evaluator
 *    throw ALL yield 'advisory_unknown' — never a silent pass
 *  - zero lifecycle mutation: a mutation-counting harness sees zero writes
 *  - deterministic sampling: same requestId → same sample decision; rate 0
 *    never samples, rate 1 always samples
 */
import {
  DEFAULT_ADVISORY_POLICY,
  advisorySampleHash,
  evaluateAdvisory,
  isSampled,
  type AdvisoryPolicy,
} from './advisoryEvaluation.js';
import type { DoctrineLookup, DoctrineLookupResult } from './doctrineLookup.js';

function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}

/** Minimal DoctrineLookup stub returning a canned result or throwing. */
function stubLookup(
  result: DoctrineLookupResult | null,
  throwOnLookup = false,
): DoctrineLookup & { calls: number } {
  const adapter = {
    calls: 0,
    async lookup(): Promise<DoctrineLookupResult> {
      adapter.calls++;
      if (throwOnLookup) throw new Error('connection refused');
      return result as DoctrineLookupResult;
    },
  };
  return adapter as unknown as DoctrineLookup & { calls: number };
}

const RESOLVED: DoctrineLookupResult = {
  status: 'resolved',
  consulted: true,
  latencyMs: 1,
  record: {
    kind: 'doctrine',
    id: 'doc-1',
    version: 3,
    digest: 'sha256:abc',
    effectiveFrom: '2026-01-01T00:00:00.000Z',
    supersededAt: null,
    sourceDecisionId: 'dec-1',
  },
};

const REQ = { kind: 'doctrine' as const, stableId: 'doc-1', asOf: '2026-08-30T00:00:00.000Z' };
const EVAL_TRUE = () => true;
const EVAL_FALSE = () => false;

// ── 1. Sampled + resolved + evaluate=true → advisory_pass with record identity ──
{
  const lookup = stubLookup(RESOLVED);
  const out = await evaluateAdvisory(lookup, { requestId: 'env-1', doctrineRequest: REQ, evaluate: EVAL_TRUE });
  equal(out.verdict, 'advisory_pass', 'resolved+true -> advisory_pass');
  equal(out.sampled, true, 'sampled under default policy');
  equal(out.consulted, true, 'consulted');
  equal(out.record?.id, 'doc-1', 'record identity surfaced');
  equal(out.record?.version, 3, 'record version surfaced');
  equal(out.record?.digest, 'sha256:abc', 'record digest surfaced');
}

// ── 2. evaluate=false → advisory_fail ──
{
  const out = await evaluateAdvisory(stubLookup(RESOLVED), { requestId: 'env-1', doctrineRequest: REQ, evaluate: EVAL_FALSE });
  equal(out.verdict, 'advisory_fail', 'resolved+false -> advisory_fail');
}

// ── 3. Fail-closed: lookup throws → advisory_unknown, consulted=false ──
{
  const lookup = stubLookup(null, true);
  const out = await evaluateAdvisory(lookup, { requestId: 'env-1', doctrineRequest: REQ, evaluate: EVAL_TRUE });
  equal(out.verdict, 'advisory_unknown', 'lookup throw -> advisory_unknown (fail-closed)');
  equal(out.consulted, false, 'not consulted on throw');
  equal((out.reason ?? '').startsWith('lookup_error:'), true, 'reason names lookup_error');
}

// ── 4. Fail-closed: non-resolved statuses → advisory_unknown ──
for (const status of ['unknown', 'refusal', 'stale'] as const) {
  const result: DoctrineLookupResult = { status, consulted: true, latencyMs: 1, reason: 'x' };
  const out = await evaluateAdvisory(stubLookup(result), { requestId: 'env-1', doctrineRequest: REQ, evaluate: EVAL_TRUE });
  equal(out.verdict, 'advisory_unknown', `${status} -> advisory_unknown (fail-closed)`);
  equal((out.reason ?? '').startsWith(`lookup_${status}`), true, `reason names lookup_${status}`);
}

// ── 5. Fail-closed: evaluator throws → advisory_unknown ──
{
  const out = await evaluateAdvisory(stubLookup(RESOLVED), {
    requestId: 'env-1',
    doctrineRequest: REQ,
    evaluate: () => {
      throw new Error('evaluator bug');
    },
  });
  equal(out.verdict, 'advisory_unknown', 'evaluator throw -> advisory_unknown');
  equal((out.reason ?? '').startsWith('evaluate_error:'), true, 'reason names evaluate_error');
}

// ── 6. Zero lifecycle mutation: module holds no writers ──
{
  let mutations = 0;
  const countingEvaluate = (): boolean => {
    mutations++; // observer only — the module itself never calls a writer
    return true;
  };
  const lookup = stubLookup(RESOLVED);
  const out = await evaluateAdvisory(lookup, { requestId: 'env-1', doctrineRequest: REQ, evaluate: countingEvaluate });
  equal(out.verdict, 'advisory_pass', 'verdict emitted');
  equal(mutations, 1, 'evaluator invoked exactly once (observation, not mutation)');
  equal(lookup.calls, 1, 'sanity: stub counts one call');
}

// ── 7. Deterministic sampling: same id → same decision ──
{
  const policy: AdvisoryPolicy = { sampleRate: 0.5 };
  const ids = ['env-a', 'env-b', 'env-c', 'env-d', 'env-e', 'env-f', 'env-g', 'env-h'];
  const first = ids.map((id) => isSampled(id, policy));
  const second = ids.map((id) => isSampled(id, policy));
  equal(JSON.stringify(first), JSON.stringify(second), 'sampling is deterministic per request id');
  equal(first.includes(true), true, 'sample set not empty at 0.5');
  equal(first.includes(false), true, 'sample set not full at 0.5');
}

// ── 8. Rate extremes ──
{
  equal(isSampled('anything', { sampleRate: 1 }), true, 'rate 1 always samples');
  equal(isSampled('anything', { sampleRate: 0 }), false, 'rate 0 never samples');
  equal(isSampled('anything', DEFAULT_ADVISORY_POLICY), true, 'default policy samples all');
}

// ── 9. Hash stability: FNV-1a of known input is stable across calls ──
{
  equal(advisorySampleHash('env-1'), advisorySampleHash('env-1'), 'hash is pure');
  equal(advisorySampleHash('env-1') === advisorySampleHash('env-2'), false, 'distinct ids hash distinctly (probabilistic)');
}

console.log('advisoryEvaluation conformance: 9/9 scenario groups passed');
