import { Errors } from "moleculer";

// Hermetic: mock ./store so handlers run without PostgreSQL.
const queryMock = jest.fn();
jest.mock("../services/store", () => ({
  __esModule: true,
  getDb: () => ({ query: queryMock }),
}));

import {
  fail,
  metaHandler,
  canonicalAssetEnvelope,
  assetRevisionEnvelope,
  handlersFor,
  claimResolve,
  caRelationsAdd,
  caExtIdsAdd,
  driftResolve,
} from "../services/handlers";
import { TABLES } from "../services/tables";
import fs from "fs";
import path from "path";

beforeEach(() => {
  queryMock.mockReset();
});

const MoleculerError = Errors.MoleculerError;

function expectMole(err: any, status: number, code: string) {
  expect(err).toBeInstanceOf(MoleculerError);
  expect(err.code).toBe(status);
  expect(err.type).toBe(code);
}

// ── fail() helper ────────────────────────────────────────────────────

describe("fail helper", () => {
  it("builds a MoleculerError carrying status + code", () => {
    const e = fail(404, "not_found", "nope");
    expectMole(e, 404, "not_found");
    expect(e.message).toBe("nope");
  });
});

// ── generated CRUD: add ──────────────────────────────────────────────

describe("handlersFor(concept).add (resolution.* direct INSERT)", () => {
  const t = TABLES.find((x) => x.table === "concept")!;
  const h = handlersFor(t);

  it("builds a direct INSERT with the writable cols (no add_ proc for resolution.*)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "c-1", name: "Concept" }] });
    const r = await h.add({ p_name: "Concept", p_description: "d" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toMatch(/^INSERT INTO resolution\.concept \(name, description\) VALUES \(\$1, \$2\) RETURNING \*$/);
    expect(values).toEqual(["Concept", "d"]);
    expect(r.status).toBe(201);
  });

  it("maps SQLSTATE 23505 → duplicate_active_key (400)", async () => {
    const err: any = new Error("duplicate key");
    err.code = "23505";
    queryMock.mockRejectedValueOnce(err);
    await expect(h.add({ p_name: "dup" })).rejects.toMatchObject({
      code: 400,
      type: "duplicate_active_key",
    });
  });

  it("maps other failures → add_failed (400)", async () => {
    queryMock.mockRejectedValueOnce(new Error("boom"));
    await expect(h.add({ p_name: "x" })).rejects.toMatchObject({
      code: 400,
      type: "add_failed",
    });
  });
});

describe("handlersFor(snapshot).add (semantics.* proc path)", () => {
  const t = TABLES.find((x) => x.table === "snapshot")!;
  const h = handlersFor(t);

  it("calls the add_ proc with p_* named params and returns 201", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "uuid-1", label: "S1" }] });
    const r = await h.add({ p_label: "S1", p_version: "v1", p_created_by: "eng" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toBe("SELECT * FROM semantics.add_snapshot(p_label => $1, p_version => $2, p_created_by => $3)");
    expect(values).toEqual(["S1", "v1", "eng"]);
    expect(r.status).toBe(201);
  });

  it("omits undefined writable params (auto columns stay DB-filled)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "uuid-2" }] });
    await h.add({ p_label: "S2" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toContain("p_label => $1");
    expect(sql).not.toContain("p_created_by");
    expect(values).toEqual(["S2"]);
  });
});

describe("handlersFor(concept_relationship).add (resolution.* direct INSERT)", () => {
  const t = TABLES.find((x) => x.table === "concept_relationship")!;
  const h = handlersFor(t);

  it("builds a direct INSERT (no add_ proc for resolution.*)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "r-1" }] });
    const r = await h.add({ p_name: "edge", p_definition: "d" });
    const [sql] = queryMock.mock.calls[0];
    expect(sql).toMatch(/^INSERT INTO resolution\.concept_relationship \(/);
    expect(sql).toContain("RETURNING *");
    expect(r.status).toBe(201);
  });
});

// ── generated CRUD: update ───────────────────────────────────────────

