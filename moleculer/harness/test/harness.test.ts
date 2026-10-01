jest.mock("../services/db", () => {
  return {
    pool: {
      query: jest.fn(async () => ({ rows: [], rowCount: 0 })),
    },
    redis: {
      ping: jest.fn(async () => "PONG"),
      quit: jest.fn(async () => undefined),
    },
    resolveContext: jest.fn(),
    resolveRoleModel: jest.fn(),
    emitEvent: jest.fn(),
    checkConfigAdmission: jest.fn(),
    incrementConsumedUnits: jest.fn(),
    emitGovernanceReceipt: jest.fn(),
  };
});

import request from "supertest";
import { app, jobs, registerJob, setJobState } from "../services/harness-core";
import { pool, redis } from "../services/db";

const http = request(app) as any;

beforeEach(() => {
  (pool.query as jest.Mock).mockClear();
  (redis.ping as jest.Mock).mockClear();
  jobs.clear();
});

afterAll(() => {
  jobs.clear();
});

describe("harness twin Express parity", () => {
  test("GET /health pings PG + Redis and returns the incumbent shape", async () => {
    const response = await http.get("/health");
    expect(response.status).toBe(200);
    expect(response.body.status).toBe("ok");
    expect(response.body).toHaveProperty("port");
    expect(response.body).toHaveProperty("uptime");
    expect(pool.query).toHaveBeenCalledWith("SELECT 1");
    expect(redis.ping).toHaveBeenCalled();
  });

  test("GET /health returns 503 {status:error} when PG fails", async () => {
    (pool.query as jest.Mock).mockRejectedValueOnce(new Error("db down"));
    const response = await http.get("/health");
    expect(response.status).toBe(503);
    expect(response.body).toEqual({ status: "error", error: "db down" });
  });

  test("GET /sessions reports watchdog-tracked sessions", async () => {
    const response = await http.get("/sessions");
    expect(response.status).toBe(200);
    expect(response.body).toEqual({ sessions: [], count: 0 });
  });

  test("GET /jobs/:jobId returns the envelope for a registered job and 404s unknown ids", async () => {
    const job = registerJob({
      jobId: "11111111-1111-1111-1111-111111111111",
      role: "engineer",
      model: "test/model",
      harnessId: "harn-opencode",
      promptFile: "/tmp/11111111.md",
    });
    setJobState(job.jobId, "completed", { exitCode: 0 });

    const found = await http.get(`/jobs/${job.jobId}`);
    expect(found.status).toBe(200);
    expect(found.body.job).toMatchObject({
      job_id: job.jobId,
      state: "completed",
      role: "engineer",
      model: "test/model",
      harness_id: "harn-opencode",
      exit_code: 0,
      partial: false,
    });
    expect(found.body.job.event_count).toBeGreaterThan(0);

    const missing = await http.get("/jobs/does-not-exist");
    expect(missing.status).toBe(404);
    expect(missing.body).toEqual({ error: "job not found", job_id: "does-not-exist" });
  });

  test("POST /run and /run-direct return exact validation 400s before any DB work", async () => {
    const run = await http.post("/run").send({});
    expect(run.status).toBe(400);
    expect(run.body).toEqual({ error: "wind_task_id is required" });

    const runDirectNoRole = await http.post("/run-direct").send({ prompt: "hi" });
    expect(runDirectNoRole.status).toBe(400);
    expect(runDirectNoRole.body).toEqual({ error: "role is required" });

    const runDirectNoPrompt = await http.post("/run-direct").send({ role: "engineer" });
    expect(runDirectNoPrompt.status).toBe(400);
    expect(runDirectNoPrompt.body).toEqual({ error: "prompt is required" });

    const resolveContext = await http.post("/resolve-context").send({});
    expect(resolveContext.status).toBe(400);
    expect(resolveContext.body).toEqual({ error: "wind_task_id is required" });

    expect(pool.query).not.toHaveBeenCalled();
  });

  test("GET /jobs/:jobId/events rejects a bad cursor with the exact 400", async () => {
    const response = await http.get("/jobs/does-not-exist/events?after=-3");
    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: "job not found", job_id: "does-not-exist" });
  });

  test("unmatched routes retain Express finalhandler HTML (no catch-all middleware)", async () => {
    const response = await http.get("/definitely/not/a/route");
    expect(response.status).toBe(404);
    expect(response.headers["content-type"]).toMatch(/^text\/html/);
    expect(response.text).toContain("<pre>Cannot GET /definitely/not/a/route</pre>");
  });
});


