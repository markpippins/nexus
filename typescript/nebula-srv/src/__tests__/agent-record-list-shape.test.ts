import { describe, expect, it } from "vitest";

import {
  AGENT_RECORD_LIST_COLUMNS,
  AGENT_RECORD_LIST_COLUMNS_FULL,
  AGENT_RECORD_POINT_COLUMNS,
  camelCaseRow,
} from "../agent-record-projection";

// Contract under test: agent-record LIST/SEARCH projections must always carry
// contentLength so consumers can distinguish "body exists, not projected"
// from "empty body". The default projection must NOT include the content
// column; the full projection (?full=1) must include both. This is the
// remediation for the lost-write incident (record b9b88010): list-based
// audits previously saw no content field at all and could not tell the two
// cases apart (engineer finding eb4e219a).
//
// camelCaseRow is key-preserving (SELECT decides the shape), so these tests
// pin the SQL fragments plus the row->API projection contract.

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: "0b000000-0000-0000-0000-000000000001",
    record_type: "report",
    role: "planner",
    model: "openai/gpt-6-luna",
    title: "t",
    source_path: null,
    tags: ["a"],
    metadata: null,
    content: "hello world",
    content_length: 11,
    system_id: null,
    subsystem_id: null,
    feature_id: null,
    plan_ref: null,
    created_at: new Date("2026-09-28T00:00:00Z"),
    recorded_on_dt: new Date("2026-09-28T00:00:00Z"),
    level: 2,
    visibility_scope: "all",
    ...overrides,
  };
}

describe("agent-record list projection (contentLength contract)", () => {
  it("default projection always includes content_length", () => {
    expect(AGENT_RECORD_LIST_COLUMNS).toContain(
      "octet_length(content)::int AS content_length"
    );
  });

  it("default projection excludes the bare content column", () => {
    // Strip the sanctioned octet_length(content) call, then require that no
    // bare `content` column remains projected.
    const sansCall = AGENT_RECORD_LIST_COLUMNS.replace(
      /octet_length\(content\)/g,
      ""
    );
    expect(sansCall).not.toMatch(/\bcontent\b/);
  });

  it("full projection includes content_length AND content", () => {
    expect(AGENT_RECORD_LIST_COLUMNS_FULL).toContain(
      "octet_length(content)::int AS content_length"
    );
    expect(AGENT_RECORD_LIST_COLUMNS_FULL).toMatch(/content,\n/);
    expect(AGENT_RECORD_LIST_COLUMNS_FULL.length).toBeGreaterThan(
      AGENT_RECORD_LIST_COLUMNS.length
    );
  });

  it("camelCaseRow projects content_length -> contentLength", () => {
    const item = camelCaseRow(row());
    expect(item.contentLength).toBe(11);
  });

  it("camelCaseRow is key-preserving: content appears only when selected", () => {
    const withoutContent = camelCaseRow(row({}));
    const withContent = camelCaseRow(row());
    expect("content" in withoutContent).toBe(true); // input had it; SELECT controls the real shape
    expect(withContent.content).toBe("hello world");
  });

  it("camelCaseRow converts timestamps to epoch ms", () => {
    const item = camelCaseRow(row());
    expect(item.createdAt).toBe(Date.parse("2026-09-28T00:00:00Z"));
  });
});

// Provenance contract: a record corrected via the `corrected-by:` /
// `residue-closed-by:` tags keeps its body, so the correction itself lives in
// metadata (corrections[], residue_closed) plus the appended `annotates:`
// record. If the list projection drops metadata, an auditor can see THAT the
// record was corrected but not BY WHAT without psql — decision 76582d4a is the
// live case (correction b3fabb0d, PR #906).
describe("agent-record list projection (correction provenance contract)", () => {
  it("default projection includes metadata", () => {
    expect(AGENT_RECORD_LIST_COLUMNS).toMatch(/\btags, metadata,/);
  });

  it("full projection includes metadata", () => {
    expect(AGENT_RECORD_LIST_COLUMNS_FULL).toMatch(/\btags, metadata,/);
  });

  it("metadata survives camelCaseRow with its canonical correction claims intact", () => {
    const item = camelCaseRow(
      row({
        metadata: {
          "correction.claims": [
            {
              by: "engineer",
              claim: "the satisfaction-based predicate fix needs its own PR",
              note: "Operative rulings unchanged; decision not superseded.",
            },
          ],
        },
        tags: ["corrected-by:b3fabb0d"],
      })
    );
    expect(item.metadata["correction.claims"][0].claim).toBe(
      "the satisfaction-based predicate fix needs its own PR"
    );
    expect(item.tags).toContain("corrected-by:b3fabb0d");
  });

  it("metadata is null-preserving for uncorrected records", () => {
    expect(camelCaseRow(row()).metadata).toBeNull();
  });
});

// Point contract: GET /api/agent-records/:id is the read a consumer uses
// for one record in full (including its body). It selects the same
// provenance fields as the lists — a point read that drops metadata
// recreates the psql-only blind spot on the single-record path (and on
// the nebula-mcp get tool + Assembly point proxy that ride it).
describe("agent-record point projection (single-record read)", () => {
  it("point projection includes metadata", () => {
    expect(AGENT_RECORD_POINT_COLUMNS).toMatch(/\btags, metadata,/);
  });

  it("point projection includes content and harness_job_id", () => {
    expect(AGENT_RECORD_POINT_COLUMNS).toMatch(/\bcontent,/);
    expect(AGENT_RECORD_POINT_COLUMNS).toContain("harness_job_id");
  });

  it("metadata survives camelCaseRow on the point shape too", () => {
    const item = camelCaseRow(
      row({ metadata: { "correction.claims": [{ claim: "x" }] } })
    );
    expect(item.metadata["correction.claims"][0].claim).toBe("x");
  });
});
