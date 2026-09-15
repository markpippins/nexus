/**
 * run-solscript-ts-parity.ts — TS-side runner + diff driver for the P3
 * parity proof.
 *
 * Executes the shared scenario fixture on the TypeScript port, then (as
 * the driver) runs the Python reference runner and canonical-JSON-diffs
 * the two outputs. Exit 0 = behavioral parity proven.
 *
 * Usage:
 *   npx tsx test/run-solscript-ts-parity.ts            # TS run + full diff
 *   npx tsx test/run-solscript-ts-parity.ts --emit-only # just emit TS side
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ResolutionInterpreter } from "../src/interpreter.js";
import { stableDigest } from "../src/events.js";
import { ExpressionKind, SolOperator, Disposition } from "../src/models.js";
import { QueryBuilder } from "../src/query-builder.js";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = resolve(here, "..");            // typescript/solscript
const wtRoot = resolve(pkgRoot, "..", "..");    // worktree root
const scenarioPath = join(wtRoot, "typescript/solscript/test/parity/scenario.json");
const outDir = join(pkgRoot, "test", "parity");
const tsOut = join(outDir, "ts-results.json");
const pyOut = join(outDir, "py-results.json");
const pyRunner = join(wtRoot, "python", "solscript_parity_runner.py");

interface ScenarioExpression {
  id: string;
  kind: string;
  return_type?: string;
  operator?: string;
  literal_value?: unknown;
  attribute_id?: string;
  operands: ScenarioExpression[];
}

interface Scenario {
  concepts: Array<Record<string, any>>;
  state_transitions: Array<Record<string, any>>;
  entities: Array<Record<string, any>>;
  propositions: Array<Record<string, any>>;
  frame_dimensions: Array<Record<string, any>>;
  frame_dimension_values: Array<Record<string, any>>;
  function_calls: Array<Record<string, unknown>>;
  digest_cases: Array<Record<string, unknown>>;
  queries: Array<Record<string, any>>;
  transition_checks: Array<Record<string, any>>;
  evaluation_checks: Array<Record<string, any>>;
}

function buildExpression(d: ScenarioExpression): any {
  return {
    id: d.id,
    kind: d.kind as ExpressionKind,
    returnType: d.return_type ?? "",
    ...(d.operator ? { operator: d.operator as SolOperator } : {}),
    ...(d.literal_value !== undefined ? { literalValue: d.literal_value as never } : {}),
    ...(d.attribute_id ? { attributeId: d.attribute_id } : {}),
    operands: (d.operands ?? []).map(buildExpression),
  };
}

function buildRule(d: Record<string, any>): any {
  return {
    id: d.id,
    name: d.name,
    ruleType: d.rule_type,
    ...(d.expression ? { expression: buildExpression(d.expression) } : {}),
    severity: d.severity,
    isRelationalCheck: d.is_relational_check ?? false,
    conditions: [],
  };
}

function runScenario(sc: Scenario): Record<string, unknown> {
  const interp = new ResolutionInterpreter();

  for (const c of sc.concepts) {
    const attributes: Record<string, any> = {};
    for (const a of c.attributes) {
      attributes[a.id] = {
        id: a.id, conceptId: a.concept_id, name: a.name,
        valueType: a.value_type, isStateAttribute: a.is_state_attribute,
        allowedValues: a.allowed_values ?? [],
      };
    }
    interp.addConcept({
      id: c.id, name: c.name, description: c.description,
      attributes, relationships: {}, invariants: [], derivations: [],
      stateTransitions: [], rules: [],
    });
  }
  for (const t of sc.state_transitions) {
    interp.stateTransitions.set(t.id, {
      id: t.id, conceptId: t.concept_id, fromValue: t.from_value,
      toValue: t.to_value, name: t.name,
      guards: (t.guards ?? []).map(buildRule),
    });
  }
  for (const e of sc.entities) {
    interp.addEntity({
      id: e.id, conceptId: e.concept_id, attributes: { ...e.attributes },
      ...(e.external_id !== undefined ? { externalId: e.external_id } : {}),
    });
  }
  for (const p of sc.propositions) {
    interp.addProposition({
      id: p.id, title: p.title, assetConceptId: p.asset_concept_id,
      subjectEntityId: p.subject_entity_id,
      disposition: p.disposition as Disposition,
      assertions: (p.assertions ?? []).map(buildRule),
      comparisons: [], frameValues: [],
    });
    for (const pfv of p.frame_values ?? []) {
      interp.addPropositionFrameValue({
        id: pfv.id, propositionId: pfv.proposition_id, dimensionId: pfv.dimension_id,
        ...(pfv.reference_value_id ? { referenceValueId: pfv.reference_value_id } : {}),
        ...(pfv.scalar_value !== undefined ? { scalarValue: pfv.scalar_value } : {}),
      });
    }
  }
  for (const d of sc.frame_dimensions) {
    interp.addFrameDimension({ id: d.id, name: d.name, valueKind: d.value_kind });
  }
  for (const v of sc.frame_dimension_values) {
    interp.addFrameDimensionValue({ id: v.id, dimensionId: v.dimension_id, value: v.value });
  }

  const out: Record<string, unknown[]> = {
    function_calls: [], digests: [], transitions: [], evaluations: [], queries: [],
  };

  for (const fc of sc.function_calls) {
    const fn = interp.getFunction(String(fc.function))?.fn;
    out.function_calls!.push({ id: fc.id, result: fn ? fn(...(fc.args as unknown[])) : null });
  }

  for (const dg of sc.digest_cases) {
    out.digests!.push({ id: dg.id, digest: stableDigest(dg.value) });
  }

  for (const tc of sc.transition_checks) {
    const res = interp.transitionEntity(tc.entity_id, tc.transition_id, {
      sourceEventId: `evt-${tc.id}`, actor: "parity",
    });
    const entity = interp.getEntity(tc.entity_id)!;
    const concept = interp.getConcept(entity.conceptId)!;
    const stateAttr = Object.values(concept.attributes).find((a) => a.isStateAttribute);
    out.transitions!.push({
      id: tc.id, committed: res.committed,
      state_after: stateAttr ? entity.attributes[stateAttr.name] ?? null : null,
      results: res.results,
      event_kind: interp.lastTransitionEvent?.kind ?? null,
    });
  }

  for (const ec of sc.evaluation_checks) {
    const prop = interp.getProposition(ec.proposition_id)!;
    try {
      const [d, , status] = interp.evaluateProposition(
        prop, ec.context === null ? undefined : ec.context);
      out.evaluations!.push({
        id: ec.id, disposition: d ?? null, context_status: status,
      });
    } catch (exc) {
      out.evaluations!.push({ id: ec.id, error: exc instanceof Error ? exc.message : String(exc) });
    }
  }

  for (const q of sc.queries) {
    const [, op, value] = q.where;
    let query = new QueryBuilder(interp).select(q.concept)
      .where(q.where[0], op as SolOperator, value as never);
    if (q.order_by) query = query.orderBy(q.order_by[0], q.order_by[1]);
    query = query.selectFieldsTo(...q.select_fields);
    out.queries!.push({ id: q.id, rows: query.execute() });
  }

  return out;
}

function emitOnly(): number {
  const sc: Scenario = JSON.parse(readFileSync(scenarioPath, "utf8"));
  const result = runScenario(sc);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(tsOut, JSON.stringify(result, sortKeysReplacer, 1));
  console.log(`wrote ${tsOut}`);
  return 0;
}

function sortKeysReplacer(_key: string, value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)),
    );
  }
  return value;
}

function main(): number {
  const emitOnlyFlag = process.argv.includes("--emit-only");
  if (emitOnlyFlag) return emitOnly();

  // 1. TS side
  const tsCode = emitOnly();
  if (tsCode !== 0) return tsCode;

  // 2. Python side
  if (!existsSync(pyRunner)) {
    console.error(`missing python runner: ${pyRunner}`);
    return 1;
  }
  const r = spawnSync("python3", [pyRunner, "--scenario", scenarioPath, "--out", pyOut], {
    stdio: "inherit",
  });
  if (r.status !== 0) {
    console.error("python parity runner failed");
    return 1;
  }

  // 3. Diff (canonical JSON: sorted keys already applied on both sides)
  const ts = JSON.parse(readFileSync(tsOut, "utf8"));
  const py = JSON.parse(readFileSync(pyOut, "utf8"));
  const tsCanon = JSON.stringify(sortKeysReplacer("", ts));
  const pyCanon = JSON.stringify(sortKeysReplacer("", py));

  if (tsCanon === pyCanon) {
    console.log("PARITY PROVEN — TS port and Python reference produce identical results.");
    return 0;
  }

  console.error("PARITY FAILED — outputs differ.");
  // Per-section diff for debugging
  for (const section of Object.keys(ts as Record<string, unknown>)) {
    const a = JSON.stringify((ts as any)[section], sortKeysReplacer);
    const b = JSON.stringify((py as any)[section], sortKeysReplacer);
    if (a !== b) {
      console.error(`--- section: ${section}`);
      console.error(`TS: ${a?.slice(0, 800)}`);
      console.error(`PY: ${b?.slice(0, 800)}`);
    }
  }
  return 1;
}

process.exit(main());
