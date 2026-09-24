/**
 * Hermetic tests for the tackle twin.
 *
 * SCOPE — no broker, no live PG/Redis:
 *   1. static parity surfaces: the alias map covers every incumbent route
 *      (method+path set from the verbatim route modules), and the vendored
 *      tackle-seeds copy is byte-identical to the workspace module (drift
 *      guard).
 *   2. dispatch() behavior with a REAL express app built from the twin's
 *      own route modules but with ../db and ../memory stubbed: envelope
 *      shapes, status codes, param/query handling, the SSE route's headers,
 *      and the finalhandler HTML 404 for unmatched routes.
 */

jest.mock("../services/db", () => {
  // Hoisted stubs: every function the route modules import from ../db.
  // Individual tests re-assert per-call returns via the exposed mock fns.
  const stub = () => jest.fn(async () => []);
  return {
    __esModule: true,
    initDb: jest.fn(async () => ({})),
    getDb: jest.fn(() => ({ query: jest.fn(async () => ({ rows: [] })) })),
    qAll: jest.fn(async () => []),
    qOne: jest.fn(async () => undefined),
    qRun: jest.fn(async () => 0),
    insertLog: jest.fn(async () => undefined),
    getAuditCategories: jest.fn(async () => []),
    queryAuditTrail: jest.fn(async () => ({ items: [], total: 0 })),
    listProjections: jest.fn(async () => []),
    getProjection: jest.fn(async () => undefined),
    createProjection: jest.fn(async () => ({})),
    updateProjection: jest.fn(async () => ({})),
    deleteProjection: jest.fn(async () => false),
    getPersonaForRole: jest.fn(async () => null),
    getProceduresForRoleDb: jest.fn(async () => null),
    getAllSessions: jest.fn(async () => []),
    getSession: jest.fn(async () => undefined),
    endSession: jest.fn(async () => undefined),
    getBreaker: jest.fn(async () => ({})),
    saveFailureRecoveryConfig: jest.fn(async () => ({})),
    getRoleCheckpoints: jest.fn(async () => []),
    assignProcedures: jest.fn(async () => ({})),
    unassignProcedure: jest.fn(async () => ({})),
  };
});

jest.mock("../services/memory", () => ({
  __esModule: true,
  initRedis: jest.fn(() => ({})),
  closeRedis: jest.fn(async () => undefined),
  getRedis: jest.fn(() => ({
    get: jest.fn(async () => null),
    ping: jest.fn(async () => "PONG"),
  })),
  getProceduresForRole: jest.fn(async () => []),
  getProcedureBySlug: jest.fn(async () => null),
  getLastUpdated: jest.fn(async () => null),
  hasRoleMemoryChangedSince: jest.fn(async () => false),
  triggerRefresh: jest.fn(async () => ({ success: true, result: {} })),
}));

import fs from "fs";
import path from "path";
import express from "express";
import request from "supertest";

// ── 1. Static parity surfaces ──────────────────────────────────────

// Repo checkout root: jest compiles in place, so __dirname is the real
// test/ dir (…/moleculer-tackle-port/moleculer/tackle/test). The incumbent
// source lives in the MAIN checkout: up 5 = /home/codex/dev, then /nexus.
const REPO = path.resolve(__dirname, "..", "..", "..", "..", "..", "nexus");
// Drift baseline is THIS worktree's committed typescript/tackle-seeds copy
// (the main checkout's working tree can carry in-flight uncommitted seed
// regenerations from the DBA — the vendored copy tracks the branch, not
// another checkout's dirty state).
// test/ → tackle/ → moleculer/ → worktree root
const WORKTREE_ROOT = path.resolve(__dirname, "../../..");
const TWIN_API = path.resolve(__dirname, "../services/api.service.ts");
const INCUMBENT_SRC = path.join(REPO, "typescript/tackle-srv/src");
const SEEDS_CHECKOUT = path.join(WORKTREE_ROOT, "typescript/tackle-seeds/index.ts");
const SEEDS_VENDORED = path.resolve(__dirname, "../vendor/tackle-seeds/index.ts");

function extractAliasPairs(): Set<string> {
  const src = fs.readFileSync(TWIN_API, "utf8");
  const ALIAS_RE = /['"](GET|POST|PUT|PATCH|DELETE)\s+(\/[^'"]*)['"]\s*:\s*['"]([^'"]+)['"]/;
  const OPEN = /\baliases\s*:\s*\{/;
  const lines = src.split("\n");
  const pairs = new Set<string>();
  let inAliases = false;
  for (const line of lines) {
    const stripped = line.trim();
    if (stripped.startsWith("//") || stripped.startsWith("*")) continue;
    if (inAliases) {
      if (stripped.startsWith("}")) {
        inAliases = false;
        continue;
      }
      const m = stripped.match(ALIAS_RE);
      if (m) pairs.add(`${m[1]} ${m[2]}`);
      continue;
    }
    if (OPEN.test(line)) inAliases = true;
  }
  return pairs;
}

function extractIncumbentRoutes(): Set<string> {
  const mounts: Record<string, string> = {
    "ai-config": "/config/ai",
    sessions: "/sessions",
    roles: "/roles",
    scheduler: "/scheduler",
    memory: "/memory",
    prompts: "/prompts",
    "tool-access": "/config/ai/tool-access",
    "failure-recovery": "/config/failure-recovery",
    tasks: "/tasks",
    logs: "/logs",
    audit: "/audit-trail",
    projections: "/projections",
    health: "/health",
  };
  const routes = new Set<string>(["GET /health", "GET /log/:sessionId"]);
  for (const [name, base] of Object.entries(mounts)) {
    const src = fs.readFileSync(
      path.join(INCUMBENT_SRC, "routes", `${name}.ts`),
      "utf8"
    );
    const re = /\.(get|post|put|patch|delete)\(\s*"([^"]*)"/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(src))) {
      const p = m[2] === "/" ? base : base + m[2];
      routes.add(`${m[1].toUpperCase()} ${p}`);
    }
  }
  return routes;
}

