/**
 * run-solscript-ts-tests.ts — dependency-free unit harness for the
 * SOLScript TypeScript core. Covers edge cases beyond the parity scenario.
 * Exit 0 = all assertions pass.
 */

import { strict as assert } from "node:assert";

import {
  Disposition, ExpressionKind, Quantifier, RuleType, Severity, SolOperator,
} from "../src/models.js";
import type { Concept, Entity, Expression, Rule } from "../src/models.js";
import { ResolutionInterpreter } from "../src/interpreter.js";
import { QueryBuilder, TransactionContext } from "../src/query-builder.js";
import { buildReadSetManifest, stableDigest } from "../src/events.js";
import { canonicalJson } from "../src/expression-compiler.js";

let passed = 0;
function test(name: string, fn: () => void): void {
  fn();
  passed += 1;
  console.log(`  ok  ${name}`);
}

function attrExpr(id: string, attributeId: string, returnType = "text"): Expression {
  return { id, kind: ExpressionKind.AttributeRef, returnType, attributeId, operands: [] };
}
function litExpr(id: string, value: unknown, returnType: string): Expression {
  return { id, kind: ExpressionKind.Literal, returnType, literalValue: value as never, operands: [] };
}
function opExpr(id: string, operator: SolOperator, operands: Expression[]): Expression {
  return { id, kind: ExpressionKind.Operator, returnType: "boolean", operator, operands };
}

function makeConcept(): Concept {
  return {
    id: "c1", name: "Widget", description: undefined,
    attributes: {
      "a1": { id: "a1", conceptId: "c1", name: "size", valueType: "integer", isStateAttribute: false, allowedValues: [] },
      "a2": { id: "a2", conceptId: "c1", name: "status", valueType: "text", isStateAttribute: true, allowedValues: ["off", "on"] },
    },
    relationships: {}, invariants: [], derivations: [], stateTransitions: [], rules: [],
  };
}

function makeEntity(id: string, attrs: Record<string, unknown>): Entity {
  return { id, conceptId: "c1", attributes: attrs as never };
}

// ── Enum parity with Python reference ────────────────────────────────

test("enum string values are identical to the Python reference", () => {
  assert.equal(SolOperator.Eq, "=");
  assert.equal(SolOperator.Neq, "<>");
  assert.equal(SolOperator.And, "AND");
  assert.equal(Quantifier.Exists, "EXISTS");
  assert.equal(RuleType.Invariant, "invariant");
  assert.equal(Severity.Hard, "hard");
  // Disposition values are capitalized in Python
  assert.equal(Disposition.Asserted, "Asserted");
  assert.equal(Disposition.Disputed, "Disputed");
  assert.equal(Disposition.Rejected, "Rejected");
});

// ── Literal coercion ─────────────────────────────────────────────────

test("literals coerce to declared return types (schema stores text)", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  const expr = opExpr("op", SolOperator.Gt, [
    litExpr("l", "10", "integer"),
    litExpr("r", "5", "integer"),
  ]);
  assert.equal(interp.evaluate(expr, {}), true);
});

// ── Builtins ─────────────────────────────────────────────────────────

test("builtin function registry matches Python semantics", () => {
  const interp = new ResolutionInterpreter();
  assert.equal((interp.getFunction("sum")!.fn as any)(2, 3, null, 5), 10);
  assert.equal((interp.getFunction("count")!.fn as any)(1, null, 2), 2);
  assert.equal((interp.getFunction("coalesce")!.fn as any)(null, "x"), "x");
  assert.equal((interp.getFunction("contains")!.fn as any)("solscript", "scri"), true);
  assert.equal((interp.getFunction("is_null")!.fn as any)(null), true);
  assert.equal((interp.getFunction("is_null")!.fn as any)(0), false);
});

// ── Relationship quantifiers ─────────────────────────────────────────

