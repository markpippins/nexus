/**
 * SOLScript TypeScript core — ResolutionInterpreter.
 *
 * Ported from python/SOLScript/solscript/interpreter.py: in-memory
 * interpreter for the resolution language. Holds the full concept graph,
 * entity store, propositions, rules, and expression/function registries.
 * Evaluation uses compiled expression trees.
 *
 * Wire-shape note: guard-result records keep snake_case keys
 * (rule_id/rule_name/passed/reason) and read_sets keep Python-shaped
 * values, because they flow into KeychainEvent read_sets whose digests
 * must match the Python reference byte-for-byte.
 */

import {
  Concept,
  ConceptAttribute,
  ConceptRelationship,
  ConceptStateTransition,
  Disposition,
  Entity,
  Expression,
  FrameDimension,
  FrameDimensionMeaning,
  FrameDimensionValue,
  Proposition,
  PropositionFrameValue,
  Representation,
  Rule,
  Severity,
} from "./models.js";
import {
  EvalContext,
  ExpressionCompiler,
  FunctionRegistry,
} from "./expression-compiler.js";
import {
  KeychainEvent,
  buildTransitionEvent,
} from "./events.js";

export interface GuardResult {
  rule_id: string;
  rule_name: string;
  passed: boolean;
  reason: string;
}

export interface TransitionOutcome {
  committed: boolean;
  results: GuardResult[] | { error: string }[];
  event: KeychainEvent;
}

export type TransitionListener = (info: {
  transition_id: string;
  entity_id: string;
  to_value: string;
  effective_at: string | null;
  event: KeychainEvent;
}) => void;

export type TransitionEventListener = (info: { event: KeychainEvent }) => void;

export type ChangeHandler = (
  conceptName: string,
  entityId: string,
  results: { propositionId: string; action: string; disposition: Disposition }[],
) => void;

export type BuiltinFunction = (...args: unknown[]) => unknown;

export class ResolutionInterpreter {
  concepts = new Map<string, Concept>();
  entities = new Map<string, Entity>();
  propositions = new Map<string, Proposition>();
  expressions = new Map<string, Expression>();
  rules = new Map<string, Rule>();
  functions: FunctionRegistry = new Map();
  representations = new Map<string, Representation>();
  relationships = new Map<string, ConceptRelationship>();
  stateTransitions = new Map<string, ConceptStateTransition>();
  // v31: frame discipline
  frameDimensions = new Map<string, FrameDimension>();
  frameDimensionValues = new Map<string, FrameDimensionValue>();
  // v35: frame semantics
  frameDimensionMeanings = new Map<string, FrameDimensionMeaning>();

  // Runtime state
  evaluationCache = new Map<string, unknown>();
  executionContext: Record<string, unknown> = {};
  eventHandlers: ChangeHandler[] = [];
  /** Fired after a SUCCESSFUL transition only (catalogue 332d6831 decision points). */
  transitionListeners: TransitionListener[] = [];
  /** Fired for EVERY transition attempt (committed + refused + rejected). */
  transitionEventListeners: TransitionEventListener[] = [];
  lastTransitionEvent: KeychainEvent | null = null;

  private readonly compiler: ExpressionCompiler;

  constructor() {
    this.compiler = new ExpressionCompiler(this.host());
    this.registerBuiltinFunctions();
  }

  /** ExpressionCompiler host view over this interpreter's registries. */
  host() {
    const self = this;
    return {
      getAttribute(attributeId: string): ConceptAttribute | undefined {
        return self.getAttribute(attributeId) ?? undefined;
      },
      getRelationship(relationshipId: string): ConceptRelationship | undefined {
        return self.relationships.get(relationshipId);
      },
      getProposition(propositionId: string) {
        return self.propositions.get(propositionId);
      },
      getFunction(name: string) {
        return self.functions.get(name);
      },
      entities(): Iterable<Entity> {
        return self.entities.values();
      },
    };
  }

  // ── Built-in functions ───────────────────────────────────────

