/**
 * aegis-srv — deterministic state-space model checker.
 *
 * Runs a bounded, deterministic model check over the *structured* state graph
 * encoded in the `aegis` schema (states with is_initial/is_terminal,
 * transitions with from_state_id/to_state_id, invariants, properties,
 * temporal_properties). It performs genuine reachability analysis, deadlock
 * detection, unreachable-state discovery, and best-effort structural
 * invariant/property/temporal verdicts, producing counterexample traces.
 *
 * This is a real model-check baseline that does NOT require the TLA+ model
 * checker (TLC). A future TLC integration can augment it to evaluate the
 * free-form guard/action/invariant text; here those expressions are treated
 * structurally (identifier-reference validation) because they are arbitrary
 * TLA+ text with no evaluator in this environment.
 */

// ── Input model (plain JSON, DB-independent — unit-testable) ─────────

export interface MCState {
  id: string;
  name: string;
  is_initial: boolean;
  is_terminal: boolean;
  variable_assignments?: Record<string, unknown>;
}

export interface MCTransition {
  id: string;
  name: string;
  from_state_id: string | null;
  to_state_id: string | null;
  guard_expression?: string | null;
  action?: Record<string, unknown>;
  weak_fairness?: boolean;
  strong_fairness?: boolean;
  priority?: number;
}

export interface MCInvariant {
  id: string;
  name: string;
  expression: string;
  is_type_invariant: boolean;
}

export interface MCProperty {
  id: string;
  name: string;
  type: string; // safety | liveness | fairness
  expression: string;
}

export interface MCTemporalProperty {
  id: string;
  name: string;
  operator: string; // [] | <> | -> | ~> | =>
  expression: string;
}

export interface MCModel {
  states: MCState[];
  transitions: MCTransition[];
  invariants: MCInvariant[];
  properties: MCProperty[];
  temporal_properties: MCTemporalProperty[];
  variables?: string[];
  constants?: string[];
}

// ── Verdicts ─────────────────────────────────────────────────────────

export interface CheckVerdict {
  kind: 'invariant' | 'property' | 'temporal';
  name: string;
  type?: string;
  result: 'PASS' | 'FAIL' | 'WARN';
  detail: string;
}

export interface ModelCheckReport {
  /** success | failure */
  status: string;
  reachableStates: string[];
  unreachableStates: string[];
  /** counterexample path (state names) for a deadlock, if any */
  deadlockTrace?: string[];
  /** per-invariant / per-property / per-temporal verdicts */
  verdicts: CheckVerdict[];
  /** human-readable error list (empty on success) */
  errors: string[];
  warnings: string[];
}

// ── TLA+ identifier reference validation ─────────────────────────────

// TLA+ reserved words / common operators / standard module symbols. Any
// bare identifier in an expression not in this set must resolve to a known
// state name, variable name, or constant name.
const TLA_RESERVED = new Set([
  'MODULE', 'CONSTANTS', 'VARIABLES', 'ASSUME', 'THEOREM', 'IN', 'IF', 'THEN',
  'ELSE', 'CASE', 'LET', 'IN', 'TRUE', 'FALSE', 'BOOLEAN', 'CHOOSE', 'EXCEPT',
  'DOMAIN', 'UNION', 'SUBSET', 'LAMBDA', '\\in', '\\notin', '\\subseteq',
  '\\union', '\\intersect', '\\cup', '\\cap', '\\div', '\\o', '\\A', '\\E',
  '\\AA', '\\EE', '\\lnot', '\\land', '\\lor', '\\implies', '\\equiv',
  '\\x', '\\times', '\\geq', '\\leq', '\\neq', '=', '#', ':=', '->', '<->',
  '\\cdot', 'Nat', 'Int', 'Real', 'Seq', 'Set', 'NULL', 'ENABLED', 'UNCHANGED',
]);

const IDENTIFIER_RE = /[A-Za-z_][A-Za-z0-9_]*/g;

/** Extract bare identifiers from a TLA+ expression, ignoring string literals and backslash-operators. */
function identifiers(expr: string): string[] {
  // Strip single/double-quoted string literals and backslash-prefixed operators
  // (\in, \land, ...) so their contents / trailing tokens are not matched.
  const cleaned = expr
    .replace(/"[^"]*"/g, ' ')
    .replace(/'[^']*'/g, ' ')
    .replace(/\\[A-Za-z]+/g, ' ');
  return (cleaned.match(IDENTIFIER_RE) || []).filter(
    (s) => !TLA_RESERVED.has(s) && !['id', 'name', 'type', 'status'].includes(s),
  );
}

// ── Pure checker (no DB / no I/O) ─────────────────────────────────────

/**
 * Run the deterministic model check over a plain model. Pure and unit-testable.
 */
