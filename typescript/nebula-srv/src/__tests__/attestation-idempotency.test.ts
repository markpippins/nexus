import { describe, expect, it } from "vitest";

import {
  attestationBinding,
  headTag,
  headsMatch,
  isAttestationShaped,
  isWithdrawn,
  prTag,
  retirementPatch,
  supersedableDuplicates,
} from "../attestation-idempotency";

// Contract under test: a tester attestation is idempotent on (pr, head) at
// WRITE time. When a retry writes a new row for the same (pr, head), the live
// predecessor is retired under it (Decision 24 Amendment 1) so a duplicate
// never accumulates; a row that cannot be keyed on both tags retires nothing.
//
// The scope rules matter as much as the happy path: head-less legacy rows are
// shared across PRs and must never be collapsed, and a non-attestation row
// (intent, finding) must never retire a real attestation.

const HEAD = "a8b1dfc600000000000000000000000000000000";
const ACC = "11111111-1111-1111-1111-111111111111";
const DUP = "22222222-2222-2222-2222-222222222222";

function attestationTags(overrides: string[] = []) {
  return ["type:approval", "status:done", "pr:487", "head:a8b1dfc6", ...overrides];
}

function dbRow(id: string, tags: string[], overrides: Record<string, unknown> = {}) {
  return {
    id,
    role: "tester",
    record_type: "assessment",
    title: "Tester attestation: PR #487",
    tags,
    metadata: {},
    ...overrides,
  };
}

describe("attestation binding", () => {
  it("keys a canonical tester attestation on (pr, head)", () => {
    expect(attestationBinding("tester", "assessment", attestationTags())).toEqual({
      pr: 487,
      head: "a8b1dfc6",
    });
  });

  it("accepts the tester's gate:<N> tag and the legacy type:attestation shape", () => {
    expect(
      attestationBinding(
        "tester",
        "report",
        ["type:attestation", "gate:491", "head:a8b1dfc6", "verdict:approve"],
      ),
    ).toEqual({ pr: 491, head: "a8b1dfc6" });
  });

  it("is case-insensitive on the role", () => {
    expect(attestationBinding("Tester", "assessment", attestationTags())).not.toBeNull();
  });

  it("never keys a non-tester row", () => {
    expect(attestationBinding("engineer", "assessment", attestationTags())).toBeNull();
  });

  it("never keys an intent row that merely mentions the PR", () => {
    // "I intend to attest" is not "attested"; retiring a live row because of
    // a status update would destroy the gate's only answer.
    expect(
      attestationBinding(
        "tester",
        "engineering_log",
        ["to:engineer", "status:open", "pr:487", "head:a8b1dfc6"],
      ),
    ).toBeNull();
  });

  it("returns null without a head binding (head-less legacy rows stay untouched)", () => {
    expect(
      attestationBinding("tester", "assessment", ["type:approval", "status:done", "pr:487"]),
    ).toBeNull();
  });

  it("returns null without a PR tag", () => {
    expect(
      attestationBinding("tester", "assessment", [
        "type:approval",
        "status:done",
        "head:a8b1dfc6",
      ]),
    ).toBeNull();
  });

  it("treats a malformed head tag as no binding at all", () => {
    // 4 hex chars would prefix-bind at ~1/65536; the gate fails such a row on
    // shape rather than binding on it, and so does this path.
    expect(
      attestationBinding("tester", "assessment", [
        "type:approval",
        "status:done",
        "pr:487",
        "head:a8b1",
      ]),
    ).toBeNull();
    expect(headTag(["head:a8b1"])).toBe("a8b1");
    expect(headsMatch("a8b1", "a8b1dfc6")).toBe(false);
  });
});

describe("shape, tag and head helpers", () => {
  it("reads pr: and gate: tags", () => {
    expect(prTag(["pr:487"])).toBe(487);
    expect(prTag(["gate:491"])).toBe(491);
    expect(prTag(["proj:487"])).toBeNull();
  });

  it("requires status:done for the canonical shape", () => {
    expect(isAttestationShaped("assessment", ["type:approval", "status:done"])).toBe(true);
    expect(isAttestationShaped("assessment", ["type:approval"])).toBe(false);
    expect(isAttestationShaped("report", ["type:approval", "status:done"])).toBe(false);
    expect(isAttestationShaped("assessment", ["type:attestation"])).toBe(true);
  });

  it("matches head prefixes either direction, 7-40 hex", () => {
    expect(headsMatch("a8b1dfc6", HEAD)).toBe(true);
    expect(headsMatch(HEAD, "a8b1dfc6")).toBe(true);
    expect(headsMatch("a8b1dfc6", "0000aaaa")).toBe(false);
    expect(headsMatch(null, HEAD)).toBe(false);
  });

  it("treats retired and superseded as withdrawn", () => {
    expect(isWithdrawn(["status:retired"])).toBe(true);
    expect(isWithdrawn(["status:superseded"])).toBe(true);
    expect(isWithdrawn(["status:done"])).toBe(false);
    expect(isWithdrawn(["status:refused"])).toBe(false);
  });
});