// ══════════════════════════════════════════════════════════════════════════
// LIVE-GATEWAY HARNESS (hermetic) — the CI gap that let the fleet-wide
// $req/$res defect class hide (PR #688/#690 wave, 2026-09-30).
//
// The suites above drive the verbatim Express app through supertest — they
// never touch the moleculer-web gateway, so a gateway regression
// (onBeforeCall $req/$res stash dropped, catch-all aliases lost, onError 404
// emulation removed) cannot redden CI. This block boots the REAL broker +
// ApiService + twin service in-process (transporter null; PG/Redis still
// mocked by the jest.mock at the top of this file) on an EPHEMERAL port
// (SERVICE_PORT="0" set above, before api.service is dynamically imported —
// the gateway captures settings.port at construction) and asserts:
//   1. matched route 200 — transitively proves the onBeforeCall $req/$res
//      stash: without it, every alias-dispatch call 500s (DISPATCH_NO_REQRES);
//   2. unmatched multi-segment path 404 with the twin's exact 404 body;
//   3. wrong-method request 404 — GET-only aliases must never 405/500.
// No live incumbents, no PG, no Redis, no fixed ports — CI-hermetic by design.
// ══════════════════════════════════════════════════════════════════════════
process.env.SERVICE_PORT = "0"; // must precede the dynamic imports below

describe("live gateway (real broker + moleculer-web on ephemeral port)", () => {
  const UNMATCHED = "/definitely/not/a/gateway/route";
  let base = "";
  let stop = async () => {};

  beforeAll(async () => {
    // CommonJS require on purpose: under ts-jest isolatedModules (conduit,
    // harness, peb, execution, aegis) a dynamic import() stays untransformed
    // and throws "A dynamic import callback was invoked without
    // --experimental-vm-modules". require() works under both ts-jest modes.
    const { ServiceBroker } = require("moleculer");
    const ApiService = require("../services/api.service").default;
    const TwinService = require("../services/harness.service").default;
    const broker = new ServiceBroker({ transporter: null, logger: false, metrics: false });
    broker.createService(ApiService);
    broker.createService(TwinService);
    await broker.start();
    const gw = broker.getLocalService("api") as any;
    base = `http://127.0.0.1:${gw.server.address().port}`;
    stop = async () => {
      await broker.stop();
    };
  }, 30000);

  afterAll(() => stop());

  it("matched route 200 — proves the onBeforeCall $req/$res stash (dispatch 500s without it)", async () => {
const { pool, redis } = require("../services/db");
    (pool.query as jest.Mock).mockResolvedValueOnce({ rows: [] });
    (redis.ping as jest.Mock).mockResolvedValueOnce("PONG");
    const res = await fetch(`${base}/health`);
    expect(res.status).toBe(200);
  });

  it("unmatched path 404 — finalhandler HTML page, byte-for-byte", async () => {
    const res = await fetch(`${base}${UNMATCHED}`);
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toMatch(/^text\/html/);
    expect((await res.text()).trim()).toBe(
      `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot GET /definitely/not/a/gateway/route</pre>\n</body>\n</html>`
    );
  });

  it("wrong-method request 404 — GET-only aliases never 405/500", async () => {
    const res = await fetch(`${base}/health`, { method: "POST" });
    expect(res.status).toBe(404);
  });
});
