/**
 * Route-contract guard tests — hermetic (no network; fetch is injected).
 *
 * Covers: template matching (params, segments), openapi path/method lookup,
 * the happy contract, drift detection with the exact PR #448 failure shape
 * (bare /api/{plan_id}/latest-type missing while /api/receipts/... serves),
 * and unreachable-server semantics.
 */
import { describe, expect, it } from "vitest";
import { checkRouteContract, matchesTemplate, routeIsServed, REQUIRED_ROUTES } from "./route-contract-guard";

const LIVE_PATHS: Record<string, Record<string, unknown>> = {
  "/api/receipts/": { post: {} },
  "/api/receipts/{plan_id}": { get: {}, delete: {} },
  "/api/receipts/{plan_id}/latest-type": { get: {} },
  "/api/receipts/{plan_id}/raw": { get: {} },
  "/api/sessions/": { get: {} },
  "/api/sessions/running": { get: {} },
  "/api/sessions/stale": { get: {} },
  "/api/sessions/{session_id}": { get: {}, patch: {} },
  "/api/sessions/{session_id}/cost": { patch: {} },
  "/api/sessions/{session_id}/heartbeat": { post: {} },
  "/api/sessions/{session_id}/kill": { post: {} },
  "/api/breaker/": { get: {} },
  "/api/breaker/trip": { post: {} },
  "/api/breaker/reset": { post: {} },
  "/api/breaker/pause": { post: {} },
  "/api/breaker/resume": { post: {} },
  "/api/breaker/failure-recovery": { get: {}, post: {} },
  "/healthz": { get: {} },
};

function fakeFetch(paths: Record<string, Record<string, unknown>>, ok = true, status = 200): typeof fetch {
  return (async () =>
    new Response(JSON.stringify({ paths }), { status: ok ? status : 500 })) as unknown as typeof fetch;
}

describe("matchesTemplate", () => {
  it("matches exact paths", () => {
    expect(matchesTemplate("/healthz", "/healthz")).toBe(true);
  });

  it("matches {param} templates regardless of parameter name", () => {
    expect(matchesTemplate("/api/receipts/{plan_id}/latest-type", "/api/receipts/{plan_id}/latest-type")).toBe(true);
    expect(matchesTemplate("/api/receipts/{plan_id}", "/api/receipts/{planId}")).toBe(true);
    expect(matchesTemplate("/api/sessions/{session_id}", "/api/sessions/{sid}")).toBe(true);
  });

  it("rejects segment-count mismatches", () => {
    expect(matchesTemplate("/api/receipts/{plan_id}", "/api/receipts/8261654/latest-type")).toBe(false);
  });

  it("keeps param positions distinct from literals", () => {
    expect(matchesTemplate("/api/{plan_id}/latest-type", "/api/receipts/8261654/latest-type")).toBe(false);
  });
});

describe("routeIsServed / checkRouteContract", () => {
  it("every required route is served by the live-shaped contract", async () => {
    const r = await checkRouteContract("http://localhost:3103", fakeFetch(LIVE_PATHS));
    expect(r.serverReachable).toBe(true);
    expect(r.ok).toBe(true);
    expect(r.missing).toHaveLength(0);
    expect(r.checked).toBe(REQUIRED_ROUTES.length);
  });

  it("flags the exact PR #448 drift shape (bare /api/{plan_id}/latest-type missing)", async () => {
    const drifted: Record<string, Record<string, unknown>> = {};
    for (const [p, m] of Object.entries(LIVE_PATHS)) {
      // Drop the receipts sub-routes, as the pre-consolidation server would.
      if (p.startsWith("/api/receipts")) continue;
      drifted[p] = m;
    }
    const r = await checkRouteContract("http://localhost:3103", fakeFetch(drifted));
    expect(r.ok).toBe(false);
    expect(r.missing.length).toBeGreaterThanOrEqual(5);
    expect(r.missing.some((m) => m.path.includes("latest-type"))).toBe(true);
  });

  it("flags a method mismatch (path served, verb absent)", async () => {
    const paths = { ...LIVE_PATHS };
    delete paths["/api/breaker/trip"];
    paths["/api/breaker/trip"] = { get: {} };
    const r = await checkRouteContract("http://localhost:3103", fakeFetch(paths));
    expect(r.ok).toBe(false);
    expect(r.missing.some((m) => m.method === "POST" && m.path === "/api/breaker/trip")).toBe(true);
  });

  it("server unreachable → reachable=false, ok=false, error set", async () => {
    const r = await checkRouteContract("http://localhost:3103", (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch);
    expect(r.serverReachable).toBe(false);
    expect(r.ok).toBe(false);
    expect(r.error).toContain("ECONNREFUSED");
  });
});
