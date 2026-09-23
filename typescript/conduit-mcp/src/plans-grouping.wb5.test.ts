/**
 * W-B5 archived-bucket guard (architect ruling, 2026-09-22).
 *
 * The ruling: conduit /state MUST surface archived plans in their own
 * bucket instead of omitting them (5 legacy plans were invisible). This is
 * a source-level guard in the house style (cf. python/conduit
 * test_c3_single_fanout.py): if the archived surfacing is removed or the
 * archive marker changes, this fails and the author must consciously
 * update it against the ruling.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync("src/db.ts", "utf8");

describe("getPlansGroupedByStatus surfaces archived plans (W-B5)", () => {
  it("joins soft-deleted plans to the canonical archive marker", () => {
    expect(src).toContain("i.status = 'archived'");
    expect(src).toMatch(/p\.deleted <> 0/);
  });

  it("maps archived rows into the archived bucket with an ARCHIVED override", () => {
    expect(src).toMatch(/result\.archived\.push\(\{ \.\.\.plan, derived_status: "ARCHIVED" \}\)/);
  });

  it("guards against double-insertion across queries", () => {
    expect(src).toContain("result.archived.some((a) => a.id === plan.id)");
  });
});
