import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { Pool } from "pg";
import { createRoutes } from "./routes";

/**
 * The execution-srv Express app, extracted verbatim from index.ts (PR #575
 * remediation, tester review 269a6ca5 finding 1: "no tests cover the changed
 * lines" — the app must be importable without triggering listen/heartbeat).
 * index.ts builds the process lifecycle (pool/uncaughtException/SIGTERM/
 * listen) around this app; the middleware order and every handler are
 * unchanged.
 */

const pool = new Pool({
  host: process.env.PGHOST || 'localhost',
  port: process.env.PGPORT ? parseInt(process.env.PGPORT) : 5432,
  user: process.env.PGUSER || 'pguser',
  password: process.env.PGPASSWORD || 'pgpass',
  database: process.env.PGDATABASE || 'nexus',
  options: '-c search_path=execution',
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

// ── Express Setup ──────────────────────────────────────────────────
export const app = express();

app.use(cors());
app.use(express.json({ limit: '1mb' }));

// Global request limiter — CodeQL js/missing-rate-limiting remediation
// (alerts #668/#671 family, 18 sites). Same posture as nebula-srv's
// limiter.ts: 300 req/min/IP keeps normal operator/agent polling (this
// service is read-only observability) well clear of the ceiling while
// capping resource-exhaustion floods.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: 'draft-7',
    legacyHeaders: false,
    message: { error: 'execution-srv rate limit exceeded' },
  }),
);

// ── API Routes ─────────────────────────────────────────────────────
app.use('/api/execution', createRoutes(pool));

// ── Health Check ───────────────────────────────────────────────────
// Two-level health: process-up + DB-reachable. The integrity-scan
// endpoint (/api/execution/health/integrity-scan) is the deeper check.
app.get('/health', async (_req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT
         (SELECT count(*) FROM requests)   AS requests,
         (SELECT count(*) FROM leases)     AS leases,
         (SELECT count(*) FROM attempts)   AS attempts,
         (SELECT count(*) FROM receipts)   AS receipts`
    );
    res.json({
      status: 'ok',
      db: true,
      schema: 'execution',
      counts: rows[0],
    });
  } catch (err: any) {
    res.status(503).json({ status: 'error', db: false, message: err.message });
  }
});

// ── 404 for unknown routes (read-only service) ─────────────────────
app.use((_req, res) => {
  res.status(404).json({
    error: 'not_found',
    hint: 'execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog.',
  });
});

export { pool };
export default app;
