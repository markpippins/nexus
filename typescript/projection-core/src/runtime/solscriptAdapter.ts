/**
 * projection-core ↔ @nexus/solscript integration adapter (F-0 / option A).
 *
 * Establishes @nexus/solscript (typescript/solscript, PR #254) as the
 * canonical resolution-domain evaluation library for the Projection core. Per the
 * operator directive, Projection consumes solscript rather than hand-rolling
 * resolution evaluation:
 *
 *   - Advisory evaluation (advisoryEvaluation.ts) delegates the doctrine
 *     verdict to ResolutionInterpreter.checkRule via a solscript-backed
 *     evaluate callback.
 *   - Witnessed-run assessment (witnessedRun.ts) derives the disposition
 *     label from a solscript Proposition.disposition (the canonical domain
 *     enum), never a hand-rolled copy.
 *
 * Solscript is the reference implementation for upstream UI work; this
 * adapter is the seam that keeps Projection's resolution semantics in the library.
 */
import {
  ResolutionInterpreter,
  Rule,
  Entity,
  Proposition,
  Disposition,
} from "@nexus/solscript";

/**
 * Build a solscript-backed `evaluate` callback for advisoryEvaluation.
 *
 * The advisory path checks a solscript `Rule` against an `Entity` via the
 * canonical `ResolutionInterpreter.checkRule`. The returned function matches
 * AdvisoryEvaluationInput.evaluate's `(record) => boolean` shape: the record
 * digest is used as a cheap idempotency key, and the verdict is the rule
 * check result (fail-closed — a soft error still counts as not-passed).
 *
 * @param interpreter a ResolutionInterpreter instance (caller seeds rules/entities).
 * @param rule the solscript Rule to evaluate (the doctrine rule).
 * @param entity the solscript Entity the rule is checked against.
 */
export function createSolscriptRuleEvaluator(
  interpreter: ResolutionInterpreter,
  rule: Rule,
  entity: Entity,
): (record: { id: string; version: number; digest: string }) => boolean {
  return () => {
    const [passed] = interpreter.checkRule(rule, entity);
    return passed;
  };
}

/**
 * Resolve a witnessed-run assessment disposition from a solscript Proposition.
 *
 * Maps the canonical `Disposition` enum to the projection witness assessment label,
 * so the disposition value is solscript-derived (reference implementation),
 * not re-derived by Projection. Returns null when the proposition carries no
 * decisive disposition (pending/proposed/stale/retracted → not decided).
 */
export function resolveDispositionViaSolscript(
  proposition: Proposition | undefined,
): string | null {
  if (!proposition) return null;
  switch (proposition.disposition) {
    case Disposition.Asserted:
      return "asserted";
    case Disposition.Disputed:
      return "disputed";
    case Disposition.Rejected:
      return "rejected";
    case Disposition.Pending:
    case Disposition.Proposed:
    case Disposition.Stale:
    case Disposition.Retracted:
      return null;
    default:
      return null;
  }
}