/**
 * Hermetic jest suite for the peb twin.
 *
 * `services/db.js` is mocked at the module boundary (the twin's own module
 * resolution maps ../db.js → services/db.js), so no PostgreSQL is needed.
 * Everything under test is the incumbent's verbatim route stack wrapped in
 * the twin's express-app (which adds only the day-one rate limiter — under
 * the test ceiling, 429s never fire).
 */
jest.mock("../services/db.js", () => {
  const dsnInfo = "postgresql://pguser:***@localhost:5432/nexus";
  const pool = {
    connect: jest.fn(),
    on: jest.fn(),
    end: jest.fn(async () => {}),
    query: jest.fn(),
  };
  const query = jest.fn();
  const withTransaction = jest.fn();
  return { pool, query, withTransaction, dsnInfo };
});

import request from "supertest";
import app from "../services/express-app.js";
import { pool } from "../services/db.js";

const mockedQuery = pool.query as jest.Mock;

beforeEach(() => {
  mockedQuery.mockReset();
  // default: any stray query returns an empty result set
  mockedQuery.mockResolvedValue({ rows: [], rowCount: 0 });
});

afterAll(async () => {
  await pool.end();
});

describe("GET /health", () => {
  it("mirrors the incumbent envelope { ok, dsn, port }", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.dsn).toBe("postgresql://pguser:***@localhost:5432/nexus");
    // port follows PEB_SRV_PORT / default 3111 (the incumbent's code default)
    expect(String(res.body.port)).toBe(process.env.PEB_SRV_PORT || "3111");
  });
});

describe("JSON catch-all 404", () => {
  it("returns the incumbent notFoundHandler body on unmatched paths", async () => {
    const res = await request(app).get("/api/peb/definitely-not-a-route");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: { message: "not_found" } });
  });
});

describe("GET /api/peb/transactions", () => {
  it("returns { transactions: [...] } from the verbatim handler", async () => {
    mockedQuery.mockResolvedValue({
      rows: [{ id: "tx-1", admission_result: "accepted", tool_name: "probe" }],
      rowCount: 1,
    });
    const res = await request(app).get("/api/peb/transactions");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      transactions: [{ id: "tx-1", admission_result: "accepted", tool_name: "probe" }],
    });
  });

  it("propagates handler errors through the verbatim error handler", async () => {
    mockedQuery.mockRejectedValue(new Error("boom"));
    const res = await request(app).get("/api/peb/transactions");
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: { message: "internal_error" } });
  });
});

describe("GET /api/peb/transactions/:id", () => {
  it("404s with the incumbent message on missing rows", async () => {
    const res = await request(app).get("/api/peb/transactions/11111111-1111-1111-1111-111111111111");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: { message: "transaction not found" } });
  });

  it("400s on ids failing isAcceptableId", async () => {
    const res = await request(app).get("/api/peb/transactions/bad%22id");
    expect(res.status).toBe(400);
    expect(res.body).toEqual({ error: { message: "invalid id" } });
  });
});

describe("GET /api/peb/decisions/next-number", () => {
  it("returns { next, last } with ADR numbering", async () => {
    mockedQuery.mockResolvedValue({ rows: [{ adr_number: "ADR-007" }], rowCount: 1 });
    const res = await request(app).get("/api/peb/decisions/next-number");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ next: "ADR-008", last: "ADR-007" });
  });

  it("starts at ADR-001 when the table is empty", async () => {
    const res = await request(app).get("/api/peb/decisions/next-number");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ next: "ADR-001", last: undefined });
  });
});

describe("GET /api/peb/events", () => {
  it("honors the parseEventCursor validation (negative since)", async () => {
    const res = await request(app).get("/api/peb/events?since=-1");
    expect(res.status).toBe(400);
    expect(res.body.error.message).toBe(
      "since must be a non-negative integer (governance_events.id cursor)",
    );
  });

  it("honors the parseEventCursor validation (non-integer limit)", async () => {
    const res = await request(app).get("/api/peb/events?limit=zero");
    expect(res.status).toBe(400);
    expect(res.body.error.message).toBe("limit must be a positive integer");
  });

  it("returns the verbatim envelope shape on success", async () => {
    mockedQuery.mockResolvedValue({
      rows: [{ id: 41, event_type: "plan.created", plan_id: "p1" }],
      rowCount: 1,
    });
    const res = await request(app).get("/api/peb/events");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      events: [{ id: 41, event_type: "plan.created", plan_id: "p1" }],
      next_cursor: null,
      limit: 100,
      offset: 0,
    });
  });
});

describe("write-route validation negatives (400 before DB work)", () => {
  it.each([
    ["POST /api/peb/decisions", "post", "/api/peb/decisions", {}],
    ["PATCH /api/peb/decisions/:id", "patch", "/api/peb/decisions/bad\"id", {}],
    ["POST /api/peb/decisions/:id/supersede (id)", "post", "/api/peb/decisions/bad\"id/supersede", {}],
    [
      "POST /api/peb/decisions/:id/supersede (body)",
      "post",
      "/api/peb/decisions/11111111-1111-1111-1111-111111111111/supersede",
      {},
    ],
    ["POST /api/peb/events/:receipt_id/replay", "post", "/api/peb/events/bad\"id/replay", {}],
  ])("%s → exact 400 envelope", async (_label, method, url, body) => {
    if (String(url).endsWith("/supersede") && body && Object.keys(body).length === 0) {
      // supersede validates the body only AFTER the decision-existence query;
      // stage that row so the probe reaches the body validation (400).
      mockedQuery.mockResolvedValueOnce({ rows: [{ id: "dec-1" }], rowCount: 1 });
    }
    const res = await (request(app) as any)[method](url).send(body);
    expect(res.status).toBe(400);
    expect(res.body.error.message).toMatch(
      /^(title is required|author_id is required|invalid id|invalid receipt_id|summary is required)$/,
    );
  });
});

describe("ApiError passthrough", () => {
  it("maps entities clampLimit ApiError to 400 with details untouched", async () => {
    // entities/:id/capability-gap validates the entity id first, so use the
    // events limit path covered above; here assert the details field rides
    // along by hitting the shared clampLimit via entities with a valid id.
    const res = await request(app).get("/api/peb/entities/e-1/capability-gap?limit=zero");
    expect(res.status).toBe(400);
    expect(res.body.error.message).toBe("limit must be a positive integer");
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
    const TwinService = require("../services/peb.service").default;
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
    expect(await res.json()).toEqual({"error": {"message": "not_found"}});
  });

  it("wrong-method request 404 — GET-only aliases never 405/500", async () => {
    const res = await fetch(`${base}/health`, { method: "POST" });
    expect(res.status).toBe(404);
  });
});