  private registerBuiltinFunctions(): void {
    const builtins: Record<string, BuiltinFunction> = {
      count: (...args) => args.filter((a) => a !== null && a !== undefined).length,
      sum: (...args) =>
        args.reduce<number>((acc, a) => acc + (a === null || a === undefined ? 0 : Number(a)), 0),
      avg: (...args) => {
        const vals = args.filter((a) => a !== null && a !== undefined);
        const total = vals.reduce<number>((acc, a) => acc + Number(a), 0);
        return total / Math.max(vals.length, 1);
      },
      min: (...args) => {
        const vals = args.filter((a) => a !== null && a !== undefined).map((a) => Number(a));
        return vals.length ? Math.min(...vals) : Number.POSITIVE_INFINITY;
      },
      max: (...args) => {
        const vals = args.filter((a) => a !== null && a !== undefined).map((a) => Number(a));
        return vals.length ? Math.max(...vals) : Number.NEGATIVE_INFINITY;
      },
      coalesce: (...args) => args.find((a) => a !== null && a !== undefined) ?? null,
      concat: (...args) => args.map((a) => (a === null || a === undefined ? "" : String(a))).join(""),
      contains: (s, sub) => (s === null || s === undefined ? false : String(s).includes(String(sub))),
      starts_with: (s, prefix) => (s === null || s === undefined ? false : String(s).startsWith(String(prefix))),
      ends_with: (s, suffix) => (s === null || s === undefined ? false : String(s).endsWith(String(suffix))),
      is_null: (v) => v === null || v === undefined,
      is_not_null: (v) => v !== null && v !== undefined,
    };
    for (const [name, fn] of Object.entries(builtins)) {
      this.functions.set(name, {
        functionName: name,
        sqlTemplate: "",
        argCount: 0,
        returnType: "any",
        notes: `Built-in: ${name}`,
        fn,
      });
    }
  }

  /** Register or replace a callable function binding (Python: python_func). */
  registerFunction(name: string, fn: BuiltinFunction, notes?: string): void {
    this.functions.set(name, {
      functionName: name,
      sqlTemplate: "",
      argCount: 0,
      returnType: "any",
      notes: notes ?? `Registered: ${name}`,
      fn,
    });
  }

  // ── Concept / entity / proposition lookups ───────────────────

  addConcept(concept: Concept): void {
    this.concepts.set(concept.id, concept);
  }

  getConcept(conceptId: string): Concept | undefined {
    return this.concepts.get(conceptId);
  }

  getConceptByName(name: string): Concept | undefined {
    for (const c of this.concepts.values()) if (c.name === name) return c;
    return undefined;
  }

  addEntity(entity: Entity): void {
    this.entities.set(entity.id, entity);
  }

  getEntity(entityId: string): Entity | undefined {
    return this.entities.get(entityId);
  }

  getEntityByExternalId(externalId: string): Entity | undefined {
    for (const e of this.entities.values()) if (e.externalId === externalId) return e;
    return undefined;
  }

  addProposition(proposition: Proposition): void {
    this.propositions.set(proposition.id, proposition);
  }

  getProposition(propositionId: string): Proposition | undefined {
    return this.propositions.get(propositionId);
  }

  getAttribute(attributeId: string): ConceptAttribute | undefined {
    for (const concept of this.concepts.values()) {
      for (const attr of Object.values(concept.attributes)) {
        if (attr.id === attributeId) return attr;
      }
    }
    return undefined;
  }

  getRelationship(relationshipId: string): ConceptRelationship | undefined {
    return this.relationships.get(relationshipId);
  }

  getStateTransition(transitionId: string): ConceptStateTransition | undefined {
    return this.stateTransitions.get(transitionId);
  }

  getFunction(name: string) {
    return this.functions.get(name);
  }

  // ── Expression evaluation ────────────────────────────────────

  evaluate(expression: Expression, context: EvalContext): unknown {
    const compiled = this.compiler.compileExpression(expression);
    return compiled(context);
  }

  // ── Rule evaluation ──────────────────────────────────────────

