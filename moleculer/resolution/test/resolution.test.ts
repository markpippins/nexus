import request from "supertest";
import { Pool } from "pg";

/**
 * Hermetic contract tests for the resolution twin (:4171).
 *
 * Hermeticity: the pg Pool is mocked at the module boundary (the verbatim
 * routes talk to it exclusively through getDb().query). No Redis, no
 * heartbeat, no migrations (resolution-srv ships none — schema is
 * producer-owned).
 *
 * Canary discipline (Decision 32 Ruling 3): reads + boundary negatives
 * ONLY. The write family is exercised exclusively via POST-must-405
 * probes — the twin never creates or mutates resolution.* rows.
 *
 * What is pinned:
 *   - /health degraded envelope (DB up, missing table → missingTables)
 *   - /api/meta registry envelope {service, schema, readOnly, tables[]}
 *   - per-table list envelope {table, count, limit, offset, items}
 *   - per-table row fetch + 404 not_found {error, table, id}
 *   - unknown table → 404 {error:"unknown_table"} WITHOUT DB work
 *     (tableOf() rejects before any query)
 *   - THE 405 BOUNDARY (Ruling 3): POST/PATCH/DELETE on a known table
 *     → 405 {error:"read_only", table, method}; on an unknown table →
 *     404 unknown_table; and NOTHING reaches the DB
 *   - PG error-code mapping: 42P01 → 404 unknown_table, 22P02 → 400
 *     invalid_id (verbatim mapQueryError contract)
 */

jest.mock("pg", () => {
  const mPool = { query: jest.fn(), connect: jest.fn(), end: jest.fn(), on: jest.fn() };
  return {
    Pool: jest.fn(() => mPool),
    // db.ts registers timestamp type parsers at module load.
    types: {
      setTypeParser: jest.fn(),
      builtins: { TIMESTAMPTZ: 1184, TIMESTAMP: 1114 },
    },
  };
});

process.env.RESOLUTION_PG_DSN = "postgresql://twin:twin@localhost:5432/twin";

import app from "../services/express-app.js";

const poolMock = (Pool as unknown as jest.Mock).mock.results[0].value as {
  query: jest.Mock;
  end: jest.Mock;
};

beforeEach(() => {
  poolMock.query.mockReset();
  poolMock.query.mockResolvedValue({ rows: [], rowCount: 0 });
});

afterEach(() => {
  jest.restoreAllMocks();
});

const KNOWN_TABLE = "concept"; // from tables.ts (48 registered)

describe("health envelope (DB probe + degraded missing-tables)", () => {
  it("GET /health → 200 {service,status:'ok',database:'connected',missingTables:[]}", async () => {
    poolMock.query.mockResolvedValue({ rows: [{ "?column?": 1 }], rowCount: 1 });
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.service).toBe("resolution-srv");
    expect(res.body.status).toBe("ok");
    expect(res.body.database).toBe("connected");
    expect(res.body.missingTables).toEqual([]);
  });

  it("missing table → 200 degraded with missingTables named", async () => {
    // First query = SELECT 1 (connectivity ok); health probe of the first
    // HEALTH_CHECK table throws (42P01) → degraded, named.
    const err: any = new Error('relation "resolution.entity" does not exist');
    err.code = "42P01";
    poolMock.query.mockImplementation((sql: string) => {
      if (sql.startsWith("SELECT 1 FROM")) return Promise.reject(err);
      return Promise.resolve({ rows: [{ "?column?": 1 }], rowCount: 1 });
    });
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("degraded");
    expect(res.body.missingTables.length).toBeGreaterThan(0);
  });
});