describe("handlersFor(snapshot).update", () => {
  const t = TABLES.find((x) => x.table === "snapshot")!;
  const h = handlersFor(t);

  it("uses named params with the id first, and returns superseded_id", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "new-uuid" }] });
    const r = await h.update("old-uuid", { p_label: "S1b" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toContain("semantics.update_snapshot(p_id => $1");
    expect(values[0]).toBe("old-uuid");
    expect((r.body as any).superseded_id).toBe("old-uuid");
  });

  it("'no active row' message → 404 not_found", async () => {
    queryMock.mockRejectedValueOnce(new Error("no active row for id x"));
    await expect(h.update("x", { p_label: "y" })).rejects.toMatchObject({
      code: 404,
      type: "not_found",
    });
  });
});

describe("handlersFor(relationship_type).update (p_new_name law)", () => {
  const t = TABLES.find((x) => x.table === "relationship_type")!;
  const h = handlersFor(t);

  it("requires p_new_name (names are never reused)", async () => {
    await expect(h.update("some-id", { p_description: "x" })).rejects.toMatchObject({
      code: 400,
      type: "update_failed",
    });
  });

  it("passes p_name (the old name, from the path) and p_new_name", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ name: "renamed" }] });
    await h.update("old-name", { p_new_name: "renamed" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toContain("p_name => $1");
    expect(sql).toContain("p_new_name => $2");
    expect(values).toEqual(["old-name", "renamed"]);
  });
});

describe("handlersFor(concept).update — resolution.* shape", () => {
  const t = TABLES.find((x) => x.table === "concept")!;
  const h = handlersFor(t);

  it("builds the expire+insert CTE for resolution.* tables", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "c-2" }] });
    await h.update("c-1", { p_id: "c-1", p_name: "renamed" });
    const [sql] = queryMock.mock.calls[0];
    expect(sql).toContain("WITH expired AS (");
    expect(sql).toContain("SET expired_at = now()");
    expect(sql).toContain("INSERT INTO resolution.concept");
  });
});

// ── generated CRUD: get / list / remove ──────────────────────────────

describe("handlersFor(...).get", () => {
  it("returns the row on hit", async () => {
    const t = TABLES.find((x) => x.table === "snapshot")!;
    const h = handlersFor(t);
    queryMock.mockResolvedValueOnce({ rows: [{ id: "s-1" }] });
    const r = await h.get("s-1");
    expect(r.status).toBe(200);
    expect(r.body).toEqual({ id: "s-1" });
  });

  it("404 not_found on miss", async () => {
    const t = TABLES.find((x) => x.table === "snapshot")!;
    const h = handlersFor(t);
    queryMock.mockResolvedValueOnce({ rows: [] });
    await expect(h.get("nope")).rejects.toMatchObject({ code: 404, type: "not_found" });
  });

  it("idCol tables match uuid PK OR natural key", async () => {
    const t = TABLES.find((x) => x.table === "relationship_type")!;
    const h = handlersFor(t);
    queryMock.mockResolvedValueOnce({ rows: [{ name: "relates" }] });
    await h.get("relates");
    const [sql] = queryMock.mock.calls[0];
    expect(sql).toContain("id::text = $1 OR name = $1");
  });
});

describe("handlersFor(...).list clamps", () => {
  const t = TABLES.find((x) => x.table === "snapshot")!;
  const h = handlersFor(t);

  it.each([
    ["", 100, 0],
    ["limit=9999", 500, 0],
    ["limit=abc", 100, 0],
    ["offset=50", 100, 50],
    ["offset=-5", 100, 0],
  ])("query '%s' → limit %i offset %i", async (qs, lim, off) => {
    queryMock.mockResolvedValue({ rows: [] });
    const params = Object.fromEntries(new URLSearchParams(qs));
    await h.list(params);
    const [, values] = queryMock.mock.calls[0];
    expect(values).toEqual([lim, off]);
  });

  it("includeExpired drops the active filter", async () => {
    queryMock.mockResolvedValue({ rows: [] });
    await h.list({ includeExpired: "true" });
    const [sql] = queryMock.mock.calls[0];
    expect(sql).not.toContain("WHERE expired_at IS NULL");
  });
});