test("relationship EXISTS/ALL/COUNT navigate via bound columns", () => {
  const interp = new ResolutionInterpreter();
  const concept = makeConcept();
  concept.relationships["r1"] = {
    id: "r1", fromConceptId: "c1", toConceptId: "c1", relationshipType: "peer",
    binding: { fromSchema: "s", fromTable: "t", fromColumn: "grp", toSchema: "s", toTable: "t", toColumn: "grp" },
    conditionals: [], name: "peers",
  };
  interp.addConcept(concept);
  // Relationships are registered on the interpreter's flat registry too
  // (DatabaseLoader does this in the Python reference).
  interp.relationships.set("r1", concept.relationships["r1"]!);
  interp.addEntity(makeEntity("e1", { grp: "A", size: 10 }));
  interp.addEntity(makeEntity("e2", { grp: "A", size: 20 }));
  interp.addEntity(makeEntity("e3", { grp: "B", size: 30 }));

  const rel = (quantifier: Quantifier, child?: Expression): Expression => ({
    id: "rexpr", kind: ExpressionKind.RelationshipRef, returnType: "boolean",
    conceptRelationshipId: "r1", quantifier,
    operands: child ? [child] : [],
  });

  const e1 = interp.getEntity("e1")!;
  const ctx = { entity: e1 } as Record<string, unknown>;
  assert.equal(interp.evaluate(rel(Quantifier.Exists), ctx), true);
  assert.equal(interp.evaluate(rel(Quantifier.Count), ctx), 2);
  // ALL peers have size > 5 (both A rows do)
  assert.equal(
    interp.evaluate(rel(Quantifier.All, opExpr("c", SolOperator.Gt, [attrExpr("a", "a1", "integer"), litExpr("l", 5, "integer")])), ctx),
    true,
  );
  // NOT ALL peers have size > 15 (10 fails)
  assert.equal(
    interp.evaluate(rel(Quantifier.All, opExpr("c", SolOperator.Gt, [attrExpr("a", "a1", "integer"), litExpr("l", 15, "integer")])), ctx),
    false,
  );
});

// ── Severity semantics ───────────────────────────────────────────────

test("soft rules pass on error, hard rules fail (severity semantics)", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  const ent = makeEntity("e1", { size: 1 });
  const broken: Expression = {
    id: "boom", kind: ExpressionKind.FunctionCall, returnType: "boolean",
    functionName: "no_such_function", operands: [],
  };
  const hard: Rule = { id: "rh", name: "hard", ruleType: RuleType.Invariant, expression: broken, severity: Severity.Hard, isRelationalCheck: false, conditions: [] };
  const soft: Rule = { id: "rs", name: "soft", ruleType: RuleType.Invariant, expression: broken, severity: Severity.Soft, isRelationalCheck: false, conditions: [] };
  assert.equal(interp.checkRule(hard, ent)[0], false);
  assert.equal(interp.checkRule(soft, ent)[0], true);
});

// ── Transition listener contract ─────────────────────────────────────

test("transition listener fires on committed only; event listener on every attempt", () => {
  const interp = new ResolutionInterpreter();
  const concept = makeConcept();
  interp.addConcept(concept);
  interp.stateTransitions.set("t1", {
    id: "t1", conceptId: "c1", fromValue: "off", toValue: "on", name: "switch", guards: [],
  });
  let committedCalls = 0;
  let eventCalls = 0;
  interp.registerTransitionListener(() => { committedCalls += 1; });
  interp.registerTransitionEventListener(() => { eventCalls += 1; });

  interp.addEntity(makeEntity("e-ok", { status: "off" }));
  interp.transitionEntity("e-ok", "t1");
  assert.equal(committedCalls, 1);
  assert.equal(eventCalls, 1);

  // Unknown entity → rejected; event listener fires, committed listener does not
  interp.transitionEntity("e-missing", "t1");
  assert.equal(committedCalls, 1);
  assert.equal(eventCalls, 2);

  // Unknown transition → rejected
  interp.transitionEntity("e-ok", "t-missing");
  assert.equal(committedCalls, 1);
  assert.equal(eventCalls, 3);
});

// ── Transaction rollback ─────────────────────────────────────────────

