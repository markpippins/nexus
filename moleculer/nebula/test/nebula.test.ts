import request from "supertest";
import { Pool } from "pg";
import { requirementCompileUrl } from "../src/routes.js";

/**
 * Hermetic contract tests for the nebula twin (:4101).
 *
 * Hermeticity: the pg Pool is mocked at the module boundary (the verbatim
 * routes.ts talks to it exclusively through pool.query), and global fetch
 * is mocked for the verbatim substance-proxy (the only network surface the
 * twin touches besides the DB). No Redis is initialized (lazy-connect;
 * nothing in these paths calls initRedis). No migrations ever run.
 *
 * Canary discipline: reads + validation negatives ONLY. Write probes are
 * body-parser negatives that reject before any DB work.
 *
 * What is pinned:
 *   - health envelope {status:"ok", db:true} (and the 503 DB-down variant)
 *   - Paged/list envelopes for DB-backed reads (camelCaseRow projection)
 *   - 404 propagation for unknown agent records
 *   - 500 error envelope {error: message} on query failure
 *   - THE SUBSTANCE COUPLING: segment-set reads go through substance-proxy
 *     to SUBSTANCE_BASE_URL (default :3115; the substance twin :4115 serves
 *     the same contract), with snake_case→camelCase normalization, the
 *     {items,total,limit,offset} list envelope, 404 propagation
 *     ("substance 404" → 404) and transport-failure → 502 mapping.
 *   - malformed-JSON body-parser negative (400, no DB touch)
 */

jest.mock("pg", () => {
  // One dispatch point: the pool and every checked-out client share `query`,
  // so a transaction test observes exactly the statements the route runs.
  const mPool: any = { query: jest.fn(), end: jest.fn() };
  mPool.connect = jest.fn(async () => ({ query: mPool.query, release: jest.fn() }));
  return { Pool: jest.fn(() => mPool) };
});

// app import instantiates the twin pool via the mocked constructor.
// eslint-disable-next-line @typescript-eslint/no-var-requires
import app from "../services/express-app.js";

const poolMock = (Pool as unknown as jest.Mock).mock.results[0].value as {
  query: jest.Mock;
  end: jest.Mock;
};

// substance-proxy uses global fetch (Node's native undici fetch); replace it
// with a jest mock for the whole suite (requests fire at handler time).
(global as any).fetch = jest.fn();
const fetchMock = global.fetch as jest.Mock;

function agentRecordRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "0a000000-0000-0000-0000-000000000001",
    record_type: "report",
    role: "engineer",
    model: "codebuff/space-bunny-alpha",
    title: "t",
    source_path: null,
    tags: ["a"],
    content: "hello world",
    content_length: 11,
    system_id: null,
    subsystem_id: null,
    feature_id: null,
    plan_ref: null,
    created_at: new Date("2026-09-30T00:00:00Z"),
    recorded_on_dt: new Date("2026-09-30T00:00:00Z"),
    level: 2,
    visibility_scope: "all",
    ...overrides,
  };
}

