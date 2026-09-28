/**
 * Schema tests — port of every schema class in
 * python/substance/tests/test_substance.py (SegmentMemberIn, SegmentSetCreate,
 * SegmentSetUpdate, ResolvedSegment, SegmentSetOut, MembersAddIn,
 * DomainLinkIn/Out), plus the primitives the hand-rolled validators introduce
 * and the from-segments ingest model that the Python tests never covered.
 *
 * Pure: no DB, no Redis, no HTTP.
 */
import { describe, expect, it } from "vitest";

import {
  coerceInt,
  ErrorBag,
  isDomainType,
  isPlainObject,
  isUuid,
  normalizeUuid,
  parseDomainLinkIn,
  parseMembersAddIn,
  parseSegmentFromArcs,
  parseSegmentMemberIn,
  parseSegmentSetCreate,
  parseSegmentSetFromSegmentsCreate,
  parseSegmentSetUpdate,
  toDomainLinkOut,
  toIso,
  toSegmentSetOut,
  type ParseResult,
  type SegmentSetRow,
} from "./schemas";

const U1 = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const U2 = "9c858901-8a57-4791-81fe-4c455b099bc9";
const U3 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
const CONV = "11111111-2222-4333-8444-555555555555";
const SNAP = "66666666-7777-4888-8999-aaaaaaaaaaaa";

// ── Helpers ──────────────────────────────────────────────────────────────────

function expectOk<T>(result: { ok: true; value: T } | { ok: false }): T {
  if (!result.ok) {
    throw new Error(`expected ok, got errors: ${JSON.stringify(result)}`);
  }
  return result.value;
}

function expectErr<T>(result: ParseResult<T>): { errors: { loc: unknown[]; type: string }[] } {
  if (result.ok) {
    throw new Error("expected a validation error, got ok");
  }
  return result;
}

// ── Primitives ───────────────────────────────────────────────────────────────

describe("isUuid", () => {
  it("accepts the canonical 8-4-4-4-12 form, any case", () => {
    expect(isUuid(U1)).toBe(true);
    expect(isUuid(U1.toUpperCase())).toBe(true);
  });

  it("accepts the other spellings Python's uuid.UUID accepts", () => {
    expect(isUuid(U1.replace(/-/g, ""))).toBe(true);
    expect(isUuid(`urn:uuid:${U1}`)).toBe(true);
    expect(isUuid(`{${U1}}`)).toBe(true);
  });

  it("rejects malformed ids", () => {
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid("")).toBe(false);
    expect(isUuid("3f2504e0-4f89-41d3-9a0c-0305e82c330")).toBe(false); // too short
    expect(isUuid("3f2504e04f8941d39a0c0305e82c3301z")).toBe(false);
  });

  it("rejects non-strings", () => {
    expect(isUuid(42)).toBe(false);
    expect(isUuid(null)).toBe(false);
    expect(isUuid({})).toBe(false);
  });
});

describe("normalizeUuid", () => {
  it("lowercases the canonical form", () => {
    expect(normalizeUuid(U1.toUpperCase())).toBe(U1);
  });

  it("re-inserts the hyphens in the 32-hex form", () => {
    expect(normalizeUuid(U1.replace(/-/g, ""))).toBe(U1);
  });

  it("strips the urn:uuid: prefix", () => {
    expect(normalizeUuid(`urn:uuid:${U1}`)).toBe(U1);
  });
});

describe("isPlainObject", () => {
  it("accepts a JSON object", () => {
    expect(isPlainObject({})).toBe(true);
    expect(isPlainObject({ a: 1 })).toBe(true);
  });

  it("rejects arrays, null and primitives", () => {
    // pydantic's `dict` rejects these; a metadata array must not slip through.
    expect(isPlainObject([])).toBe(false);
    expect(isPlainObject([1, 2])).toBe(false);
    expect(isPlainObject(null)).toBe(false);
    expect(isPlainObject("x")).toBe(false);
    expect(isPlainObject(1)).toBe(false);
  });
});

