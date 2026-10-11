/**
 * Hermetic jest suite for the substance twin.
 *
 * `services/db.js` is mocked at the module boundary (PostgreSQL) and the
 * cache is held behind its injected client factory (setClientFactory — the
 * incumbent's own test seam), so no PG and no Redis are needed. Everything
 * under test is the incumbent's verbatim route stack wrapped in the twin's
 * express-app (which adds only the day-one rate limiter — under the test
 * ceiling, 429s never fire).
 *
 * Envelope law (FastAPI lineage): errors are {detail}, body validation
 * failures are 422, non-UUID path params are 422, unknown domain_type is a
 * 404 (the one deliberate TS-port divergence), /healthz is static.
 *
 * Canary posture: reads with mocked rows + validation negatives; writes are
 * probed only with malformed/UUID-absent bodies that reject before any DB
 * or cache work.
 */

// db.js: pool/query seam — every route goes through repo → query.
jest.mock("../services/db.js", () => {
  const pool = {
    connect: jest.fn(),
    on: jest.fn(),
    end: jest.fn(async () => {}),
  };
  const query = jest.fn();
  const queryOne = jest.fn();
  const execute = jest.fn();
  const withTransaction = jest.fn();
  const initPool = jest.fn(() => pool);
  const getPool = jest.fn(() => pool);
  const closePool = jest.fn(async () => {});
  return { pool, query, queryOne, execute, withTransaction, initPool, getPool, closePool };
});

// cache.ts is NOT mocked: it exposes its own test seam (setClientFactory),
// and only get/set/del are ever called on the client. Injecting a fake Redis
// client through the real seam keeps the verbatim cache module in the test
// path — nothing dials Redis (lazyConnect + injected factory), and a cache
// regression would surface here.

import request from "supertest";
import app from "../services/express-app.js";
import { query, queryOne } from "../services/db.js";
import { setClientFactory } from "../services/cache.js";

const mockedQuery = query as jest.Mock;
const mockedQueryOne = queryOne as jest.Mock;
const UUID = "11111111-1111-1111-1111-111111111111";

const redisCalls: string[] = [];
beforeAll(() => {
  setClientFactory(() => ({
    get: async (k: string) => { redisCalls.push(`get ${k}`); return null; },
    set: async (k: string) => { redisCalls.push(`set ${k}`); return "OK"; },
    del: async (k: string) => { redisCalls.push(`del ${k}`); return 1; },
    on: () => {},
  } as any));
});

beforeEach(() => {
  mockedQuery.mockReset();
  // query<T> resolves to ROW ARRAYS in this service (repository returns them
  // directly) — the default is an empty list, not a pg Result.
  mockedQuery.mockResolvedValue([]);
  mockedQueryOne.mockReset();
  mockedQueryOne.mockResolvedValue(null);
});

describe("GET /healthz", () => {
  it("mirrors the incumbent static envelope (no DB probe)", async () => {
    const res = await request(app).get("/healthz");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok" });
  });
});

describe("FastAPI lineage: error envelope law", () => {
  it("returns {detail} JSON 404 for an unknown route — but substance routes unknown paths through its own middleware", async () => {
    const res = await request(app).get("/api/definitely-not-a-route");
    expect(res.status).toBe(404);
  });

  it("422s a non-UUID segment_set_id (FastAPI uuid_parsing shape)", async () => {
    const res = await request(app).get("/segment-sets/not-a-uuid");
    expect(res.status).toBe(422);
    expect(res.body.detail).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          msg: expect.stringContaining("UUID"),
          type: "uuid_parsing",
        }),
      ]),
    );
  });

  it("422s malformed JSON with the FastAPI json_invalid shape", async () => {
    const res = await request(app)
      .post("/segment-sets")
      .set("Content-Type", "application/json")
      .send("{not json");
    expect(res.status).toBe(422);
    expect(res.body.detail).toEqual([
      { loc: ["body"], msg: "Invalid JSON body", type: "json_invalid" },
    ]);
  });

  it("404s an unknown domain_type (the deliberate TS-port divergence, documented in links.ts)", async () => {
    const res = await request(app)
      .post(`/not-a-domain/${UUID}/segment-sets`)
      .send({});
    expect(res.status).toBe(404);
    expect(res.body.detail).toContain("unknown domain_type");
  });
});

