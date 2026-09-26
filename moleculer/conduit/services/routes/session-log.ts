// /log/:sessionId — SSE endpoint for streaming live session logs.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import { Router } from "express";
import path from "node:path";
import fs from "node:fs";

const router = Router();

// The twin is one directory deeper than the incumbent build, so the
// compatibility fallback includes that extra level.
const PIPELINE_DIR =
  process.env.PIPELINE_DIR ||
  path.resolve(__dirname, "../../../../../../nexus/audit/CONDUIT_DATA");

router.get("/:sessionId", async (req, res) => {
  const { sessionId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  // Containment guard — mirrored from the incumbent (CodeQL
  // js/path-injection remediation, alerts #752-#755).
  const sessionsDir = path.resolve(PIPELINE_DIR, "sessions");
  const logPath = path.resolve(sessionsDir, `${sessionId}.log`);
  if (!logPath.startsWith(sessionsDir + path.sep)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  let lastSize = 0;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let resolved = false;

  const sendLines = () => {
    try {
      if (!fs.existsSync(logPath)) return;
      // Re-resolve the real path at every open: a symlink swapped in after
      // the initial check could otherwise point outside the sessions dir
      // (CodeQL js/path-injection remediation — lexical startsWith guards
      // don't cover runtime symlink escapes; tester review 269a6ca5).
      const realPath = fs.realpathSync(logPath);
      if (realPath !== logPath && !realPath.startsWith(sessionsDir + path.sep)) {
        resolved = true;
        return;
      }
      const stats = fs.statSync(realPath);
      if (stats.size <= lastSize) return;
      const fd = fs.openSync(realPath, "r");
      const buf = Buffer.alloc(stats.size - lastSize);
      fs.readSync(fd, buf, 0, buf.length, lastSize);
      fs.closeSync(fd);
      lastSize = stats.size;
      const newContent = buf.toString("utf-8");
      const lines = newContent.split("\n");
      for (const line of lines) {
        if (line.length === 0) continue;
        const isStderr = line.startsWith("[stderr] ") || line.startsWith("[stderr]");
        const logType = isStderr ? "stderr" : "stdout";
        const event = JSON.stringify({
          type: "session_log",
          data: { sessionId, line, timestamp: new Date().toISOString(), logType },
        });
        res.write(`data: ${event}\n\n`);
      }
    } catch {
      // file may disappear — stop polling
    }
  };

  let logExists = false;
  try {
    const initialReal = fs.realpathSync(logPath);
    logExists = initialReal === logPath || initialReal.startsWith(sessionsDir + path.sep);
  } catch {
    logExists = false;
  }
  res.write(
    `data: ${JSON.stringify({
      type: "session_log_meta",
      data: { sessionId, logFileExists: logExists, logPath },
    })}\n\n`
  );
  if (logExists) {
    sendLines();
    pollTimer = setInterval(() => {
      if (resolved) return;
      sendLines();
    }, 500);
  }

  const keepAlive = setInterval(() => {
    if (resolved) return;
    res.write(`: keepalive\n\n`);
  }, 15000);

  req.on("close", () => {
    resolved = true;
    if (pollTimer) clearInterval(pollTimer);
    clearInterval(keepAlive);
  });
});

export default router;