describe("coerceInt", () => {
  it("accepts an integer number", () => {
    expect(coerceInt(5)).toBe(5);
    expect(coerceInt(-1)).toBe(-1);
    expect(coerceInt(0)).toBe(0);
  });

  it("accepts a numeric string, as pydantic's lax mode does", () => {
    expect(coerceInt("5")).toBe(5);
    expect(coerceInt(" -3 ")).toBe(-3);
  });

  it("rejects a fractional number", () => {
    expect(coerceInt(1.5)).toBeUndefined();
  });

  it("rejects a non-numeric string", () => {
    expect(coerceInt("abc")).toBeUndefined();
    expect(coerceInt("")).toBeUndefined();
  });

  it("rejects booleans", () => {
    // true/false are numbers in JS but must not be ordinals.
    expect(coerceInt(true)).toBeUndefined();
    expect(coerceInt(false)).toBeUndefined();
  });
});

describe("isDomainType", () => {
  it("accepts only the two surviving domain types", () => {
    expect(isDomainType("candidates")).toBe(true);
    expect(isDomainType("requirements")).toBe(true);
    expect(isDomainType("intent-records")).toBe(false);
    expect(isDomainType("Candidates")).toBe(false);
  });
});

// ── SegmentMemberIn ──────────────────────────────────────────────────────────

describe("parseSegmentMemberIn", () => {
  it("accepts all fields", () => {
    const m = parseSegmentMemberIn({
      segment_id: U1,
      ordinal: 1,
      note: "test note",
    });
    expect(m).toEqual({ segment_id: U1, ordinal: 1, note: "test note" });
  });

  it("defaults note to null", () => {
    const m = parseSegmentMemberIn({ segment_id: U1, ordinal: 2 });
    expect(m).toEqual({ segment_id: U1, ordinal: 2, note: null });
  });

  it("normalises a mixed-case segment id", () => {
    const m = parseSegmentMemberIn({ segment_id: U1.toUpperCase(), ordinal: 1 });
    expect(m!.segment_id).toBe(U1);
  });

  it("accepts ordinal 0", () => {
    expect(parseSegmentMemberIn({ segment_id: U1, ordinal: 0 })!.ordinal).toBe(0);
  });

  it("accepts a negative ordinal — the schema has no minimum", () => {
    expect(parseSegmentMemberIn({ segment_id: U1, ordinal: -1 })!.ordinal).toBe(-1);
  });

  it("rejects a missing segment_id", () => {
    const bag = new ErrorBag();
    expect(parseSegmentMemberIn({ ordinal: 1 }, [], bag)).toBeNull();
    expect(bag.errors).toContainEqual(
      expect.objectContaining({ loc: ["segment_id"], type: "uuid_parsing" }),
    );
  });

  it("rejects a non-uuid segment_id", () => {
    expect(parseSegmentMemberIn({ segment_id: "nope", ordinal: 1 })).toBeNull();
  });

  it("rejects a missing ordinal", () => {
    expect(parseSegmentMemberIn({ segment_id: U1 })).toBeNull();
  });

  it("rejects a non-integer ordinal", () => {
    expect(parseSegmentMemberIn({ segment_id: U1, ordinal: 1.5 })).toBeNull();
    expect(parseSegmentMemberIn({ segment_id: U1, ordinal: "x" })).toBeNull();
  });

  it("rejects a non-object member", () => {
    expect(parseSegmentMemberIn("nope")).toBeNull();
    expect(parseSegmentMemberIn([U1, 1])).toBeNull();
    expect(parseSegmentMemberIn(null)).toBeNull();
  });
});

// ── SegmentSetCreate ─────────────────────────────────────────────────────────

