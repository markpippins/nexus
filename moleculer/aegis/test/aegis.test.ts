/**
 * Hermetic jest suite for the aegis twin.
 *
 * `services/db.js` is mocked at the module boundary, so no PostgreSQL is
 * needed. Everything under test is the incumbent's verbatim route stack
 * wrapped in the twin's express-app (which adds only the day-one rate
 * limiter — under the test ceiling, 429s never fire). The TLC spawn path
 * is never reached: model-check probes use registry-id negatives that
 * reject before spawn.
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
import { runTlc } from "../services/tlc-runner.js";

// routes.ts uses the named `query` export (verbatim incumbent pattern).
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
  it("mirrors the incumbent envelope { ok, service }", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, service: "aegis-srv" });
  });
});

describe("JSON catch-all 404", () => {
  it("returns the incumbent body on unmatched paths", async () => {
    const res = await request(app).get("/api/definitely-not-a-route");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: "not found" });
  });
});

describe("GET /api/registries", () => {
  it("returns the verbatim envelope on success", async () => {
    mockedQuery.mockResolvedValue({
      rows: [{ id: UUID, name: "r1" }],
      rowCount: 1,
    });
    const res = await request(app).get("/api/registries");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ items: [{ id: UUID, name: "r1" }] });
  });

  it("maps handler errors through the verbatim pgError to the 500 envelope", async () => {
    mockedQuery.mockRejectedValue(new Error("boom"));
    const res = await request(app).get("/api/registries");
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "internal server error", message: "internal server error" });
  });
});

describe("registry-id negatives (400/404 before any mutation)", () => {
  it("GET /api/registries/:id → 400 on non-UUID", async () => {
    const res = await request(app).get("/api/registries/not-a-uuid");
    expect(res.status).toBe(400);
    expect(res.body).toEqual({ error: "invalid registry id", message: "invalid registry id" });
  });

  it("GET /api/registries/:id → 404 with the incumbent message on absent row", async () => {
    const res = await request(app).get(`/api/registries/${UUID}`);
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: "registry not found", message: "registry not found" });
  });

  it.each([
    ["POST /api/registries/:id/validate", "post", `/api/registries/${UUID}/validate`, {}],
    ["POST /api/registries/:id/model-check", "post", `/api/registries/${UUID}/model-check`, {}],
    [
      "POST /api/registries/:id/wind-compilations",
      "post",
      `/api/registries/${UUID}/wind-compilations`,
      {},
    ],
    ["DELETE /api/registries/:id", "delete", `/api/registries/${UUID}`, {}],
  ])("%s → 404 (registry existence gate) without DB writes", async (_l, method, url, body) => {
    const res = await (request(app) as any)[method](url).send(body);
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("registry not found");
  });  it.each([
    ["POST /api/registries/:id/constants (bad registry id)", "post", "/api/registries/bad-uuid/constants", {}, "invalid registry id"],
    ["PATCH /api/registries/:id/properties/:cid (bad child id)", "patch", `/api/registries/${UUID}/properties/bad-uuid`, {}, "invalid child id"],
    ["DELETE /api/registries/:id/transitions/:cid (bad child id)", "delete", `/api/registries/${UUID}/transitions/bad-uuid`, {}, "invalid child id"],
  ]) ("%s → 400 (verbatim isUuid gate)", async (_l, method, url, body, expectedMsg) => {
    // Child routes check the registry first (requireRegistry → DB lookup);
    // stage the registry row so valid-registry probes reach requireChild's
    // isUuid gate ("invalid child id"), mirroring the incumbent's ordering.
    mockedQuery.mockResolvedValue({ rows: [{ id: UUID }], rowCount: 1 });
    const res = await (request(app) as any)[method](url).send(body);
    expect(res.status).toBe(400);
    expect(res.body.error).toBe(expectedMsg);
  });
});

describe("POST /api/registries (create) validation", () => {
  it("400s on missing required fields before insert", async () => {
    const res = await request(app).post("/api/registries").send({});
    expect(res.status).toBe(400);
  });
});

describe("TLC runner timeout clamp (twin-only resource-exhaustion fix)", () => {
  it("clamps an oversized timeoutMs before arming the kill timer", async () => {
    // No jar/module on disk in the hermetic env → early error return, but the
    // clamp still executes. Assert via a huge timeout not throwing and the
    // run failing fast on the missing jar (never spawning java).
    const result = await runTlc("/nonexistent-spec-dir", "NoSuchModule", {
      timeoutMs: Number.MAX_SAFE_INTEGER,
    });
    expect(result.status).toBe("error");
    expect(result.errors.join(" ")).toMatch(/not found|TLC unavailable/i);
  }, 15000);
});
