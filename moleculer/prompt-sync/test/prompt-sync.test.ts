/**
 * Hermetic parity tests for the prompt-sync twin.
 *
 * SCOPE — pure semantics that must match typescript/tackle-prompt-sync-srv:
 *   - health: {status:"ok", lastUpdated, uptime, namespace:"prompt:"} shape;
 *     the exact structured 503 {status:"error", message} when Redis fails
 *   - prompts: missing role → [] with 200 (NOT 404 — incumbent quirk),
 *     index JSON round-trip
 *   - prompt: exact 404 string "Prompt not found", card round-trip
 *   - tasks: missing role → [], index round-trip
 *   - refresh: syncAll result envelope; PG read failure → 500 {error}
 *   - envelopes: {error} shape (NOT kernel's {status:"error"})
 *   - static: gateway alias map covers every incumbent route (method+path
 *     set from the incumbent's index.ts)
 *
 * NOT COVERED HERE — the live route surface. That is enforced by the apidocs
 * drift gate (MOLECULER_MIRRORS → openapi.yaml) and the live canary diff.
 *
 * The ./store module is mocked wholesale: these tests drive the extracted
 * handlers without Redis, PG, or a broker.
 */

jest.mock("../services/store", () => {
  const state = {
    redisData: new Map<string, string>(),
    failGet: false,
    failPipelineExec: false,
    pgError: null as string | null,
  };
  const makeRedis = () => ({
    get: jest.fn(async (key: string) => {
      if (state.failGet) throw new Error("GET failed: connection dropped");
      return state.redisData.get(key) ?? null;
    }),
    set: jest.fn(async (key: string, val: string) => {
      state.redisData.set(key, val);
      return "OK";
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
          if (state.failPipelineExec) {
            return ops.map(() => [new Error("pipeline write failed"), null]);
          }
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
    KEY_PREFIX: "prompt:",
    PROC_KEY: (role: string, slug: string) => `prompt:proc:${role}::${slug}`,
    IDX_KEY: (role: string) => `prompt:idx:${role}`,
    META_UPDATED_KEY: "prompt:meta:last_updated",
    TASK_IDX_KEY: (role: string) => `task:idx:${role}`,
    initRedis: jest.fn(() => redisInstance),
    getRedis: jest.fn(() => redisInstance),
    closeRedis: jest.fn(async () => undefined),
    initDb: jest.fn(),
    getDb: jest.fn(),
    fetchLatestPrompts: jest.fn(async () => {
      if (state.pgError) throw new Error(state.pgError);
      const now = new Date().toISOString();
      return [
        {
          id: "p1", role: "engineer", slug: "opencode-persona", version: 3,
          title: "Engineer Persona", body_md: "# Engineer",
          parameter_schema: {}, tags: ["persona"],
          created_at: now, updated_at: now,
        },
        {
          id: "p2", role: "planner", slug: "opencode-persona", version: 2,
          title: "Planner Persona", body_md: "# Planner",
          parameter_schema: {}, tags: ["persona"],
          created_at: now, updated_at: now,
        },
      ];
    }),
    fetchActiveTasks: jest.fn(async () => {
      if (state.pgError) throw new Error(state.pgError);
      const now = new Date().toISOString();
      return [
        {
          id: "t1", role: "engineer", task_slug: "dispatch",
          scope: "implementation", acceptance_criteria: ["tests pass"],
          prompt_id: "p1", active: true,
          created_at: now, updated_at: now,
        },
      ];
    }),
    __state: state,
    __reset: () => {
      state.redisData = new Map();
      state.failGet = false;
      state.failPipelineExec = false;
      state.pgError = null;
      redisInstance = makeRedis();
    },
  };
});

import fs from "fs";
import path from "path";
import { Errors } from "moleculer";
import {
  healthHandler,
  promptsHandler,
  promptHandler,
  tasksHandler,
  refreshHandler,
} from "../services/handlers";

// The mocked module exposes __state/__reset test hooks (hoisted factory).
const store: any = jest.requireMock("../services/store");

beforeEach(() => {
  store.__reset();
});

// ── 1. health ──────────────────────────────────────────────────────

describe("health parity", () => {
  test("ok shape: {status, lastUpdated, uptime, namespace}", async () => {
    store.__state.redisData.set("prompt:meta:last_updated", "2026-09-24T15:00:00.000Z");
    const r: any = await healthHandler();
    expect(r.status).toBe("ok");
    expect(r.lastUpdated).toBe("2026-09-24T15:00:00.000Z");
    expect(typeof r.uptime).toBe("number");
    expect(r.namespace).toBe("prompt:");
  });

  test("lastUpdated null when stamp missing (200, not 503)", async () => {
    const r: any = await healthHandler();
    expect(r.status).toBe("ok");
    expect(r.lastUpdated).toBeNull();
  });

  test("Redis failure → 503 tagged PS_HEALTH", async () => {
    store.__state.failGet = true;
    const err: any = await healthHandler().catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(503);
    expect(err.type).toBe("PS_HEALTH");
  });
});

// ── 2. prompts (role index) ────────────────────────────────────────

describe("prompts/:role parity", () => {
  test("missing role → [] with 200 (NOT 404 — incumbent quirk)", async () => {
    const r = await promptsHandler({ params: { role: "no-such-role" } });
    expect(r).toEqual([]);
  });

  test("index JSON round-trip", async () => {
    const entries = [
      { slug: "opencode-persona", title: "Engineer Persona", version: 3, tags: ["persona"], updated_at: "2026-09-24T15:00:00.000Z" },
    ];
    store.__state.redisData.set("prompt:idx:engineer", JSON.stringify(entries));
    const r = await promptsHandler({ params: { role: "engineer" } });
    expect(r).toEqual(entries);
  });

  test("Redis failure → 500 {error} envelope", async () => {
    store.__state.failGet = true;
    const err: any = await promptsHandler({ params: { role: "engineer" } }).catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(500);
    expect(err.type).toBe("PS_ERROR");
  });
});

// ── 3. prompt (single card) ────────────────────────────────────────

describe("prompt/:role/:slug parity", () => {
  test("miss → 404 exact string 'Prompt not found'", async () => {
    const err: any = await promptHandler({ params: { role: "engineer", slug: "nope" } }).catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(404);
    expect(err.message).toBe("Prompt not found");
  });

  test("card round-trip", async () => {
    const card = {
      id: "p1", role: "engineer", slug: "opencode-persona", version: 3,
      title: "Engineer Persona", body_md: "# Engineer",
      parameter_schema: {}, tags: ["persona"],
      created_at: "2026-09-24T15:00:00.000Z", updated_at: "2026-09-24T15:00:00.000Z",
    };
    store.__state.redisData.set("prompt:proc:engineer::opencode-persona", JSON.stringify(card));
    const r = await promptHandler({ params: { role: "engineer", slug: "opencode-persona" } });
    expect(r).toEqual(card);
  });

  test("corrupt JSON → 500 {error} (incumbent catch shape)", async () => {
    store.__state.redisData.set("prompt:proc:engineer::bad", "{not json");
    const err: any = await promptHandler({ params: { role: "engineer", slug: "bad" } }).catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(500);
  });
});

// ── 4. tasks (role index) ──────────────────────────────────────────

describe("tasks/:role parity", () => {
  test("missing role → [] with 200", async () => {
    const r = await tasksHandler({ params: { role: "no-such-role" } });
    expect(r).toEqual([]);
  });

  test("task index round-trip", async () => {
    const entries = [
      { task_slug: "dispatch", scope: "implementation", acceptance_criteria: ["tests pass"], prompt_id: "p1", prompt_slug: "opencode-persona", updated_at: "2026-09-24T15:00:00.000Z" },
    ];
    store.__state.redisData.set("task:idx:engineer", JSON.stringify(entries));
    const r = await tasksHandler({ params: { role: "engineer" } });
    expect(r).toEqual(entries);
  });
});

// ── 5. refresh (syncAll) ───────────────────────────────────────────

describe("POST /refresh parity", () => {
  test("syncAll writes cards + indices + stamp, returns envelope", async () => {
    const r: any = await refreshHandler();
    expect(r.prompts).toBe(2);
    expect(r.rolePromptIndices).toBe(2);
    expect(r.tasks).toBe(1);
    expect(r.roleTaskIndices).toBe(1);
    expect(typeof r.timestamp).toBe("string");

    // Post-state: exact key shapes (shared key space with the incumbent)
    expect(store.__state.redisData.has("prompt:proc:engineer::opencode-persona")).toBe(true);
    expect(store.__state.redisData.has("prompt:proc:planner::opencode-persona")).toBe(true);
    expect(store.__state.redisData.has("prompt:idx:engineer")).toBe(true);
    expect(store.__state.redisData.has("prompt:idx:planner")).toBe(true);
    expect(store.__state.redisData.has("task:idx:engineer")).toBe(true);
    expect(store.__state.redisData.has("prompt:meta:last_updated")).toBe(true);

    // Task index resolves prompt_id → prompt_slug at sync time
    const taskIdx = JSON.parse(store.__state.redisData.get("task:idx:engineer"));
    expect(taskIdx[0].prompt_slug).toBe("opencode-persona");
  });

  test("PG read failure → 500 {error} envelope", async () => {
    store.__state.pgError = "connection refused";
    const err: any = await refreshHandler().catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(500);
    expect(err.message).toContain("connection refused");
  });

  test("pipeline write failure → 500 (no fake success)", async () => {
    store.__state.failPipelineExec = true;
    const err: any = await refreshHandler().catch((e) => e);
    expect(err).toBeInstanceOf(Errors.MoleculerError);
    expect(err.code).toBe(500);
    expect(err.message).toContain("pipeline write failed");
  });
});

// ── 6. static route parity (alias map vs incumbent index.ts) ──────

describe("alias-map parity (static)", () => {
  test("gateway aliases cover every incumbent route", () => {
    const twinApi = path.resolve(__dirname, "../services/api.service.ts");
    const incumbent = path.resolve(
      __dirname, "../../..", "typescript/tackle-prompt-sync-srv/src/index.ts"
    );

    const ALIAS_RE = /['"](GET|POST|PUT|PATCH|DELETE)\s+(\/[^'"]*)['"]\s*:\s*['"]([^'"]+)['"]/;
    const apiSrc = fs.readFileSync(twinApi, "utf8");
    const aliases = new Set<string>();
    let inAliases = false;
    for (const line of apiSrc.split("\n")) {
      const stripped = line.trim();
      if (stripped.startsWith("//") || stripped.startsWith("*")) continue;
      if (inAliases) {
        if (stripped.startsWith("}")) { inAliases = false; continue; }
        const m = stripped.match(ALIAS_RE);
        if (m) aliases.add(`${m[1]} ${m[2]}`);
        continue;
      }
      if (/\baliases\s*:\s*\{/.test(line)) inAliases = true;
    }

    const incumbentSrc = fs.readFileSync(incumbent, "utf8");
    const re = /\.(get|post|put|patch|delete)\(\s*"([^"]*)"/g;
    const incumbentRoutes = new Set<string>();
    let m: RegExpExecArray | null;
    while ((m = re.exec(incumbentSrc))) {
      incumbentRoutes.add(`${m[1].toUpperCase()} ${m[2]}`);
    }

    expect([...aliases].sort()).toEqual([...incumbentRoutes].sort());
    expect(aliases.size).toBe(5);
  });
});
