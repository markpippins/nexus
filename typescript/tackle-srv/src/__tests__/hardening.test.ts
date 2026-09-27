/**
 * Changed-line coverage for the CodeQL hardening (PR #575 + tester review
 * 269a6ca5 remediation): the app-level rate limiter, the SSE log-path
 * containment guard, and the static-format console conversions.
 *
 * Imports the REAL Express app from src/app.ts (supertest drives actual
 * requests through the middleware stack) — this is the coverage the tester
 * review asked for. DB writes are mocked so the suite stays hermetic.
 *
 * ORDERING: the 429 test exhausts the per-window counter and runs LAST —
 * the limiter is module-level, so once tripped it stays tripped for the
 * window and would starve every later assertion in this file.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import request from "supertest";
import http from "node:http";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

vi.mock("../db", () => {
  const pool = {
    connect: vi.fn(),
    on: vi.fn(),
    end: vi.fn(async () => {}),
    query: vi.fn(),
  };
  return {
    pool,
    query: vi.fn(),
    withTransaction: vi.fn(),
    insertLog: vi.fn(async () => {}),
  };
});

vi.mock("../memory", () => ({
  initRedis: vi.fn(),
  closeRedis: vi.fn(async () => {}),
  getRedisClient: vi.fn(() => ({ get: vi.fn(), on: vi.fn() })),
}));

let app: any;
let server: http.Server;
let baseUrl = "";

beforeAll(async () => {
  const appMod = await import("../app");
  app = appMod.app;
  const dbMod = await import("../db");
  const pool = (dbMod as any).pool;
  pool.query.mockReset();
  pool.query.mockResolvedValue({ rows: [], rowCount: 0 });
  server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as any).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

describe("SSE log-path containment guard (tester review 269a6ca5)", () => {
  it("rejects session ids failing the regex with the exact 400 envelope", async () => {
    const res = await fetch(`${baseUrl}/log/bad%22id`);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Invalid session ID" });
  });

  it("opens the SSE stream for a well-formed id (realpath guard passes)", async () => {
    const res = await fetch(`${baseUrl}/log/hardening-test-session`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    await res.body?.cancel();
  });

  it("refuses to stream a log file symlinked OUTSIDE the logs dir", async () => {
    // The negative half of the guard (tester review ecfe5977 GAP 2). The
    // case above only proves the happy path. This builds the real attack: a
    // sessionId that passes the regex and whose path is textually inside the
    // logs dir, but which is a symlink resolving OUTSIDE it. A lexical
    // startsWith guard cannot see this — only realpath can.
    const tmpRoot = await mkdtemp(path.join(tmpdir(), "tackle-sse-"));
    const outsideDir = await mkdtemp(path.join(tmpdir(), "tackle-outside-"));
    const secret = path.join(outsideDir, "secret.log");
    await writeFile(secret, "TOP-SECRET-CONTENT\n");

    const logsDir = path.join(tmpRoot, "nexus", "logs");
    await mkdir(logsDir, { recursive: true });
    await symlink(secret, path.join(logsDir, "escaped.log"));

    // The route reads PIPELINE_ROOT per request, so this stays hermetic.
    const prevRoot = process.env.PIPELINE_ROOT;
    process.env.PIPELINE_ROOT = tmpRoot;
    try {
      const res = await fetch(`${baseUrl}/log/escaped`);
      expect(res.status).toBe(200);
      expect(res.headers.get("content-type")).toContain("text/event-stream");

      const reader = res.body!.getReader();
      const { value } = await reader.read();
      const text = new TextDecoder().decode(value);
      // The symlink target is not inside logs/, so it must read as absent and
      // its content must never be emitted.
      expect(text).toContain("session_log_meta");
      expect(text).toContain('"logFileExists":false');
      expect(text).not.toContain("TOP-SECRET-CONTENT");
      await reader.cancel();
    } finally {
      if (prevRoot === undefined) delete process.env.PIPELINE_ROOT;
      else process.env.PIPELINE_ROOT = prevRoot;
      await rm(tmpRoot, { recursive: true, force: true });
      await rm(outsideDir, { recursive: true, force: true });
    }
  });
});

describe("app-level rate limiter (PR #575) — runs last, exhausts the window", () => {
  it("sets the draft-7 RateLimit header on a normal request", async () => {
    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);
    expect(res.headers.get("ratelimit")).toMatch(/limit=300/);
    expect(res.headers.get("ratelimit-policy")).toMatch(/300/);
  });

  it("returns the documented 429 envelope when the ceiling is exceeded", async () => {
    let saw429 = false;
    for (let i = 0; i < 305; i++) {
      const res = await fetch(`${baseUrl}/health`);
      if (res.status === 429) {
        saw429 = true;
        expect(await res.json()).toEqual({ error: "tackle-srv rate limit exceeded" });
        break;
      }
    }
    expect(saw429).toBe(true);
  }, 60000);
});
