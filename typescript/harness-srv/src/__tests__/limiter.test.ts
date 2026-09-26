/**
 * Changed-line coverage for the CodeQL hardening (PR #575, tester review
 * ecfe5977 GAP 1): the global rate limiter at src/app.ts.
 *
 * The other three services in this PR (tackle-srv, conduit-srv,
 * execution-srv) each received a limiter test; harness-srv shipped the
 * limiter with none, which is exactly the untested CodeQL-closing fix
 * this PR exists to prevent. This closes that gap against the REAL app —
 * src/app.ts is the module under test, so removing or reconfiguring the
 * limiter fails these assertions.
 *
 * Ordering note: the 429 case exhausts the per-window counter, so it must
 * run LAST in this file.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import http from "node:http";

import app from "../app";

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

describe("global rate limiter (PR #575, tester GAP 1) — runs last, exhausts the window", () => {
  it("sets the draft-7 RateLimit headers on a normal request", async () => {
    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);
    // express-rate-limit 7.5.1 emits `ratelimit` (combined) + `ratelimit-policy`
    // for draft-7 — NOT the legacy `ratelimit-limit`/`x-ratelimit-*` names.
    expect(res.headers.get("ratelimit")).toMatch(/limit=300/);
    expect(res.headers.get("ratelimit-policy")).toMatch(/300/);
  });

  it("does not emit legacy X-RateLimit-* headers", async () => {
    const res = await fetch(`${baseUrl}/health`);
    expect(res.headers.get("x-ratelimit-limit")).toBeNull();
  });

  it("returns the documented 429 envelope once the ceiling is exceeded", async () => {
    let saw429 = false;
    for (let i = 0; i < 305; i++) {
      const res = await fetch(`${baseUrl}/health`);
      if (res.status === 429) {
        saw429 = true;
        expect(await res.json()).toEqual({
          error: "harness-srv rate limit exceeded",
        });
        break;
      }
    }
    expect(saw429).toBe(true);
  }, 60000);
});