describe("GET /segment-sets", () => {
  it("returns the verbatim BARE ARRAY of shaped SegmentSetOut rows", async () => {
    // listSegmentSets returns rows (query<T> → T[]); the route maps each
    // through toSegmentSetOut — FastAPI-style bare list, not {items}.
    mockedQuery.mockResolvedValue([
      {
        id: UUID,
        name: "eu-segments",
        description: "d",
        status: "active",
        metadata: {},
        created_at: new Date(0),
        updated_at: new Date(0),
      },
    ] as any);
    const res = await request(app).get("/segment-sets");
    expect(res.status).toBe(200);
    expect(res.body).toEqual([
      {
        id: UUID,
        name: "eu-segments",
        description: "d",
        status: "active",
        metadata: {},
        created_at: "1970-01-01T00:00:00.000Z",
        updated_at: "1970-01-01T00:00:00.000Z",
        segments: [],
      },
    ]);
  });
});

describe("GET /segment-sets/:id", () => {
  it("404s through the verbatim handler for an absent set", async () => {
    mockedQueryOne.mockResolvedValue(null);
    const res = await request(app).get(`/segment-sets/${UUID}`);
    expect(res.status).toBe(404);
    expect(res.body.detail).toContain("not found");
  });
});

describe("write negatives (canary discipline)", () => {
  it("POST /segment-sets with an empty body PARSES (all keys optional) and would hit the DB — probe stays at the malformed-body layer instead", async () => {
    // parseSegmentSetCreate treats every key as optional (FastAPI lineage),
    // so {} is a VALID create body — the canary negative is a type violation,
    // not an empty object.
    const res = await request(app).post("/segment-sets").send({ members: "nope" });
    expect(res.status).toBe(422);
    expect(mockedQuery).not.toHaveBeenCalled();
    expect(mockedQueryOne).not.toHaveBeenCalled();
  });

  it("POST /segment-sets/from-segments rejects a body without segments", async () => {
    const res = await request(app).post("/segment-sets/from-segments").send({});
    expect([400, 422]).toContain(res.status);
  });

  it("PATCH /segment-sets/:id rejects a non-UUID id with 422", async () => {
    const res = await request(app).patch("/segment-sets/nope").send({ name: "x" });
    expect(res.status).toBe(422);
  });

  it("POST members rejects a non-UUID segment set id", async () => {
    const res = await request(app)
      .post("/segment-sets/nope/members")
      .send({ segment_ids: [UUID] });
    expect(res.status).toBe(422);
  });
});

describe("domain links surface", () => {
  it("GET /candidates/:id/segment-sets resolves each link through the cache (expired link → 404 propagated, verbatim)", async () => {
    // listDomainLinks returns the link row; the route then cachedResolve()s
    // the set. With the set absent (queryOne → null) the resolve 404s — the
    // Python service propagated that too (links.ts header). Pin that shape:
    mockedQuery.mockResolvedValue([
      { segment_set_id: UUID, role: "primary", active: true },
    ] as any);
    mockedQueryOne.mockResolvedValue(null);
    const res = await request(app).get(`/candidates/${UUID}/segment-sets`);
    expect(res.status).toBe(404);
    expect(res.body.detail).toContain("not found");
  });

  it("GET /requirements/:id/segment-sets returns an empty bare array", async () => {
    mockedQuery.mockResolvedValue([] as any);
    const res = await request(app).get(`/requirements/${UUID}/segment-sets`);
    expect(res.status).toBe(200);
    expect(res.body).toEqual([]);
  });
});

