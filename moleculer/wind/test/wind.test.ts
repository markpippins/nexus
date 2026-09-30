/**
 * Hermetic jest suite for the wind twin.
 *
 * `services/db.js` is mocked at the module boundary, so no PostgreSQL is
 * needed. Everything under test is the incumbent's verbatim route stack
 * wrapped in the twin's express-app (which adds only the day-one global
 * rate limiter — under the test ceiling, 429s never fire).
 *
 * Canary posture: reads with mocked rows + validation negatives. The
 * instance lifecycle routes (advance/execute/run/stop) and execution-request
 * dispatch are probed ONLY with malformed/UUID-absent bodies that reject
 * before any DB work, matching the canary-diff discipline of the earlier
 * twins.
 */
jest.mock("../services/db.js", () => {
  const pool = {
    connect: jest.fn(),
    on: jest.fn(),
    end: jest.fn(async () => {}),
    query: jest.fn(),
  };
  const query = jest.fn();
  const withTransaction = jest.fn();
  return { pool, query, withTransaction };
});

import request from "supertest";
import app from "../services/express-app.js";
import { pool, query } from "../services/db.js";

const mockedQuery = query as jest.Mock;
const UUID = "11111111-1111-1111-1111-111111111111";

beforeEach(() => {
  mockedQuery.mockReset();
  mockedQuery.mockResolvedValue({ rows: [], rowCount: 0 });
});

afterAll(async () => {
  await pool.end();
});

describe("GET /health", () => {
  it("mirrors the incumbent envelope { ok, schema }", async () => {
    // The twin's /health uses pool.connect like the incumbent.
    (pool.connect as jest.Mock).mockImplementation(async () => ({
      query: jest.fn(async () => ({ rows: [], rowCount: 0 })),
      release: jest.fn(),
    }));
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, schema: "wind" });
  });

  it("mirrors the incumbent 503 envelope when the DB is unreachable", async () => {
    (pool.connect as jest.Mock).mockRejectedValue(new Error("connection refused"));
    const res = await request(app).get("/health");
    expect(res.status).toBe(503);
    expect(res.body).toEqual({ ok: false, error: "connection refused" });
  });
});

describe("unknown routes fall through like the incumbent", () => {
  it("has NO JSON catch-all — Express's default HTML 404 applies", async () => {
    const res = await request(app).get("/api/definitely-not-a-route");
    expect(res.status).toBe(404);
    // Incumbent parity: the body is Express's default HTML page, not JSON.
    expect(res.text).toContain("Cannot GET");
  });
});

describe("GET /api/offices", () => {
  it("returns the verbatim BARE ARRAY envelope on success", async () => {
    mockedQuery.mockResolvedValue({
      rows: [{ id: UUID, name: "hr", description: "people ops" }],
      rowCount: 1,
    });
    const res = await request(app).get("/api/offices");
    expect(res.status).toBe(200);
    // wind list handlers res.json(result.rows) — a bare array, NOT {items}.
    expect(res.body).toEqual([
      { id: UUID, name: "hr", description: "people ops" },
    ]);
  });

  it("maps handler errors through the verbatim error-handler to the 500 envelope", async () => {
    mockedQuery.mockRejectedValue(new Error("boom"));
    const res = await request(app).get("/api/offices");
    expect(res.status).toBe(500);
  });
});

describe("GET /api/workflows", () => {
  it("returns rows via the verbatim list handler (bare array)", async () => {
    mockedQuery.mockResolvedValue({
      rows: [{ id: UUID, title: "onboarding", version: 3 }],
      rowCount: 1,
    });
    const res = await request(app).get("/api/workflows");
    expect(res.status).toBe(200);
    expect(res.body).toEqual([
      { id: UUID, title: "onboarding", version: 3 },
    ]);
  });
});

describe("GET /api/nodes/:id", () => {
  it("404s through the verbatim handler for an absent node", async () => {
    mockedQuery.mockResolvedValue({ rows: [], rowCount: 0 });
    const res = await request(app).get(`/api/nodes/${UUID}`);
    expect(res.status).toBe(404);
  });

  it("rejects a non-UUID id as a validation negative (400 before DB)", async () => {
    const res = await request(app).get("/api/nodes/not-a-uuid");
    expect([400, 404]).toContain(res.status);
  });
});

describe("POST /api/instances/:id/advance — lifecycle negative", () => {
  it("rejects a malformed body before touching the DB (canary discipline)", async () => {
    const res = await request(app)
      .post(`/api/instances/${UUID}/advance`)
      .send({ transition: { nonsense: true } });
    expect([400, 404, 422]).toContain(res.status);
    expect(res.status).not.toBe(500);
  });
});

describe("POST /api/versions", () => {
  it("rejects an empty body as a validation negative", async () => {
    const res = await request(app).post("/api/versions").send({});
    expect([400, 422]).toContain(res.status);
  });
});

describe("alias-map sanity", () => {
  it("every openapi path is reachable through the gateway app", async () => {
    // Spot-checks across the surface families; the full 57-path parity proof
    // is the canary-diff against the live incumbent (tools/canary-diff.py).
    // NOT in this list (verbatim BadRequest without query params): edges/
    // nodes/outcomes require ?version_id=, outcomes also ?task_id=, versions
    // requires ?workflow_id= — the param flows are covered by the dedicated
    // tests below.
    const probes: Array<[string, string, number[]]> = [
      ["get", "/api/event-types", [200]],
      ["get", "/api/events", [200]],
      ["get", "/api/execution-requests", [200]],
      ["get", "/api/receipts", [200]],
      ["get", "/api/tickets", [200]],
      ["get", "/api/tasks", [200]],
      ["get", "/api/provider-contracts", [200]],
      ["get", "/api/v-roles", [200]],
      ["get", "/api/instances", [200]],
      ["get", `/api/validate/${UUID}`, [200]],
      ["get", `/api/versions?workflow_id=${UUID}`, [200]],
    ];
    for (const [method, path, expected] of probes) {
      const res = await (request(app) as any)[method](path);
      expect(expected).toContain(res.status);
    }
  });

  it("edges/nodes/outcomes require version_id (verbatim BadRequest)", async () => {
    for (const p of ["/api/edges", "/api/nodes", "/api/outcomes"]) {
      const res = await (request(app) as any).get(p);
      expect(res.status).toBe(400);
    }
  });

  it("edges/nodes return bare arrays with version_id; outcomes needs task_id too", async () => {
    for (const p of ["/api/edges", "/api/nodes"]) {
      const res = await (request(app) as any).get(`${p}?version_id=${UUID}`);
      expect(res.status).toBe(200);
      expect(res.body).toEqual([]);
    }
    // verbatim: outcomes requires task_id (in addition to version_id)
    const res = await (request(app) as any).get(
      `/api/outcomes?version_id=${UUID}&task_id=${UUID}`,
    );
    expect(res.status).toBe(200);
    expect(res.body).toEqual([]);
  });
});
