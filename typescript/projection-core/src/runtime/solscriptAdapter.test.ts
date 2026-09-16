/**
 * F-0 — projection-core ↔ @nexus/solscript adapter conformance tests
 * (dependency-free, tsx-runnable).
 *
 * Verifies:
 *  - createSolscriptRuleEvaluator delegates to ResolutionInterpreter.checkRule:
 *    a passing rule evaluates true, a failing rule false, a rule with no
 *    expression fails closed (false)
 *  - resolveDispositionViaSolscript maps the canonical Disposition enum to
 *    projection witness labels (Asserted/Disputed/Rejected) and returns null for
 *    non-decisive dispositions and undefined propositions
 */
import {
  Disposition,
  ExpressionKind,
  ResolutionInterpreter,
  RuleType,
  Severity,
  type Entity,
  type Proposition,
  type Rule,
} from '@nexus/solscript';
import {
  createSolscriptRuleEvaluator,
  resolveDispositionViaSolscript,
} from './solscriptAdapter.js';

function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}

const ENTITY: Entity = { id: 'ent-1', conceptId: 'concept-1', attributes: {} };
const RECORD = { id: 'doc-1', version: 3, digest: 'sha256:abc' };

function literalRule(id: string, value: boolean): Rule {
  return {
    id,
    name: id,
    ruleType: RuleType.Invariant,
    expression: {
      id: `${id}-expr`,
      kind: ExpressionKind.Literal,
      returnType: 'boolean',
      literalValue: value,
      operands: [],
    },
    severity: Severity.Hard,
    isRelationalCheck: false,
    conditions: [],
  };
}

// ── 1. Passing rule evaluates true ──
{
  const interpreter = new ResolutionInterpreter();
  const evaluate = createSolscriptRuleEvaluator(interpreter, literalRule('rule-pass', true), ENTITY);
  equal(evaluate(RECORD), true, 'passing rule -> true');
}

// ── 2. Failing rule evaluates false ──
{
  const interpreter = new ResolutionInterpreter();
  const evaluate = createSolscriptRuleEvaluator(interpreter, literalRule('rule-fail', false), ENTITY);
  equal(evaluate(RECORD), false, 'failing rule -> false');
}

// ── 3. Rule with no expression fails closed ──
{
  const interpreter = new ResolutionInterpreter();
  const rule: Rule = {
    id: 'rule-empty',
    name: 'rule-empty',
    ruleType: RuleType.Invariant,
    severity: Severity.Hard,
    isRelationalCheck: false,
    conditions: [],
  };
  const evaluate = createSolscriptRuleEvaluator(interpreter, rule, ENTITY);
  equal(evaluate(RECORD), false, 'expression-less rule fails closed -> false');
}

// ── 4. Decisive dispositions map to witness labels ──
{
  const base: Proposition = {
    id: 'prop-1',
    title: 'prop-1',
    assetConceptId: 'concept-1',
    subjectEntityId: 'ent-1',
    disposition: Disposition.Asserted,
    assertions: [],
    comparisons: [],
    frameValues: [],
  };
  equal(resolveDispositionViaSolscript(base), 'asserted', 'Asserted -> asserted');
  equal(
    resolveDispositionViaSolscript({ ...base, disposition: Disposition.Disputed }),
    'disputed',
    'Disputed -> disputed',
  );
  equal(
    resolveDispositionViaSolscript({ ...base, disposition: Disposition.Rejected }),
    'rejected',
    'Rejected -> rejected',
  );
}

// ── 5. Non-decisive dispositions and missing propositions yield null ──
{
  const base: Proposition = {
    id: 'prop-2',
    title: 'prop-2',
    assetConceptId: 'concept-1',
    subjectEntityId: 'ent-1',
    disposition: Disposition.Pending,
    assertions: [],
    comparisons: [],
    frameValues: [],
  };
  for (const disposition of [
    Disposition.Pending,
    Disposition.Proposed,
    Disposition.Stale,
    Disposition.Retracted,
  ]) {
    equal(resolveDispositionViaSolscript({ ...base, disposition }), null, `${disposition} -> null`);
  }
  equal(resolveDispositionViaSolscript(undefined), null, 'undefined proposition -> null');
}

console.log('solscriptAdapter conformance: 5/5 scenario groups passed');