describe("handlersFor(...).remove", () => {
  it("semantics.* goes through the soft_delete_ proc", async () => {
    const t = TABLES.find((x) => x.table === "snapshot")!;
    const h = handlersFor(t);
    queryMock.mockResolvedValueOnce({ rows: [{ deleted: 1 }] });
    const r = await h.remove("s-1");
    const [sql] = queryMock.mock.calls[0];
    expect(sql).toContain("semantics.soft_delete_snapshot(p_id => $1)");
    expect(r.body).toEqual({ table: "snapshot", id: "s-1", deleted: 1 });
  });

  it("resolution.* expires via direct UPDATE", async () => {
    const t = TABLES.find((x) => x.table === "concept")!;
    const h = handlersFor(t);
    queryMock.mockResolvedValueOnce({ rows: [{ id: "c-1" }] });
    const r = await h.remove("c-1");
    const [sql] = queryMock.mock.calls[0];
    expect(sql).toMatch(/^UPDATE resolution\.concept SET expired_at = now\(\)/);
    expect((r.body as any).deleted).toBe(1);
  });
});

// ── meta handler ─────────────────────────────────────────────────────

describe("metaHandler", () => {
  it("failure → meta_failed (500)", async () => {
    queryMock.mockRejectedValue(new Error("db down"));
    await expect(metaHandler()).rejects.toMatchObject({ code: 500, type: "meta_failed" });
  });
});

// ── envelope routes ──────────────────────────────────────────────────

describe("canonicalAssetEnvelope", () => {
  it("404 not_found when the asset misses", async () => {
    queryMock.mockResolvedValueOnce({ rows: [] });
    await expect(canonicalAssetEnvelope("ghost")).rejects.toMatchObject({
      code: 404,
      type: "not_found",
    });
  });

  it("assembles the envelope; nebula unavailability degrades to []", async () => {
    const asset = { id: "a-1", canonical_asset_id: "CA-1", asset_kind: "module" };
    queryMock
      .mockResolvedValueOnce({ rows: [asset] }) // asset lookup
      .mockResolvedValueOnce({ rows: [{ id: "rev-1", revision_id: "R1" }] }) // revisions
      .mockResolvedValueOnce({ rows: [] }) // identity claims
      .mockResolvedValueOnce({ rows: [] }) // relations
      .mockRejectedValueOnce(new Error("nebula unavailable")); // external ids
    const r = await canonicalAssetEnvelope("CA-1");
    expect(r.status).toBe(200);
    const body = r.body as any;
    expect(body.canonicalAssetId).toBe("CA-1");
    expect(body.revisions).toHaveLength(1);
    expect(body.identityClaims).toEqual([]);
    expect(body.relations).toEqual([]);
    expect(body.externalIds).toEqual([]);
  });
});

describe("assetRevisionEnvelope", () => {
  it("404 not_found when the revision misses", async () => {
    queryMock.mockResolvedValueOnce({ rows: [] });
    await expect(assetRevisionEnvelope("ghost")).rejects.toMatchObject({
      code: 404,
      type: "not_found",
    });
  });

  it("expands asset / observations / parent / children", async () => {
    const rev = { id: "rev-1", revision_id: "R1", asset_id: "a-1", parent_revision_id: "rev-0" };
    queryMock
      .mockResolvedValueOnce({ rows: [rev] }) // revision lookup
      .mockResolvedValueOnce({ rows: [{ id: "a-1" }] }) // asset
      .mockResolvedValueOnce({ rows: [{ id: "so-1" }] }) // source observations
      .mockResolvedValueOnce({ rows: [{ id: "rev-0", revision_id: "R0" }] }) // parent
      .mockResolvedValueOnce({ rows: [{ id: "rev-2" }] }); // children
    const r = await assetRevisionEnvelope("R1");
    const body = r.body as any;
    expect(body.asset).toEqual({ id: "a-1" });
    expect(body.sourceObservations).toEqual([{ id: "so-1" }]);
    expect(body.parentRevision).toEqual({ id: "rev-0", revision_id: "R0" });
    expect(body.childRevisions).toEqual([{ id: "rev-2" }]);
  });

  it("parent stays null when parent_revision_id is null (no parent query)", async () => {
    const rev = { id: "rev-1", revision_id: "R1", asset_id: "a-1", parent_revision_id: null };
    queryMock
      .mockResolvedValueOnce({ rows: [rev] })
      .mockResolvedValueOnce({ rows: [{ id: "a-1" }] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] });
    const r = await assetRevisionEnvelope("R1");
    expect((r.body as any).parentRevision).toBeNull();
  });
});