describe("supersedable duplicates", () => {
  const binding = { pr: 487, head: "a8b1dfc6" };

  it("returns the live same-head rows and never the successor itself", () => {
    const accepted = dbRow(ACC, attestationTags());
    const dup = dbRow(DUP, attestationTags());
    const out = supersedableDuplicates([accepted, dup], binding, ACC);
    expect(out.map((r) => r.id)).toEqual([DUP]);
  });

  it("keeps other heads, other PRs, withdrawn rows and non-tester rows", () => {
    const rows = [
      dbRow("head0001", ["type:approval", "status:done", "pr:487", "head:0000aaaa"]),
      dbRow("pr000001", ["type:approval", "status:done", "pr:999", "head:a8b1dfc6"]),
      dbRow("gone0001", ["type:approval", "status:retired", "pr:487", "head:a8b1dfc6"]),
      dbRow("other001", attestationTags(), { role: "engineer" }),
      dbRow("intent01", ["to:engineer", "status:open", "pr:487", "head:a8b1dfc6"], {
        record_type: "engineering_log",
      }),
    ];
    expect(supersedableDuplicates(rows, binding, ACC)).toEqual([]);
  });

  it("includes a refused row for the same head (it is not the current answer)", () => {
    const refused = dbRow("ref00001", [
      "type:attestation",
      "status:refused",
      "gate-code:ATT_CI_COVERAGE",
      "pr:487",
      "head:a8b1dfc6",
    ]);
    expect(supersedableDuplicates([refused], binding, ACC).map((r) => r.id)).toEqual([
      "ref00001",
    ]);
  });

  it("never collapses a head-less row, even for the same PR", () => {
    // The 679/681 shared pair: without a head tag there is nothing to prove
    // the row answers this head, so a write must not retire it.
    const legacy = dbRow("legacy01", ["type:attestation", "status:done", "pr:487"]);
    expect(supersedableDuplicates([legacy], binding, ACC)).toEqual([]);
  });
});

describe("retirement patch", () => {
  const binding = { pr: 487, head: "a8b1dfc6" };

  it("drops every status tag, adds status:retired, and keeps the shape tags", () => {
    const row = dbRow(DUP, attestationTags(["verdict:approved", "policy:attestation/v2"]));
    const patch = retirementPatch(row, binding, ACC, "2026-11-23T06:00:00Z")!;
    expect(patch.tags.filter((t) => t.startsWith("status:"))).toEqual(["status:retired"]);
    expect(patch.tags).toContain("type:approval");
    expect(patch.tags).toContain("pr:487");
    expect(patch.tags).toContain("head:a8b1dfc6");
    expect(patch.tags).toContain("verdict:approved");
  });

  it("prefixes the title once and names the successor in metadata.retired", () => {
    const row = dbRow(DUP, attestationTags());
    const patch = retirementPatch(row, binding, ACC, "2026-11-23T06:00:00Z")!;
    expect(patch.title).toBe("[RETIRED 2026-11-23] Tester attestation: PR #487");
    expect(patch.metadata.retired.note).toContain("PR #487");
    expect(patch.metadata.retired.note).toContain("head a8b1dfc6");
    expect(patch.metadata.retired.note).toContain("11111111");
    expect(patch.metadata.retired.convention).toContain("Decision 24");
  });

  it("preserves existing metadata instead of replacing it", () => {
    const row = dbRow(DUP, attestationTags(), {
      metadata: { harness_job_id: "abc", retired: undefined },
    });
    (row.metadata as Record<string, unknown>).snapshot_purpose = "keep me";
    const patch = retirementPatch(row, binding, ACC, "2026-11-23T06:00:00Z")!;
    expect(patch.metadata.snapshot_purpose).toBe("keep me");
  });

  it("is a no-op on an already-withdrawn row", () => {
    const row = dbRow(DUP, ["type:attestation", "status:retired", "pr:487", "head:a8b1dfc6"]);
    expect(retirementPatch(row, binding, ACC, "2026-11-23T06:00:00Z")).toBeNull();
  });
});