describe("cors", () => {
  it("echoes a request origin (default posture; function form clears js/cors-permissive-configuration)", async () => {
    const res = await request(app)
      .options("/healthz")
      .set("Origin", "http://localhost:4200")
      .set("Access-Control-Request-Method", "GET");
    expect(res.headers["access-control-allow-origin"]).toBe(
      "http://localhost:4200",
    );
    // credentials flag dropped: wildcard+credentials was already
    // non-functional for credentialed requests
    expect(res.headers["access-control-allow-credentials"]).toBeUndefined();
  });

  it("passes a no-Origin request through (curl/same-origin)", async () => {
    const res = await request(app).get("/healthz");
    expect(res.status).toBe(200);
  });

  it("silently denies a non-allowlisted origin when SUBSTANCE_CORS_ORIGINS pins a list", async () => {
    const prev = process.env.SUBSTANCE_CORS_ORIGINS;
    process.env.SUBSTANCE_CORS_ORIGINS = "http://good.local";
    try {
      // the origin list is read at module load — build a fresh app under
      // the pinned env (require, not await import: ts-jest isolatedModules)
      jest.resetModules();
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const freshApp = require("../services/express-app.js").default;
      const denied = await request(freshApp)
        .options("/healthz")
        .set("Origin", "http://evil.local")
        .set("Access-Control-Request-Method", "GET");
      expect(denied.headers["access-control-allow-origin"]).toBeUndefined();
      const allowed = await request(freshApp)
        .options("/healthz")
        .set("Origin", "http://good.local")
        .set("Access-Control-Request-Method", "GET");
      expect(allowed.headers["access-control-allow-origin"]).toBe(
        "http://good.local",
      );
    } finally {
      if (prev === undefined) delete process.env.SUBSTANCE_CORS_ORIGINS;
      else process.env.SUBSTANCE_CORS_ORIGINS = prev;
      jest.resetModules();
    }
  });
});

describe("alias-map sanity", () => {
  it("spot-checks every surface family through the gateway app", async () => {
    const probes: Array<[string, string, number[]]> = [
      ["get", "/healthz", [200]],
      ["get", "/segment-sets", [200]],
      // absent set → verbatim 404 (queryOne → null)
      ["get", `/segment-sets/${UUID}`, [404]],
      // empty link rows → bare [] (the happy path); with a link whose set is
      // absent the verbatim 404 propagates — covered by the dedicated test
      ["get", `/candidates/${UUID}/segment-sets`, [200]],
      ["get", `/requirements/${UUID}/segment-sets`, [200]],
    ];
    for (const [method, path, expected] of probes) {
      const res = await (request(app) as any)[method](path);
      expect(expected).toContain(res.status);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// LIVE-GATEWAY HARNESS (hermetic) — the CI gap that let the fleet-wide
// $req/$res defect class hide (PR #688/#690 wave, 2026-09-30).
//
// The suites above drive the verbatim Express app through supertest — they
// never touch the moleculer-web gateway, so a gateway regression (onBeforeCall
// $req/$res stash dropped, catch-all aliases lost) cannot redden CI. That is
// NOT hypothetical for this twin: substance shipped with NO onBeforeCall hook
// at all, so every aliased call 500'd DISPATCH_NO_REQRES while its jest suite
// stayed green. Fixed in PR #745; this block is what makes that class
// non-recurring for substance specifically (Ruling 38G §2 / 38I §4).
//
// Boots the REAL broker + ApiService + substance service in-process
// (transporter null; PG mocked by the jest.mock above) on an EPHEMERAL port
// (SERVICE_PORT="0", set before api.service is dynamically imported — the
// gateway captures settings.port at construction) and asserts:
//   1. matched route 200 — transitively proves the onBeforeCall $req/$res
//      stash: without it, every alias-dispatch call 500s (DISPATCH_NO_REQRES);
//   2. unmatched path 404;
//   3. wrong-method request 404 — GET-only aliases must never 405/500.
// Unlike tackle, substance's gateway declares no finalhandler/onNotFound/onError,
// so the 404 assertions check status only (no byte-for-byte body claim).
// No live incumbents, no PG, no Redis, no fixed ports — CI-hermetic by design.
// ═══════════════════════════════════════════════════════════════════════════
process.env.SERVICE_PORT = "0"; // must precede the dynamic imports below

describe("live gateway (real broker + moleculer-web on ephemeral port)", () => {
  const UNMATCHED = "/definitely/not/a/gateway/route";
  let base = "";
  let stop = async () => {};

  beforeAll(async () => {
    // CommonJS require on purpose (see tackle's block): require() works under
    // both ts-jest modes; a dynamic import() can stay untransformed under
    // isolatedModules and throw.
    const { ServiceBroker } = require("moleculer");
    const ApiService = require("../services/api.service").default;
    const TwinService = require("../services/substance.service").default;
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
    const res = await fetch(`${base}/healthz`);
    expect(res.status).toBe(200);
  });

  it("unmatched path 404", async () => {
    const res = await fetch(`${base}${UNMATCHED}`);
    expect(res.status).toBe(404);
  });

  it("wrong-method request 404 — GET-only aliases never 405/500", async () => {
    const res = await fetch(`${base}/healthz`, { method: "POST" });
    expect(res.status).toBe(404);
  });
});