describe("registry-driven per-table surface (literal-alias behavior)", () => {
  it("GET /api/:table → {table,count,limit,offset,items} envelope", async () => {
    poolMock.query.mockResolvedValueOnce({
      rows: [{ id: "r1" }, { id: "r2" }],
      rowCount: 2,
    });
    const res = await request(app).get(`/api/${KNOWN_TABLE}`);
    expect(res.status).toBe(200);
    expect(res.body.table).toBe(KNOWN_TABLE);
    expect(res.body.count).toBe(2);
    expect(res.body.limit).toBe(100);
    expect(res.body.offset).toBe(0);
    expect(res.body.items).toHaveLength(2);
  });

  it("GET /api/:table/:id found → bare row (verbatim shape)", async () => {
    poolMock.query.mockResolvedValueOnce({ rows: [{ id: "r1", label: "x" }], rowCount: 1 });
    const res = await request(app).get(`/api/${KNOWN_TABLE}/r1`);
    expect(res.status).toBe(200);
    expect(res.body.id).toBe("r1");
    expect(res.body.label).toBe("x");
  });

  it("GET /api/:table/:id unknown → 404 {error:'not_found', table, id}", async () => {
    const res = await request(app).get(`/api/${KNOWN_TABLE}/nope`);
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("not_found");
    expect(res.body.table).toBe(KNOWN_TABLE);
    expect(res.body.id).toBe("nope");
  });

  it("unknown table → 404 {error:'unknown_table'} WITHOUT touching the DB", async () => {
    const res = await request(app).get("/api/__no_such_table_zzz__");
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("unknown_table");
    expect(res.body.table).toBe("__no_such_table_zzz__");
    expect(poolMock.query).not.toHaveBeenCalled();
  });

  it("PG 42P01 (table dropped mid-flight) → 404 unknown_table via mapQueryError", async () => {
    const err: any = new Error("relation does not exist");
    err.code = "42P01";
    poolMock.query.mockRejectedValueOnce(err);
    const res = await request(app).get(`/api/${KNOWN_TABLE}`);
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("unknown_table");
  });

  it("PG 22P02 (bad id representation) → 400 invalid_id via mapQueryError", async () => {
    const err: any = new Error("invalid input syntax for type uuid");
    err.code = "22P02";
    poolMock.query.mockRejectedValueOnce(err);
    const res = await request(app).get(`/api/${KNOWN_TABLE}/zzz`);
    expect(res.status).toBe(400);
    expect(res.body.error).toBe("invalid_id");
  });
});

describe("THE 405 BOUNDARY (Decision 32 Ruling 3 — canary write family)", () => {
  it("POST known table → 405 {error:'read_only', table, method:'POST'}, no DB work", async () => {
    const res = await request(app).post(`/api/${KNOWN_TABLE}`).send({});
    expect(res.status).toBe(405);
    expect(res.body.error).toBe("read_only");
    expect(res.body.table).toBe(KNOWN_TABLE);
    expect(res.body.method).toBe("POST");
    expect(poolMock.query).not.toHaveBeenCalled();
  });

  it("PATCH and DELETE known table → 405 read_only", async () => {
    const p = await request(app).patch(`/api/${KNOWN_TABLE}/r1`).send({});
    expect(p.status).toBe(405);
    expect(p.body.method).toBe("PATCH");
    const d = await request(app).delete(`/api/${KNOWN_TABLE}/r1`);
    expect(d.status).toBe(405);
    expect(d.body.method).toBe("DELETE");
  });

  it("POST unknown table → 404 unknown_table (boundary knows the registry)", async () => {
    const res = await request(app).post("/api/__no_such_table_zzz__").send({});
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("unknown_table");
    expect(poolMock.query).not.toHaveBeenCalled();
  });
});

describe("/api/meta (registry overview)", () => {
  it("→ {service, schema:'resolution', readOnly:true, tables[]}", async () => {
    // meta issues two count subqueries per table via one query call each;
    // return a count-shaped row for every call.
    poolMock.query.mockImplementation(() =>
      Promise.resolve({ rows: [{ active: 0, total: 0 }], rowCount: 1 }),
    );
    const res = await request(app).get("/api/meta");
    expect(res.status).toBe(200);
    expect(res.body.service).toBe("resolution-srv");
    expect(res.body.schema).toBe("resolution");
    expect(res.body.readOnly).toBe(true);
    expect(Array.isArray(res.body.tables)).toBe(true);
    expect(res.body.tables[0].table).toBeDefined();
  });
});
