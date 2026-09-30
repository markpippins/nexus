import request from "supertest";
import { Pool } from "pg";

/**
 * Hermetic contract tests for the assembly twin (:4107).
 *
 * Hermeticity: db.js's pool is mocked at the pg module boundary (all
 * routes reach the DB exclusively through db.js's `query`/`pool`), and
 * global fetch is mocked for BOTH proxy layers (substance-proxy.js and
 * utils/fetchNebula.js are the only network surfaces). No Redis, no
 * migrations, no heartbeat.
 *
 * Canary discipline: reads + validation negatives ONLY. Write probes are
 * malformed-body negatives that reject before any DB work (the forums
 * handlers validate title/body/postedById before the INSERT).
 *
 * What is pinned:
 *   - /health {ok:true} (static) vs /api/health {status:"healthy"}
 *   - forums validation negatives → AppError {error} shapes
 *   - THE PROXY CHAIN:
 *     · segment-sets reads → SUBSTANCE_BASE_URL (:3115) with
 *       substanceToCamel normalization and the {items,total,limit,offset}
 *       envelope; substance 404 → NotFoundError → 404 {error}
 *     · nebula-domain reads → NEBULA_SRV_URL (:3101) /api-prefixed URL
 *       building with snakeToCamel projection (utils/fetchNebula.js)
 *   - mount-order doctrine: /api/segment-sets/:id must NOT shadow the
 *     concrete domain paths or other family mounts
 *   - the custom zlib gzip middleware (GET + gzip Accept-Encoding →
 *     gzip-encoded body)
 */

jest.mock("pg", () => {
  const mPool = {
    query: jest.fn(),
    connect: jest.fn(),
    end: jest.fn(),
    on: jest.fn(),
  };
  return { Pool: jest.fn(() => mPool) };
});

// db.js builds a DSN from env before importing; keep it deterministic.
process.env.ASSEMBLY_PG_DSN = "postgresql://twin:twin@localhost:5432/twin";

import app from "../services/express-app.js";

const poolMock = (Pool as unknown as jest.Mock).mock.results[0].value as {
  query: jest.Mock;
  connect: jest.Mock;
  end: jest.Mock;
};

(global as any).fetch = jest.fn();
const fetchMock = global.fetch as jest.Mock;

function okResponse(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

beforeEach(() => {
  poolMock.query.mockReset();
  poolMock.connect.mockReset();
  fetchMock.mockReset();
  poolMock.query.mockResolvedValue({ rows: [], rowCount: 0 });
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("health envelopes (two DIFFERENT shapes, both pinned)", () => {
  it("GET /health → 200 {ok:true} (static, no DB)", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });

  it("GET /api/health → 200 {status:'healthy'} (routes/health.js)", async () => {
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "healthy" });
  });
});

describe("forums validation negatives (canary-safe: reject before DB)", () => {
  it("POST /api/forums/:slug/threads without title/body → 400 {error}", async () => {
    const res = await request(app)
      .post("/api/forums/port-map/threads")
      .send({ postedById: "11111111-1111-1111-1111-111111111111" });
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("Title and body are required");
    expect(poolMock.query).not.toHaveBeenCalled();
  });

  it("POST /api/forums/by-id/:forumId/threads without postedById → 400 before DB", async () => {
    const res = await request(app)
      .post("/api/forums/by-id/11111111-1111-1111-1111-111111111111/threads")
      .send({ title: "t", body: "b" });
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("postedById is required");
    expect(poolMock.query).not.toHaveBeenCalled();
  });
});

describe("substance-proxy coupling (segment-set evidence reads)", () => {
  it("GET /api/segment-sets → proxies to substance :3115, camelCases, wraps {items,total,limit,offset}", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse([{ segment_set_id: "ss1", display_name: "Alpha" }]),
    );
    const res = await request(app).get("/api/segment-sets");
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:3115/segment-sets?limit=200&offset=0",
    );
    expect(res.body.items[0].segmentSetId).toBe("ss1");
    expect(res.body.items[0].displayName).toBe("Alpha");
    expect(res.body.total).toBe(1);
    expect(res.body.limit).toBe(200);
  });

  it("substance 404 → NotFoundError → 404 {error:'Not found'}", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => "nope",
    });
    const res = await request(app).get(
      "/api/segment-sets/0b000000-0000-0000-0000-000000000001",
    );
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Not found");
  });

  it("substance unreachable → AppError 502 {error}", async () => {
    fetchMock.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    const res = await request(app).get("/api/segment-sets");
    expect(res.status).toBe(502);
    expect(res.body.error).toContain("substance unreachable");
  });

  it("GET /api/candidates/:id/segment-sets → proxies to substance /candidates/:id/segment-sets", async () => {
    fetchMock.mockResolvedValueOnce(okResponse([{ segment_set_id: "ss2" }]));
    const res = await request(app).get(
      "/api/candidates/0c000000-0000-0000-0000-000000000001/segment-sets",
    );
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:3115/candidates/0c000000-0000-0000-0000-000000000001/segment-sets",
    );
    expect(res.body.items ?? res.body[0]).toBeDefined();
  });
});

describe("nebula-proxy coupling (domain reads via utils/fetchNebula.js)", () => {
  it("GET /api/candidates → nebula :3101/api/harvest-candidates with snakeToCamel projection", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse({
        items: [
          { id: "d1", harvest_source: "seed", title: "T", created_at: "2026-09-30T00:00:00Z" },
        ],
        total: 1,
        page: 1,
        pageSize: 100,
      }),
    );
    const res = await request(app).get("/api/candidates");
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:3101/api/harvest-candidates?page=1&pageSize=100",
    );
    expect(res.body.items[0].harvestSource).toBe("seed");
    expect(res.body.items[0].created_at).toBeUndefined();
    expect(res.body.total).toBe(1);
  });

  it("nebula 500 → Error → errorHandler 500 {error:'Internal server error'}", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "boom",
    });
    const res = await request(app).get("/api/candidates");
    expect(res.status).toBe(500);
    expect(res.body.error).toBe("Internal server error");
  });
});

describe("mount-order doctrine (segment-sets mounted LAST must not shadow)", () => {
  it("GET /api/forums is served by the forums router, not caught by /:id", async () => {
    poolMock.query.mockResolvedValueOnce({ rows: [], rowCount: 0 });
    const res = await request(app).get("/api/forums");
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls.length).toBe(0); // forums hits the DB, not nebula
  });
});

describe("gzip middleware (custom zlib, GET-only)", () => {
  it("GET with gzip Accept-Encoding → Content-Encoding: gzip and a body supertest decodes", async () => {
    fetchMock.mockResolvedValueOnce(okResponse([]));
    const res = await request(app)
      .get("/api/segment-sets")
      .set("accept-encoding", "gzip");
    expect(res.headers["content-encoding"]).toBe("gzip");
    expect(res.headers.vary).toContain("Accept-Encoding");
    expect(res.status).toBe(200);
  });

  it("POST is never gzipped (GET-only middleware)", async () => {
    const res = await request(app)
      .post("/api/forums/x/threads")
      .set("accept-encoding", "gzip")
      .send({});
    expect(res.headers["content-encoding"]).toBeUndefined();
    expect(res.status).toBe(400);
  });
});