  checkRule(rule: Rule, entity: Entity): [boolean, string] {
    const context: EvalContext = { entity };
    try {
      if (rule.expression) {
        const result = this.evaluate(rule.expression, context);
        const passed = Boolean(result);
        return [passed, `Rule '${rule.name}' ${passed ? "passed" : "failed"}`];
      }
      return [false, `Rule '${rule.name}' has no expression`];
    } catch (exc) {
      if (rule.severity === Severity.Hard) {
        return [false, `Rule '${rule.name}' error: ${exc instanceof Error ? exc.message : String(exc)}`];
      }
      return [true, `Rule '${rule.name}' soft error: ${exc instanceof Error ? exc.message : String(exc)}`];
    }
  }

  checkTransitionGuard(transition: ConceptStateTransition, entity: Entity): [boolean, GuardResult[]] {
    const results: GuardResult[] = [];
    let allPassed = true;

    const check = (rule: Rule): void => {
      const [passed, reason] = this.checkRule(rule, entity);
      results.push({ rule_id: rule.id, rule_name: rule.name, passed, reason });
      if (!passed) allPassed = false;
    };

    for (const rule of transition.guards) check(rule);

    const concept = this.concepts.get(transition.conceptId);
    if (concept) for (const rule of concept.invariants) check(rule);

    return [allPassed, results];
  }

  // ── State transitions ────────────────────────────────────────

  transitionEntity(
    entityId: string,
    transitionId: string,
    opts: {
      sourceEventId?: string;
      correlationId?: string;
      actor?: string;
      sourceNamespace?: string;
    } = {},
  ): TransitionOutcome {
    const nowIso = () => new Date().toISOString();
    const reject = (results: { error: string }[], conceptId?: string): TransitionOutcome => {
      const event = buildTransitionEvent({
        sourceEventId: opts.sourceEventId ?? `transition:${transitionId}:entity:${entityId}`,
        entityId,
        transitionId,
        outcome: "rejected",
        results,
        conceptId: conceptId ?? null,
        correlationId: opts.correlationId ?? null,
        actor: opts.actor ?? null,
        sourceNamespace: opts.sourceNamespace,
        effectiveAt: nowIso(),
      });
      this.lastTransitionEvent = event;
      this.notifyTransitionEvent(event);
      return { committed: false, results, event };
    };

    const entity = this.entities.get(entityId);
    if (!entity) return reject([{ error: "Entity not found" }]);
    const transition = this.stateTransitions.get(transitionId);
    if (!transition) return reject([{ error: "Transition not found" }], entity.conceptId);

    const [allPassed, results] = this.checkTransitionGuard(transition, entity);
    const eventId = opts.sourceEventId ?? `transition:${transitionId}:entity:${entityId}`;
    const concept = this.concepts.get(entity.conceptId);
    const stateAttr = concept
      ? Object.values(concept.attributes).find((a) => a.isStateAttribute)
      : undefined;
    const stateBefore = stateAttr ? entity.attributes[stateAttr.name] ?? null : null;
    const outcome = allPassed ? "committed" : "refused";

    if (!allPassed) {
      const event = buildTransitionEvent({
        sourceEventId: eventId,
        entityId,
        transitionId,
        outcome,
        results,
        conceptId: entity.conceptId,
        stateBefore,
        stateAfter: stateBefore,
        correlationId: opts.correlationId ?? null,
        actor: opts.actor ?? null,
        sourceNamespace: opts.sourceNamespace,
        effectiveAt: nowIso(),
      });
      this.lastTransitionEvent = event;
      this.notifyTransitionEvent(event);
      return { committed: false, results, event };
    }

    if (stateAttr && stateAttr.allowedValues.includes(transition.toValue)) {
      entity.attributes[stateAttr.name] = transition.toValue;
    }
    const stateAfter = stateAttr ? entity.attributes[stateAttr.name] ?? null : null;
    const event = buildTransitionEvent({
      sourceEventId: eventId,
      entityId,
      transitionId,
      outcome,
      results,
      conceptId: entity.conceptId,
      stateBefore,
      stateAfter,
      correlationId: opts.correlationId ?? null,
      actor: opts.actor ?? null,
      sourceNamespace: opts.sourceNamespace,
      effectiveAt: nowIso(),
    });
    this.lastTransitionEvent = event;
    // The source API persists this event before dispatching its outbox.
    this.notifyTransitionEvent(event);

    // Fired only on a successful transition (refused/rejected do NOT snapshot).
    for (const listener of this.transitionListeners) {
      try {
        listener({
          transition_id: transitionId,
          entity_id: entityId,
          to_value: transition.toValue,
          effective_at: event.effectiveAt,
          event,
        });
      } catch {
        /* listener errors never break the transition */
      }
    }

    return { committed: true, results, event };
  }

