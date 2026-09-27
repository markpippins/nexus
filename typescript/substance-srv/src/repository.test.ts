/**
 * Repository tests — port of the TestRepositoryHelpers class in
 * python/substance/tests/test_substance.py, plus coverage for the SET-clause
 * builder that was factored out of updateSegmentSet so it is testable without a
 * database.
 *
 * Pure: no Postgres connection is opened. getPool() throws if initPool() was
 * never called, which is exactly the guarantee these tests rely on.
 */
import { describe, expect, it } from "vitest";

import {
  buildUpdateSetClauses,
  DOMAIN_TABLES,
  domainTable,
  FOREVER,
  isKnownDomainType,
  SEG_HISTORY_FOREVER,
  UnknownDomainTypeError,
} from "./repository";

describe("domainTable", () => {
  it("maps 'candidates' to its join table and FK column", () => {
    expect(domainTable("candidates")).toEqual({
      table: "nebula.candidate_segment_sets",
      fkColumn: "candidate_id",
    });
  });

  it("maps 'requirements' to its join table and FK column", () => {
    expect(domainTable("requirements")).toEqual({
      table: "nebula.requirement_segment_sets",
      fkColumn: "requirement_id",
    });
  });

  it("rejects 'intent-records' — the concept was eliminated", () => {
    // nebula.intent_records no longer exists; the join table is dropped by
    // 002_drop_intent_record_segment_sets.sql. A stale caller must fail loudly
    // rather than query a table that is gone.
    expect(() => domainTable("intent-records")).toThrow(UnknownDomainTypeError);
    expect(() => domainTable("intent-records")).toThrow(/unknown domain_type/);
  });

  it("rejects an unknown domain type", () => {
    expect(() => domainTable("unknown")).toThrow(/unknown domain_type/);
  });

  it("rejects the empty string", () => {
    expect(() => domainTable("")).toThrow(/unknown domain_type/);
  });

  it("rejects null", () => {
    expect(() => domainTable(null as unknown as string)).toThrow(
      /unknown domain_type/,
    );
  });

  it("exposes only the two surviving domain types", () => {
    expect(Object.keys(DOMAIN_TABLES).sort()).toEqual([
      "candidates",
      "requirements",
    ]);
  });
});

describe("isKnownDomainType", () => {
  it("narrows on the supported types", () => {
    expect(isKnownDomainType("candidates")).toBe(true);
    expect(isKnownDomainType("requirements")).toBe(true);
    expect(isKnownDomainType("intent-records")).toBe(false);
    expect(isKnownDomainType("")).toBe(false);
  });
});

describe("validity sentinels", () => {
  it("keeps the FOREVER literal byte-identical to the Python constant", () => {
    // Postgres matches the ON CONFLICT predicate against the partial unique
    // index's WHERE clause, so a drifted sentinel is a hard query error rather
    // than silent data corruption — but it still has to be exact.
    expect(FOREVER).toBe("9999-12-31 00:00:00+00");
  });

  it("keeps the segments_history sentinel distinct from FOREVER", () => {
    expect(SEG_HISTORY_FOREVER).toBe("9999-12-31 23:59:59+00");
    expect(SEG_HISTORY_FOREVER).not.toBe(FOREVER);
  });
});

describe("buildUpdateSetClauses", () => {
  it("numbers placeholders from $1 and appends updated_at", () => {
    const { setClauses, values } = buildUpdateSetClauses({ name: "new-name" });
    expect(setClauses).toEqual(["name = $1", "updated_at = now()"]);
    expect(values).toEqual(["new-name"]);
  });

  it("preserves the order the fields were supplied in", () => {
    const { setClauses, values } = buildUpdateSetClauses({
      description: "d",
      name: "n",
    });
    expect(setClauses).toEqual(["description = $1", "name = $2", "updated_at = now()"]);
    expect(values).toEqual(["d", "n"]);
  });

  it("casts metadata to jsonb and serialises it", () => {
    const { setClauses, values } = buildUpdateSetClauses({
      metadata: { key: "val" },
    });
    expect(setClauses).toEqual(["metadata = $1::jsonb", "updated_at = now()"]);
    expect(values).toEqual(['{"key":"val"}']);
  });

  it("passes an explicit null through, so PATCH can clear a column", () => {
    const { setClauses, values } = buildUpdateSetClauses({ description: null });
    expect(setClauses).toEqual(["description = $1", "updated_at = now()"]);
    expect(values).toEqual([null]);
  });

  it("passes an explicit null metadata through without casting to a string", () => {
    const { setClauses, values } = buildUpdateSetClauses({ metadata: null });
    expect(setClauses).toEqual(["metadata = $1::jsonb", "updated_at = now()"]);
    expect(values).toEqual([null]);
  });

  it("updates all four columns when every field is present", () => {
    const { setClauses, values } = buildUpdateSetClauses({
      name: "n",
      description: "d",
      status: "archived",
      metadata: { a: 1 },
    });
    expect(setClauses).toEqual([
      "name = $1",
      "description = $2",
      "status = $3",
      "metadata = $4::jsonb",
      "updated_at = now()",
    ]);
    expect(values).toEqual(["n", "d", "archived", '{"a":1}']);
  });

  it("still bumps updated_at for an empty field set", () => {
    // The repository short-circuits this case to a plain SELECT, but the
    // builder must not produce an empty SET clause if it ever gets here.
    const { setClauses } = buildUpdateSetClauses({});
    expect(setClauses).toEqual(["updated_at = now()"]);
  });

  it("refuses a column outside the allowlist", () => {
    // Column identifiers are interpolated into SQL. The Python version was only
    // safe because pydantic constrained the field set; here the constraint is
    // explicit, so an injected key throws instead of reaching the statement.
    expect(() =>
      buildUpdateSetClauses({ id: "x" } as never),
    ).toThrow(/not updatable/);
    expect(() =>
      buildUpdateSetClauses({ "name = 'pwned', id": "x" } as never),
    ).toThrow(/not updatable/);
    expect(() =>
      buildUpdateSetClauses({ valid_until: "x" } as never),
    ).toThrow(/not updatable/);
  });
});
