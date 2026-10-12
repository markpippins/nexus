jest.mock("../services/db/client", () => ({
  query: jest.fn(),
  queryOne: jest.fn(),
  closePool: jest.fn(async () => undefined),
  pool: {},
  PG_SCHEMA: "conduit",
  VISION_SCHEMA: "vision",
  PEB_SCHEMA: "peb",
  TACKLE_SCHEMA: "tackle",
}));

import request from "supertest";
import app from "../services/express-app";
import { query, queryOne } from "../services/db/client";

const queryMock = query as jest.MockedFunction<typeof query>;
const queryOneMock = queryOne as jest.MockedFunction<typeof queryOne>;
const http = request(app) as any;

beforeEach(() => {
  queryMock.mockReset();
  queryOneMock.mockReset();
});

describe("conduit twin Express parity", () => {
  test("GET / preserves the incumbent service descriptor", async () => {
    const response = await http.get("/");
    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({
      name: "conduit-srv",
      version: "1.0.0",
      port: 3104,
      source: "conduit/vision/peb/tackle PostgreSQL schemas",
    });
    expect(response.body.endpoints).toHaveLength(17);
  });

  test("GET /health preserves the structured success and 503 bodies", async () => {
    queryMock.mockResolvedValueOnce([{ ok: 1 }] as any);
    const ok = await http.get("/health");
    expect(ok.status).toBe(200);
    expect(ok.body).toMatchObject({ status: "ok", port: 3104, db: "up" });

    queryMock.mockRejectedValueOnce(new Error("database unavailable"));
    const failed = await http.get("/health");
    expect(failed.status).toBe(503);
    expect(failed.body).toEqual({ status: "error", error: "database unavailable" });
  });

  test("GET /workflows formats active sessions and preserves the 500 envelope", async () => {
    queryMock.mockResolvedValueOnce([
      {
        id: "session-1",
        agent_role: "engineer",
        start_iso: "2026-09-24T00:00:00Z",
        end_iso: null,
        is_running: 1,
        plans_processed: '["123"]',
        pid: 42,
      },
      { id: "old", agent_role: "planner", is_running: 0, plans_processed: "[]" },
    ] as any);
    const response = await http.get("/workflows");
    expect(response.status).toBe(200);
    expect(response.body.counts).toEqual({ running: 1, completed: 0, failed: 0, cancelled: 0, total: 1 });
    expect(response.body.workflows[0]).toMatchObject({
      workflowId: "plan-123-engineer",
      runId: "session-1",
      planId: "123",
      role: "engineer",
    });

    queryMock.mockRejectedValueOnce(new Error("workflow query failed"));
    const failed = await http.get("/workflows");
    expect(failed.status).toBe(500);
    expect(failed.body).toEqual({ error: "workflow query failed" });
  });

  test("preserves validation envelopes for tokens, lineage, receipts, and session logs", async () => {
    const responses = await Promise.all([
      http.get("/tokens/role/not-a-role"),
      http.get("/tokens/plan/bad%2Fid"),
      http.get("/tickets/lineage/bad%2Fid"),
      http.get("/vision/receipts"),
      http.get("/log/bad%2Fid"),
    ]);
    expect(responses.map((r) => r.status)).toEqual([400, 400, 400, 400, 400]);
    expect(responses[0].body).toEqual({ error: "Invalid role: not-a-role" });
    expect(responses[1].body).toEqual({ error: "Invalid plan ID" });
    expect(responses[2].body).toEqual({ error: "Invalid plan ID" });
    expect(responses[3].body).toEqual({ ok: false, error: "Missing required query: planId" });
    expect(responses[4].body).toEqual({ error: "Invalid session ID" });
  });

  test("preserves work-request and projection-drift 404 envelopes", async () => {
    queryOneMock.mockResolvedValueOnce(undefined);
    const workRequest = await http.get("/vision/work-requests/missing");
    expect(workRequest.status).toBe(404);
    expect(workRequest.body).toEqual({ ok: false, error: "Not found" });

    queryOneMock.mockResolvedValueOnce(undefined).mockResolvedValueOnce(undefined);
    const drift = await http.get("/wr/missing/projection-drift");
    expect(drift.status).toBe(404);
    expect(drift.body).toEqual({ ok: false, error: "WorkRequest missing not found" });
  });

  test("preserves governance error envelopes", async () => {
    queryMock.mockRejectedValueOnce(new Error("governance unavailable"));
    const response = await http.get("/governance/events?limit=5");
    expect(response.status).toBe(500);
    expect(response.body).toEqual({ ok: false, error: "governance unavailable" });
  });

  test("unmatched routes retain Express finalhandler HTML", async () => {
    const response = await http.get("/not-a-route");
    expect(response.status).toBe(404);
    expect(response.headers["content-type"]).toMatch(/^text\/html/);
    expect(response.text).toContain("<pre>Cannot GET /not-a-route</pre>");
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
    const TwinService = require("../services/conduit.service").default;
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
const { query } = require("../services/db/client");
    (query as jest.Mock).mockResolvedValueOnce([{ ok: 1 }]);
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