beforeEach(() => {
  poolMock.query.mockReset();
  fetchMock.mockReset();
  // Default: a query that returns no rows (404 paths); individual tests override.
  poolMock.query.mockResolvedValue({ rows: [], rowCount: 0 });
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("health (contract: /health and /api/health, DB probe)", () => {
  it("GET /api/health → 200 {status:'ok', db:true}", async () => {
    poolMock.query.mockResolvedValueOnce({ rows: [{ ok: 1 }], rowCount: 1 });
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok", db: true });
  });

  it("GET /health (unprefixed alias) → same envelope", async () => {
    poolMock.query.mockResolvedValueOnce({ rows: [{ ok: 1 }], rowCount: 1 });
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok", db: true });
  });

  it("DB down → 503 {status:'error', message}", async () => {
    poolMock.query.mockRejectedValueOnce(new Error("connection refused"));
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(503);
    expect(res.body.status).toBe("error");
    expect(res.body.message).toContain("connection refused");
  });
});

describe("agent-records (DB-backed read, verbatim projection)", () => {
  it("GET /api/agent-records/:id unknown → 404 {error}", async () => {
    const res = await request(app).get(
      "/api/agent-records/0a000000-0000-0000-0000-000000000099",
    );
    expect(res.status).toBe(404);
    expect(res.body.error).toBeDefined();
  });

  it("GET /api/agent-records/:id found → camelCase row shape", async () => {
    poolMock.query.mockResolvedValueOnce({
      rows: [agentRecordRow()],
      rowCount: 1,
    });
    const res = await request(app).get(
      "/api/agent-records/0a000000-0000-0000-0000-000000000001",
    );
    expect(res.status).toBe(200);
    expect(res.body.id).toBe("0a000000-0000-0000-0000-000000000001");
    expect(res.body.recordType).toBe("report");
    expect(res.body.contentLength).toBe(11);
    expect(res.body.record_type).toBeUndefined();
  });

  it("query failure → 400 {error} (verbatim first-registration catch shape)", async () => {
    poolMock.query.mockRejectedValueOnce(new Error("boom"));
    const res = await request(app).get(
      "/api/agent-records/0a000000-0000-0000-0000-000000000001",
    );
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("boom");
  });
});

describe("substance-coupled segment-set reads (the cache boundary)", () => {
  it("GET /api/segment-sets → proxies to substance, normalizes to camelCase, wraps in {items,total,limit,offset}", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [
        {
          segment_set_id: "ss1",
          display_name: "Alpha",
          start_block_index: 3,
        },
      ],
    });
    const res = await request(app).get("/api/segment-sets");
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:3115/segment-sets?limit=200&offset=0",
    );
    expect(res.body.items[0]).toEqual({
      segmentSetId: "ss1",
      displayName: "Alpha",
      startBlockIndex: 3,
    });
    expect(res.body.total).toBe(1);
    expect(res.body.limit).toBe(200);
    expect(res.body.offset).toBe(0);
  });

  it("substance 404 propagates as 404 (GET /api/segment-sets/:id)", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => '{"detail":"not found"}',
    });
    const res = await request(app).get(
      "/api/segment-sets/0b000000-0000-0000-0000-000000000001",
    );
    expect(res.status).toBe(404);
    expect(res.body.error).toContain("substance 404");
  });

  it("substance unreachable → 502 (transport failure mapping)", async () => {
    fetchMock.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    const res = await request(app).get("/api/segment-sets");
    expect(res.status).toBe(502);
    expect(res.body.error).toContain("substance unreachable");
  });

  it("GET /api/harvest-candidates/:id/segment-sets → {items,total} envelope over substance data", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [{ segment_set_id: "ss2" }],
    });
    const res = await request(app).get(
      "/api/harvest-candidates/0c000000-0000-0000-0000-000000000001/segment-sets",
    );
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:3115/candidates/0c000000-0000-0000-0000-000000000001/segment-sets",
    );
    expect(res.body.items[0].segmentSetId).toBe("ss2");
    expect(res.body.total).toBe(1);
  });
});

describe("body-parser negative (canary-safe write probe)", () => {
  it("POST /api/agent-records with malformed JSON → 400 before any DB work", async () => {
    const res = await request(app)
      .post("/api/agent-records")
      .set("Content-Type", "application/json")
      .send('{"broken":');
    expect(res.status).toBe(400);
    expect(poolMock.query).not.toHaveBeenCalled();
  });
});

// ── CodeQL SSRF: requirement compile trigger URL ───────────────────────
// The Backlog→ToDo trigger interpolates a request-derived requirement id
// into an outgoing URL. encodeURIComponent is the sanitizer CodeQL
// recognises for js/request-forgery: it escapes path separators so a
// crafted id cannot traverse the path or reach another host.
describe("requirementCompileUrl (CodeQL js/request-forgery fix)", () => {
  it("URI-encodes the id; benign ids are unchanged", () => {
    expect(requirementCompileUrl(42)).toBe(
      "http://localhost:3101/api/requirements/42/compile",
    );
    expect(requirementCompileUrl("2717")).toBe(
      "http://localhost:3101/api/requirements/2717/compile",
    );
  });

  it("escapes path separators so a crafted id cannot retarget the request", () => {
    expect(requirementCompileUrl("../../evil")).toBe(
      "http://localhost:3101/api/requirements/..%2F..%2Fevil/compile",
    );
    expect(requirementCompileUrl("a/b")).toBe(
      "http://localhost:3101/api/requirements/a%2Fb/compile",
    );
  });
});