// ── lifecycle / sub-resource validation ──────────────────────────────

describe("claimResolve", () => {
  it("invalid status → invalid_status (400)", async () => {
    await expect(claimResolve("id", { status: "maybe" })).rejects.toMatchObject({
      code: 400,
      type: "invalid_status",
    });
  });

  it("missing claim → not_found", async () => {
    queryMock.mockResolvedValueOnce({ rows: [] });
    await expect(claimResolve("ghost", { status: "resolved" })).rejects.toMatchObject({
      code: 404,
      type: "not_found",
    });
  });

  it("non-open claim → invalid_transition", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "c-1", status: "resolved" }] });
    await expect(claimResolve("c-1", { status: "rejected" })).rejects.toMatchObject({
      code: 400,
      type: "invalid_transition",
    });
  });

  it("open claim resolves through the update_ proc with supersededId", async () => {
    const claim = {
      id: "c-1", status: "open", asset_id: "a-1", candidate_asset_id: null,
      claim_type: "exact", confidence: null, basis: null, decided_by: null,
    };
    queryMock
      .mockResolvedValueOnce({ rows: [claim] })
      .mockResolvedValueOnce({ rows: [{ id: "c-2", status: "resolved" }] });
    const r = await claimResolve("c-1", { status: "resolved", decidedBy: "engineer" });
    const [sql, values] = queryMock.mock.calls[1];
    expect(sql).toContain("semantics.update_asset_identity_claim(");
    expect(values[6]).toBe("resolved");
    expect(values[7]).toBe("engineer");
    expect((r.body as any).supersededId).toBe("c-1");
    expect((r.body as any).previousStatus).toBe("open");
  });
});

describe("caRelationsAdd", () => {
  it("asset-miss before validation → not_found (incumbent resolve-first order)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [] });
    await expect(caRelationsAdd("ghost", { relationType: "supersedes" })).rejects.toMatchObject({
      code: 404,
      type: "not_found",
    });
  });

  it("missing relatedAssetId → missing_field (asset resolved first)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "a-1" }] });
    await expect(caRelationsAdd("a-1", { relationType: "supersedes" })).rejects.toMatchObject({
      code: 400,
      type: "missing_field",
    });
  });

  it("missing relationType → missing_field (asset resolved first)", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "a-1" }] });
    await expect(caRelationsAdd("a-1", { relatedAssetId: "a-2" })).rejects.toMatchObject({
      code: 400,
      type: "missing_field",
    });
  });

  it("related asset miss → not_found naming the related id", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "a-1" }] })
      .mockResolvedValueOnce({ rows: [] });
    await expect(
      caRelationsAdd("a-1", { relatedAssetId: "ghost-2", relationType: "supersedes" }),
    ).rejects.toMatchObject({ code: 404, type: "not_found" });
  });

  it("self-relation → self_relation", async () => {
    const asset = { id: "a-1", canonical_asset_id: "CA-1", asset_kind: "module" };
    queryMock
      .mockResolvedValueOnce({ rows: [asset] }) // :id lookup
      .mockResolvedValueOnce({ rows: [asset] }); // relatedAssetId lookup
    await expect(
      caRelationsAdd("CA-1", { relatedAssetId: "CA-1", relationType: "supersedes" }),
    ).rejects.toMatchObject({ code: 400, type: "self_relation" });
  });
});

describe("caExtIdsAdd", () => {
  it("asset resolved first; missing nebulaSystemId → missing_field", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ id: "a-1" }] });
    await expect(caExtIdsAdd("a-1", {})).rejects.toMatchObject({
      code: 400,
      type: "missing_field",
    });
  });

  it("system without asset_id → no_asset (400)", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "a-1" }] }) // asset
      .mockResolvedValueOnce({ rows: [{ id: "sys-1", asset_id: null }] }); // nebula system
    await expect(caExtIdsAdd("a-1", { nebulaSystemId: "sys-1" })).rejects.toMatchObject({
      code: 400,
      type: "no_asset",
    });
  });

  it("existing active relation → duplicate_active_key (409)", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "a-1" }] })
      .mockResolvedValueOnce({ rows: [{ id: "sys-1", asset_id: "sys-asset" }] })
      .mockResolvedValueOnce({ rows: [{ id: "rel-9" }] }); // existing relation
    await expect(caExtIdsAdd("a-1", { nebulaSystemId: "sys-1" })).rejects.toMatchObject({
      code: 409,
      type: "duplicate_active_key",
    });
  });
});

