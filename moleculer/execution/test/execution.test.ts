jest.mock("../services/db", () => ({
  pool: { query: jest.fn() },
  closePool: jest.fn(async () => undefined),
  default: { query: jest.fn() },
}));

import request from "supertest";
import app from "../services/express-app";
import { pool } from "../services/db";

const queryMock = pool.query as any;
const http = request(app) as any;

const UUID = "11111111-1111-1111-1111-111111111111";

beforeEach(() => {
  queryMock.mockReset();
});

describe("execution twin Express parity", () => {
  test("GET /health preserves the two-level health shape and 503 body", async () => {
    queryMock.mockResolvedValueOnce({
      rows: [{ requests: "416", leases: "411", attempts: "417", receipts: "1965" }],
    } as any);
    const ok = await http.get("/health");
    expect(ok.status).toBe(200);
    expect(ok.body).toEqual({
      status: "ok",
      db: true,
      schema: "execution",
      counts: { requests: "416", leases: "411", attempts: "417", receipts: "1965" },
    });

    queryMock.mockRejectedValueOnce(new Error("db down"));
    const failed = await http.get("/health");
    expect(failed.status).toBe(503);
    expect(failed.body).toEqual({ status: "error", db: false, message: "db down" });
  });

  test("GET /api/execution/metrics snapshots the registry shape", async () => {
    const response = await http.get("/api/execution/metrics");
    expect(response.status).toBe(200);
    expect(Object.keys(response.body).sort()).toEqual(["counters", "generatedAt", "latencies"]);
  });

  test("GET /api/execution/requests paginates and strips full_count", async () => {
    queryMock.mockResolvedValueOnce({
      rows: [
        { id: "a", full_count: "2" },
        { id: "b", full_count: "2" },
      ],
    } as any);
    const response = await http.get("/api/execution/requests?limit=2&offset=0&status=READY");
    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      total: 2,
      limit: 2,
      offset: 0,
      items: [{ id: "a" }, { id: "b" }],
    });
    const [[sql, params]] = queryMock.mock.calls;
    expect(sql).toContain("FROM requests");
    expect(params).toEqual(["READY", 2, 0]);
  });

  test("validation misses return exact 400/404 envelopes", async () => {
    const bad = await http.get(`/api/execution/requests/not-a-uuid/state`);
    expect(bad.status).toBe(400);
    expect(bad.body).toEqual({ error: "id must be a UUID" });

    queryMock.mockResolvedValueOnce({ rows: [], rowCount: 0 } as any);
    const miss = await http.get(`/api/execution/requests/${UUID}/state`);
    expect(miss.status).toBe(404);
    expect(miss.body).toEqual({ error: "request not found" });

    queryMock.mockResolvedValueOnce({ rows: [], rowCount: 0 } as any);
    const lease = await http.get(`/api/execution/leases/${UUID}/lifecycle`);
    expect(lease.status).toBe(404);
    expect(lease.body).toEqual({ error: "lease not found" });

    queryMock.mockResolvedValueOnce({ rows: [], rowCount: 0 } as any);
    const receipt = await http.get(`/api/execution/receipts/${UUID}/pipeline-origin`);
    expect(receipt.status).toBe(404);
    expect(receipt.body).toEqual({ error: "execution.receipt not found" });

    const witnessed = await http.get("/api/execution/witnessed-runs");
    expect(witnessed.status).toBe(400);
    expect(witnessed.body).toEqual({ error: "workflow_instance_id and node_id are required" });

    queryMock.mockResolvedValueOnce({ rows: [], rowCount: 0 } as any);
    const witnessedMiss = await http.get(
      "/api/execution/witnessed-runs?workflow_instance_id=wf&node_id=n1"
    );
    expect(witnessedMiss.status).toBe(404);
    expect(witnessedMiss.body).toEqual({ error: "witnessed run not found" });
  });

  test("query failures surface the incumbent 500 {error} envelope", async () => {
    queryMock.mockRejectedValueOnce(new Error("relation does not exist"));
    const response = await http.get("/api/execution/leases/stale");
    expect(response.status).toBe(500);
    expect(response.body).toEqual({ error: "relation does not exist" });
  });

  test("unmatched routes retain the read-only JSON catch-all (not HTML)", async () => {
    const response = await http.get("/definitely/not/a/route");
    expect(response.status).toBe(404);
    expect(response.headers["content-type"]).toMatch(/application\/json/);
    expect(response.body).toEqual({
      error: "not_found",
      hint: "execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog.",
    });
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
    const TwinService = require("../services/execution.service").default;
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
const { pool } = require("../services/db");
    (pool.query as jest.Mock).mockResolvedValueOnce({ rows: [{ requests: "0", leases: "0", attempts: "0", receipts: "0" }] });
    const res = await fetch(`${base}/health`);
    expect(res.status).toBe(200);
  });

  it("unmatched path 404 — incumbent app-level 404 shape, exact JSON", async () => {
    const res = await fetch(`${base}${UNMATCHED}`);
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({"error": "not_found", "hint": "execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog."});
  });

  it("wrong-method request 404 — GET-only aliases never 405/500", async () => {
    const res = await fetch(`${base}/health`, { method: "POST" });
    expect(res.status).toBe(404);
  });
});
