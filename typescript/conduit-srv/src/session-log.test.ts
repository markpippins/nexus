/**
 * Changed-line coverage for the CodeQL hardening (PR #575 + tester review
 * 269a6ca5 remediation): the /log/:sessionId SSE containment guard including
 * the realpath re-check, plus the app-level rate limiter.
 *
 * Imports the REAL app from src/app.ts — no listen/heartbeat (lifecycle
 * stays in index.ts). PIPELINE_DIR points at a temp dir so no real session
 * data is read; the session-log route performs no SQL.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Server } from "node:http";

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "conduit-slog-test-"));
process.env.PIPELINE_DIR = tmpDir;
process.env.CONDUIT_SRV_PORT = "3104";

// Dynamic import AFTER the env vars above — route modules capture
// PIPELINE_DIR at module-load time.
let app: any;

let server: Server;
let baseUrl = "";

beforeAll(async () => {
  ({ app } = await import("./app.js"));
  server = app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => server.once("listening", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as any).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe("/log/:sessionId containment (tester review 269a6ca5)", () => {
  it("rejects session ids failing the regex with the exact 400 envelope", async () => {
    const res = await fetch(`${baseUrl}/log/bad%22id`);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Invalid session ID" });
  });

  it("streams meta for a well-formed id whose log file is absent", async () => {
    const res = await fetch(`${baseUrl}/log/hardening-test-session`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    const reader = res.body!.getReader();
    const { value } = await reader.read();
    const text = new TextDecoder().decode(value);
    expect(text).toContain("session_log_meta");
    expect(text).toContain('"logFileExists":false');
    await reader.cancel();
  });

  it("streams existing log content through the realpath-hardened read path", async () => {
    const sessionsDir = path.join(tmpDir, "sessions");
    fs.mkdirSync(sessionsDir, { recursive: true });
    fs.writeFileSync(path.join(sessionsDir, "hardening-real.log"), "hello from the log\n");
    const res = await fetch(`${baseUrl}/log/hardening-real`);
    expect(res.status).toBe(200);
    const reader = res.body!.getReader();
    const { value } = await reader.read();
    const text = new TextDecoder().decode(value);
    expect(text).toContain("session_log_meta");
    expect(text).toContain("hello from the log");
    await reader.cancel();
  });
});

describe("app-level rate limiter (PR #575)", () => {
  it("sets the draft-7 RateLimit header", async () => {
    const res = await fetch(`${baseUrl}/`);
    expect(res.headers.get("ratelimit")).toMatch(/limit=300/);
  });
});
