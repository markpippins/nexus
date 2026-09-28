/**
 * Cache tests — port of the TestCacheHelpers class in
 * python/substance/tests/test_substance.py, plus the JSON-encoding contract
 * that Python's `_JSONEncoder` provided.
 *
 * Pure: no Redis connection is opened. getClient() is lazyConnect, so importing
 * this module touches no sockets.
 */
import { describe, expect, it } from "vitest";

import {
  decodeCachePayload,
  domainIndexKey,
  encodeCachePayload,
  getSegset,
  invalidateDomainIndex,
  invalidateDomainIndexBestEffort,
  invalidateSegset,
  invalidateSegsetBestEffort,
  jsonReplacer,
  segsetKey,
  setClientFactory,
  setSegset,
} from "./cache";
import { getSettings } from "./config";
import { toSegmentSetOut, type SegmentSetRow } from "./schemas";

const UUID_A = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const UUID_B = "9c858901-8a57-4791-81fe-4c455b099bc9";

describe("segsetKey", () => {
  it("formats a segment set id into nexus:segset:{id}", () => {
    expect(segsetKey(UUID_A)).toBe(`nexus:segset:${UUID_A}`);
  });

  it("accepts any string id", () => {
    expect(segsetKey("abc-123")).toBe("nexus:segset:abc-123");
  });

  it("handles the empty-string edge case", () => {
    expect(segsetKey("")).toBe("nexus:segset:");
  });
});

describe("domainIndexKey", () => {
  it("formats the reserved per-domain reverse index", () => {
    expect(domainIndexKey("candidates", UUID_A)).toBe(
      `nexus:candidates:${UUID_A}:segsets`,
    );
  });

  it("handles the empty-type edge case", () => {
    expect(domainIndexKey("", UUID_A)).toContain(":segsets");
  });
});

describe("jsonReplacer", () => {
  it("serialises a Date to an ISO-8601 string", () => {
    // node-postgres hands back timestamptz columns as JS Dates, so this is what
    // Python's `_JSONEncoder` did for datetime.
    const result = encodeCachePayload({ ts: new Date("2026-07-04T12:00:00Z") });
    expect(result).toContain("2026-07-04T12:00:00");
  });

  it("passes standard JSON types through unchanged", () => {
    const data = { a: 1, b: "hello", c: [1, 2, 3] };
    expect(JSON.parse(encodeCachePayload(data))).toEqual(data);
  });

  it("leaves strings and numbers alone", () => {
    expect(jsonReplacer("k", "v")).toBe("v");
    expect(jsonReplacer("k", 42)).toBe(42);
    expect(jsonReplacer("k", null)).toBeNull();
  });

  it("passes UUID strings through — they are already strings in TypeScript", () => {
    expect(encodeCachePayload({ id: UUID_A })).toBe(`{"id":"${UUID_A}"}`);
  });
});

describe("decodeCachePayload", () => {
  it("returns null on a miss", () => {
    expect(decodeCachePayload(null)).toBeNull();
  });

  it("round-trips a payload", () => {
    const payload = { id: UUID_A, segments: [{ ordinal: 1 }] };
    expect(decodeCachePayload(encodeCachePayload(payload))).toEqual(payload);
  });

  it("treats a truncated blob as a miss rather than a 500", () => {
    // Postgres rebuilds it on the next read; a corrupt cache must never take
    // the service down.
    expect(decodeCachePayload('{"id":"abc')).toBeNull();
  });
});