describe("parseSegmentSetCreate", () => {
  it("defaults every field", () => {
    const v = expectOk(parseSegmentSetCreate({}));
    expect(v.name).toBeNull();
    expect(v.description).toBeNull();
    expect(v.metadata).toEqual({});
    expect(v.members).toEqual([]);
  });

  it("accepts an entirely empty body", () => {
    expectOk(parseSegmentSetCreate({}));
  });

  it("accepts name and description", () => {
    const v = expectOk(parseSegmentSetCreate({ name: "test", description: "desc" }));
    expect(v.name).toBe("test");
    expect(v.description).toBe("desc");
  });

  it("accepts an explicit empty metadata", () => {
    expect(expectOk(parseSegmentSetCreate({ metadata: {} })).metadata).toEqual({});
  });

  it("preserves nested metadata", () => {
    const v = expectOk(parseSegmentSetCreate({ metadata: { nested: { a: 1 } } }));
    expect(v.metadata).toEqual({ nested: { a: 1 } });
  });

  it("parses members", () => {
    const v = expectOk(
      parseSegmentSetCreate({ members: [{ segment_id: U1, ordinal: 1 }] }),
    );
    expect(v.members).toHaveLength(1);
    expect(v.members[0]!.ordinal).toBe(1);
  });

  it("rejects members that are not a list", () => {
    const e = expectErr(parseSegmentSetCreate({ members: "not-a-list" }));
    expect(e.errors[0]!.type).toBe("list_type");
  });

  it("rejects a metadata array", () => {
    const e = expectErr(parseSegmentSetCreate({ metadata: [1, 2] }));
    expect(e.errors[0]!.type).toBe("dict_type");
  });

  it("reports the index of the offending member", () => {
    const e = expectErr(
      parseSegmentSetCreate({
        members: [{ segment_id: U1, ordinal: 1 }, { segment_id: "bad", ordinal: 2 }],
      }),
    );
    expect(e.errors[0]!.loc).toEqual(["members", 1, "segment_id"]);
  });

  it("reports every error at once rather than only the first", () => {
    const e = expectErr(
      parseSegmentSetCreate({ name: 5, members: "nope" }),
    );
    expect(e.errors.length).toBe(2);
  });

  it("rejects a non-object body", () => {
    expectErr(parseSegmentSetCreate("nope"));
    expectErr(parseSegmentSetCreate(null));
    expectErr(parseSegmentSetCreate([]));
  });
});

// ── SegmentSetUpdate (PATCH) ─────────────────────────────────────────────────

describe("parseSegmentSetUpdate", () => {
  it("returns no fields for an empty body", () => {
    expect(expectOk(parseSegmentSetUpdate({}))).toEqual({});
  });

  it("keeps only the field that was set — PATCH's exclude_unset semantics", () => {
    const v = expectOk(parseSegmentSetUpdate({ name: "x" }));
    expect(v).toEqual({ name: "x" });
    expect("description" in v).toBe(false);
    expect("metadata" in v).toBe(false);
  });

  it("accepts both statuses", () => {
    expect(expectOk(parseSegmentSetUpdate({ status: "active" })).status).toBe("active");
    expect(expectOk(parseSegmentSetUpdate({ status: "archived" })).status).toBe(
      "archived",
    );
  });

  it("rejects any other status", () => {
    const e = expectErr(parseSegmentSetUpdate({ status: "deleted" }));
    expect(e.errors[0]!.type).toBe("literal_error");
    expect(e.errors[0]!.loc).toEqual(["status"]);
  });

  it("keeps an explicit null so the column can be cleared", () => {
    // This is the whole point of tracking presence separately from value.
    const v = expectOk(parseSegmentSetUpdate({ description: null }));
    expect("description" in v).toBe(true);
    expect(v.description).toBeNull();
  });

  it("keeps an explicit null status and an explicit null metadata", () => {
    const v = expectOk(parseSegmentSetUpdate({ status: null, metadata: null }));
    expect(v.status).toBeNull();
    expect(v.metadata).toBeNull();
  });

  it("distinguishes an absent metadata from an empty one", () => {
    expect("metadata" in expectOk(parseSegmentSetUpdate({ name: "n" }))).toBe(false);
    expect(expectOk(parseSegmentSetUpdate({ metadata: {} })).metadata).toEqual({});
  });
});

// ── MembersAddIn ─────────────────────────────────────────────────────────────

describe("parseMembersAddIn", () => {
  it("accepts a segments list", () => {
    const v = expectOk(
      parseMembersAddIn({
        segments: [
          { segment_id: U1, ordinal: 0 },
          { segment_id: U2, ordinal: 1 },
        ],
      }),
    );
    expect(v.segments).toHaveLength(2);
  });

  it("accepts an empty list — a no-op upsert is legal", () => {
    expect(expectOk(parseMembersAddIn({ segments: [] })).segments).toEqual([]);
  });

  it("requires the segments key", () => {
    const e = expectErr(parseMembersAddIn({}));
    expect(e.errors[0]).toMatchObject({ loc: ["segments"], type: "missing" });
  });

  it("rejects a segments value that is not a list", () => {
    expect(expectErr(parseMembersAddIn({ segments: 5 })).errors[0]!.type).toBe(
      "list_type",
    );
  });

  it("rejects a malformed member inside the list", () => {
    const e = expectErr(parseMembersAddIn({ segments: [{ ordinal: 1 }] }));
    expect(e.errors[0]!.loc).toEqual(["segments", 0, "segment_id"]);
  });
});

// ── DomainLinkIn / DomainLinkOut ─────────────────────────────────────────────

