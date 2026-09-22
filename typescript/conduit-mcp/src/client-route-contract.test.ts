/**
 * Route-contract regression guard (Conduit walk-through finding, 2026-09-22).
 *
 * The client once carried pre-consolidation bare-/api route forms that NO
 * deployed server served; the resulting 404→null was swallowed and silently
 * zeroed receipt validation ("current state is none") fleet-wide. This guard
 * asserts the live-contract shapes against the source, matching the
 * source-assertion style of python/conduit's test_c3_single_fanout.py.
 *
 * If the consolidated kernel (plan 8261651) actually deploys the no-prefix
 * forms, update BOTH this guard and the routes in the same change.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// vitest runs with cwd = package root; tsc (module: commonjs) forbids import.meta.
const src = readFileSync("src/conduit-client.ts", "utf8");

describe("conduit-client route contract (live :3103)", () => {
  it("receipt routes use the /api/receipts prefix", () => {
    expect(src).toContain('get(`/api/receipts/${encodeURIComponent(planId)}`)');
    expect(src).toContain('get(`/api/receipts/${encodeURIComponent(planId)}/raw`)');
    expect(src).toContain('get(`/api/receipts/${encodeURIComponent(planId)}/latest-type`)');
    expect(src).toContain('post("/api/receipts/"');
    expect(src).toContain('del(`/api/receipts/${encodeURIComponent(planId)}?types=');
  });

  it("session routes use the /api/sessions prefix", () => {
    expect(src).toContain('get("/api/sessions/")');
    expect(src).toContain('get(`/api/sessions/${encodeURIComponent(sessionId)}`)');
    expect(src).toContain('get("/api/sessions/running")');
    expect(src).toContain('get(`/api/sessions/stale?threshold_seconds=');
    expect(src).toContain('patch(`/api/sessions/${encodeURIComponent(sessionId)}/cost`');
    expect(src).toContain('post(`/api/sessions/${encodeURIComponent(sessionId)}/heartbeat`)');
    expect(src).toContain('post(`/api/sessions/${encodeURIComponent(sessionId)}/kill`)');
  });

  it("breaker routes use the /api/breaker prefix", () => {
    expect(src).toContain('get("/api/breaker/")');
    expect(src).toContain('post("/api/breaker/trip"');
    expect(src).toContain('post("/api/breaker/reset")');
    expect(src).toContain('"/api/breaker/pause" : "/api/breaker/resume"');
    expect(src).toContain('get("/api/breaker/failure-recovery")');
    expect(src).toContain('post("/api/breaker/failure-recovery"');
  });

  it("no bare-/api pre-consolidation forms remain", () => {
    // The failure mode: bare /api/{id}... paths that no server serves.
    expect(src).not.toMatch(/get\(`\/api\/\$\{encodeURIComponent\(/);
    expect(src).not.toMatch(/patch\(`\/api\/\$\{encodeURIComponent\(/);
    expect(src).not.toMatch(/post\(`\/api\/\$\{encodeURIComponent\(/);
    expect(src).not.toMatch(/await del\(`\/api\/\$\{/);
    expect(src).not.toContain('get("/api")');
    expect(src).not.toContain('post("/api"');
    expect(src).not.toContain('get("/api/running")');
    expect(src).not.toContain('get("/api/stale');
    expect(src).not.toContain('post("/api/trip"');
    expect(src).not.toContain('post("/api/reset")');
  });
});
