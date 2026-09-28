/**
 * Library entry points for decorator resolution.
 *
 * `tsp` reads `$lib` (library definition) and `$decorators` (decorator
 * implementations, shaped { Namespace: { name: fn } } with UNPREFIXED
 * names — the `$` on the JS functions is stripped by the binder) from the
 * package entrypoint resolved via the "typespec" export condition.
 */
import type { EmitContext } from "@typespec/compiler";
import { walkTsp } from "./walker.js";
import { renderSql } from "./emit-sql.js";
import type { Catalog } from "./catalog.js";
import { lib, ShrapnelCatalog } from "./decorators.js";

// Decorator implementations for consuming .tsp files.
export const $decorators = {
  ShrapnelCatalog: {
    instanceStorage: ShrapnelCatalog.$instanceStorage,
    extendsRationale: ShrapnelCatalog.$extendsRationale,
    calculated: ShrapnelCatalog.$calculated,
  },
};

// Library definition (diagnostics, name).
export const $lib = lib;

export interface TspEavEmitterOptions {
  /** Output SQL file name (relative to the emit output dir). Default: shrapnel-catalog.sql */
  "output-file"?: string;
  /** Namespace to walk. Default: walk every namespace. */
  namespace?: string;
  /**
   * Emit `stereotype_reconcile` instead of `stereotype_create_revision`, so
   * re-applying an unchanged compiled catalog mints no revisions (migration
   * 0008). Opt-in; see RenderOptions.reconcile for why the default is false.
   */
  reconcile?: boolean;
}

export async function $onEmit(context: EmitContext<TspEavEmitterOptions>): Promise<void> {
  const catalog: Catalog = {
    stereotypes: new Map(),
    fields: new Map(),
    revisions: [],
    storageRegistrations: [],
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

  const sql = renderSql(catalog, { reconcile: context.options["reconcile"] === true });
  const outFile = context.options["output-file"] ?? "shrapnel-catalog.sql";
  await context.program.host.writeFile(outFile, sql);
}