test("TransactionContext rollback restores entities + propositions", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  interp.addEntity(makeEntity("e1", { size: 1, status: "off" }));

  const tx = new TransactionContext(interp).begin();
  interp.getEntity("e1")!.attributes["size"] = 999;
  tx.addChange("entity_update", { entity: interp.getEntity("e1") });
  tx.rollback();
  assert.equal(interp.getEntity("e1")!.attributes["size"], 1);

  const tx2 = new TransactionContext(interp).begin();
  interp.getEntity("e1")!.attributes["size"] = 42;
  tx2.addChange("entity_update", { entity: interp.getEntity("e1") });
  tx2.commit();
  assert.equal(interp.getEntity("e1")!.attributes["size"], 42);
});

// ── Query paging ─────────────────────────────────────────────────────

test("query offset/limit/orderBy semantics match the reference", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  for (const [i, n] of [1, 2, 3, 4, 5].entries()) {
    interp.addEntity(makeEntity(`e${i}`, { size: n * 10, status: "off" }));
  }
  const q = new QueryBuilder(interp).select("Widget")
    .orderBy("size", "DESC").offsetN(1).limitN(2)
    .selectFieldsTo("id", "size");
  const rows = q.execute();
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((r) => r["size"]), [40, 30]);
});

// ── Digest / canonical JSON ──────────────────────────────────────────

test("canonical JSON sorts keys and stableDigest is sha-256 hex", () => {
  const a = canonicalJson({ b: 1, a: { z: [2, 1], y: null } });
  const b = canonicalJson({ a: { y: null, z: [2, 1] }, b: 1 });
  assert.equal(a, b); // key order + array order canonical
  assert.match(stableDigest({ x: 1 }), /^[0-9a-f]{64}$/);
});

test("ReadSetManifest digest excludes as_of (retry dedup identity)", () => {
  const m1 = buildReadSetManifest({
    sourceNamespace: "ns", evaluationId: "e1", evaluationKind: "proposition",
    targetId: "t1", asOf: "2026-01-01T00:00:00Z", evaluatorId: "ev",
  });
  const m2 = buildReadSetManifest({
    sourceNamespace: "ns", evaluationId: "e1", evaluationKind: "proposition",
    targetId: "t1", asOf: "2099-12-31T23:59:59Z", evaluatorId: "ev",
  });
  assert.equal(m1.manifestDigest, m2.manifestDigest); // as_of excluded
  assert.equal(m1.idempotencyKey, "ns:evaluation:proposition:e1");
  assert.notEqual(m1.asOf, m2.asOf); // ...but retained on the manifest
});

// ── Frame discipline edge cases ──────────────────────────────────────

test("framed proposition errors on unknown context dimension", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  interp.addEntity(makeEntity("e1", { size: 10, status: "off" }));
  interp.addFrameDimension({ id: "d1", name: "env", valueKind: "governed_reference" });
  interp.addProposition({
    id: "p1", title: "t", assetConceptId: "c1", subjectEntityId: "e1",
    disposition: Disposition.Pending, assertions: [], comparisons: [],
    frameValues: [{ id: "f1", propositionId: "p1", dimensionId: "d1", referenceValueId: "v1" }],
  });
  assert.throws(
    () => interp.evaluateProposition(interp.getProposition("p1")!, { bogus: "x" }),
    /no known frame_dimension/,
  );
});

test("typed_scalar frame dimension matches numerically and textually", () => {
  const interp = new ResolutionInterpreter();
  interp.addConcept(makeConcept());
  interp.addEntity(makeEntity("e1", { size: 10, status: "off" }));
  interp.addFrameDimension({ id: "d2", name: "threshold", valueKind: "typed_scalar", scalarType: "integer" });
  interp.addProposition({
    id: "p2", title: "t", assetConceptId: "c1", subjectEntityId: "e1",
    disposition: Disposition.Pending, assertions: [], comparisons: [],
    frameValues: [{ id: "f2", propositionId: "p2", dimensionId: "d2", scalarValue: "42" }],
  });
  const prop = interp.getProposition("p2")!;
  const [dOk, , sOk] = interp.evaluateProposition(prop, { threshold: 42 });
  assert.equal(sOk, "scoped");
  assert.equal(dOk, Disposition.Asserted);
  const [, , sBad] = interp.evaluateProposition(prop, { threshold: 43 });
  assert.equal(sBad, "context_mismatch");
});

console.log(`\n${passed} tests passed.`);
