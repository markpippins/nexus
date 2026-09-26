/**
 * The heart of the compiler backend: a navigateProgram walk that turns
 * TypeSpec models into catalog records.
 *
 * What is checked HERE (compile time, precise locations) vs left to the DB
 * (the oracle, at COMMIT):
 *   here:  anonymous models, cross-namespace duplicate property_name with a
 *          DIFFERENT mapped type (uq_field_property_name would collide), and
 *          extends-without-rationale (ck_sterev_parent_rationale would
 *          reject it — C2 doctrine says justify extends, so we demand the
 *          @doc up front rather than writing a lie).
 *   DB:    fingerprint v2, superset v2, depth<=3, acyclicity, freeze — all
 *          enforced by 0004/0005 triggers at COMMIT of the emitted SQL.
 */
import type { Model, ModelProperty, Program } from "@typespec/compiler";
import { navigateProgram, getDoc, getTypeName } from "@typespec/compiler";
import type { Catalog, RevisionRow } from "./catalog.js";
import { DIAG } from "./catalog.js";
import { mapScalarToTypeCode, CODE_JSONB } from "./mapping.js";

export function walkTsp(program: Program, catalog: Catalog, namespaceFilter?: string): void {
  const models: Model[] = [];

  navigateProgram(program, {
    model(model: Model) {
      // Instantiation templates (Array<T>, Record<...>) are shapes, not
      // authored types — skip them; they surface as property types instead.
      if (!model.name) return;
      // The std library's own namespaces (TypeSpec.*, e.g. decorator
      // metadata models) are compiler plumbing, not catalog content.
      if (inStdNamespace(model)) return;
      if (namespaceFilter && !inNamespace(model, namespaceFilter)) return;
      models.push(model);
    },
  });

  // Topologically order by inheritance so parent revisions are emitted
  // before children (the DB's parent-head lookup needs the parent row).
  const ordered = topoSort(models);

  for (const model of ordered) {
    emitModel(program, model, catalog);
  }
}

function inNamespace(model: Model, filter: string): boolean {
  let ns = model.namespace;
  while (ns) {
    if (ns.name === filter) return true;
    ns = ns.namespace;
  }
  return false;
}

function inStdNamespace(model: Model): boolean {
  let ns = model.namespace;
  while (ns) {
    if (ns.name === "TypeSpec") return true;
    ns = ns.namespace;
  }
  return false;
}

