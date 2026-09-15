/**
 * SOLScript TypeScript core — query builder + transaction context.
 *
 * Ported from python/SOLScript/solscript/query_builder.py. Fluent queries
 * over a single concept's entities; TransactionContext provides
 * snapshot/rollback/commit semantics.
 */

import { randomUUID } from "node:crypto";

import type {
  Concept,
  Entity,
  Expression,
  JsonValue,
  Proposition,
  SolOperator,
} from "./models.js";
import { ExpressionKind } from "./models.js";
import { ExpressionCompiler } from "./expression-compiler.js";
import type { ResolutionInterpreter } from "./interpreter.js";

/** Entry point for building queries over concept entities. */
export class QueryBuilder {
  constructor(private readonly interpreter: ResolutionInterpreter) {}

  select(conceptName: string): Query {
    const concept = this.interpreter.getConceptByName(conceptName);
    if (!concept) throw new Error(`Concept not found: ${conceptName}`);
    return new Query(this.interpreter, concept);
  }
}

interface OrderKey {
  attribute: string;
  direction: string;
}

/** Fluent query interface over a single concept. */
export class Query {
  private readonly filters: Expression[] = [];
  private readonly orderBys: OrderKey[] = [];
  private limit: number | null = null;
  private offset: number | null = null;
  private selectFields: string[] = [];

  constructor(
    private readonly interpreter: ResolutionInterpreter,
    private readonly concept: Concept,
  ) {}

  filter(condition: Expression): Query {
    this.filters.push(condition);
    return this;
  }

  where(attribute: string, op: SolOperator, value: JsonValue): Query {
    const attr = Object.values(this.concept.attributes).find((a) => a.name === attribute);
    if (!attr) throw new Error(`Attribute not found: ${attribute}`);

    const attrExpr: Expression = {
      id: randomUUID(),
      kind: ExpressionKind.AttributeRef,
      returnType: attr.valueType,
      attributeId: attr.id,
      operands: [],
    };
    const literalExpr: Expression = {
      id: randomUUID(),
      kind: ExpressionKind.Literal,
      returnType: attr.valueType,
      literalValue: value,
      operands: [],
    };
    const opExpr: Expression = {
      id: randomUUID(),
      kind: ExpressionKind.Operator,
      returnType: "boolean",
      operator: op,
      operands: [attrExpr, literalExpr],
    };
    this.filters.push(opExpr);
    return this;
  }

  orderBy(attribute: string, direction = "ASC"): Query {
    this.orderBys.push({ attribute, direction });
    return this;
  }

  limitN(n: number): Query {
    this.limit = n;
    return this;
  }

  offsetN(n: number): Query {
    this.offset = n;
    return this;
  }

  selectFieldsTo(...fields: string[]): Query {
    this.selectFields = [...fields];
    return this;
  }

  execute(): Record<string, JsonValue | null>[] {
    const compiler = new ExpressionCompiler(this.interpreter.host());
    const entities: Entity[] = [];
    for (const e of this.interpreter.entities.values()) {
      if (e.conceptId === this.concept.id) entities.push(e);
    }

    const results: Record<string, JsonValue | null>[] = [];
    for (const entity of entities) {
      const ctx = { entity } as Record<string, unknown>;
      let passed = true;
      for (const fExpr of this.filters) {
        try {
          const compiled = compiler.compileExpression(fExpr);
          if (!compiled(ctx)) {
            passed = false;
            break;
          }
        } catch {
          passed = false;
          break;
        }
      }
      if (!passed) continue;

      if (this.selectFields.length > 0) {
        const row: Record<string, JsonValue | null> = {};
        for (const field of this.selectFields) {
          if (field in entity.attributes) {
            row[field] = entity.attributes[field] ?? null;
          } else if (field === "id") {
            row["id"] = entity.id;
          } else if (field === "external_id") {
            row["external_id"] = entity.externalId ?? null;
          }
        }
        results.push(row);
      } else {
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
        let cmp: number;
        if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
        else cmp = String(av) < String(bv) ? -1 : String(av) > String(bv) ? 1 : 0;
        return reverse ? -cmp : cmp;
      });
    }

    if (this.offset !== null) results.splice(0, this.offset);
    if (this.limit !== null) results.length = Math.min(results.length, this.limit);

    return results;
  }

  count(): number {
    return this.execute().length;
  }
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
export class TransactionContext {
  changes: ChangeRecord[] = [];
  private snapshot: {
    entities: Map<string, Entity>;
    propositions: Map<string, Proposition>;
    evaluationCache: Map<string, unknown>;
  } | null = null;

  constructor(private readonly interpreter: ResolutionInterpreter) {}

  /** Python __enter__ — capture the snapshot. */
  begin(): TransactionContext {
    this.snapshot = {
      entities: new Map(structuredClone(Array.from(this.interpreter.entities.entries()))),
      propositions: new Map(structuredClone(Array.from(this.interpreter.propositions.entries()))),
      evaluationCache: new Map(this.interpreter.evaluationCache.entries()),
    };
    return this;
  }

  addChange(changeType: string, data: Record<string, unknown>): void {
    this.changes.push({ type: changeType, data, timestamp: new Date().toISOString() });
  }

  rollback(): void {
    if (!this.snapshot) throw new Error("TransactionContext not started");
    this.interpreter.entities = this.snapshot.entities;
    this.interpreter.propositions = this.snapshot.propositions;
    this.interpreter.evaluationCache = this.snapshot.evaluationCache;
    this.changes = [];
  }

  commit(): void {
    for (const change of this.changes) {
      const ctype = change.type;
      const data = change.data;
      if (ctype === "entity_update") {
        const entity = data["entity"] as Entity;
        this.interpreter.entities.set(entity.id, entity);
        const concept = this.interpreter.getConcept(entity.conceptId);
        if (concept) this.interpreter.onChange(concept.name, entity.id);
      } else if (ctype === "proposition_update") {
        const prop = data["proposition"] as Proposition;
        this.interpreter.propositions.set(prop.id, prop);
      }
    }
    this.changes = [];
  }
}
