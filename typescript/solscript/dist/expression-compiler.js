/**
 * SOLScript TypeScript core — expression compiler.
 *
 * Ported from python/SOLScript/solscript/expression_compiler.py. Compiles
 * expression trees into `(ctx) => value` callables. Builtin function
 * bindings are provided by an injectable registry (Python's
 * FunctionBinding.python_func has no wire equivalent).
 */
import { ExpressionKind, Quantifier, SolOperator } from "./models.js";
export class ExpressionCompiler {
    host;
    compiledCache = new Map();
    constructor(host) {
        this.host = host;
    }
    compileExpression(expr) {
        const cacheKey = `${expr.id}_${stableHash(expr)}`;
        const cached = this.compiledCache.get(cacheKey);
        if (cached)
            return cached;
        const compiled = this.compileNode(expr);
        this.compiledCache.set(cacheKey, compiled);
        return compiled;
    }
    // ── Node compiler ────────────────────────────────────────────
    /** Coerce a literal to its declared return type (schema stores text). */
    static coerceLiteral(value, returnType) {
        if (value === null || value === undefined)
            return null;
        const rt = (returnType ?? "").toLowerCase();
        if (["integer", "int", "bigint", "smallint"].includes(rt)) {
            const n = Number(value);
            return Number.isNaN(n) ? value : Math.trunc(n);
        }
        if (["numeric", "decimal", "double", "double precision", "float", "real"].includes(rt)) {
            const n = Number(value);
            return Number.isNaN(n) ? value : n;
        }
        if (rt === "boolean") {
            if (typeof value === "boolean")
                return value;
            if (["true", "True", "t", "1"].includes(String(value)))
                return true;
            if (["false", "False", "f", "0"].includes(String(value)))
                return false;
            return value;
        }
        return value;
    }
    compileNode(expr) {
        switch (expr.kind) {
            case ExpressionKind.Literal: {
                const val = ExpressionCompiler.coerceLiteral(expr.literalValue, expr.returnType);
                return () => val;
            }
            case ExpressionKind.AttributeRef: {
                const attr = this.host.getAttribute(expr.attributeId ?? "");
                return (ctx) => resolveAttribute(ctx, attr);
            }
            case ExpressionKind.Operator:
                return this.compileOperator(expr);
            case ExpressionKind.FunctionCall: {
                const func = this.host.getFunction(expr.functionName ?? "");
                const argFns = expr.operands.map((op) => this.compileNode(op));
                if (!func || !func.fn)
                    throw new Error(`Unknown function: ${expr.functionName}`);
                const pf = func.fn;
                return (ctx) => pf(...argFns.map((fn) => fn(ctx)));
            }
            case ExpressionKind.RelationshipRef:
                return this.compileRelationship(expr);
            case ExpressionKind.PropositionRef: {
                const prop = this.host.getProposition(expr.referencedPropositionId ?? "");
                const fieldName = expr.propositionRefField;
                if (fieldName === "value")
                    return () => (prop ? prop.value : null);
                if (fieldName === "disposition")
                    return () => (prop ? prop.disposition : null);
                return () => prop ?? null;
            }
            default:
                throw new Error(`Unsupported expression kind: ${expr.kind}`);
        }
    }
    // ── Operator compilation ─────────────────────────────────────
    compileOperator(expr) {
        const leftFn = this.compileNode(expr.operands[0]);
        const rightFn = expr.operands.length > 1 ? this.compileNode(expr.operands[1]) : null;
        const op = expr.operator;
        switch (op) {
            case SolOperator.And:
                return (ctx) => Boolean(leftFn(ctx)) && (rightFn ? Boolean(rightFn(ctx)) : true);
            case SolOperator.Or:
                return (ctx) => Boolean(leftFn(ctx)) || (rightFn ? Boolean(rightFn(ctx)) : false);
            case SolOperator.Not:
                return (ctx) => !leftFn(ctx);
            case SolOperator.Eq:
                return (ctx) => leftFn(ctx) === (rightFn ? rightFn(ctx) : null);
            case SolOperator.Neq:
                return (ctx) => leftFn(ctx) !== (rightFn ? rightFn(ctx) : null);
            case SolOperator.Gt:
                return (ctx) => compare(leftFn(ctx), rightFn ? rightFn(ctx) : null) > 0;
            case SolOperator.Lt:
                return (ctx) => compare(leftFn(ctx), rightFn ? rightFn(ctx) : null) < 0;
            case SolOperator.Gte:
                return (ctx) => compare(leftFn(ctx), rightFn ? rightFn(ctx) : null) >= 0;
            case SolOperator.Lte:
                return (ctx) => compare(leftFn(ctx), rightFn ? rightFn(ctx) : null) <= 0;
            default:
                throw new Error(`Unsupported operator: ${op}`);
        }
    }
    // ── Relationship compilation ─────────────────────────────────
    compileRelationship(expr) {
        const relation = this.host.getRelationship(expr.conceptRelationshipId ?? "");
        const childExpr = expr.operands[0];
        if (expr.quantifier === Quantifier.Exists)
            return (ctx) => this.checkRelationshipExists(ctx, relation, childExpr);
        if (expr.quantifier === Quantifier.All)
            return (ctx) => this.checkRelationshipAll(ctx, relation, childExpr);
        if (expr.quantifier === Quantifier.Count)
            return (ctx) => this.countRelationship(ctx, relation, childExpr);
        return (ctx) => this.getRelatedEntities(ctx, relation);
    }
    // ── Relationship helpers ─────────────────────────────────────
    checkRelationshipExists(ctx, relation, childExpr) {
        const related = this.navigateRelationship(ctx, relation);
        if (related.length === 0)
            return false;
        if (!childExpr)
            return true;
        const childFn = this.compileNode(childExpr);
        return related.some((entity) => childFn(childContext(ctx, entity)));
    }
    checkRelationshipAll(ctx, relation, childExpr) {
        const related = this.navigateRelationship(ctx, relation);
        if (related.length === 0)
            return true; // vacuously true
        if (!childExpr)
            return true;
        const childFn = this.compileNode(childExpr);
        return related.every((entity) => childFn(childContext(ctx, entity)));
    }
    countRelationship(ctx, relation, childExpr) {
        const related = this.navigateRelationship(ctx, relation);
        if (related.length === 0)
            return 0;
        if (!childExpr)
            return related.length;
        const childFn = this.compileNode(childExpr);
        return related.filter((entity) => childFn(childContext(ctx, entity))).length;
    }
    getRelatedEntities(ctx, relation) {
        return this.navigateRelationship(ctx, relation);
    }
    navigateRelationship(ctx, relation) {
        const entity = (ctx["entity"] ?? ctx["target_entity"] ?? ctx["subject"]);
        if (!entity || !relation)
            return [];
        const related = [];
        for (const other of this.host.entities()) {
            if (other.conceptId === relation.toConceptId && relationshipExists(entity, other, relation)) {
                related.push(other);
            }
        }
        return related;
    }
}
// ── Module-level helpers (pure; shared with query builder) ──────────
export function childContext(ctx, entity) {
    return { ...ctx, entity, parent: ctx["entity"] };
}
export function relationshipExists(fromEntity, toEntity, relation) {
    const binding = relation.binding;
    if (binding) {
        const fromVal = fromEntity.attributes[binding.fromColumn];
        const toVal = toEntity.attributes[binding.toColumn];
        return fromVal === toVal;
    }
    return false;
}
export function resolveAttribute(ctx, attr) {
    if (!attr)
        return null;
    const entity = ctx["entity"];
    if (entity && typeof entity === "object" && "attributes" in entity) {
        return entity.attributes[attr.name] ?? null;
    }
    if (attr.name in ctx)
        return ctx[attr.name];
    return null;
}
/** Total-order comparison for >, <, >=, <= over mixed scalars. */
function compare(a, b) {
    if (typeof a === "number" && typeof b === "number")
        return a - b;
    const sa = String(a);
    const sb = String(b);
    return sa < sb ? -1 : sa > sb ? 1 : 0;
}
/** Deterministic structural hash for expression cache keys (parity with Python's repr-hash intent). */
export function stableHash(value) {
    return stableStringify(value);
}
/** Canonical JSON: sorted keys, no whitespace — matches events.py _canonical_json. */
export function canonicalJson(value) {
    return stableStringify(value);
}
function stableStringify(value) {
    if (value === null || value === undefined)
        return "null";
    if (typeof value === "number")
        return Number.isFinite(value) ? JSON.stringify(value) : "null";
    if (typeof value === "boolean")
        return String(value);
    if (typeof value === "string")
        return JSON.stringify(value);
    if (Array.isArray(value))
        return `[${value.map(stableStringify).join(",")}]`;
    if (typeof value === "object") {
        const entries = Object.entries(value)
            .filter(([, v]) => v !== undefined)
            .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
            .map(([k, v]) => `${JSON.stringify(k)}:${stableStringify(v)}`);
        return `{${entries.join(",")}}`;
    }
    return JSON.stringify(String(value));
}
//# sourceMappingURL=expression-compiler.js.map