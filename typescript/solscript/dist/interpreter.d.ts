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
import { Concept, ConceptAttribute, ConceptRelationship, ConceptStateTransition, Disposition, Entity, Expression, FrameDimension, FrameDimensionMeaning, FrameDimensionValue, Proposition, PropositionFrameValue, Representation, Rule } from "./models.js";
import { EvalContext, FunctionRegistry } from "./expression-compiler.js";
import { KeychainEvent } from "./events.js";
export interface GuardResult {
    rule_id: string;
    rule_name: string;
    passed: boolean;
    reason: string;
}
export interface TransitionOutcome {
    committed: boolean;
    results: GuardResult[] | {
        error: string;
    }[];
    event: KeychainEvent;
}
export type TransitionListener = (info: {
    transition_id: string;
    entity_id: string;
    to_value: string;
    effective_at: string | null;
    event: KeychainEvent;
}) => void;
export type TransitionEventListener = (info: {
    event: KeychainEvent;
}) => void;
export type ChangeHandler = (conceptName: string, entityId: string, results: {
    propositionId: string;
    action: string;
    disposition: Disposition;
}[]) => void;
export type BuiltinFunction = (...args: unknown[]) => unknown;
export declare class ResolutionInterpreter {
    concepts: Map<string, Concept>;
    entities: Map<string, Entity>;
    propositions: Map<string, Proposition>;
    expressions: Map<string, Expression>;
    rules: Map<string, Rule>;
    functions: FunctionRegistry;
    representations: Map<string, Representation>;
    relationships: Map<string, ConceptRelationship>;
    stateTransitions: Map<string, ConceptStateTransition>;
    frameDimensions: Map<string, FrameDimension>;
    frameDimensionValues: Map<string, FrameDimensionValue>;
    frameDimensionMeanings: Map<string, FrameDimensionMeaning>;
    evaluationCache: Map<string, unknown>;
    executionContext: Record<string, unknown>;
    eventHandlers: ChangeHandler[];
    /** Fired after a SUCCESSFUL transition only (catalogue 332d6831 decision points). */
    transitionListeners: TransitionListener[];
    /** Fired for EVERY transition attempt (committed + refused + rejected). */
    transitionEventListeners: TransitionEventListener[];
    lastTransitionEvent: KeychainEvent | null;
    private readonly compiler;
    constructor();
    /** ExpressionCompiler host view over this interpreter's registries. */
    host(): {
        getAttribute(attributeId: string): ConceptAttribute | undefined;
        getRelationship(relationshipId: string): ConceptRelationship | undefined;
        getProposition(propositionId: string): Proposition | undefined;
        getFunction(name: string): (import("./models.js").FunctionBinding & {
            fn?: (...args: unknown[]) => unknown;
        }) | undefined;
        entities(): Iterable<Entity>;
    };
    private registerBuiltinFunctions;
    /** Register or replace a callable function binding (Python: python_func). */
    registerFunction(name: string, fn: BuiltinFunction, notes?: string): void;
    addConcept(concept: Concept): void;
    getConcept(conceptId: string): Concept | undefined;
    getConceptByName(name: string): Concept | undefined;
    addEntity(entity: Entity): void;
    getEntity(entityId: string): Entity | undefined;
    getEntityByExternalId(externalId: string): Entity | undefined;
    addProposition(proposition: Proposition): void;
    getProposition(propositionId: string): Proposition | undefined;
    getAttribute(attributeId: string): ConceptAttribute | undefined;
    getRelationship(relationshipId: string): ConceptRelationship | undefined;
    getStateTransition(transitionId: string): ConceptStateTransition | undefined;
    getFunction(name: string): (import("./models.js").FunctionBinding & {
        fn?: (...args: unknown[]) => unknown;
    }) | undefined;
    evaluate(expression: Expression, context: EvalContext): unknown;
    checkRule(rule: Rule, entity: Entity): [boolean, string];
    checkTransitionGuard(transition: ConceptStateTransition, entity: Entity): [boolean, GuardResult[]];
    transitionEntity(entityId: string, transitionId: string, opts?: {
        sourceEventId?: string;
        correlationId?: string;
        actor?: string;
        sourceNamespace?: string;
    }): TransitionOutcome;
    registerTransitionListener(listener: TransitionListener): void;
    registerTransitionEventListener(listener: TransitionEventListener): void;
    private notifyTransitionEvent;
    addFrameDimension(dim: FrameDimension): void;
    getFrameDimension(dimId: string): FrameDimension | undefined;
    getFrameDimensionByName(name: string): FrameDimension | undefined;
    addFrameDimensionValue(val: FrameDimensionValue): void;
    addPropositionFrameValue(pfv: PropositionFrameValue): void;
    addFrameDimensionMeaning(meaning: FrameDimensionMeaning): void;
    /** Meaning propositions describing a frame dimension (id or name). */
    meaningsOf(dimension: string, value?: string): Proposition[];
    /**
     * Evaluate a proposition with optional frame-context discipline (v32).
     * Returns [disposition, allPassed, contextStatus] where contextStatus is
     * 'not_scoped' | 'context_required' | 'context_mismatch' | 'scoped'.
     * A null disposition with a context gate status means "not evaluated".
     */
    evaluateProposition(prop: Proposition, context?: Record<string, unknown>): [Disposition | null, boolean, string];
    reopenDisputedProposition(prop: Proposition, _externalId: string): Disposition;
    onChange(conceptName: string, entityId: string): {
        propositionId: string;
        action: string;
        disposition: Disposition;
    }[];
    registerEventHandler(handler: ChangeHandler): void;
    addEntityByConceptName(conceptName: string, attributes: Record<string, Entity["attributes"][string]>, externalId?: string): Entity;
}
//# sourceMappingURL=interpreter.d.ts.map