describe("best-effort invalidation", () => {
  /** A stand-in client whose every op rejects, as an unreachable Redis would. */
  function failingClient() {
    return {
      del: async () => {
        throw new Error("ECONNREFUSED");
      },
    };
  }

  it("swallows a Redis failure instead of failing a committed write", async () => {
    // The invalidation runs *after* the transaction commits, so the data is
    // already durable. Raising here would turn a successful write into a 500,
    // and a client retrying on a 500 would duplicate the row. The TTL covers it.
    setClientFactory(() => failingClient() as never);
    try {
      await expect(invalidateSegsetBestEffort(UUID_A)).resolves.toBeUndefined();
      await expect(
        invalidateDomainIndexBestEffort("candidates", UUID_A),
      ).resolves.toBeUndefined();
    } finally {
      setClientFactory(null);
    }
  });

  it("still lets the strict form surface the error to callers that need it", async () => {
    setClientFactory(() => failingClient() as never);
    try {
      await expect(invalidateSegset(UUID_A)).rejects.toThrow("ECONNREFUSED");
      await expect(invalidateDomainIndex("candidates", UUID_A)).rejects.toThrow(
        "ECONNREFUSED",
      );
    } finally {
      setClientFactory(null);
    }
  });

  it("deletes the segset key on the happy path", async () => {
    // Guards the key name, not just the error handling: a typo here would
    // silently stop invalidating the right blob.
    const deleted: string[] = [];
    setClientFactory(
      () =>
        ({
          del: async (key: string) => {
            deleted.push(key);
            return 1;
          },
        }) as never,
    );
    try {
      await invalidateSegset(UUID_A);
      await invalidateDomainIndex("candidates", UUID_B);
      expect(deleted).toEqual([
        `nexus:segset:${UUID_A}`,
        `nexus:candidates:${UUID_B}:segsets`,
      ]);
    } finally {
      setClientFactory(null);
    }
  });

  it("writes the safety-net TTL when populating", async () => {
    const calls: unknown[][] = [];
    setClientFactory(
      () =>
        ({
          set: async (...args: unknown[]) => {
            calls.push(args);
            return "OK";
          },
        }) as never,
    );
    try {
      await setSegset(UUID_A, { id: UUID_A });
      expect(calls).toHaveLength(1);
      const [key, value, mode, ttl] = calls[0]!;
      expect(key).toBe(`nexus:segset:${UUID_A}`);
      expect(value).toBe(`{"id":"${UUID_A}"}`);
      expect(mode).toBe("EX");
      expect(ttl).toBe(getSettings().redisTtlSeconds);
    } finally {
      setClientFactory(null);
    }
  });

  it("reads back a stored payload", async () => {
    setClientFactory(
      () =>
        ({
          get: async () => `{"id":"${UUID_A}","segments":[]}`,
        }) as never,
    );
    try {
      await expect(getSegset(UUID_A)).resolves.toEqual({
        id: UUID_A,
        segments: [],
      });
    } finally {
      setClientFactory(null);
    }
  });

  it("reports a miss as null", async () => {
    setClientFactory(() => ({ get: async () => null }) as never);
    try {
      await expect(getSegset(UUID_A)).resolves.toBeNull();
    } finally {
      setClientFactory(null);
    }
  });
});

describe("resolved segment set cache round-trip", () => {
  const row: SegmentSetRow = {
    id: UUID_A.toUpperCase(),
    name: "auth-flow candidate evidence",
    description: null,
    status: "active",
    metadata: { harvested: true },
    created_at: new Date("2026-07-04T12:00:00Z"),
    updated_at: new Date("2026-07-04T13:30:00Z"),
  };

  it("survives the Redis encode/decode cycle with every field intact", () => {
    const resolved = toSegmentSetOut(row, [
      {
        segment_id: UUID_B,
        ordinal: 0,
        note: null,
        conversation_id: UUID_A,
        start_block_index: 5,
        end_block_index: 8,
        segment_type: "discussion",
        title: "Arc 1",
      },
    ]);
    const restored = decodeCachePayload(encodeCachePayload(resolved));
    expect(restored).toEqual(resolved);
    // The cache is keyed by the canonical lowercase id.
    expect(restored!.id).toBe(UUID_A);
  });

  it("keeps snake_case keys — nebula-srv's substanceToCamel depends on it", () => {
    const resolved = toSegmentSetOut(row, [
      {
        segment_id: UUID_B,
        ordinal: 0,
        note: null,
        conversation_id: UUID_A,
        start_block_index: 5,
        end_block_index: 8,
        segment_type: "discussion",
        title: null,
      },
    ]);
    const encoded = encodeCachePayload(resolved);
    for (const key of [
      "created_at",
      "updated_at",
      "segment_id",
      "conversation_id",
      "start_block_index",
    ]) {
      expect(encoded).toContain(key);
    }
    // The camelCase spellings must NOT appear: substance-proxy's
    // substanceToCamel only has work to do if the wire form is snake_case.
    expect(encoded).not.toContain("createdAt");
    expect(encoded).not.toContain("segmentId");
  });
});