describe("driftResolve", () => {
  it("calls resolve_drift_finding with the body timestamp", async () => {
    queryMock.mockResolvedValueOnce({ rows: [{ resolved: 1 }] });
    const r = await driftResolve("d-1", { p_resolved_at: "2026-09-23T00:00:00Z" });
    const [sql, values] = queryMock.mock.calls[0];
    expect(sql).toContain("semantics.resolve_drift_finding($1, $2)");
    expect(values).toEqual(["d-1", "2026-09-23T00:00:00Z"]);
    expect(r.body).toEqual({ id: "d-1", resolved: 1 });
  });

  it("failure → resolve_failed (500)", async () => {
    queryMock.mockRejectedValueOnce(new Error("no proc"));
    await expect(driftResolve("d-1", {})).rejects.toMatchObject({
      code: 500,
      type: "resolve_failed",
    });
  });
});

// ── static alias-map checks (drift-gate surface, extractor-shaped) ───

describe("api.service.ts alias map (static)", () => {
  const gatewaySrc = fs.readFileSync(
    path.resolve(__dirname, "../services/api.service.ts"),
    "utf-8",
  );

  const aliasRe = /^\s*"(GET|POST|PUT|PATCH|DELETE) (\/[^"]*)": "([^"]+)",?$/gm;
  const aliases = new Map<string, string>();
  let m: RegExpExecArray | null;
  while ((m = aliasRe.exec(gatewaySrc)) !== null) {
    aliases.set(`${m[1]} ${m[2]}`, m[3]);
  }

  it("carries exactly 92 alias entries (the live route surface)", () => {
    expect(aliases.size).toBe(92);
  });

  it("health → semantics.x.health_get", () => {
    expect(aliases.get("GET /health")).toBe("semantics.x.health_get");
  });

  it("meta → semantics.x.meta_get", () => {
    expect(aliases.get("GET /api/meta")).toBe("semantics.x.meta_get");
  });

  it("envelope overrides: canonical_asset.get / asset_revision.get serve :id", () => {
    expect(aliases.get("GET /api/canonical_asset/:id")).toBe("semantics.canonical_asset.get");
    expect(aliases.get("GET /api/asset_revision/:id")).toBe("semantics.asset_revision.get");
  });

  it("filter overrides: evidence_item / statement_evidence list serve the filter GET", () => {
    expect(aliases.get("GET /api/evidence_item")).toBe("semantics.evidence_item.list");
    expect(aliases.get("GET /api/statement_evidence")).toBe("semantics.statement_evidence.list");
  });

  it("evidence_item is immutable — NO PATCH route (incumbent parity)", () => {
    expect(aliases.has("PATCH /api/evidence_item/:id")).toBe(false);
  });

  it("every table has the CRUD quartet (list/get/update/remove where legal)", () => {
    for (const t of TABLES) {
      expect(aliases.get(`GET /api/${t.table}`)).toBe(`semantics.${t.table}.list`);
      expect(aliases.get(`POST /api/${t.table}`)).toBe(`semantics.${t.table}.create`);
      expect(aliases.get(`GET /api/${t.table}/:id`)).toBe(`semantics.${t.table}.get`);
      expect(aliases.get(`DELETE /api/${t.table}/:id`)).toBe(`semantics.${t.table}.remove`);
      if (t.table !== "evidence_item") {
        expect(aliases.get(`PATCH /api/${t.table}/:id`)).toBe(`semantics.${t.table}.update`);
      }
    }
  });

  it("lifecycle transitions and sub-resources route to semantics.x.*", () => {
    expect(aliases.get("POST /api/asset_identity_claim/:id/resolve")).toBe(
      "semantics.x.asset_identity_claim_id_resolve_post",
    );
    expect(aliases.get("POST /api/drift_finding/:id/resolve")).toBe(
      "semantics.x.drift_finding_id_resolve_post",
    );
    expect(aliases.get("DELETE /api/canonical_asset/:id/external-ids/:eid")).toBe(
      "semantics.x.canonical_asset_id_external_ids_eid_delete",
    );
  });
});
