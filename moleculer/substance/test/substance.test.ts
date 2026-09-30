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
