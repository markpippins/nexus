import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import * as path from "path";

import {
  BLANK_RECORD_ERROR,
  blankRecordError,
  isBlank,
} from "../agent-record-write-guard";

// Contract under test: POST /api/agent-records must refuse a record that has
// neither a title nor a body. Two role=tester rows (65d01e3f, b1e328c4) were
// accepted on 2026-11-04 with an empty title, `{}` tags and a zero-length body
// — unreadable, unroutable, unattributable — because the route validated
// recordType/level/tags but never title/content.
//
// The rule is narrow on purpose. 47 records in the corpus legitimately have an
// empty body and 5 have a title-only shape; those must keep writing.

describe("isBlank", () => {
  it("treats absent values as blank", () => {
    expect(isBlank(undefined)).toBe(true);
    expect(isBlank(null)).toBe(true);
  });

  it("treats empty and whitespace-only strings as blank", () => {
    expect(isBlank("")).toBe(true);
    expect(isBlank("   ")).toBe(true);
    expect(isBlank("\n\t \r\n")).toBe(true);
  });

  it("treats any real text as present", () => {
    expect(isBlank("a")).toBe(false);
    expect(isBlank(" x ")).toBe(false);
  });

  it("does not audit non-string shapes it was not given", () => {
    // Type coercion is the route's/DB's job; rejecting here would make this
    // guard a validator of things it does not administer.
    expect(isBlank(0)).toBe(false);
    expect(isBlank({})).toBe(false);
  });
});

describe("blankRecordError", () => {
  it("refuses a record with neither title nor content", () => {
    expect(blankRecordError(undefined, undefined)).toBe(BLANK_RECORD_ERROR);
    expect(blankRecordError(null, null)).toBe(BLANK_RECORD_ERROR);
    expect(blankRecordError("", "")).toBe(BLANK_RECORD_ERROR);
    expect(blankRecordError("  ", "\n\t")).toBe(BLANK_RECORD_ERROR);
  });

  it("permits a title-only record", () => {
    // Status projections and metadata-only rows live here (5 in the corpus).
    expect(blankRecordError("Stale role lease swept: engineer", "")).toBeNull();
    expect(blankRecordError("Stale role lease swept: engineer", undefined)).toBeNull();
  });

  it("permits a body-only record", () => {
    expect(blankRecordError("", "a body with no title")).toBeNull();
    expect(blankRecordError(undefined, "a body with no title")).toBeNull();
  });

  it("permits a normal record", () => {
    expect(blankRecordError("R2: something", "## What\n\nsomething")).toBeNull();
  });

  it("permits a title-only record whose body is whitespace", () => {
    // 47 records carry a whitespace/empty body; that shape stays writable.
    expect(blankRecordError("title", "   \n  ")).toBeNull();
  });

  it("names both fields in the refusal so the writer can fix it", () => {
    const msg = blankRecordError("", "") as string;
    expect(msg).toContain("title");
    expect(msg).toContain("content");
    expect(msg).toMatch(/refusing the write/);
  });
});

describe("wiring", () => {
  it("the POST /agent-records handler actually calls the guard", () => {
    // A guard that is defined but never invoked protects nothing, which is the
    // failure mode this file exists to close.
    const routes = readFileSync(path.join(__dirname, "..", "routes.ts"), "utf8");
    const postStart = routes.indexOf("router.post('/agent-records'");
    expect(postStart).toBeGreaterThan(-1);
    const postEnd = routes.indexOf("router.patch('/agent-records/:id'", postStart);
    expect(postEnd).toBeGreaterThan(postStart);
    const handler = routes.slice(postStart, postEnd);

    expect(handler).toContain("blankRecordError(title, content)");
    expect(handler).toMatch(/status\(400\)/);
  });
});