describe("requirement delete guard (P4a) — same contract as nebula-srv", () => {
  const REQ = "804b3f87-fe84-4167-b5a7-15eaa35c2d4c";
  const OTHER = "5fcf5826-b7ad-48bd-900a-c8ed54b95970";
  const BLOCKED = "11111111-2222-4333-8444-555555555555";
  const SPEC = "22222222-3333-4444-8555-666666666666";
  const SYS = "99999999-9999-4999-8999-999999999991";
  const FEAT = "77777777-6666-4555-8444-333333333333";

  type State = {
    deleted: string[][];
    parents: string[];
    committed: boolean;
    rolledBack: boolean;
  };

  function fakeDb(opts: { guardInstalled?: boolean; blocked?: string[]; throwOnDelete?: any } = {}): State {
    const guardInstalled = opts.guardInstalled ?? true;
    const blocked = new Set(opts.blocked ?? []);
    const state: State = { deleted: [], parents: [], committed: false, rolledBack: false };

    poolMock.query.mockImplementation(async (sql: string, values: any[] = []) => {
      const s = String(sql).replace(/\s+/g, " ").trim();

      if (s === "BEGIN" || s === "COMMIT" || s === "ROLLBACK") {
        if (s === "COMMIT") state.committed = true;
        if (s === "ROLLBACK") state.rolledBack = true;
        return { rows: [], rowCount: null };
      }
      if (s.includes("to_regprocedure")) {
        return { rows: [{ installed: guardInstalled }], rowCount: 1 };
      }
      if (s.startsWith("SELECT id FROM nebula.requirements WHERE")) {
        return { rows: [{ id: REQ }, { id: OTHER }, { id: BLOCKED }], rowCount: 3 };
      }
      if (s.includes("FROM unnest(")) {
        const ids = values[0] as string[];
        return {
          rows: ids
            .filter((id) => blocked.has(id))
            .map((id) => ({ id, blockers: { specifications: [SPEC] } })),
          rowCount: 1,
        };
      }
      if (s.includes("SELECT resolution.delete_requirements(")) {
        if (opts.throwOnDelete) throw opts.throwOnDelete;
        state.deleted.push(values[0]);
        return { rows: [{ deleted: (values[0] as string[]).length }], rowCount: 1 };
      }
      if (s.includes("DELETE FROM requirements WHERE id = ANY(")) {
        state.deleted.push(values[0]);
        return { rows: (values[0] as string[]).map((id) => ({ id })), rowCount: 1 };
      }
      if (/DELETE FROM (systems|subsystems|features) WHERE id/.test(s)) {
        state.parents.push(s.split(" ")[2]);
        return { rows: [], rowCount: 1 };
      }
      if (s.startsWith("DELETE FROM work_sessions")) {
        return { rows: [], rowCount: 0 };
      }
      throw new Error(`Unexpected query: ${s}`);
    });

    return state;
  }

  it("refuses a referenced requirement with 409 + blockers (never 500)", async () => {
    const state = fakeDb({ blocked: [BLOCKED] });

    const res = await request(app).delete(`/api/requirements/${BLOCKED}`);

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("requirement_delete_blocked");
    expect(res.body.blockers).toEqual([
      { requirementId: BLOCKED, blockers: { specifications: [SPEC] } },
    ]);
    expect(state.deleted).toEqual([]);
    expect(state.committed).toBe(false);
    expect(state.rolledBack).toBe(true);
  });

  it("reports what a successful delete removed", async () => {
    const state = fakeDb();

    const res = await request(app).delete(`/api/requirements/${REQ}`);

    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, deletedRequirements: 1 });
    expect(state.deleted).toEqual([[REQ]]);
    expect(state.committed).toBe(true);
  });

  it("refuses the whole feature cascade and deletes nothing", async () => {
    const state = fakeDb({ blocked: [BLOCKED] });

    const res = await request(app).delete(`/api/features/${FEAT}`);

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("requirement_delete_blocked");
    expect(res.body.blockers).toEqual([
      { requirementId: BLOCKED, blockers: { specifications: [SPEC] } },
    ]);
    expect(state.deleted).toEqual([]);
    expect(state.parents).toEqual([]);
    expect(state.committed).toBe(false);
  });

  it("cascades a system's requirements before the system row", async () => {
    const state = fakeDb();

    const res = await request(app).delete(`/api/systems/${SYS}`);

    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, deletedRequirements: 3 });
    expect(state.deleted).toEqual([[REQ, OTHER, BLOCKED]]);
    expect(state.parents).toEqual(["systems"]);
    expect(state.committed).toBe(true);
  });

  it("maps a racing guard refusal (P4A01) to 409 with the trigger's detail", async () => {
    const err: any = new Error("P4A-DELETE-BLOCKED: requirement x still has dependants");
    err.code = "P4A01";
    err.detail = JSON.stringify({ requirementId: REQ, blockers: { specifications: [SPEC] } });
    fakeDb({ throwOnDelete: err });

    const res = await request(app).delete(`/api/requirements/${REQ}`);

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("requirement_delete_blocked");
    expect(res.body.blockers).toEqual([
      { requirementId: REQ, blockers: { specifications: [SPEC] } },
    ]);
  });

  it("maps a residual FK violation (23503) to 409, not 500", async () => {
    const err: any = new Error("update or delete violates foreign key constraint");
    err.code = "23503";
    fakeDb({ throwOnDelete: err });

    const res = await request(app).delete(`/api/requirements/${REQ}`);

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("requirement_delete_blocked");
  });

  it("still deletes through the transition view before V222 is applied", async () => {
    const state = fakeDb({ guardInstalled: false });

    const res = await request(app).delete(`/api/requirements/${REQ}`);

    expect(res.status).toBe(200);
    expect(res.body.deletedRequirements).toBe(1);
    expect(state.deleted).toEqual([[REQ]]);
  });
});
