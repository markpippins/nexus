import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
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
import { app } from "./app";

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
