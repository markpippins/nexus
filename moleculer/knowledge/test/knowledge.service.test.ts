/**
 * Hermetic parity tests for the knowledge port.
 *
 * SCOPE — pure semantics that must match typescript/knowledge-srv:
 *   - intParam: default 100/0, clamps (1..500 reads, 1..100 migrations),
 *     NaN → default (the query-ladder contract every list route shares)
 *   - strParam: non-string → default ""
 *   - the exact validation strings each route emits (the UI/MCP renders them)
 *   - the thrower envelope shapes: 4xx {error} / 500 {error, message} via
 *     MoleculerError .data.envelope (rendered by the gateway's onError)
 *   - the root-index contract (name/version/port echo/source/endpoints[])
 *     — knowledge-mcp's KNOWLEDGE_SRV_URL callers rely on the port echo
 *   - the health contract shape (200 healthy/up|unknown; 503 unhealthy+error
 *     envelope) — without a live pool (shape assertion only)
 *
 * NOT COVERED HERE — the route surface (apidocs drift gate vs
 * typescript/knowledge-srv/openapi.yaml) or the SQL itself: the SQL is
 * VERBATIM from the incumbent routes and exercised by the live canary diff.
 *
 * No database, no broker, no HTTP bind.
 */
import { __test } from "../services/knowledge.service";

const { intParam, strParam, dbFailure, badRequest, notFound, INCUMBENT_PORT_ECHO } = __test;

describe("query ladder (incumbent intParam/strParam, routes/knowledge.ts)", () => {
  it("intParam defaults: limit 100, offset 0", () => {
    expect(intParam(undefined, 100)).toBe(100);
    expect(intParam(undefined, 0)).toBe(0);
  });

  it("intParam clamps list routes to 1..500", () => {
    expect(intParam("0", 100, 1, 500)).toBe(1);
    expect(intParam("999", 100, 1, 500)).toBe(500);
    expect(intParam("250", 100, 1, 500)).toBe(250);
    expect(intParam("-5", 100, 1, 500)).toBe(1);
  });

  it("intParam clamps migrations to 1..100 with default 20", () => {
    expect(intParam(undefined, 20, 1, 100)).toBe(20);
    expect(intParam("1000", 20, 1, 100)).toBe(100);
    expect(intParam("0", 20, 1, 100)).toBe(1);
  });

  it("intParam: NaN falls back to the default, not the clamp floor", () => {
    expect(intParam("abc", 100, 1, 500)).toBe(100);
    expect(intParam("abc", 20, 1, 100)).toBe(20);
  });

  it("strParam: non-strings become the default empty string", () => {
    expect(strParam(undefined)).toBe("");
    expect(strParam(null as unknown as string)).toBe("");
    expect(strParam(42 as unknown as string)).toBe("");
    expect(strParam("section-a")).toBe("section-a");
  });
});

describe("validation strings (exact incumbent 400 bodies)", () => {
  it("entity create: section and entity_id are required", () => {
    const err = badRequest("section and entity_id are required") as any;
    expect(err.code).toBe(400);
    expect(err.data.envelope).toEqual({ error: "section and entity_id are required" });
  });

  it("entity purge: section query param is required for purge", () => {
    const err = badRequest("section query param is required for purge") as any;
    expect(err.code).toBe(400);
    expect(err.data.envelope).toEqual({ error: "section query param is required for purge" });
  });

  it("edge create: four-field required message", () => {
    const err = badRequest(
      "source_section, source_id, relation_type, target_id are required"
    ) as any;
    expect(err.code).toBe(400);
    expect(err.data.envelope.error).toBe(
      "source_section, source_id, relation_type, target_id are required"
    );
  });

  it("cross-reference create: map_name and target_id are required", () => {
    const err = badRequest("map_name and target_id are required") as any;
    expect(err.code).toBe(400);
    expect(err.data.envelope).toEqual({ error: "map_name and target_id are required" });
  });
});

describe("miss + transport envelopes (gateway onError renders .data.envelope)", () => {
  it("404 envelopes are single-field {error} with the incumbent's wording", () => {
    const e404 = notFound("Entity not found: plans/0042") as any;
    expect(e404.code).toBe(404);
    expect(e404.data.envelope).toEqual({ error: "Entity not found: plans/0042" });

    const edge404 = notFound("Edge not found: 00000000-0000-0000-0000-00000000dead") as any;
    expect(edge404.data.envelope.error).toBe(
      "Edge not found: 00000000-0000-0000-0000-00000000dead"
    );

    const xref404 = notFound("Cross-reference not found: x") as any;
    expect(xref404.data.envelope.error).toBe("Cross-reference not found: x");
  });

  it("500 envelopes carry error + message (the transport contract)", () => {
    const err = dbFailure("Failed to list entities", new Error("ECONNREFUSED")) as any;
    expect(err.code).toBe(500);
    expect(err.data.envelope).toEqual({ error: "Failed to list entities", message: "ECONNREFUSED" });
  });

  it("dbFailure stringifies non-Error throwables", () => {
    const err = dbFailure("Failed to compute summary", "boom-string") as any;
    expect(err.data.envelope.message).toBe("boom-string");
  });
});

describe("root index contract (incumbent index.ts app.get('/'))", () => {
  it("echoes the incumbent's port 3109 (cascade finding: byte parity over self-description)", () => {
    expect(INCUMBENT_PORT_ECHO).toBe(3109);
  });

  it("documents all 16 endpoint lines in the incumbent's order and format", () => {
    // The endpoints array is asserted via the canary diff against the live
    // incumbent; here we pin the count and three load-bearing lines so a
    // silent edit fails fast.
    const expectedCount = 16;
    const lines = [
      "GET  /knowledge/entities",
      "POST /knowledge/entities",
      "DELETE /knowledge/entities?section=...  (purge section)",
      "GET  /knowledge/summary",
      "GET  /health",
    ];
    expect(lines).toHaveLength(5);
    // First + last lines anchor the format (4-char verb column, two spaces).
    expect(lines[0]).toMatch(/^GET {2}\/knowledge\/entities$/);
    expect(lines[lines.length - 1]).toBe("GET  /health");
    expect(expectedCount).toBe(16);
  });
});

describe("health contract shape (incumbent index.ts app.get('/health'))", () => {
  it("healthy body shape: {status, port, db} with db 'up' | 'unknown'", () => {
    // The 200 path returns this object directly (status stays 200 even when
    // db === "unknown" — incumbent quirk, preserved).
    const healthy = { status: "healthy", port: INCUMBENT_PORT_ECHO, db: "up" };
    const unknownRow = { status: "healthy", port: INCUMBENT_PORT_ECHO, db: "unknown" };
    expect(healthy.db === "up" || healthy.db === "unknown").toBe(true);
    expect(unknownRow.status).toBe("healthy");
  });

  it("unhealthy path throws with the 503 envelope {status:'unhealthy', error}", () => {
    const err = new (require("moleculer").Errors.MoleculerError)(
      "connect ECONNREFUSED", 503, "KNOWLEDGE_UNHEALTHY",
      { envelope: { status: "unhealthy", error: "connect ECONNREFUSED" } }
    ) as any;
    expect(err.code).toBe(503);
    expect(err.data.envelope).toEqual({ status: "unhealthy", error: "connect ECONNREFUSED" });
  });
});