  registerTransitionListener(listener: TransitionListener): void {
    this.transitionListeners.push(listener);
  }

  registerTransitionEventListener(listener: TransitionEventListener): void {
    this.transitionEventListeners.push(listener);
  }

  private notifyTransitionEvent(event: KeychainEvent): void {
    for (const listener of this.transitionEventListeners) {
      try {
        listener({ event });
      } catch {
        /* listener errors never break the transition */
      }
    }
  }

  // ── Frame discipline (v31/v35) ───────────────────────────────

  addFrameDimension(dim: FrameDimension): void {
    this.frameDimensions.set(dim.id, dim);
  }

  getFrameDimension(dimId: string): FrameDimension | undefined {
    return this.frameDimensions.get(dimId);
  }

  getFrameDimensionByName(name: string): FrameDimension | undefined {
    for (const d of this.frameDimensions.values()) if (d.name === name) return d;
    return undefined;
  }

  addFrameDimensionValue(val: FrameDimensionValue): void {
    this.frameDimensionValues.set(val.id, val);
  }

  addPropositionFrameValue(pfv: PropositionFrameValue): void {
    const prop = this.propositions.get(pfv.propositionId);
    if (prop) prop.frameValues.push(pfv);
  }

  addFrameDimensionMeaning(meaning: FrameDimensionMeaning): void {
    this.frameDimensionMeanings.set(meaning.id, meaning);
  }

  /** Meaning propositions describing a frame dimension (id or name). */
  meaningsOf(dimension: string, value?: string): Proposition[] {
    const dim = this.frameDimensions.get(dimension) ?? this.getFrameDimensionByName(dimension);
    if (!dim) return [];

    const results: Proposition[] = [];
    for (const meaning of this.frameDimensionMeanings.values()) {
      const prop = this.propositions.get(meaning.propositionId);
      if (!prop) continue;
      if (meaning.dimensionId === dim.id) {
        results.push(prop); // whole-dimension meaning always applies
      } else if (meaning.frameDimensionValueId) {
        const fdv = this.frameDimensionValues.get(meaning.frameDimensionValueId);
        if (fdv && fdv.dimensionId === dim.id) {
          if (value === undefined || fdv.value === value) results.push(prop);
        }
      }
    }
    return results;
  }

  // ── Proposition evaluation ───────────────────────────────────

