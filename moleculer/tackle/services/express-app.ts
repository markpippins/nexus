import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import fs from "fs";
import path from "path";
import { loadEnv } from "./env.js";
import { initRedis, closeRedis } from "./memory.js";
import { aiConfigRouter } from "./routes/ai-config.js";
import { sessionsRouter } from "./routes/sessions.js";
import { rolesRouter } from "./routes/roles.js";
import { schedulerRouter } from "./routes/scheduler.js";
import { memoryRouter } from "./routes/memory.js";
import { promptsRouter } from "./routes/prompts.js";
import { toolAccessRouter } from "./routes/tool-access.js";
import { failureRecoveryRouter } from "./routes/failure-recovery.js";
import { tasksRouter } from "./routes/tasks.js";
import { logsRouter } from "./routes/logs.js";
import { auditRouter } from "./routes/audit.js";
import { healthRouter } from "./routes/health.js";
import { projectionsRouter } from "./routes/projections.js";
import { insertLog } from "./db.js";

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.ts so the moleculer gateway can dispatch into it.
 *
 * ONLY deviations from typescript/tackle-srv/src/index.ts:
 *   - no app.listen / heartbeat / process-level uncaughtException handler
 *     (lifecycle belongs to the broker; parity surface is HTTP-only)
 *   - routes imported with .js extensions (NodeNext compilation)
 *   - PORT constant inlined where the log line references it (the twin
 *     logs its own port via SERVICE_PORT at the gateway layer)
 * Everything else — cors(), express.json(), the request-log middleware,
 * /health, the /log/:sessionId SSE route, and all 13 router mounts — is
 * byte-identical to the incumbent.
 */

const PORT = parseInt(process.env.TACKLE_SRV_PORT || "3410", 10);

const app = express();
app.use(cors());
app.use(express.json());

// Global request limiter — mirrored from the incumbent (CodeQL
// js/missing-rate-limiting remediation): 300 req/min/IP, nebula-srv posture.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "tackle-srv rate limit exceeded" },
  }),
);

// Request logging middleware — fire-and-forget async DB writes (verbatim)
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
// (verbatim — the twin reports its own port/pid like the incumbent does;
//  canary normalization covers the pid/port fields)

app.get("/health", async (_req, res) => {
  res.json({
    status: "ok",
    port: PORT,
    pid: process.pid,
    timestamp: new Date().toISOString(),
  });
});

// ── Session log SSE (verbatim) ─────────────────────────────────────
// Stream nexus/logs/<sessionId>.log (test/verify invocations write there).
// Mirrors tackle-mcp's /log/:sessionId so the UI proxy chain
// (tackle-ui :4202 → tackle-srv :3410) can stream logs.

app.get("/log/:sessionId", (req, res) => {
  const { sessionId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  const projectRoot = process.env.PIPELINE_ROOT || "/home/codex/dev";
  // Containment guard — mirrored from the incumbent (CodeQL
  // js/path-injection remediation).
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
  const sendLines = () => {
    try {
      if (!fs.existsSync(logPath)) return;
      const stats = fs.statSync(logPath);
      if (stats.size <= lastSize) return;

      const fd = fs.openSync(logPath, "r");
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

  const logExists = fs.existsSync(logPath);
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

// ── Route mounting (verbatim) ──────────────────────────────────────

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

export async function initTackleApp(): Promise<void> {
  loadEnv();
  const { initDb } = await import("./db.js");
  await initDb();
  initRedis();
}

export async function closeTackleApp(): Promise<void> {
  try {
    await closeRedis();
  } catch {
    /* already closed */
  }
  // The incumbent never closes the pool on shutdown; end it here only so
  // the twin exits cleanly in canary runs (process death is the incumbent's
  // shutdown path — same effect).
  try {
    const { getDb } = await import("./db.js");
    await getDb().end();
  } catch {
    /* pool not initialized */
  }
}

export default app;
