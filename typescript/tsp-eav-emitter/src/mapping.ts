/**
 * Scalar -> field_type_code mapping against the shrapnel.field_type
 * registry seeded by 0001_init.sql (code 166):
 *   1=Long, 2=String, 3=Double, 4=Boolean, 5=Timestamp, 6=JSONB, 7=UUID
 */
import type { Scalar, Program, Type } from "@typespec/compiler";

export const TYPE_CODE = {
  LONG: 1,
  STRING: 2,
  DOUBLE: 3,
  BOOLEAN: 4,
  TIMESTAMP: 5,
  JSONB: 6,
  UUID: 7,
} as const;

export function mapScalarToTypeCode(program: Program, scalar: Scalar): number | undefined {
  const direct = directCode(scalar.name);
  if (direct !== undefined) return direct;
  // Derived/custom scalars: chase extendsTarget up the chain so e.g.
  // `scalar Email extends string` maps to String, not the fallback.
  return chaseBase(program, scalar);
}

/** The 0001 registry lookup for a BUILT-IN scalar name (undefined if custom). */
function directCode(name: string): number | undefined {
  switch (name) {
    case "int8":
    case "int16":
    case "int32":
    case "int64":
    case "uint8":
    case "uint16":
    case "uint32":
    case "uint64":
    case "safeint":
    case "integer":
    case "numeric":
      return TYPE_CODE.LONG;
    case "float32":
    case "float64":
    case "float":
    case "decimal":
    case "decimal128":
      return TYPE_CODE.DOUBLE;
    case "boolean":
      return TYPE_CODE.BOOLEAN;
    case "utcDateTime":
    case "offsetDateTime":
    case "duration":
    case "plainDate":
    case "plainTime":
      return TYPE_CODE.TIMESTAMP;
    case "string":
      return TYPE_CODE.STRING;
    case "uuid":
      return TYPE_CODE.UUID;
    case "url":
      return TYPE_CODE.STRING;
    case "bytes":
      return TYPE_CODE.JSONB;
    default:
      return undefined;
  }
}

/**
 * Walks the baseScalar chain ITERATIVELY. The cycle guard only works
 * because there is no recursion here: a per-frame `seen` inside mutual
 * mapScalarToTypeCode/chaseBase recursion re-created the set at every
 * level, so a `scalar A extends B; scalar B extends A;` cycle blew the
 * stack instead of returning undefined (found by the mapping unit test;
 * the old comment claimed a guard the code did not perform — the 054
 * comment/DDL defect class, caught by its own test this time).
 */
function chaseBase(_program: Program, scalar: Scalar): number | undefined {
  const seen = new Set<string>([scalar.name]);
  let cur = (scalar as unknown as { baseScalar?: Type }).baseScalar;
  while (cur && cur.kind === "Scalar" && !seen.has(cur.name)) {
    seen.add(cur.name);
    const direct = directCode(cur.name);
    if (direct !== undefined) return direct;
    cur = (cur as unknown as { baseScalar?: Type }).baseScalar;
  }
  return undefined;
}

/** Records / arrays / unknown shapes -> JSONB (the EAV catch-all). */
export const CODE_JSONB = TYPE_CODE.JSONB;
