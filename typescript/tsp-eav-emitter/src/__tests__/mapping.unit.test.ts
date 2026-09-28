/**
 * Unit tests for src/mapping.ts — the scalar -> field_type_code table
 * against the shrapnel.field registry seeded by 0001_init.sql.
 *
 * Hermetic by design: mapScalarToTypeCode only reads `.name` and the
 * optional `baseScalar` chain off the scalar object, so fake scalars
 * exercise the real switch + chase logic without a TypeSpec Program
 * (which the function never dereferences — pinned by these tests).
 */
import { describe, expect, it } from "vitest";
import { mapScalarToTypeCode, TYPE_CODE, CODE_JSONB } from "../mapping.js";
import type { Program, Scalar } from "@typespec/compiler";

function fakeScalar(name: string, base?: Scalar): Scalar {
  return { kind: "Scalar", name, baseScalar: base } as unknown as Scalar;
}

const program = {} as Program;

describe("TYPE_CODE registry table", () => {
  it("matches the 0001 registry codes", () => {
    expect(TYPE_CODE).toEqual({
      LONG: 1,
      STRING: 2,
      DOUBLE: 3,
      BOOLEAN: 4,
      TIMESTAMP: 5,
      JSONB: 6,
      UUID: 7,
    });
  });

  it("exports the JSONB catch-all equal to the registry code", () => {
    expect(CODE_JSONB).toBe(6);
  });
});

describe("mapScalarToTypeCode", () => {
  it("maps integer scalars to Long (1)", () => {
    for (const name of [
      "int8", "int16", "int32", "int64",
      "uint8", "uint16", "uint32", "uint64",
      "safeint", "integer", "numeric",
    ]) {
      expect(mapScalarToTypeCode(program, fakeScalar(name))).toBe(1);
    }
  });

  it("maps float scalars to Double (3)", () => {
    for (const name of ["float32", "float64", "float", "decimal", "decimal128"]) {
      expect(mapScalarToTypeCode(program, fakeScalar(name))).toBe(3);
    }
  });

  it("maps boolean to Boolean (4)", () => {
    expect(mapScalarToTypeCode(program, fakeScalar("boolean"))).toBe(4);
  });

  it("maps temporal scalars to Timestamp (5)", () => {
    for (const name of [
      "utcDateTime", "offsetDateTime", "duration", "plainDate", "plainTime",
    ]) {
      expect(mapScalarToTypeCode(program, fakeScalar(name))).toBe(5);
    }
  });

  it("maps string to String (2)", () => {
    expect(mapScalarToTypeCode(program, fakeScalar("string"))).toBe(2);
  });

  it("maps uuid to UUID (7)", () => {
    expect(mapScalarToTypeCode(program, fakeScalar("uuid"))).toBe(7);
  });

  it("maps url to String (2)", () => {
    expect(mapScalarToTypeCode(program, fakeScalar("url"))).toBe(2);
  });

  it("maps bytes to JSONB (6)", () => {
    expect(mapScalarToTypeCode(program, fakeScalar("bytes"))).toBe(6);
  });

  it("chases derived scalars to their base (Email extends string -> 2)", () => {
    const stringScalar = fakeScalar("string");
    const email = fakeScalar("Email", stringScalar);
    expect(mapScalarToTypeCode(program, email)).toBe(2);
  });

  it("chases multi-level derivations (TimestampedId -> string)", () => {
    const stringScalar = fakeScalar("string");
    const middle = fakeScalar("Identifier", stringScalar);
    const derived = fakeScalar("TimestampedId", middle);
    expect(mapScalarToTypeCode(program, derived)).toBe(2);
  });

  it("returns undefined for a scalar with no mappable base", () => {
    const mystery = fakeScalar("Mystery");
    expect(mapScalarToTypeCode(program, mystery)).toBeUndefined();
  });

  it("does not loop forever on a self-referential base chain", () => {
    // Cycle guard: `seen` must terminate even on a pathological chain.
    const a = fakeScalar("A") as Scalar & { baseScalar?: Scalar };
    const b = fakeScalar("B", a) as Scalar & { baseScalar?: Scalar };
    a.baseScalar = b;
    expect(mapScalarToTypeCode(program, a)).toBeUndefined();
  });

  it("does not dereference the program (fake-safe contract)", () => {
    // The function signature takes a Program it never uses; passing a
    // degenerate object pins that so tests stay hermetic.
    expect(mapScalarToTypeCode({} as Program, fakeScalar("int32"))).toBe(1);
  });
});
