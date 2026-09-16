/**
 * SOLScript TypeScript core — query builder + transaction context.
 *
 * Ported from python/SOLScript/solscript/query_builder.py. Fluent queries
 * over a single concept's entities; TransactionContext provides
 * snapshot/rollback/commit semantics.
 */
import { randomUUID } from "node:crypto";
import { ExpressionKind } from "./models.js";
import { ExpressionCompiler } from "./expression-compiler.js";
/** Entry point for building queries over concept entities. */
export class QueryBuilder {
    interpreter;
    constructor(interpreter) {
        this.interpreter = interpreter;
    }
    select(conceptName) {
        const concept = this.interpreter.getConceptByName(conceptName);
        if (!concept)
            throw new Error(`Concept not found: ${conceptName}`);
        return new Query(this.interpreter, concept);
    }
}
/** Fluent query interface over a single concept. */
export class Query {
    interpreter;
    concept;
    filters = [];
    orderBys = [];
    limit = null;
    offset = null;
    selectFields = [];
    constructor(interpreter, concept) {
        this.interpreter = interpreter;
        this.concept = concept;
    }
    filter(condition) {
        this.filters.push(condition);
        return this;
    }
    where(attribute, op, value) {
        const attr = Object.values(this.concept.attributes).find((a) => a.name === attribute);
        if (!attr)
            throw new Error(`Attribute not found: ${attribute}`);
        const attrExpr = {
            id: randomUUID(),
            kind: ExpressionKind.AttributeRef,
            returnType: attr.valueType,
            attributeId: attr.id,
            operands: [],
        };
        const literalExpr = {
            id: randomUUID(),
            kind: ExpressionKind.Literal,
            returnType: attr.valueType,
            literalValue: value,
            operands: [],
        };
        const opExpr = {
            id: randomUUID(),
            kind: ExpressionKind.Operator,
            returnType: "boolean",
            operator: op,
            operands: [attrExpr, literalExpr],
        };
        this.filters.push(opExpr);
        return this;
    }
    orderBy(attribute, direction = "ASC") {
        this.orderBys.push({ attribute, direction });
        return this;
    }
    limitN(n) {
        this.limit = n;
        return this;
    }
    offsetN(n) {
        this.offset = n;
        return this;
    }
    selectFieldsTo(...fields) {
        this.selectFields = [...fields];
        return this;
    }
    execute() {
        const compiler = new ExpressionCompiler(this.interpreter.host());
        const entities = [];
        for (const e of this.interpreter.entities.values()) {
            if (e.conceptId === this.concept.id)
                entities.push(e);
        }
        const results = [];
        for (const entity of entities) {
            const ctx = { entity };
            let passed = true;
            for (const fExpr of this.filters) {
                try {
                    const compiled = compiler.compileExpression(fExpr);
                    if (!compiled(ctx)) {
                        passed = false;
                        break;
                    }
                }
                catch {
                    passed = false;
                    break;
                }
            }
            if (!passed)
                continue;
            if (this.selectFields.length > 0) {
                const row = {};
                for (const field of this.selectFields) {
                    if (field in entity.attributes) {
                        row[field] = entity.attributes[field] ?? null;
                    }
                    else if (field === "id") {
                        row["id"] = entity.id;
                    }
                    else if (field === "external_id") {
                        row["external_id"] = entity.externalId ?? null;
                    }
                }
                results.push(row);
            }
            else {
                results.push({
                    id: entity.id,
                    external_id: entity.externalId ?? null,
                    ...entity.attributes,
                });
            }
        }
        // Python applies order_bys in reversed() order (last wins).
        for (const { attribute, direction } of [...this.orderBys].reverse()) {
            const reverse = direction.toUpperCase() === "DESC";
            results.sort((a, b) => {
                const av = a[attribute] ?? "";
                const bv = b[attribute] ?? "";
                let cmp;
                if (typeof av === "number" && typeof bv === "number")
                    cmp = av - bv;
                else
                    cmp = String(av) < String(bv) ? -1 : String(av) > String(bv) ? 1 : 0;
                return reverse ? -cmp : cmp;
            });
        }
        if (this.offset !== null)
            results.splice(0, this.offset);
        if (this.limit !== null)
            results.length = Math.min(results.length, this.limit);
        return results;
    }
    count() {
        return this.execute().length;
    }
}
/**
 * Snapshot/rollback/commit semantics (Python context manager → explicit
 * async-with pattern: `const tx = interpreter.transaction()` … commit/rollback).
 */
export class TransactionContext {
    interpreter;
    changes = [];
    snapshot = null;
    constructor(interpreter) {
        this.interpreter = interpreter;
    }
    /** Python __enter__ — capture the snapshot. */
    begin() {
        this.snapshot = {
            entities: new Map(structuredClone(Array.from(this.interpreter.entities.entries()))),
            propositions: new Map(structuredClone(Array.from(this.interpreter.propositions.entries()))),
            evaluationCache: new Map(this.interpreter.evaluationCache.entries()),
        };
        return this;
    }
    addChange(changeType, data) {
        this.changes.push({ type: changeType, data, timestamp: new Date().toISOString() });
    }
    rollback() {
        if (!this.snapshot)
            throw new Error("TransactionContext not started");
        this.interpreter.entities = this.snapshot.entities;
        this.interpreter.propositions = this.snapshot.propositions;
        this.interpreter.evaluationCache = this.snapshot.evaluationCache;
        this.changes = [];
    }
    commit() {
        for (const change of this.changes) {
            const ctype = change.type;
            const data = change.data;
            if (ctype === "entity_update") {
                const entity = data["entity"];
                this.interpreter.entities.set(entity.id, entity);
                const concept = this.interpreter.getConcept(entity.conceptId);
                if (concept)
                    this.interpreter.onChange(concept.name, entity.id);
            }
            else if (ctype === "proposition_update") {
                const prop = data["proposition"];
                this.interpreter.propositions.set(prop.id, prop);
            }
        }
        this.changes = [];
    }
}
//# sourceMappingURL=query-builder.js.map