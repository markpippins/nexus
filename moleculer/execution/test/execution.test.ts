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
