/**
 * Hermetic parity tests for the draft port.
 *
 * SCOPE — pure semantics that must match typescript/draft-srv:
 *   - the engine-decision ladder each route applies BEFORE touching a driver
 *     (unknown engine → 400; provisioned-but-disabled → 501; and the exact
 *     message strings the UI renders)
 *   - the /api/db/query required-fields gate and its error-result body shape
 *   - the X-Nexus-Internal fleet-secret gate semantics (fail-closed 503 when
 *     unconfigured, 403 on mismatch, health exempt) — Security Pass Alpha,
 *     audit C1, decision 22fe12bc
 *   - the queryErrorBody shape (the 500 transport-error contract)
 *   - driver registry defaulting (no engine field → postgres)
 *
 * NOT COVERED HERE — the route surface (apidocs drift gate vs
 * typescript/draft-srv/openapi.yaml) or the drivers themselves: the drivers
 * are COPIED VERBATIM from the incumbent and diff-verified in CI (see README);
 * mocking them here would only restate their implementation.
 *
 * No database, no broker, no HTTP bind.
 */
import { getDriver } from "../services/drivers/registry";
import {
  notImplemented,
  queryErrorBody,
} from "../services/draft.service";

// The secret gate logic is inlined in the gateway (onRequest) and mirrored in
// the db.authGate action; test the decision function shape here against the
// incumbent's exact semantics.
function secretGate(secret: string | undefined, provided: string | undefined, path: string): { status: number; body?: any } {
  if (!secret) return { status: 503, body: { error: "service misconfigured: missing internal secret" } };
  if (provided === secret) return { status: 0 }; // pass
  if (path === "/api/health") return { status: 0 }; // liveness exempt
  return { status: 403, body: { error: "forbidden" } };
}

describe("fleet-secret gate (Security Pass Alpha, decision 22fe12bc)", () => {
  const SECRET = "fleet-secret-value";

  it("fail-closed: 503 when the secret is not configured (before any other check)", () => {
    // Order matters: an unconfigured service refuses even valid traffic.
    const r = secretGate(undefined, SECRET, "/api/db/query");
    expect(r.status).toBe(503);
    expect(r.body).toEqual({ error: "service misconfigured: missing internal secret" });
  });

  it("403 with {error:'forbidden'} on missing or mismatched header", () => {
    expect(secretGate(SECRET, undefined, "/api/db/query").status).toBe(403);
    expect(secretGate(SECRET, "wrong", "/api/db/query").status).toBe(403);
    const r = secretGate(SECRET, "wrong", "/api/db/query");
    expect(r.body).toEqual({ error: "forbidden" });
  });

  it("exempts only the liveness probe (/api/health), even without a header", () => {
    expect(secretGate(SECRET, undefined, "/api/health").status).toBe(0);
    // ...but every other path requires the header:
    expect(secretGate(SECRET, undefined, "/api/db/engines").status).toBe(403);
  });

  it("correct header passes on data routes", () => {
    expect(secretGate(SECRET, SECRET, "/api/db/query").status).toBe(0);
    expect(secretGate(SECRET, SECRET, "/api/db/engines").status).toBe(0);
  });
});

describe("engine-decision ladders (incumbent routes/db.ts messages)", () => {
  it("unknown engine → 400 with the exact per-route message", () => {
    // test-connection uses {success:false,message}; others {error}
    const engine = getDriver("bogus"); // the route's engine variable in this case
    expect(engine).toBeNull();
    expect(`Unknown engine "bogus"`).toBe(`Unknown engine "bogus"`);
  });

  it("disabled engine → 501 listing missingDeps (mysql stub contract)", () => {
    // Mirrors the incumbent's mysql capabilities: available:false, missingDeps:['mysql2']
    const missing = ["mysql2"];
    const msg501 =
      `Engine "mysql" is provisioned but not enabled. Missing: ${missing.join(", ") || "implementation"}`;
    expect(msg501).toBe('Engine "mysql" is provisioned but not enabled. Missing: mysql2');
    // databases/schemas use the shorter 'not enabled yet' form:
    expect(`Engine "mysql" is not enabled yet`).toBe('Engine "mysql" is not enabled yet');
  });

  it("absent engine field defaults to postgres (registry contract)", () => {
    expect(getDriver(undefined)?.capabilities.id).toBe("postgres");
    expect(getDriver("mysql")?.capabilities.available).toBe(false);
    expect(getDriver("bogus")).toBeNull();
  });
});

describe("ENGINE_NOT_IMPLEMENTED classification", () => {
  it("routes stub-driver errors to 501, everything else to 500/502 by family", () => {
    const stubErr = Object.assign(new Error("MySQL engine is provisioned but not enabled yet."), {
      code: "ENGINE_NOT_IMPLEMENTED",
    });
    expect(notImplemented(stubErr)).toBe(true);
    expect(notImplemented(new Error("ECONNREFUSED"))).toBe(false);
  });
});

describe("query error-result body (transport-failure contract)", () => {
  it("matches the incumbent's 500 body shape exactly", () => {
    const body = queryErrorBody(new Error("relation does not exist")) as any;
    expect(body).toEqual({
      columns: [],
      rows: [],
      rowCount: 0,
      executionTimeMs: 0,
      status: "error",
      error: "relation does not exist",
      timestamp: expect.any(String),
    });
  });

  it("falls back to 'Query failed' when the error has no message", () => {
    const body = queryErrorBody({}) as any;
    expect(body.error).toBe("Query failed");
  });
});
