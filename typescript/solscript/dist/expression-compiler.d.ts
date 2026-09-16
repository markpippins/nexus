/**
 * SOLScript TypeScript core — expression compiler.
 *
 * Ported from python/SOLScript/solscript/expression_compiler.py. Compiles
 * expression trees into `(ctx) => value` callables. Builtin function
 * bindings are provided by an injectable registry (Python's
 * FunctionBinding.python_func has no wire equivalent).
 */
import type { ConceptAttribute, ConceptRelationship, Entity, Expression, FunctionBinding } from "./models.js";
/** Evaluation context: the subject entity plus optional frame/parent scope. */
export type EvalContext = Record<string, unknown>;
type Compiled = (ctx: EvalContext) => unknown;
export type FunctionRegistry = Map<string, FunctionBinding & {
    fn?: (...args: unknown[]) => unknown;
}>;
export interface ExpressionCompilerHost {
    getAttribute(attributeId: string): ConceptAttribute | undefined;
    getRelationship(relationshipId: string): ConceptRelationship | undefined;
    getProposition(propositionId: string): {
        value?: boolean;
        disposition?: unknown;
    } | undefined;
    getFunction(name: string): (FunctionBinding & {
        fn?: (...args: unknown[]) => unknown;
    }) | undefined;
    entities(): Iterable<Entity>;
}
export declare class ExpressionCompiler {
    private readonly host;
    private readonly compiledCache;
    constructor(host: ExpressionCompilerHost);
    compileExpression(expr: Expression): Compiled;
    /** Coerce a literal to its declared return type (schema stores text). */
    private static coerceLiteral;
    private compileNode;
    private compileOperator;
    private compileRelationship;
    private checkRelationshipExists;
    private checkRelationshipAll;
    private countRelationship;
    private getRelatedEntities;
    private navigateRelationship;
}
export declare function childContext(ctx: EvalContext, entity: Entity): EvalContext;
export declare function relationshipExists(fromEntity: Entity, toEntity: Entity, relation: ConceptRelationship): boolean;
export declare function resolveAttribute(ctx: EvalContext, attr: ConceptAttribute | undefined): unknown;
/** Deterministic structural hash for expression cache keys (parity with Python's repr-hash intent). */
export declare function stableHash(value: unknown): string;
/** Canonical JSON: sorted keys, no whitespace — matches events.py _canonical_json. */
export declare function canonicalJson(value: unknown): string;
export {};
//# sourceMappingURL=expression-compiler.d.ts.map