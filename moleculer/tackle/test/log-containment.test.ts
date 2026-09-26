/**
 * Session-log path containment (CodeQL js/path-injection, PR #531/#575).
 *
 * The SSE route at GET /log/:sessionId validates sessionId against
 * /^[a-zA-Z0-9_-]+$/ and then resolves the path defensively. A lexical
 * `startsWith(logsDir)` check is NOT sufficient: a path that merely
 * *looks* like it is under logs/ satisfies it, which is exactly what a
 * symlink produces. The route therefore resolves the realpath of the base
 * directory and requires the resolved file to sit under it.
 *
 * This asserts the property directly — a sessionId that passes the regex
 * but names a symlink to a file OUTSIDE the logs dir must be reported as
 * absent and must never stream the target's content.
 *
 * PIPELINE_ROOT is set before the dynamic import because express-app.ts
 * reads it at module load.
 */

import os from "node:os";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "tackle-log-containment-"));
process.env.PIPELINE_ROOT = tmpRoot;
process.env.TACKLE_SRV_PORT = "0";

describe("GET /log/:sessionId symlink containment", () => {
  let server: http.Server;
  let baseUrl = "";

  beforeAll(async () => {
    const { createRequire } = await import("module");
    const req = createRequire(__filename);
    const app = req("../services/express-app").default;
    server = http.createServer(app);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    baseUrl = `http://127.0.0.1:${(server.address() as any).port}`;
  });

  afterAll(async () => {
    // forceExit is set in jest.config.js, but close the listener explicitly so
    // the suite does not leave a TCP handle behind.
    await new Promise<void>((resolve) => server.close(() => resolve()));
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }, 15000);

  test("rejects a session id that fails the regex", async () => {
    const res = await fetch(`${baseUrl}/log/bad%22id`);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Invalid session ID" });
  });

  test("refuses to stream a log symlinked OUTSIDE the logs dir", async () => {
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "tackle-outside-"));
    const secret = path.join(outsideDir, "secret.log");
    fs.writeFileSync(secret, "TOP-SECRET-CONTENT\n");

    const logsDir = path.join(tmpRoot, "nexus", "logs");
    fs.mkdirSync(logsDir, { recursive: true });
    fs.symlinkSync(secret, path.join(logsDir, "escaped.log"));

    const res = await fetch(`${baseUrl}/log/escaped`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    // Read only the first SSE frame — the stream stays open by design, so a
    // full-body read would block until the route's end-of-stream timer.
    const reader = res.body!.getReader();
    const { value } = await reader.read();
    const text = new TextDecoder().decode(value);

    expect(text).toContain("session_log_meta");
    // The symlink target is not inside logs/, so it must be reported absent.
    expect(text).toContain('"logFileExists":false');
    expect(text).not.toContain("TOP-SECRET-CONTENT");
    await reader.cancel();

    fs.rmSync(outsideDir, { recursive: true, force: true });
  });

  test("streams a legitimate log that really lives in the logs dir", async () => {
    const logsDir = path.join(tmpRoot, "nexus", "logs");
    fs.mkdirSync(logsDir, { recursive: true });
    fs.writeFileSync(path.join(logsDir, "legit.log"), "ordinary log line\n");

    const res = await fetch(`${baseUrl}/log/legit`);
    expect(res.status).toBe(200);
    const reader = res.body!.getReader();

    // Frame 1 is the meta event, which is where the containment verdict
    // appears: a real log inside logs/ must be reported as present.
    const first = await reader.read();
    const firstText = new TextDecoder().decode(first.value);
    expect(firstText).toContain("session_log_meta");
    expect(firstText).toContain('"logFileExists":true');

    // Frame 2+ carries the content — the route emits an initial sendLines()
    // immediately, then polls once a second.
    const second = await reader.read();
    const secondText = new TextDecoder().decode(second.value);
    expect(secondText).toContain("ordinary log line");
    await reader.cancel();
  });
});