function emitModel(program: Program, model: Model, catalog: Catalog): void {
  if (!model.name) {
    catalog.diagnostics.push({
      code: DIAG.ANON_MODEL,
      message: "Anonymous models cannot become stereotypes.",
      target: model,
    });
    return;
  }

  const description = getDoc(program, model);
  const existing = catalog.stereotypes.get(model.name);
  if (existing && !existing.description && description) {
    existing.description = description;
  } else if (!existing) {
    catalog.stereotypes.set(model.name, { name: model.name, description });
  }

  // ── Parent resolution ────────────────────────────────────────────────
  const parentModel = model.baseModel;
  let parentStereotypeName: string | undefined;
  let rationale: string | undefined;

  if (parentModel && parentModel.name) {
    parentStereotypeName = parentModel.name;
    rationale = description?.trim() || undefined;
    if (!rationale) {
      catalog.diagnostics.push({
        code: DIAG.EXTENDS_MISSING_DOC,
        message:
          `Model '${model.name}' extends '${parentModel.name}' but carries no @doc. ` +
          `shrapnel requires an extends rationale (ck_sterev_parent_rationale) — ` +
          `add @doc("…") explaining WHY this subtype exists (C2: extends is opt-in and justified).`,
        target: model,
      });
      return;
    }
  }

  // ── Fields: the model's own declared properties ─────────────────────
  const fields: { property_name: string; required: boolean }[] = [];

  for (const [propName, prop] of model.properties) {
    const code = resolvePropertyTypeCode(program, prop);
    if (code === undefined) {
      catalog.diagnostics.push({
        code: DIAG.UNMAPPABLE_TYPE,
        message: `Property '${model.name}.${propName}' has an unmappable type (${getTypeName(prop.type)}); cannot pick a field_type_code.`,
        target: prop,
      });
      continue;
    }

    const existingField = catalog.fields.get(propName);
    if (existingField && existingField.field_type_code !== code) {
      catalog.diagnostics.push({
        code: DIAG.DUP_FIELD_TYPE_CONFLICT,
        message: `Property name '${propName}' is reused with a different type across models (${existingField.field_type_code} vs ${code}) — shrapnel.field is unique by property_name (uq_field_property_name), so these cannot coexist.`,
        target: prop,
      });
      continue;
    }
    if (!existingField) {
      catalog.fields.set(propName, {
        property_name: propName,
        label: getDoc(program, prop) ?? propName,
        name: propName,
        field_type_code: code,
        is_calculated: false,
        // 1-based declaration order within the model (the 0004 backfill
        // precedent: field_index is NOT NULL with no other convention).
        field_index: fields.length + 1,
      });
    }

    fields.push({ property_name: propName, required: !prop.optional });
  }

  // ── Materialize inherited REQUIRED fields ──────────────────────────
  // The DB's superset-v2 check works on stereotype_field ROWS: a child
  // revision must DECLARE every field its parent requires. TypeSpec
  // `extends` keeps base properties implicit — so the emitter
  // materializes them, transitively (the parent's rows already carry ITS
  // materialized set). Optional parent fields stay implicit: they do not
  // participate in the superset check. If the child redeclares a
  // parent-required field as optional, the child's weakening flows
  // through untouched and the DB rejects it at COMMIT (required→optional
  // downgrade) — the oracle stays the oracle.
  if (parentStereotypeName) {
    const parentRev = [...catalog.revisions]
      .reverse()
      .find((r) => r.stereotypeName === parentStereotypeName);
    if (!parentRev) {
      catalog.diagnostics.push({
        code: DIAG.EXTENDS_UNKNOWN_PARENT,
        message:
          `Model '${model.name}' extends '${parentStereotypeName}' but no revision for it was emitted ` +
          `in this walk (is the parent outside the walked namespaces?).`,
        target: model,
      });
      return;
    }
    for (const pf of parentRev.fields) {
      if (!pf.required) continue;
      if (fields.some((f) => f.property_name === pf.property_name)) continue;
      fields.push({ property_name: pf.property_name, required: true });
    }
  }

  const revision: RevisionRow = {
    stereotypeName: model.name,
    parentStereotypeName,
    rationale,
    fields,
  };
  catalog.revisions.push(revision);
}

function resolvePropertyTypeCode(
  program: Program,
  prop: ModelProperty
): number | undefined {
  const t = prop.type;
  if (t.kind === "Scalar") return mapScalarToTypeCode(program, t);
  if (t.kind === "Intrinsic" && (t as { name: string }).name === "unknown") {
    // `unknown` is the anything-shape: the JSONB catch-all is exactly right.
    return CODE_JSONB;
  }
  if (t.kind === "Model") {
    // Array<T> / Record<T> / authored models-as-structs -> JSONB catch-all.
    return CODE_JSONB;
  }
  if (t.kind === "Enum" || t.kind === "Union") {
    // Enums/unions of strings stay strings for now (future: union-of-literals
    // -> a text[] value extension).
    return 2;
  }
  if (t.kind === "String" || t.kind === "Number" || t.kind === "Boolean") {
    // Defaulted literals keep their primitive type code.
    return t.kind === "String" ? 2 : t.kind === "Number" ? 3 : 4;
  }
  return undefined;
}

function topoSort(models: Model[]): Model[] {
  const byName = new Map(models.map((m) => [m.name as string, m]));
  const out: Model[] = [];
  const visiting = new Set<string>();
  const visited = new Set<string>();

  const visit = (m: Model) => {
    const key = m.name as string;
    if (visited.has(key)) return;
    if (visiting.has(key)) {
      // Circular inheritance: the DB's acyclicity trigger would reject the
      // whole migration; refusing here keeps the error message readable.
      throw new Error(`Circular model inheritance involving '${key}'`);
    }
    visiting.add(key);
    const base = m.baseModel;
    if (base?.name && byName.has(base.name)) visit(base);
    visiting.delete(key);
    visited.add(key);
    out.push(m);
  };

  for (const m of models) visit(m);
  return out;
}