  /**
   * Evaluate a proposition with optional frame-context discipline (v32).
   * Returns [disposition, allPassed, contextStatus] where contextStatus is
   * 'not_scoped' | 'context_required' | 'context_mismatch' | 'scoped'.
   * A null disposition with a context gate status means "not evaluated".
   */
  evaluateProposition(
    prop: Proposition,
    context?: Record<string, unknown>,
  ): [Disposition | null, boolean, string] {
    // ── Context gate: frame discipline (v31/v32) ─────────────
    const framedCount = prop.frameValues.length;

    let contextStatus: string;
    if (framedCount > 0) {
      if (context === undefined) {
        return [null, false, "context_required"];
      }
      for (const key of Object.keys(context)) {
        const dim = this.getFrameDimensionByName(key);
        if (!dim) {
          throw new Error(
            `evaluate_proposition: context key '${key}' names no known frame_dimension`,
          );
        }
      }
      for (const pfv of prop.frameValues) {
        const dim = this.frameDimensions.get(pfv.dimensionId);
        if (!dim) return [null, false, "context_required"];

        const ctxVal = context[dim.name];
        if (ctxVal === undefined || ctxVal === null) {
          return [null, false, "context_required"];
        }

        if (dim.valueKind === "governed_reference") {
          const fdv = this.frameDimensionValues.get(pfv.referenceValueId ?? "");
          if (!fdv || fdv.value !== String(ctxVal)) {
            return [null, false, "context_mismatch"];
          }
        } else if (dim.valueKind === "typed_scalar") {
          const scalarType = dim.scalarType ?? "text";
          const scalar = pfv.scalarValue;
          try {
            if (scalarType === "integer") {
              if (Math.trunc(Number(ctxVal)) !== Math.trunc(Number(scalar))) {
                return [null, false, "context_mismatch"];
              }
            } else if (scalarType === "numeric") {
              if (Number(ctxVal) !== Number(scalar)) {
                return [null, false, "context_mismatch"];
              }
            } else if (scalarType === "boolean") {
              const boolVal = Boolean(ctxVal);
              const scalarBool = ["true", "True", "1"].includes(String(scalar));
              if (boolVal !== scalarBool) {
                return [null, false, "context_mismatch"];
              }
            } else {
              // text / timestamp
              if (String(ctxVal) !== String(scalar)) {
                return [null, false, "context_mismatch"];
              }
            }
          } catch {
            return [null, false, "context_mismatch"];
          }
        } else {
          throw new Error(
            `evaluate_proposition: dimension ${dim.name} has unrecognized value_kind ${dim.valueKind}`,
          );
        }
      }
      contextStatus = "scoped";
    } else {
      contextStatus = "not_scoped";
    }

    // ── Assertion evaluation ─────────────────────────────────
    const entity = this.entities.get(prop.subjectEntityId);
    if (!entity) return [Disposition.Rejected, false, contextStatus];

    let allPassed = true;
    let relationalFailed = false;

    for (const rule of prop.assertions) {
      const [passed] = this.checkRule(rule, entity);
      if (!passed) {
        allPassed = false;
        if (rule.isRelationalCheck) relationalFailed = true;
      }
    }

    const disposition = allPassed
      ? Disposition.Asserted
      : relationalFailed
        ? Disposition.Disputed
        : Disposition.Rejected;

    return [disposition, allPassed, contextStatus];
  }

  reopenDisputedProposition(prop: Proposition, _externalId: string): Disposition {
    if (prop.disposition !== Disposition.Disputed) return prop.disposition;
    const [disposition] = this.evaluateProposition(prop);
    return disposition ?? prop.disposition;
  }

  // ── Change events ────────────────────────────────────────────

  onChange(conceptName: string, entityId: string): { propositionId: string; action: string; disposition: Disposition }[] {
    const results: { propositionId: string; action: string; disposition: Disposition }[] = [];
    const entity = this.entities.get(entityId);
    if (!entity) return results;
    const concept = this.getConceptByName(conceptName);
    if (!concept) return results;

    const externalId = entity.externalId;

    for (const prop of this.propositions.values()) {
      if (prop.subjectEntityId === entityId && prop.assetConceptId === concept.id) {
        const old = prop.disposition;
        let next: Disposition | null;
        if (old === Disposition.Disputed && externalId) {
          next = this.reopenDisputedProposition(prop, externalId);
        } else {
          const [d] = this.evaluateProposition(prop);
          next = d;
        }
        if (next && next !== old) {
          prop.disposition = next;
          prop.lastEvaluatedAt = new Date().toISOString();
          results.push({ propositionId: prop.id, action: "event_evaluate", disposition: next });
        }
      }
    }

    for (const handler of this.eventHandlers) {
      try {
        handler(conceptName, entityId, results);
      } catch {
        /* handler errors never break the sweep */
      }
    }
    return results;
  }

  registerEventHandler(handler: ChangeHandler): void {
    this.eventHandlers.push(handler);
  }

  // ── Convenience helpers ──────────────────────────────────────

  addEntityByConceptName(
    conceptName: string,
    attributes: Record<string, Entity["attributes"][string]>,
    externalId?: string,
  ): Entity {
    const concept = this.getConceptByName(conceptName);
    if (!concept) throw new Error(`Concept not found: ${conceptName}`);
    const entity: Entity = {
      id: crypto.randomUUID(),
      conceptId: concept.id,
      attributes,
      ...(externalId !== undefined ? { externalId } : {}),
    };
    this.entities.set(entity.id, entity);
    return entity;
  }
}
