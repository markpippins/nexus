/**
 * Canonical-shape loader for the tackle memory/role_memory reconstruction.
 *
 * The ONLY runtime consumer pattern for
 * sql/canonical/tackle_role_memory_shape.sql — both tackle-srv and
 * tackle-mcp render their DDL from this fragment instead of restating the
 * shape inline. The parity test
 * (bin/tests/test_canonical_shape_parity.py) proves every consumer surface
 * matches its rendering of the fragment, so shape drift is structurally
 * impossible: change the shape in the fragment, regenerate, and all
 * surfaces move together — CI fails on any unrendered token or stale
 * inline restatement.
 */
import { existsSync, readFileSync } from "fs";
import { dirname, join } from "path";

/**
 * Resolve sql/canonical/tackle_role_memory_shape.sql from this module's
 * location, walking up so both the source layout (typescript/tackle-seeds/)
 * and the compiled layout (typescript/tackle-seeds/dist/) work. CJS module:
 * __dirname is the directory of the compiled file at runtime.
 */
function resolveFragmentPath(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    const candidate = join(dir, "sql", "canonical", "tackle_role_memory_shape.sql");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    "canonical shape fragment not found: sql/canonical/tackle_role_memory_shape.sql",
  );
}

const FRAGMENT_PATH = resolveFragmentPath();

export function loadCanonicalRoleMemoryShape(
  schema: string,
  opts: { reftable?: string; tableSuffix?: string } = {},
): string {
  const reftable = opts.reftable ?? `${schema}.memory`;
  const tableSuffix = opts.tableSuffix ?? "";
  const raw = readFileSync(FRAGMENT_PATH, "utf8");
  return raw
    .replace(/__SCHEMA__/g, schema)
    .replace(/__REFTABLE__/g, reftable)
    .replace(/__TABLE_SUFFIX__/g, tableSuffix);
}

/** Path to the canonical fragment (for parity tests and regen tooling). */
export const CANONICAL_SHAPE_PATH = FRAGMENT_PATH;
