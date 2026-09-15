/**
 * SOLScript TypeScript core — query builder + transaction context.
 *
 * Ported from python/SOLScript/solscript/query_builder.py. Fluent queries
 * over a single concept's entities; TransactionContext provides
 * snapshot/rollback/commit semantics.
 */
import type { Concept, Expression, JsonValue, SolOperator } from "./models.js";
import type { ResolutionInterpreter } from "./interpreter.js";
/** Entry point for building queries over concept entities. */
export declare class QueryBuilder {
    private readonly interpreter;
    constructor(interpreter: ResolutionInterpreter);
    select(conceptName: string): Query;
}
/** Fluent query interface over a single concept. */
export declare class Query {
    private readonly interpreter;
    private readonly concept;
    private readonly filters;
    private readonly orderBys;
    private limit;
    private offset;
    private selectFields;
    constructor(interpreter: ResolutionInterpreter, concept: Concept);
    filter(condition: Expression): Query;
    where(attribute: string, op: SolOperator, value: JsonValue): Query;
    orderBy(attribute: string, direction?: string): Query;
    limitN(n: number): Query;
    offsetN(n: number): Query;
    selectFieldsTo(...fields: string[]): Query;
    execute(): Record<string, JsonValue | null>[];
    count(): number;
}
export interface ChangeRecord {
    type: string;
    data: Record<string, unknown>;
    timestamp: string;
}
/**
 * Snapshot/rollback/commit semantics (Python context manager → explicit
 * async-with pattern: `const tx = interpreter.transaction()` … commit/rollback).
 */
export declare class TransactionContext {
    private readonly interpreter;
    changes: ChangeRecord[];
    private snapshot;
    constructor(interpreter: ResolutionInterpreter);
    /** Python __enter__ — capture the snapshot. */
    begin(): TransactionContext;
    addChange(changeType: string, data: Record<string, unknown>): void;
    rollback(): void;
    commit(): void;
}
//# sourceMappingURL=query-builder.d.ts.map