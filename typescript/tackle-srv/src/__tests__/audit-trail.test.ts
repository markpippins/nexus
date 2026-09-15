import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import request from "supertest";

// ── Mock the DB layer ────────────────────────────────────────────────
// The mock fns are created here (hoisted) and re-configured per test.
const qOne = vi.fn();
const qAll = vi.fn();

vi.mock("../db", () => ({
  qOne: (...args: unknown[]) => qOne(...args),
  qAll: (...args: unknown[]) => qAll(...args),
}));

const CANONICAL = ["REGISTRY_AUDIT", "NEBULA_AUDIT", "KG_AUDIT"];

/** Fresh module import per call so the 60s category cache in routes/audit.ts
 *  starts cold for every test (vi.resetModules + new express app). */
async function makeApp() {
  vi.resetModules();
  const { auditRouter } = await import("../routes/audit");
  const express = (await import("express")).default;
  const app = express();
  app.use("/audit-trail", auditRouter);
  return app;
}

beforeEach(() => {
  qOne.mockReset();
  qAll.mockReset();
  // Default: canonical set from V161.
  qOne.mockResolvedValue({ cats: CANONICAL });
  qAll.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /audit-trail", () => {
  it("returns the canonical categories from tackle.audit_log_categories() as metadata", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail");
    expect(res.status).toBe(200);
    expect(res.body.categories).toEqual(CANONICAL);
    expect(res.body.note).toMatch(/append-only/);
    // Default listing does NOT force a category filter.
    expect(qAll).toHaveBeenCalledTimes(1);
    const [sql, binds] = qAll.mock.calls[0];
    expect(sql).not.toMatch(/category = ANY/);
    expect(sql).not.toMatch(/category = @category/);
  });

  it("falls back to the legacy pair when audit_log_categories() is absent (pre-V161 DB)", async () => {
    qOne.mockRejectedValue(Object.assign(new Error("function does not exist"), { code: "42883" }));
    const app = await makeApp();
    const res = await request(app).get("/audit-trail");
    expect(res.status).toBe(200);
    expect(res.body.categories).toEqual(["REGISTRY_AUDIT", "NEBULA_AUDIT"]);
  });

  it("honors ?categories= with a plural ANY(...) filter", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?categories=KG_AUDIT,NEBULA_AUDIT");
    expect(res.status).toBe(200);
    expect(res.body.categories).toEqual(CANONICAL); // truthful metadata, not echo
    const [sql, binds] = qAll.mock.calls[0];
    expect(sql).toMatch(/category = ANY\(ARRAY\[@cat0, @cat1\]\)/);
    expect(binds.cat0).toBe("KG_AUDIT");
    expect(binds.cat1).toBe("NEBULA_AUDIT");
  });

  it("refuses unknown categories with 400 and names the canonical set", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?categories=KG_AUDIT,HACKED");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/HACKED/);
    expect(res.body.canonical_categories).toEqual(CANONICAL);
    expect(qAll).not.toHaveBeenCalled();
  });

  it("normalizes case and whitespace in the categories param", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?categories=kg_audit,%20nebula_audit");
    expect(res.status).toBe(200);
    const [, binds] = qAll.mock.calls[0];
    expect(binds.cat0).toBe("KG_AUDIT");
    expect(binds.cat1).toBe("NEBULA_AUDIT");
  });

  it("keeps the legacy singular ?category= working", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?category=KG_AUDIT");
    expect(res.status).toBe(200);
    const [sql, binds] = qAll.mock.calls[0];
    expect(sql).toMatch(/category = @category/);
    expect(binds.category).toBe("KG_AUDIT");
  });

  it("still validates the legacy singular category against the canonical set", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?category=NOPE");
    expect(res.status).toBe(400);
    expect(qAll).not.toHaveBeenCalled();
  });

  it("caches the canonical categories for 60s (one qOne across two requests)", async () => {
    const app = await makeApp();
    await request(app).get("/audit-trail");
    await request(app).get("/audit-trail");
    expect(qOne).toHaveBeenCalledTimes(1);
  });

  it("returns rows from the view unchanged", async () => {
    const row = {
      timestamp: "2026-09-14T23:05:23Z",
      category: "KG_AUDIT",
      audited_table: "graph_entities",
      operation: "INSERT",
      row_count: "1",
      keys: "concepts/x",
      application_name: "psql",
      client_addr: "172.18.0.1/32",
      txid: "17261461",
      message: "INSERT on graph_entities (1 rows)",
    };
    qAll.mockResolvedValue([row]);
    const app = await makeApp();
    const res = await request(app).get("/audit-trail?categories=KG_AUDIT");
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.entries[0]).toEqual(row);
  });
});

describe("GET /audit-trail/recent", () => {
  it("works unchanged and does not include category metadata", async () => {
    const app = await makeApp();
    const res = await request(app).get("/audit-trail/recent?hours=2&limit=10");
    expect(res.status).toBe(200);
    expect(res.body.hours).toBe(2);
    expect(res.body.entries).toEqual([]);
    const [sql, binds] = qAll.mock.calls[0];
    expect(sql).toMatch(/timestamp > @since/);
    expect(binds.limit).toBe(10);
  });
});
