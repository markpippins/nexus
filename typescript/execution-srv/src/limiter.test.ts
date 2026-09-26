/**
 * Changed-line coverage for the CodeQL hardening (PR #575 + tester review
 * 269a6ca5 remediation): the app-level and router-level rate limiters and
 * the JSON catch-all 404, via the real Express app.
 *
 * HERMETIC: every assertion targets the 404 catch-all, which touches no
 * database. `/health` looks like the obvious probe but it queries pg and
 * answers 503 when no database is reachable — which is exactly the case in
 * CI, so a /health-based assertion fails there while passing locally. The
 * rate-limit middleware sits above the router and stamps its headers on
 * every response, so the catch-all exercises the limiter just as well.
 *
 * The pg Pool is constructed at import but never queried here; the server
 * binds an ephemeral port in-process.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import http from "node:http";

import app from "./app";

let server: http.Server;
let baseUrl = "";

beforeAll(async () => {
  server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as any).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

describe("rate limiters (PR #575, tester review 269a6ca5)", () => {
  it("sets the draft-7 RateLimit header (app-level limiter active)", async () => {
    const res = await fetch(`${baseUrl}/definitely/not/a/route`);
    expect(res.status).toBe(404);
    expect(res.headers.get("ratelimit")).toMatch(/limit=300/);
    expect(res.headers.get("ratelimit-policy")).toMatch(/300/);
  });

  it("keeps the JSON catch-all 404 envelope", async () => {
    const res = await fetch(`${baseUrl}/definitely/not/a/route`);
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({
      error: "not_found",
      hint: "execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog.",
    });
  });

  it("returns the documented 429 envelope when the ceiling is exceeded", async () => {
    let saw429 = false;
    for (let i = 0; i < 305; i++) {
      const res = await fetch(`${baseUrl}/definitely/not/a/route`);
      if (res.status === 429) {
        saw429 = true;
        expect(await res.json()).toEqual({ error: "execution-srv rate limit exceeded" });
        break;
      }
    }
    expect(saw429).toBe(true);
  }, 60000);
});
