/**
 * Route-contract regression guard for the conduit-mcp HTTP client.
 *
 * The client once carried bare-/api route forms that NO deployed server
 * served; the resulting 404→null was swallowed and silently zeroed receipt
 * validation ("current state is none") fleet-wide. It was then pinned to the
 * legacy prefixes. Plan 8261651 reverses that once the consolidated router
 * (PR #938) is actually deployed — which it is, on :3103 since 2026-11-10.
 *
 * This test freezes the post-repoint contract: the client calls the
 * consolidated forms, and no legacy /api/receipts|sessions|breaker prefix
 * remains. If the server ever rolls back to the prefixed-only surface, the
 * boot-time guard in route-contract-guard.ts is what fails loudly.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// vitest runs with cwd = package root; tsc (module: commonjs) forbids import.meta.
const src = readFileSync("src/conduit-client.ts", "utf8");

describe("conduit-client route contract (consolidated :3103)", () => {
  it("receipt routes use the consolidated bare /api forms", () => {
    expect(src).toContain("get(`/api/${encodeURIComponent(planId)}`)");
    expect(src).toContain("get(`/api/${encodeURIComponent(planId)}/raw`)");
    expect(src).toContain("get(`/api/${encodeURIComponent(planId)}/latest-type`)");
    expect(src).toContain('post("/api"');
    expect(src).toContain("del(`/api/${encodeURIComponent(planId)}?types=");
  });

  it("session routes use the consolidated forms", () => {
    expect(src).toContain('get("/api")');
    expect(src).toContain("get(`/api/${encodeURIComponent(sessionId)}`)");
    expect(src).toContain('get("/api/running")');
    expect(src).toContain("get(`/api/stale?threshold_seconds=");
    expect(src).toContain("patch(`/api/${encodeURIComponent(sessionId)}/cost`");
    expect(src).toContain("post(`/api/${encodeURIComponent(sessionId)}/heartbeat`)");
    expect(src).toContain("post(`/api/${encodeURIComponent(sessionId)}/kill`)");
  });

  it("breaker routes use the consolidated forms", () => {
    expect(src).toContain('get("/api/breaker")');
    expect(src).toContain('post("/api/trip"');
    expect(src).toContain('post("/api/reset")');
    expect(src).toContain('post("/api/pause")');
    expect(src).toContain('post("/api/resume")');
    expect(src).toContain('get("/api/failure-recovery")');
    expect(src).toContain('post("/api/failure-recovery"');
  });

  it("no legacy prefixed route forms remain", () => {
    expect(src).not.toContain("/api/receipts");
    expect(src).not.toContain("/api/sessions");
    expect(src).not.toContain("/api/breaker/");
  });
});
