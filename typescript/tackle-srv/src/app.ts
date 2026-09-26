import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import fs from "fs";
import path from "path";
import { aiConfigRouter } from "./routes/ai-config";
import { sessionsRouter } from "./routes/sessions";
import { rolesRouter } from "./routes/roles";
import { schedulerRouter } from "./routes/scheduler";
import { memoryRouter } from "./routes/memory";
import { promptsRouter } from "./routes/prompts";
import { toolAccessRouter } from "./routes/tool-access";
import { failureRecoveryRouter } from "./routes/failure-recovery";
import { tasksRouter } from "./routes/tasks";
import { logsRouter } from "./routes/logs";
import { auditRouter } from "./routes/audit";
import { healthRouter } from "./routes/health";
import { projectionsRouter } from "./routes/projections";
import { insertLog } from "./db";

/**
 * The tackle-srv Express app, extracted verbatim from index.ts (PR #575
 * remediation, tester review 269a6ca5 finding 1: "no tests cover the changed
 * lines" — the app must be importable without triggering listen/heartbeat).
 * index.ts builds the process lifecycle (initDb/Redis/heartbeat/SIGINT)
 * around this app; the middleware order and every handler are unchanged.
 */
const PORT = parseInt(process.env.TACKLE_SRV_PORT || "3410", 10);

export const app = express();
app.use(cors());
app.use(express.json());

// Global request limiter — CodeQL js/missing-rate-limiting remediation
// (alerts #402/#571/#608/#616). Same posture as nebula-srv's limiter.ts:
// 300 req/min/IP keeps normal operator/agent polling well clear of the
// ceiling while capping resource-exhaustion floods.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "tackle-srv rate limit exceeded" },
  }),
);

// Request logging middleware — fire-and-forget async DB writes
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    const duration = Date.now() - start;
    const level = res.statusCode >= 400 ? 'ERROR' : 'INFO';
    console.log(
      `[${new Date().toISOString()}] ${req.method} ${req.path} ${res.statusCode} ${duration}ms`,
    );
    // Fire-and-forget: never block the response on log writes
    insertLog({
      level,
      category: 'API_ROUTER',
      message: `${req.method} ${req.path} → ${res.statusCode}`,
      source: `tackle-srv :${PORT}`,
      details: { duration_ms: duration, query: Object.keys(req.query).length ? req.query : undefined },
    }).catch(e => console.error('[tackle-srv] log write failed:', e.message));
  });
  next();
});

// ── Health ─────────────────────────────────────────────────────────

app.get("/health", async (_req, res) => {
  res.json({
    status: "ok",
    port: PORT,
    pid: process.pid,
    timestamp: new Date().toISOString(),
  });
});

// ── Route mounting ────────────────────────────────────────────────

// ── Session log SSE ────────────────────────────────────────────────
// Stream nexus/logs/<sessionId>.log (test/verify invocations write there).
// Mirrors tackle-mcp's /log/:sessionId so the UI proxy chain
// (tackle-ui :4202 → tackle-srv :3410) can stream logs — previously the
// route only existed on tackle-mcp and the UI's log polls 404'd.
app.get("/log/:sessionId", (req, res) => {
  const { sessionId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  const projectRoot = process.env.PIPELINE_ROOT || "/home/codex/dev";
  // Containment guard — sessionId is regex-validated above, but resolve
  // defensively so the streamed path can never escape the logs dir
  // (CodeQL js/path-injection remediation, alerts #592-#595 family).
  const logsDir = path.resolve(projectRoot, "nexus", "logs");
  const logPath = path.resolve(logsDir, `${sessionId}.log`);
  if (!logPath.startsWith(logsDir + path.sep)) {
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
  let resolved = false;

  // Real (symlink-resolved) logs dir — this is the containment BASE for the
  // poll loop below. Comparing the realpath of the log file against the
  // realpath of the directory is what actually closes the symlink-escape
  // hole: a lexical `startsWith(logsDir)` is satisfied by any path that
  // merely looks like it is under logs/, including one reached through a
  // symlink swapped in after the request-time check. Resolving the base too
  // also keeps the comparison correct when the project root itself sits
  // behind a symlink (realPath would then never lexically start with
  // logsDir, and a naive check would refuse to stream a legitimate log).
  let realLogsDir = logsDir;
  try {
    realLogsDir = fs.realpathSync(logsDir);
  } catch {
    // logs dir not present yet — realpathSync in the poll below throws
    // ENOENT until it appears, which the catch handles.
  }

  const sendLines = () => {
    try {
      // Resolve and validate FIRST, before touching the file at all. There
      // is deliberately no fs.existsSync(logPath) pre-check here: it would
      // be an fs operation on the unvalidated user-derived path, and
      // realpathSync already throws ENOENT for a missing file (including a
      // dangling symlink), which the surrounding catch handles exactly as
      // the old `return` did. Containment is then a single unconditional
      // check that dominates every fs call below it
      // (CodeQL js/path-injection remediation).
      const realPath = fs.realpathSync(logPath);
      if (!realPath.startsWith(realLogsDir + path.sep)) {
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
      for (const line of newContent.split("\n")) {
        if (line.length === 0) continue;
        const event = JSON.stringify({
          type: "session_log",
          data: { sessionId, line, timestamp: new Date().toISOString() },
        });
        res.write(`data: ${event}\n\n`);
      }
    } catch {
      /* file may disappear — stop polling */
    }
  };

  let logExists = false;
  try {
    const initialReal = fs.realpathSync(logPath);
    logExists = initialReal.startsWith(realLogsDir + path.sep);
  } catch {
    logExists = false;
  }
  res.write(
    `data: ${JSON.stringify({
      type: "session_log_meta",
      data: { sessionId, logFileExists: logExists },
    })}\n\n`,
  );

  const interval = setInterval(sendLines, 1000);
  const timeout = setTimeout(() => {
    res.write(
      `data: ${JSON.stringify({ type: "session_log_end", data: { sessionId } })}\n\n`,
    );
    res.end();
    clearInterval(interval);
  }, 30000);

  req.on("close", () => {
    clearInterval(interval);
    clearTimeout(timeout);
    res.end();
  });
});

app.use("/config/ai", aiConfigRouter);
app.use("/sessions", sessionsRouter);
app.use("/roles", rolesRouter);
app.use("/scheduler", schedulerRouter);
app.use("/memory", memoryRouter);
app.use("/prompts", promptsRouter);
app.use("/config/ai/tool-access", toolAccessRouter);
app.use("/config/failure-recovery", failureRecoveryRouter);
app.use("/tasks", tasksRouter);
app.use("/logs", logsRouter);
app.use("/audit-trail", auditRouter);
app.use("/projections", projectionsRouter);
app.use("/health", healthRouter);

export default app;
