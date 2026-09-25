// execution-srv — Observability REST API over the PostgreSQL `execution` schema.
//
// This service is read-only. All endpoints perform SELECTs against the four
// durable nouns (execution.requests, execution.leases, execution.attempts,
// execution.receipts) and against the cross-schema lineage target
// vision.receipts. No mutation routes are mounted; if you need to write,
// conduit-mcp owns those.
//
// Pattern follows vision-srv (TypeScript + Express + pg.Pool).

import express from 'express';
import cors from 'cors';
import rateLimit from 'express-rate-limit';
import { Pool } from 'pg';
import { createRoutes } from './routes';
import { startHeartbeat } from 'heartbeat-client';

// ── PostgreSQL Connection ──────────────────────────────────────────
// The execution schema lives in the same `nexus` database as the rest of
// the system. We pin search_path to execution as the default namespace so
// unqualified table names resolve there, but cross-schema joins to
// vision.receipts still work because we qualify them explicitly.
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
const app = express();
const PORT = process.env.PORT ? parseInt(process.env.PORT) : 3110;

// ── Process-level safety net ─────────────────────────────────────
process.on('uncaughtException', (err: Error & { code?: string }) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`execution-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === 'EPIPE' || err.code === 'ECONNRESET' || err.code === 'ETIMEDOUT') {
    console.warn('[execution-srv] uncaughtException (connection noise):', err.code, err.message);
    return;
  }
  console.error('[execution-srv] uncaughtException:', err.message, err.stack?.split('\n').slice(0, 3).join('\n'));
});

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

// ── Start ──────────────────────────────────────────────────────────
const server = app.listen(PORT, () => {
  console.log(`execution-srv listening on http://localhost:${PORT}`);
  console.log(`  health:  http://localhost:${PORT}/health`);
  console.log(`  routes:  http://localhost:${PORT}/api/execution/...`);

  startHeartbeat({
    serviceId: 112,
    serviceName: 'execution-srv',
    interval: 30,
    log: (...args: any[]) => console.log(new Date().toISOString(), '[heartbeat execution-srv]', ...args),
  });
});

server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`execution-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
  } else {
    console.error('execution-srv: listen error:', err.message);
  }
  process.exit(1);
});

// ── Graceful shutdown ──────────────────────────────────────────────
process.on('SIGTERM', async () => {
  console.log('execution-srv received SIGTERM — closing pool and exiting');
  await pool.end();
  process.exit(0);
});
process.on('SIGINT', async () => {
  console.log('execution-srv received SIGINT — closing pool and exiting');
  await pool.end();
  process.exit(0);
});
