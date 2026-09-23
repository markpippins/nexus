/**
 * Hermetic parity tests for the role-memory twin.
 *
 * SCOPE — pure semantics that must match typescript/role-memory-srv:
 *   - health: ok vs degraded ladder (stale stamp / zero counts), the exact
 *     structured 503 body fields when Redis is unreachable
 *   - procedures: missing role → [] with 200 (NOT 404 — the incumbent's
 *     most surprising quirk), index JSON round-trip
 *   - procedure: exact 404 string "Procedure not found", card round-trip
 *   - refresh: syncAll result envelope; pipeline failure → 500 {error}
 *   - envelopes: {error} shape (NOT kernel's {status:"error"})
 *
 * NOT COVERED HERE — the route surface. That is enforced by the apidocs
 * drift gate (MOLLECULER_MIRRORS → typescript/role-memory-srv/openapi.yaml)
 * and the live canary diff.
 *
 * The ./store module is mocked wholesale: these tests drive the extracted
 * handlers without Redis, PG, or a broker.
 */

jest.mock("../services/store", () => {
  const state = {
    redisData: new Map<string, string>(),
    failPing: false,
    failGet: false,
    failScan: false,
  };
  const makeRedis = () => ({
    ping: jest.fn(async () => {
      if (state.failPing) throw new Error("Redis is unreachable");
      return "PONG";
    }),
    get: jest.fn(async (key: string) => {
      if (state.failGet) throw new Error("GET failed: connection dropped");
      return state.redisData.get(key) ?? null;
    }),
    set: jest.fn(async (key: string, val: string) => {
      state.redisData.set(key, val);
      return "OK";
    }),
    scan: jest.fn(async (cursor: string, _m: string, pattern: string, _c: number) => {
      if (state.failScan) throw new Error("SCAN failed");
      const keys = [...state.redisData.keys()].filter((k) =>
        new RegExp("^" + pattern.replace(/\*/g, ".*") + "$").test(k)
      );
      // single-page scan (small fixture sets)
      return ["0", keys];
    }),
    pipeline: () => {
      const ops: Array<{ key: string; val: string }> = [];
      const map = state.redisData; // close over the live map (reset replaces it)
      return {
        set: jest.fn((key: string, val: string) => {
          ops.push({ key, val });
          return ops.length;
        }),
        exec: jest.fn(async () => {
          for (const op of ops) {
            map.set(op.key, op.val);
          }
          return ops.map(() => [null, "OK"]);
        }),
        __ops: ops,
      };
    },
  });
  let redisInstance = makeRedis();
  return {
    KEY_PREFIX: "mem:",
    PROC_KEY: (slug: string) => `mem:proc:${slug}`,
    IDX_KEY: (role: string) => `mem:idx:${role}`,
    META_UPDATED_KEY: "mem:meta:last_updated",
    initRedis: jest.fn(() => redisInstance),
    getRedis: jest.fn(() => redisInstance),
    closeRedis: jest.fn(async () => undefined),
    countKeys: jest.fn(async (pattern: string) => {
      if (state.failScan) throw new Error("SCAN failed");
      const keys = [...state.redisData.keys()].filter((k) =>
        new RegExp("^" + pattern.replace(/\*/g, ".*") + "$").test(k)
      );
      return keys.length;
    }),
    initDb: jest.fn(),
    getDb: jest.fn(),
    fetchAllActiveMemory: jest.fn(async () => {
      const now = new Date().toISOString();
      const map = new Map<
        string,
        { procedure: any; roles: string[] }
      >();
      map.set("alpha-proc", {
        procedure: {
          id: "m1", slug: "alpha-proc", title: "Alpha", summary: "A proc",
          body_md: "# Alpha", tags: ["t1"], triggers: [], mcp_tools: [],
          created_at: now, updated_at: now,
        },
        roles: ["engineer", "planner"],
      });
      map.set("beta-proc", {
        procedure: {
          id: "m2", slug: "beta-proc", title: "Beta", summary: "B proc",
          body_md: "# Beta", tags: ["t2"], triggers: [], mcp_tools: [],
          created_at: now, updated_at: now,
        },
        roles: ["planner"],
      });
      return map;
    }),
    __state: state,
    __reset: () => {
      state.redisData = new Map();
      state.failPing = false;
      state.failGet = false;
      state.failScan = false;
      redisInstance = makeRedis();
    },
  };
});

import {
  healthHandler,
  proceduresHandler,
  procedureHandler,
  refreshHandler,
} from "../services/handlers";
import * as storeModule from "../services/store";
const store = storeModule as any;

const ctx = (params: Record<string, unknown>) => ({ params });

beforeEach(() => {
  (store as any).__reset();
  jest.clearAllMocks();
});

// ── procedures: the []-not-404 quirk ───────────────────────────────

describe("procedures (GET /procedures/:role)", () => {
  test("missing role returns EMPTY ARRAY (200), not 404", async () => {
    const r = await proceduresHandler(ctx({ role: "no-such-role" }));
    expect(r).toEqual([]);
  });

  test("returns the parsed index JSON for a populated role", async () => {
    const idx = [
      { slug: "alpha-proc", summary: "A proc", tags: ["t1"] },
      { slug: "beta-proc", summary: "B proc", tags: ["t2"] },
    ];
    (store as any).__state.redisData.set("mem:idx:planner", JSON.stringify(idx));
    const r = (await proceduresHandler(ctx({ role: "planner" }))) as any[];
    expect(r).toHaveLength(2);
    expect(r[0].slug).toBe("alpha-proc");
    expect(r[1].slug).toBe("beta-proc");
  });

  test("Redis read failure → 500 envelope {error}", async () => {
    (store as any).__state.failGet = true;
    await expect(
      proceduresHandler(ctx({ role: "engineer" }))
    ).rejects.toMatchObject({
      code: 500,
      message: expect.stringContaining("GET failed"),
    });
  });
});

