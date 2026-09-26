import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { pool } from "./db.js";
import { createRoutes } from "./routes.js";

/**
 * The incumbent's Express app, built without listen/heartbeat/process-level
 * handlers so the Moleculer broker owns lifecycle. Route and envelope behavior
 * remains the incumbent's implementation, including the read-only catch-all
 * 404 JSON body and the two-level /health check.
 */
const PORT = process.env.PORT ? parseInt(process.env.PORT) : 3110;
const app = express();

app.use(cors());
app.use(express.json({ limit: "1mb" }));

// Global request limiter — mirrored from the incumbent (CodeQL
// js/missing-rate-limiting remediation): 300 req/min/IP, nebula-srv posture.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "execution-srv rate limit exceeded" },
  }),
);

// ── API Routes ─────────────────────────────────────────────────────
app.use("/api/execution", createRoutes(pool));

// ── Health Check ───────────────────────────────────────────────────
// Two-level health: process-up + DB-reachable. The integrity-scan
// endpoint (/api/execution/health/integrity-scan) is the deeper check.
app.get("/health", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT
         (SELECT count(*) FROM requests)   AS requests,
         (SELECT count(*) FROM leases)     AS leases,
         (SELECT count(*) FROM attempts)   AS attempts,
         (SELECT count(*) FROM receipts)   AS receipts`
    );
    res.json({
      status: "ok",
      db: true,
      schema: "execution",
      counts: rows[0],
    });
  } catch (err: any) {
    res.status(503).json({ status: "error", db: false, message: err.message });
  }
});

// ── 404 for unknown routes (read-only service) ─────────────────────
app.use((_req, res) => {
  res.status(404).json({
    error: "not_found",
    hint: "execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog.",
  });
});

export default app;
