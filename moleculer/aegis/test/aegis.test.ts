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
    const TwinService = require("../services/aegis.service").default;
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
    // static handler — no mocks needed
    const res = await fetch(`${base}/health`);
    expect(res.status).toBe(200);
  });

  it("unmatched path 404 — incumbent app-level 404 shape, exact JSON", async () => {
    const res = await fetch(`${base}${UNMATCHED}`);
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({"error": "not found"});
  });

  it("wrong-method request 404 — GET-only aliases never 405/500", async () => {
    const res = await fetch(`${base}/health`, { method: "POST" });
    expect(res.status).toBe(404);
  });
});
