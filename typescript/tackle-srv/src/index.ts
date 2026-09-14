import express from "express";
import cors from "cors";
import fs from "fs";
import path from "path";
import { loadEnv } from "./env";
import { initRedis, closeRedis } from "./memory";
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
import { startHeartbeat } from "heartbeat-client";

const PORT = parseInt(process.env.TACKLE_SRV_PORT || "3410", 10);

// ── Process-level safety net ─────────────────────────────────────
process.on('uncaughtException', (err: Error & { code?: string }) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`tackle-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === 'EPIPE' || err.code === 'ECONNRESET' || err.code === 'ETIMEDOUT') {
    console.warn('[tackle-srv] uncaughtException (connection noise):', err.code, err.message);
    return;
  }
  console.error('[tackle-srv] uncaughtException:', err.message, err.stack?.split('\n').slice(0, 3).join('\n'));
});

const app = express();
app.use(cors());
app.use(express.json());

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
  const logPath = path.join(projectRoot, "nexus", "logs", `${sessionId}.log`);

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  let lastSize = 0;
  let resolved = false;

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

// ── Start ─────────────────────────────────────────────────────────

async function start() {
  const { initDb } = await import("./db");
  await initDb();
  console.log("[tackle-srv] PostgreSQL initialized (tackle schema)");

  initRedis();
  console.log("[tackle-srv] Redis client initialized (lazy connect)");

  const server = app.listen(PORT, () => {
    console.log(`Tackle REST server listening on http://localhost:${PORT}`);
    console.log(`Health: http://localhost:${PORT}/health`);
    console.log(`AI Config: http://localhost:${PORT}/config/ai`);

    startHeartbeat({
      serviceId: 119,
      serviceName: 'tackle-srv',
      interval: 30,
      log: (...args: any[]) => console.log(new Date().toISOString(), '[heartbeat tackle-srv]', ...args),
    });
  });

  server.on('error', (err: NodeJS.ErrnoException) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`tackle-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    } else {
      console.error('tackle-srv: listen error:', err.message);
    }
    process.exit(1);
  });
}

start().catch((err) => {
  console.error("Failed to start tackle-srv:", err);
  process.exit(1);
});

// Graceful shutdown
process.on("SIGINT", async () => {
  console.log("[tackle-srv] Shutting down...");
  await closeRedis();
  process.exit(0);
});
process.on("SIGTERM", async () => {
  console.log("[tackle-srv] Shutting down...");
  await closeRedis();
  process.exit(0);
});
