import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { query } from "./db/client.js";

const PORT = parseInt(process.env.CONDUIT_SRV_PORT || "3104", 10);
import workflowsRouter from "./routes/workflows.js";
import ticketsRouter from "./routes/tickets.js";
import tokensRouter from "./routes/tokens.js";
import configRouter from "./routes/config.js";
import governanceRouter from "./routes/governance.js";
import visionRouter from "./routes/vision.js";
import sessionLogRouter from "./routes/session-log.js";
import wrRouter from "./routes/wr.js";

/**
 * The conduit-srv Express app, extracted verbatim from index.ts (PR #575
 * remediation, tester review 269a6ca5 finding 1: the changed lines had no
 * importable app to test). index.ts keeps the process lifecycle
 * (listen/heartbeat/SIGINT); middleware order and handlers are unchanged.
 */
export const app = express();

app.use(cors());
app.use(express.json({ limit: "2mb" }));

// Global request limiter — CodeQL js/missing-rate-limiting remediation
// (alert #757). Same posture as nebula-srv's limiter.ts: 300 req/min/IP
// keeps normal operator/agent polling well clear of the ceiling while
// capping resource-exhaustion floods.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "conduit-srv rate limit exceeded" },
  }),
);

// ── Route mounting ──────────────────────────────────────────────────
// Routes are mounted at root level for backward compatibility —
// existing consumers (nexus-console, Python conduit, etc.) call
// these paths without a prefix and must continue to work unchanged.

app.use("/workflows", workflowsRouter);
app.use("/tickets", ticketsRouter);
app.use("/tokens", tokensRouter);
app.use("/config", configRouter);
app.use("/governance", governanceRouter);
app.use("/vision", visionRouter);
app.use("/log", sessionLogRouter);
app.use("/wr", wrRouter);

// ── Root health check ───────────────────────────────────────────────

app.get("/", (_req, res) => {
  res.json({
    name: "conduit-srv",
    version: "1.0.0",
    port: PORT,
    source: "conduit/vision/peb/tackle PostgreSQL schemas",
    description: "REST API extracted from conduit-mcp per Architect decision (No SQL in MCP Servers)",
    endpoints: [
      "GET    /workflows",
      "POST   /tickets/detect",
      "GET    /tickets/lineage/:planId",
      "GET    /tokens/plan/:planId",
      "GET    /tokens/role/:role",
      "GET    /tokens/ticket/:ticketId",
      "GET    /config/cron",
      "GET    /config/failure-recovery",
      "POST   /config/failure-recovery",
      "GET    /log/:sessionId (SSE)",
      "POST   /governance/replay",
      "GET    /governance/events",
      "POST   /vision/work-requests",
      "GET    /vision/work-requests",
      "GET    /vision/work-requests/:id",
      "GET    /vision/receipts",
      "GET    /health",
    ],
  });
});

app.get("/health", async (_req, res) => {
  try {
    const rows = await query("SELECT 1 AS ok");
    res.json({
      status: "ok",
      port: PORT,
      db: rows[0]?.ok === 1 ? "up" : "unknown",
      timestamp: new Date().toISOString(),
    });
  } catch (err: any) {
    res.status(503).json({ status: "error", error: err.message });
  }
});

// ── Start ───────────────────────────────────────────────────────────

export default app;