export function checkModel(model: MCModel): ModelCheckReport {
  const report: ModelCheckReport = {
    status: 'success',
    reachableStates: [],
    unreachableStates: [],
    verdicts: [],
    errors: [],
    warnings: [],
  };

  const stateById = new Map(model.states.map((s) => [s.id, s]));
  const stateNameById = new Map(model.states.map((s) => [s.id, s.name]));
  const knownNames = new Set([
    ...model.states.map((s) => s.name),
    ...(model.variables || []),
    ...(model.constants || []),
  ]);

  // Build adjacency: stateId -> outgoing transition ids. Wildcard transitions
  // (no from_state_id) apply to every state.
  const outgoing = new Map<string, MCTransition[]>();
  const wildcard = model.transitions.filter((t) => !t.from_state_id);
  for (const s of model.states) outgoing.set(s.id, []);
  for (const t of model.transitions) {
    if (t.from_state_id && outgoing.has(t.from_state_id)) {
      outgoing.get(t.from_state_id)!.push(t);
    }
  }

  // ── Structural validation ──────────────────────────────────────────
  const initial = model.states.filter((s) => s.is_initial);
  if (initial.length === 0) {
    report.status = 'failure';
    report.errors.push('no initial state declared (is_initial=false on all states)');
  }
  for (const t of model.transitions) {
    if (t.from_state_id && !stateById.has(t.from_state_id)) {
      report.errors.push(`transition "${t.name}" references missing from-state ${t.from_state_id}`);
    }
    if (t.to_state_id && !stateById.has(t.to_state_id)) {
      report.errors.push(`transition "${t.name}" references missing to-state ${t.to_state_id}`);
    }
    if (t.from_state_id && t.to_state_id && t.from_state_id === t.to_state_id && !t.guard_expression) {
      report.warnings.push(`transition "${t.name}" is an unguarded self-loop (may not terminate)`);
    }
  }

  // ── Reachability (BFS from all initial states) ─────────────────────
  const reachable = new Set<string>();
  const queue: string[] = initial.map((s) => s.id);
  const parent = new Map<string, string | null>();
  for (const id of initial) parent.set(id.id, null);
  for (const id of queue) reachable.add(id);

  while (queue.length > 0) {
    const cur = queue.shift()!;
    const edges = [...(outgoing.get(cur) || []), ...wildcard];
    for (const t of edges) {
      const nextId = t.to_state_id;
      // Only follow transitions to a state that exists in the model.
      if (nextId && stateById.has(nextId) && !reachable.has(nextId)) {
        reachable.add(nextId);
        parent.set(nextId, cur);
        queue.push(nextId);
      }
    }
  }

  report.reachableStates = model.states.filter((s) => reachable.has(s.id)).map((s) => s.name);
  report.unreachableStates = model.states.filter((s) => !reachable.has(s.id)).map((s) => s.name);
  if (report.unreachableStates.length > 0) {
    report.warnings.push(`unreachable states: ${report.unreachableStates.join(', ')}`);
  }

  const pathTo = (targetId: string): string[] => {
    const path: string[] = [];
    let cur: string | null = targetId;
    while (cur) {
      path.unshift(stateNameById.get(cur) || cur);
      cur = parent.get(cur) || null;
    }
    return path;
  };

  // ── Deadlock detection: reachable non-terminal state with no outgoing ─
  let deadlock: string | undefined;
  for (const id of reachable) {
    const s = stateById.get(id)!;
    const edges = outgoing.get(id) || [];
    const hasOut = edges.length > 0 || wildcard.length > 0;
    if (!hasOut && !s.is_terminal) {
      deadlock = id;
      break;
    }
  }
  if (deadlock) {
    report.status = 'failure';
    report.deadlockTrace = pathTo(deadlock);
    report.errors.push(`deadlock: reachable non-terminal state "${stateNameById.get(deadlock)}" has no outgoing transitions`);
  }

  // ── Invariant checks (structural) ──────────────────────────────────
  for (const inv of model.invariants) {
    if (!inv.expression || !inv.expression.trim()) {
      report.verdicts.push({ kind: 'invariant', name: inv.name, result: 'FAIL', detail: 'empty invariant expression' });
      continue;
    }
    const unknown = identifiers(inv.expression).filter((s) => !knownNames.has(s));
    if (unknown.length > 0) {
      report.verdicts.push({
        kind: 'invariant', name: inv.name, result: 'FAIL',
        detail: `references undefined identifier(s): ${unknown.join(', ')}`,
      });
      if (report.status !== 'failure') report.status = 'failure';
      continue;
    }
    // type invariants must reference a variable or constant by name
    if (inv.is_type_invariant && !(model.variables || []).some((v) => identifiers(inv.expression).includes(v))) {
      report.verdicts.push({
        kind: 'invariant', name: inv.name, result: 'WARN',
        detail: 'type invariant does not reference any declared variable (structural check cannot confirm)',
      });
      continue;
    }
    report.verdicts.push({ kind: 'invariant', name: inv.name, result: 'PASS', detail: 'references only known identifiers (structural)' });
  }

  // ── Property checks (structural) ───────────────────────────────────
  for (const prop of model.properties) {
    const referencedStates = identifiers(prop.expression).filter((s) => knownNames.has(s));
    const referencedReachable = referencedStates.every((s) => report.reachableStates.includes(s));
    if (prop.type === 'safety') {
      // A safety property "never in state X" holds if X is unreachable; if X
      // is reachable we cannot prove it without an evaluator -> WARN.
      if (referencedStates.length === 0) {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'PASS', detail: 'safety property references no state (trivially structural)' });
      } else if (referencedReachable) {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'WARN', detail: 'safety property references reachable state(s); needs TLA+ evaluation to prove' });
      } else {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'PASS', detail: 'safety property only references unreachable state(s)' });
      }
    } else if (prop.type === 'liveness') {
      // Liveness "eventually ..." needs progress: a reachable terminal OR a cycle.
      const reachableTerminal = model.states.some((s) => reachable.has(s.id) && s.is_terminal);
      const hasCycle = hasCycleInReachable(reachable, outgoing, wildcard, stateById);
      if (referencedReachable && (reachableTerminal || hasCycle)) {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'PASS', detail: 'liveness: progress possible (reachable terminal or cycle present)' });
      } else if (referencedReachable) {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'FAIL', detail: 'liveness: no reachable terminal and no cycle — progress cannot be satisfied' });
        if (report.status !== 'failure') report.status = 'failure';
      } else {
        report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'WARN', detail: 'liveness references unreachable state(s)' });
      }
    } else if (prop.type === 'fairness') {
      const hasFairness = model.transitions.some((t) => t.weak_fairness || t.strong_fairness);
      report.verdicts.push({
        kind: 'property', name: prop.name, type: prop.type,
        result: hasFairness ? 'PASS' : 'WARN',
        detail: hasFairness ? 'at least one transition declares weak/strong fairness' : 'no transition declares fairness — cannot guarantee fairness',
      });
    } else {
      report.verdicts.push({ kind: 'property', name: prop.name, type: prop.type, result: 'WARN', detail: `unknown property type "${prop.type}"` });
    }
  }

  // ── Temporal property checks (structural) ──────────────────────────
  for (const tp of model.temporal_properties) {
    const referencedStates = identifiers(tp.expression).filter((s) => knownNames.has(s));
    const referencedReachable = referencedStates.every((s) => report.reachableStates.includes(s));
    switch (tp.operator) {
      case '[]': // always
        report.verdicts.push({
          kind: 'temporal', name: tp.name, result: referencedReachable ? 'PASS' : 'WARN',
          detail: referencedReachable ? 'always: all referenced states are reachable (structural)' : 'always: references unreachable state(s)',
        });
        break;
      case '<>': // eventually
        if (referencedStates.length === 0) {
          report.verdicts.push({ kind: 'temporal', name: tp.name, result: 'PASS', detail: 'eventually: no referenced state (trivially structural)' });
        } else {
          report.verdicts.push({
            kind: 'temporal', name: tp.name,
            result: referencedReachable ? 'PASS' : 'FAIL',
            detail: referencedReachable ? 'eventually: referenced state is reachable' : 'eventually: referenced state is NOT reachable',
          });
          if (!referencedReachable && report.status !== 'failure') report.status = 'failure';
        }
        break;
      case '->':
      case '~>': // leads-to
      case '=>': // implies
        report.verdicts.push({
          kind: 'temporal', name: tp.name,
          result: referencedReachable ? 'PASS' : 'WARN',
          detail: referencedReachable ? 'leads-to/implies: referenced states reachable (structural)' : 'leads-to/implies: references unreachable state(s)',
        });
        break;
      default:
        report.verdicts.push({ kind: 'temporal', name: tp.name, result: 'WARN', detail: `unknown temporal operator "${tp.operator}"` });
    }
  }

  return report;
}

/** Detect a cycle among reachable states (DFS over outgoing + wildcard edges). */
function hasCycleInReachable(
  reachable: Set<string>,
  outgoing: Map<string, MCTransition[]>,
  wildcard: MCTransition[],
  stateById: Map<string, MCState>,
): boolean {
  const WHITE = 0, GRAY = 1, BLACK = 2;
  const color = new Map<string, number>();
  for (const id of reachable) color.set(id, WHITE);

  const visit = (id: string): boolean => {
    color.set(id, GRAY);
    const edges = [...(outgoing.get(id) || []), ...wildcard];
    for (const t of edges) {
      if (!t.to_state_id) continue;
      if (!color.has(t.to_state_id)) continue; // not reachable -> skip
      const c = color.get(t.to_state_id)!;
      if (c === GRAY) return true; // back edge -> cycle
      if (c === WHITE && visit(t.to_state_id)) return true;
    }
    color.set(id, BLACK);
    return false;
  };

  for (const id of reachable) {
    if (color.get(id) === WHITE && visit(id)) return true;
  }
  return false;
}