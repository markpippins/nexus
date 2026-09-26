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
  const name = scalar.name;
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
      // Derived/custom scalars: chase extendsTarget up the chain so e.g.
      // `scalar Email extends string` maps to String, not the fallback.
      return chaseBase(program, scalar);
  }
}

function chaseBase(program: Program, scalar: Scalar): number | undefined {
  let cur: Scalar | undefined = scalar;
  const seen = new Set<string>();
  while (cur && !seen.has(cur.name)) {
    seen.add(cur.name);
    const base: Type | undefined = (cur as unknown as { baseScalar?: Type }).baseScalar;
    if (base && base.kind === "Scalar") {
      const mapped = mapScalarToTypeCode(program, base);
      if (mapped !== undefined) return mapped;
      cur = base;
    } else {
      return undefined;
    }
  }
  return undefined;
}

/** Records / arrays / unknown shapes -> JSONB (the EAV catch-all). */
export const CODE_JSONB = TYPE_CODE.JSONB;