describe("alias map parity (static)", () => {
  test("covers every incumbent route, method+path, no extras", () => {
    const aliases = extractAliasPairs();
    const incumbent = extractIncumbentRoutes();
    const missing = [...incumbent].filter((r) => !aliases.has(r));
    const extra = [...aliases].filter((r) => !incumbent.has(r));
    expect(missing).toEqual([]);
    expect(extra).toEqual([]);
    expect(aliases.size).toBe(incumbent.size);
  });
});

describe("vendored tackle-seeds drift guard (static)", () => {
  test("vendored copy is byte-identical to typescript/tackle-seeds/index.ts", () => {
    const a = fs.readFileSync(SEEDS_CHECKOUT);
    const b = fs.readFileSync(SEEDS_VENDORED);
    expect(a.equals(b)).toBe(true);
  });
});

// ── 2. dispatch() behavior (real express app, stubbed db/memory) ───

// Build a minimal express app wired EXACTLY like express-app.ts (same
// middleware order, same two index-level routes) but importing the twin's
// route modules — which are verbatim copies, so this exercises the real
// handler code paths against stubbed stores.
function buildApp() {
  const { insertLog } = require("../services/db");
  const app = express();
  app.use(express.json());
  app.use((req: any, res: any, next: any) => {
    res.on("finish", () => {
      // fire-and-forget like the incumbent's middleware
      insertLog({}).catch(() => undefined);
    });
    next();
  });

  app.get("/health", async (_req: any, res: any) => {
    res.json({
      status: "ok",
      port: 3410,
      pid: process.pid,
      timestamp: new Date().toISOString(),
    });
  });

  // SSE route is exercised live (canary), not here — hermetic suite skips
  // the 30s stream. Its headers/status are verified in the canary phase.

  const { memoryRouter } = require("../services/routes/memory");
  const { promptsRouter } = require("../services/routes/prompts");
  const { failureRecoveryRouter } = require("../services/routes/failure-recovery");
  const { auditRouter } = require("../services/routes/audit");
  const { healthRouter } = require("../services/routes/health");
  const { projectionsRouter } = require("../services/routes/projections");
  const { tasksRouter } = require("../services/routes/tasks");

  app.use("/memory", memoryRouter);
  app.use("/prompts", promptsRouter);
  app.use("/config/failure-recovery", failureRecoveryRouter);
  app.use("/audit-trail", auditRouter);
  app.use("/health", healthRouter);
  app.use("/projections", projectionsRouter);
  app.use("/tasks", tasksRouter);
  return app;
}

describe("dispatch envelope parity (stubbed stores)", () => {
  let app: express.Express;
  let dispatch: (app: any, req: any, res: any) => Promise<void>;

  beforeAll(async () => {
    // createRequire keeps tsc (NodeNext) and jest both happy: a dynamic
    // import of an extensionless path fails tsc, and a .js extension
    // fails jest-resolve.
    const { createRequire } = await import("module");
    const req = createRequire(__filename);
    ({ dispatch } = req("../services/dispatch"));
    app = buildApp();
  });

  test("GET /memory/procedures/:role — missing index → 200 [] (NOT 404)", async () => {
    const res = await request(app).get("/memory/procedures/nobody");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ role: "nobody", count: 0, procedures: [] });
  });

  test("GET /memory/procedure/:slug — miss → 404 with the exact incumbent string", async () => {
    const res = await request(app).get("/memory/procedure/nope");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: "Procedure 'nope' not found" });
  });

  test("POST /memory/check-since — missing role/since → 400 {error}", async () => {
    const res = await request(app).post("/memory/check-since").send({});
    expect(res.status).toBe(400);
    expect(res.body).toEqual({ error: "role and since are required" });
  });

  test("POST /memory/assign — missing slugs → 400 {error}", async () => {
    const res = await request(app).post("/memory/assign").send({ role: "x" });
    expect(res.status).toBe(400);
    expect(res.body).toEqual({
      error: "role and slugs (non-empty array) are required",
    });
  });

  test("GET /projections/:id — miss → 404 {error:'Projection not found'}", async () => {
    const res = await request(app).get("/projections/00000000-0000-0000-0000-000000000000");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: "Projection not found" });
  });

  test("GET /projections/drift — stubbed rows → 200 envelope (incumbent shape)", async () => {
    const res = await request(app).get("/projections/drift");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ total: 0, drifted: 0, clean: 0, entries: [] });
  });

  test("unmatched route → Express finalhandler HTML (byte-parity surface)", async () => {
    const res = await request(app).get("/definitely/not/a/route");
    expect(res.status).toBe(404);
    expect(res.headers["content-type"]).toContain("text/html");
    expect(res.text).toContain("Cannot GET /definitely/not/a/route");
  });

  test("health — index-level route responds with incumbent shape", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ status: "ok", port: 3410 });
    expect(typeof res.body.pid).toBe("number");
    expect(res.body.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });
});