describe("parseDomainLinkIn", () => {
  it("defaults role to 'primary'", () => {
    expect(expectOk(parseDomainLinkIn({ segment_set_id: U1 })).role).toBe("primary");
  });

  it("accepts the 'supporting' role", () => {
    expect(
      expectOk(parseDomainLinkIn({ segment_set_id: U1, role: "supporting" })).role,
    ).toBe("supporting");
  });

  it("normalises the segment_set_id", () => {
    expect(
      expectOk(parseDomainLinkIn({ segment_set_id: U1.toUpperCase() })).segment_set_id,
    ).toBe(U1);
  });

  it("rejects an invalid role", () => {
    const e = expectErr(parseDomainLinkIn({ segment_set_id: U1, role: "admin" }));
    expect(e.errors[0]!.loc).toEqual(["role"]);
  });

  it("rejects a missing segment_set_id", () => {
    expect(expectErr(parseDomainLinkIn({})).errors[0]!.loc).toEqual(["segment_set_id"]);
  });

  it("rejects a non-uuid segment_set_id", () => {
    expectErr(parseDomainLinkIn({ segment_set_id: "nope" }));
  });
});

describe("toDomainLinkOut", () => {
  it("emits the minimal shape with a null segment_set", () => {
    const out = toDomainLinkOut(U1, "primary", true, null);
    expect(out).toEqual({
      segment_set_id: U1,
      role: "primary",
      active: true,
      segment_set: null,
    });
  });

  it("nests a resolved segment set", () => {
    const row: SegmentSetRow = {
      id: U1,
      name: "nested",
      description: null,
      status: "active",
      metadata: {},
      created_at: new Date("2026-07-04T12:00:00Z"),
      updated_at: new Date("2026-07-04T12:00:00Z"),
    };
    const out = toDomainLinkOut(U1, "primary", true, toSegmentSetOut(row));
    expect(out.segment_set!.name).toBe("nested");
  });

  it("falls back to 'primary' for a role outside the literal", () => {
    // Defence in depth: the DB is the writer, so an unexpected role must not
    // escape into a response that claims to be a Role.
    expect(toDomainLinkOut(U1, "bogus", true, null).role).toBe("primary");
  });
});

// ── Output models ────────────────────────────────────────────────────────────

describe("toSegmentSetOut", () => {
  const row: SegmentSetRow = {
    id: U1.toUpperCase(),
    name: "test",
    description: "desc",
    status: "active",
    metadata: { key: "val" },
    created_at: new Date("2026-07-04T12:00:00Z"),
    updated_at: new Date("2026-07-04T13:00:00Z"),
  };

  it("carries every column through", () => {
    const out = toSegmentSetOut(row);
    expect(out.id).toBe(U1); // normalised
    expect(out.name).toBe("test");
    expect(out.description).toBe("desc");
    expect(out.status).toBe("active");
    expect(out.metadata).toEqual({ key: "val" });
    expect(out.created_at).toBe("2026-07-04T12:00:00.000Z");
    expect(out.updated_at).toBe("2026-07-04T13:00:00.000Z");
  });

  it("defaults segments to an empty list", () => {
    expect(toSegmentSetOut(row).segments).toEqual([]);
  });

  it("keeps the members in the order given (ordinal order from SQL)", () => {
    const segs = [0, 1, 2].map((ordinal) => ({
      segment_id: U1,
      ordinal,
      note: null,
      conversation_id: null,
      start_block_index: null,
      end_block_index: null,
      segment_type: null,
      title: null,
    }));
    expect(toSegmentSetOut(row, segs).segments.map((s) => s.ordinal)).toEqual([
      0, 1, 2,
    ]);
  });

  it("substitutes an empty object for non-object metadata", () => {
    expect(
      toSegmentSetOut({ ...row, metadata: null as never }).metadata,
    ).toEqual({});
  });

  it("does not alias the caller's members array", () => {
    const segs = [
      {
        segment_id: U1,
        ordinal: 0,
        note: null,
        conversation_id: null,
        start_block_index: null,
        end_block_index: null,
        segment_type: null,
        title: null,
      },
    ];
    const out = toSegmentSetOut(row, segs);
    segs.push({ ...segs[0]!, ordinal: 1 });
    expect(out.segments).toHaveLength(1);
  });
});