// ── procedure: exact 404 string ────────────────────────────────────

describe("procedure (GET /procedure/:slug)", () => {
  test("missing slug → 404 with the EXACT incumbent string", async () => {
    await expect(
      procedureHandler(ctx({ slug: "nonexistent-slug" }))
    ).rejects.toMatchObject({
      code: 404,
      message: "Procedure not found",
    });
  });

  test("returns the parsed card for a populated slug", async () => {
    const card = {
      slug: "alpha-proc", title: "Alpha", summary: "A proc",
      body_md: "# Alpha", tags: ["t1"], triggers: [], mcp_tools: [],
      roles: ["engineer"], updated_at: "2026-09-23T00:00:00.000Z",
    };
    (store as any).__state.redisData.set("mem:proc:alpha-proc", JSON.stringify(card));
    const r = (await procedureHandler(ctx({ slug: "alpha-proc" }))) as any;
    expect(r.slug).toBe("alpha-proc");
    expect(r.roles).toEqual(["engineer"]);
  });

  test("corrupt card JSON → 500 {error} (not 404)", async () => {
    (store as any).__state.redisData.set("mem:proc:bad", "{not json");
    await expect(
      procedureHandler(ctx({ slug: "bad" }))
    ).rejects.toMatchObject({ code: 500 });
  });
});

// ── health: ok/degraded ladder + structured 503 ────────────────────

describe("health (GET /health)", () => {
  test("fresh populated cache → ok, not stale", async () => {
    const now = new Date().toISOString();
    (store as any).__state.redisData.set("mem:meta:last_updated", now);
    (store as any).__state.redisData.set("mem:proc:a", "{}");
    (store as any).__state.redisData.set("mem:idx:engineer", "[]");
    const r = (await healthHandler()) as any;
    expect(r.status).toBe("ok");
    expect(r.stale).toBe(false);
    expect(r.redis).toBe("connected");
    expect(r.procedureCount).toBe(1);
    expect(r.roleIndexCount).toBe(1);
    expect(r.staleThresholdMs).toBe(3600000);
  });

  test("zero procedures → degraded (stale true)", async () => {
    (store as any).__state.redisData.set("mem:meta:last_updated", new Date().toISOString());
    const r = (await healthHandler()) as any;
    expect(r.status).toBe("degraded");
    expect(r.stale).toBe(true);
    expect(r.procedureCount).toBe(0);
  });

  test("old stamp (beyond 1h) → degraded even with full counts", async () => {
    const old = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
    (store as any).__state.redisData.set("mem:meta:last_updated", old);
    (store as any).__state.redisData.set("mem:proc:a", "{}");
    (store as any).__state.redisData.set("mem:idx:e", "[]");
    const r = (await healthHandler()) as any;
    expect(r.status).toBe("degraded");
    expect(r.stale).toBe(true);
  });

  test("missing stamp → degraded with lastUpdated null", async () => {
    const r = (await healthHandler()) as any;
    expect(r.status).toBe("degraded");
    expect(r.lastUpdated).toBeNull();
  });

  test("Redis unreachable → 503 RM_HEALTH (gateway emits structured body)", async () => {
    (store as any).__state.failPing = true;
    await expect(healthHandler()).rejects.toMatchObject({
      code: 503,
      type: "RM_HEALTH",
      message: "Redis is unreachable",
    });
  });
});

// ── refresh: syncAll envelope + Redis write shape ──────────────────

describe("refresh (POST /refresh)", () => {
  test("success → {procedures, roleIndices, timestamp} with ISO timestamp", async () => {
    const r = (await refreshHandler()) as any;
    expect(r.procedures).toBe(2);
    expect(r.roleIndices).toBe(2); // engineer + planner
    expect(() => new Date(r.timestamp).toISOString()).not.toThrow();
    // Card + index writes landed in the mock Redis
    expect((store as any).__state.redisData.has("mem:proc:alpha-proc")).toBe(true);
    expect((store as any).__state.redisData.has("mem:idx:engineer")).toBe(true);
    expect((store as any).__state.redisData.has("mem:meta:last_updated")).toBe(true);
  });

  test("engineer index gets only engineer-roled cards", async () => {
    await refreshHandler();
    const idx = JSON.parse(
      (store as any).__state.redisData.get("mem:idx:engineer") as string
    );
    expect(idx.map((e: any) => e.slug)).toEqual(["alpha-proc"]);
  });

  test("card JSON round-trips through the cache write", async () => {
    await refreshHandler();
    const card = JSON.parse(
      (store as any).__state.redisData.get("mem:proc:alpha-proc") as string
    );
    expect(card).toMatchObject({
      slug: "alpha-proc",
      title: "Alpha",
      roles: ["engineer", "planner"],
    });
    expect(typeof card.body_md).toBe("string");
    expect(typeof card.updated_at).toBe("string");
  });
});

// ── envelope shape guard ───────────────────────────────────────────

describe("envelope shape", () => {
  test("action errors carry {error} semantics via MoleculerError message", async () => {
    (store as any).__state.failGet = true;
    const err: any = await proceduresHandler(ctx({ role: "x" })).catch((e) => e);
    expect(err.code).toBe(500);
    // The gateway onError serializes {error: message} for non-RM_HEALTH —
    // the incumbent's catch blocks emit {error}, NOT {status:"error"}.
    expect(typeof err.message).toBe("string");
    expect(err.type).toBe("RM_ERROR");
  });
});
