import express from "express";
import cors from "cors";
import { loadEnv } from "./env";
import { healthRouter } from "./routes/health";
import { resolutionRouter } from "./routes/resolution";
import { startHeartbeat } from "heartbeat-client";

// ── Load .env ────────────────────────────────────────────────────────
loadEnv();

const PORT = parseInt(process.env.RESOLUTION_SRV_PORT || "3171", 10);
// Service id in the service-registry (port 8085) — register the service and
// set RESOLUTION_SRV_SERVICE_ID to enable heartbeats. 0 disables.
const HEARTBEAT_SERVICE_ID = parseInt(process.env.RESOLUTION_SRV_SERVICE_ID || "0", 10);

// ── Process-level safety net ─────────────────────────────────────────
process.on("uncaughtException", (err: Error & { code?: string }) => {
  if (err.code === "EADDRINUSE") {
    console.error(`resolution-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === "EPIPE" || err.code === "ECONNRESET" || err.code === "ETIMEDOUT") {
    console.warn("[resolution-srv] uncaughtException (connection noise):", err.code, err.message);
    return;
  }
  console.error("[resolution-srv] uncaughtException:", err.message, err.stack?.split("\n").slice(0, 3).join("\n"));
});

const app = express();
app.use(cors());
app.use(express.json());

// ── Health ───────────────────────────────────────────────────────────
app.use("/health", healthRouter);

// ── Resolution API ───────────────────────────────────────────────────
app.use("/api", resolutionRouter);

// ── Start ────────────────────────────────────────────────────────────
async function start() {
  // Verify DB connectivity up front so a broken DSN fails fast under systemd.
  const { getDb } = await import("./db");
  await getDb().query("SELECT 1");
  console.log("[resolution-srv] PostgreSQL connected (nexus DB, resolution schema)");

  const server = app.listen(PORT, () => {
    console.log(`[resolution-srv] REST API listening on http://localhost:${PORT}`);
    console.log(`[resolution-srv] Health: http://localhost:${PORT}/health`);
    console.log(`[resolution-srv] Meta:   http://localhost:${PORT}/api/meta`);

    if (HEARTBEAT_SERVICE_ID > 0) {
      startHeartbeat({
        serviceId: HEARTBEAT_SERVICE_ID,
        serviceName: "resolution-srv",
        interval: 30,
        log: (...args: any[]) => console.log(new Date().toISOString(), "[heartbeat resolution-srv]", ...args),
      });
    }
  });

  server.on("error", (err: NodeJS.ErrnoException) => {
    console.error("[resolution-srv] server error:", err.message);
    if (err.code === "EADDRINUSE") process.exit(1);
  });
}

start().catch((err) => {
  console.error("[resolution-srv] fatal startup error:", err.message);
  process.exit(1);
});
