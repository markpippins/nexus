/**
 * Listener tests — port of the payload handling in
 * python/substance/listener.py.
 *
 * The Postgres LISTEN socket itself is exercised live (see the change-log for
 * the deployment run); what is covered here is the decision logic around each
 * notification, which is where the Python service could silently lose an
 * invalidation.
 */
import { describe, expect, it, vi } from "vitest";

import { handleSegmentExpired, parseSegmentExpired, queueStats } from "./listener";

const U1 = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const U2 = "9c858901-8a57-4791-81fe-4c455b099bc9";

describe("parseSegmentExpired", () => {
  it("parses a well-formed payload", () => {
    const data = parseSegmentExpired(
      JSON.stringify({ segment_id: U1, segment_set_ids: [U1, U2] }),
    );
    expect(data).toEqual({ segment_id: U1, segment_set_ids: [U1, U2] });
  });

  it("returns null for malformed JSON", () => {
    expect(parseSegmentExpired("{not json")).toBeNull();
  });

  it("returns null for a non-object payload", () => {
    expect(parseSegmentExpired("42")).toBeNull();
    expect(parseSegmentExpired('"a string"')).toBeNull();
    expect(parseSegmentExpired("null")).toBeNull();
  });

  it("returns null for an array payload", () => {
    expect(parseSegmentExpired("[1,2,3]")).toBeNull();
  });
});

describe("handleSegmentExpired", () => {
  it("invalidates every segment set named by the payload", async () => {
    // The listener must not silently no-op: a lost invalidation shows up much
    // later as a stale evidence bundle, so the count is asserted.
    const seen: string[] = [];
    const cache = await import("./cache");
    const spy = vi
      .spyOn(cache, "invalidateSegset")
      .mockImplementation(async (id: string) => {
        seen.push(id);
      });
    try {
      const n = await handleSegmentExpired(
        JSON.stringify({ segment_id: U1, segment_set_ids: [U1, U2] }),
      );
      expect(n).toBe(2);
      expect(seen).toEqual([U1, U2]);
    } finally {
      spy.mockRestore();
    }
  });

  it("returns 0 for a payload naming no sets", async () => {
    expect(await handleSegmentExpired(JSON.stringify({ segment_id: U1 }))).toBe(0);
    expect(
      await handleSegmentExpired(JSON.stringify({ segment_set_ids: [] })),
    ).toBe(0);
  });

  it("returns 0 for a malformed payload instead of throwing", async () => {
    expect(await handleSegmentExpired("{not json")).toBe(0);
  });

  it("keeps going when one invalidation fails", async () => {
    // A single bad id must not abandon the rest of the batch — partial
    // invalidation plus the TTL safety net beats wholesale loss.
    const seen: string[] = [];
    const cache = await import("./cache");
    const spy = vi.spyOn(cache, "invalidateSegset").mockImplementation(async (id: string) => {
      seen.push(id);
      if (id === U1) {
        throw new Error("redis down");
      }
    });
    try {
      const n = await handleSegmentExpired(
        JSON.stringify({ segment_id: "s", segment_set_ids: [U1, U2] }),
      );
      expect(n).toBe(1);
      expect(seen).toEqual([U1, U2]);
    } finally {
      spy.mockRestore();
    }
  });

  it("tolerates a segment_set_ids that is not an array", async () => {
    expect(
      await handleSegmentExpired(
        JSON.stringify({ segment_id: U1, segment_set_ids: "nope" }),
      ),
    ).toBe(0);
  });
});

describe("notify queue", () => {
  it("starts empty", () => {
    const stats = queueStats();
    expect(stats.size).toBe(0);
    expect(stats.dropped).toBe(0);
  });
});
