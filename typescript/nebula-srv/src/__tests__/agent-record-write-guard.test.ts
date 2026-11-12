import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import * as path from "path";

import {
  BLANK_RECORD_ERROR,
  BODYLESS_ANALYSIS_ERROR,
  BODY_REQUIRED_RECORD_TYPES,
  HARNESS_JOB_ID_INVALID,
  blankRecordError,
  bodyRequiredError,
  isBlank,
  normalizeHarnessJobId,
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

// Contract under test (V210): the optional harnessJobId on POST
// /api/agent-records. bin/check_harness_silent_success.py matches records to
// runs by role+timestamp because nothing identified the run; harness-srv
// already exports HARNESS_JOB_ID to every job child, so the record can carry
// it and the checker can compare ids.

const UUID = "1f0b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";

describe("normalizeHarnessJobId", () => {
  it("accepts the harness's own job id", () => {
    expect(normalizeHarnessJobId(UUID)).toEqual({ jobId: UUID, error: null });
  });

  it("accepts the moleculer harness basename shape", () => {
    expect(normalizeHarnessJobId("harn-freebuff-2b5c34ae")).toEqual({
      jobId: "harn-freebuff-2b5c34ae",
      error: null,
    });
  });

  it("trims surrounding whitespace", () => {
    expect(normalizeHarnessJobId("  " + UUID + "\n")).toEqual({
      jobId: UUID,
      error: null,
    });
  });

  it("treats absent and empty as 'unattributed', not as an error", () => {
    // The empty string is what a shell hands over for an UNSET variable, and
    // unattributed is a legitimate state: records written outside a harness.
    expect(normalizeHarnessJobId(undefined)).toEqual({ jobId: null, error: null });
    expect(normalizeHarnessJobId(null)).toEqual({ jobId: null, error: null });
    expect(normalizeHarnessJobId("")).toEqual({ jobId: null, error: null });
    expect(normalizeHarnessJobId("   ")).toEqual({ jobId: null, error: null });
  });

  it("refuses values that cannot be a run identifier", () => {
    // Storing these would look like attribution while matching nothing — the
    // defect the column exists to remove, wearing the column's shape.
    for (const bad of [
      "short",                    // < 8 chars
      "has spaces in it",
      "/tmp/jobs/abc12345.md",    // a path, not an id
      "-leading-dash-1234",       // must start alphanumeric
      "a".repeat(129),            // > 128 chars
      42,
      {},
    ]) {
      expect(normalizeHarnessJobId(bad)).toEqual({
        jobId: null,
        error: HARNESS_JOB_ID_INVALID,
      });
    }
  });

  it("names the field and the way out in the refusal", () => {
    expect(HARNESS_JOB_ID_INVALID).toContain("harnessJobId");
    expect(HARNESS_JOB_ID_INVALID).toMatch(/omit it/);
  });
});

describe("bodyRequiredError", () => {
  // Contract under test: an `analysis` row whose body is missing must be
  // REFUSED, because the body is the analysis. 30 analyst analysis rows from
  // 2026-11-10/11 were accepted title-only and their text is unrecoverable.
  // The rule is scoped to that one type on purpose: title-only status rows for
  // other types are legitimate and must keep writing.

  it("refuses a bodyless analysis record", () => {
    expect(bodyRequiredError("analysis", undefined)).toBe(BODYLESS_ANALYSIS_ERROR);
    expect(bodyRequiredError("analysis", null)).toBe(BODYLESS_ANALYSIS_ERROR);
    expect(bodyRequiredError("analysis", "")).toBe(BODYLESS_ANALYSIS_ERROR);
    expect(bodyRequiredError("analysis", "  \n\t ")).toBe(BODYLESS_ANALYSIS_ERROR);
  });

  it("permits an analysis record that carries its body", () => {
    expect(bodyRequiredError("analysis", "## Findings\n\nsomething real")).toBeNull();
  });

  it("does not make prose a requirement for other record types", () => {
    // 47 corpus rows legitimately carry an empty body; this must not break them.
    for (const t of ["report", "assessment", "engineering_log", "decision",
                     "architecture_note", "inspection", "prompt", "response"]) {
      expect(bodyRequiredError(t, "")).toBeNull();
      expect(bodyRequiredError(t, undefined)).toBeNull();
    }
  });

  it("ignores non-string record types it does not administer", () => {
    expect(bodyRequiredError(undefined, "")).toBeNull();
    expect(bodyRequiredError(42, "")).toBeNull();
  });

  it("names the type and the reference in the refusal", () => {
    expect(BODYLESS_ANALYSIS_ERROR).toContain("analysis records require content");
    expect(BODYLESS_ANALYSIS_ERROR).toMatch(/unrecoverable/);
    expect(BODY_REQUIRED_RECORD_TYPES).toEqual(["analysis"]);
  });
});

describe("wiring", () => {
  it("the POST /agent-records handler enforces the analysis body rule", () => {
    // A guard that is defined but never called protects nothing.
    const routes = readFileSync(path.join(__dirname, "..", "routes.ts"), "utf8");
    const postStart = routes.indexOf("router.post('/agent-records'");
    const postEnd = routes.indexOf("router.patch('/agent-records/:id'", postStart);
    const handler = routes.slice(postStart, postEnd);

    expect(handler).toContain("bodyRequiredError(recordType, content)");
    expect(handler).toMatch(/status\(400\)/);
    // and it must run BEFORE the row is inserted
    expect(handler.indexOf("bodyRequiredError(recordType, content)"))
      .toBeLessThan(handler.indexOf("INSERT INTO nebula.agent_records"));
  });

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

  it("the POST /agent-records insert actually stores harness_job_id", () => {
    // Validating the field and then dropping it would pass every unit test
    // above and still leave every record unattributed.
    const routes = readFileSync(path.join(__dirname, "..", "routes.ts"), "utf8");
    const postStart = routes.indexOf("router.post('/agent-records'");
    const postEnd = routes.indexOf("router.patch('/agent-records/:id'", postStart);
    const handler = routes.slice(postStart, postEnd);

    expect(handler).toContain("normalizeHarnessJobId(harnessJobId)");
    expect(handler).toMatch(/INSERT INTO nebula\.agent_records \([^)]*harness_job_id/s);
    expect(handler).toContain("runAttribution.jobId");
  });
});