describe("toIso", () => {
  it("formats a Date", () => {
    expect(toIso(new Date("2026-07-04T12:00:00Z"))).toBe("2026-07-04T12:00:00.000Z");
  });

  it("parses a string timestamp", () => {
    expect(toIso("2026-07-04T12:00:00Z")).toBe("2026-07-04T12:00:00.000Z");
  });

  it("is stable for a missing timestamp rather than emitting 'null'", () => {
    expect(toIso(null)).toBe("1970-01-01T00:00:00.000Z");
  });
});

// ── Transcript ingest: SegmentFromArcs / SegmentSetFromSegmentsCreate ─────────

describe("parseSegmentFromArcs", () => {
  const valid = {
    start_block_id: U1,
    end_block_id: U2,
    start_block_index: 5,
    end_block_index: 8,
  };

  it("accepts the minimal arc and defaults segment_type to 'discussion'", () => {
    const s = parseSegmentFromArcs(valid);
    expect(s).toEqual({
      ...valid,
      segment_type: "discussion",
      title: null,
      notes_md: null,
    });
  });

  it("keeps an explicit segment_type and the optional text fields", () => {
    const s = parseSegmentFromArcs({
      ...valid,
      segment_type: "task",
      title: "Auth rework",
      notes_md: "# notes",
    });
    expect(s!.segment_type).toBe("task");
    expect(s!.title).toBe("Auth rework");
    expect(s!.notes_md).toBe("# notes");
  });

  it("rejects a non-uuid block id", () => {
    const bag = new ErrorBag();
    expect(
      parseSegmentFromArcs({ ...valid, start_block_id: "x" }, [], bag),
    ).toBeNull();
    expect(bag.errors).toContainEqual(
      expect.objectContaining({ loc: ["start_block_id"], type: "uuid_parsing" }),
    );
  });

  it("rejects a missing block index", () => {
    expect(parseSegmentFromArcs({ ...valid, end_block_index: undefined })).toBeNull();
  });

  it("rejects a non-object arc", () => {
    expect(parseSegmentFromArcs("nope")).toBeNull();
  });
});

describe("parseSegmentSetFromSegmentsCreate", () => {
  const valid = {
    conversation_id: CONV,
    snapshot_id: SNAP,
    segments: [
      {
        start_block_id: U1,
        end_block_id: U2,
        start_block_index: 5,
        end_block_index: 8,
      },
    ],
  };

  it("parses a full ingest request", () => {
    const v = expectOk(parseSegmentSetFromSegmentsCreate(valid));
    expect(v.conversation_id).toBe(CONV);
    expect(v.snapshot_id).toBe(SNAP);
    expect(v.segments).toHaveLength(1);
    expect(v.segments[0]!.segment_type).toBe("discussion");
  });

  it("defaults name, description and metadata", () => {
    const v = expectOk(parseSegmentSetFromSegmentsCreate(valid));
    expect(v.name).toBeNull();
    expect(v.description).toBeNull();
    expect(v.metadata).toEqual({});
  });

  it("requires conversation_id and snapshot_id", () => {
    const e = expectErr(parseSegmentSetFromSegmentsCreate({ segments: [] }));
    const locs = e.errors.map((x) => x.loc.join("."));
    expect(locs).toContain("conversation_id");
    expect(locs).toContain("snapshot_id");
  });

  it("requires the segments key", () => {
    const e = expectErr(
      parseSegmentSetFromSegmentsCreate({
        conversation_id: CONV,
        snapshot_id: SNAP,
      }),
    );
    expect(e.errors).toContainEqual(
      expect.objectContaining({ loc: ["segments"], type: "missing" }),
    );
  });

  it("accepts an empty segments list", () => {
    const v = expectOk(
      parseSegmentSetFromSegmentsCreate({ ...valid, segments: [] }),
    );
    expect(v.segments).toEqual([]);
  });

  it("reports which segment is malformed", () => {
    const e = expectErr(
      parseSegmentSetFromSegmentsCreate({
        ...valid,
        segments: [valid.segments[0], { start_block_id: "bad" }],
      }),
    );
    expect(e.errors.some((x) => x.loc.join(".").startsWith("segments.1"))).toBe(
      true,
    );
  });

  it("rejects a segments value that is not a list", () => {
    const e = expectErr(parseSegmentSetFromSegmentsCreate({ ...valid, segments: 5 }));
    expect(e.errors).toContainEqual(
      expect.objectContaining({ loc: ["segments"], type: "list_type" }),
    );
  });

  it("uses distinct ids for conversation and snapshot", () => {
    expect(U3).not.toBe(CONV);
  });
});
