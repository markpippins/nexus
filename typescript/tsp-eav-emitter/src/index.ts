/**
 * tsp-eav-emitter — TypeSpec -> shrapnel EAV catalog compiler.
 *
 * Approach (operator-approved DeepSeek chat "TypeSpec EAV emitter start"):
 * this is NOT a code emitter. It is a compiler backend whose target is a
 * relational catalog: a navigateProgram walk of the TypeSpec program
 * produces an ordered SQL migration that populates the shrapnel EAV
 * stereotype tables through the database's own construction API
 * (shrapnel.stereotype_create_revision, 0005_stereotype_api.sql:437).
 *
 * Invariant ownership (deliberate):
 *   - contract fingerprint v2  -> computed SERVER-side by the function
 *     (reimplementing PG jsonb canonicalization client-side is a rabbit
 *     hole and the canonical source of truth is the DB itself);
 *   - superset v2 / depth<=3 / acyclicity / freeze -> enforced by the
 *     deferred triggers at COMMIT (0004_stereotype_model.sql §5);
 *   - the emitter adds only what the DB cannot know: TypeSpec mapping
 *     policy (model -> stereotype, property -> field, scalar ->
 *     field_type_code) and C2-compliant extends rationales.
 *
 * Mapping decisions:
 *   - model                     -> stereotype + stereotype_revision v1
 *   - model property            -> field (get-or-create by property_name;
 *                                  field_index = 1-based declaration order,
 *                                  mirroring the 0004 backfill precedent)
 *   - TypeSpec scalar           -> field_type_code (1..7 registry, 0001)
 *   - model extends             -> parent = stereotype_resolve(parent).head,
 *                                  rationale REQUIRED: taken from @doc on the
 *                                  extending model. The DB CHECK
 *                                  ck_sterev_parent_rationale rejects a
 *                                  parent with an empty rationale — so the
 *                                  emitter refuses at compile time instead,
 *                                  honoring C2 (extends is opt-in AND
 *                                  justified) rather than writing a lie.
 *   - prop.optional             -> stereotype_field.required = false
 *
 * Output: one .sql file, runner-safe (no psql meta-commands), wrapped in
 * BEGIN/COMMIT so the deferred fingerprint/superset triggers verify the
 * whole migration atomically.
 */
import type { EmitContext } from "@typespec/compiler";
import { walkTsp } from "./walker.js";
import { renderSql } from "./emit-sql.js";
import type { Catalog } from "./catalog.js";

export interface TspEavEmitterOptions {
  /** Output SQL file name (relative to the emit output dir). Default: shrapnel-catalog.sql */
  "output-file"?: string;
  /** Namespace to walk. Default: walk every namespace. */
  namespace?: string;
}

export async function $onEmit(context: EmitContext<TspEavEmitterOptions>): Promise<void> {
  const catalog: Catalog = {
    stereotypes: new Map(),
    fields: new Map(),
    revisions: [],
    diagnostics: [],
  };

  walkTsp(context.program, catalog, context.options["namespace"]);

  // Surface walker diagnostics as compiler diagnostics (they fail the
  // compilation with precise locations — the DB would reject them later
  // with worse messages, so fail early where possible).
  for (const d of catalog.diagnostics) {
    context.program.reportDiagnostic({
      code: d.code,
      message: d.message,
      severity: "error",
      target: d.target,
    });
  }
  if (catalog.diagnostics.length > 0) return;

  const sql = renderSql(catalog);
  const outFile = context.options["output-file"] ?? "shrapnel-catalog.sql";
  await context.program.host.writeFile(outFile, sql);
}
