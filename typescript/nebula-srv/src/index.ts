import express from 'express';
import cors from 'cors';
import { Pool } from 'pg';
import { createRoutes } from './routes';
import { runMigrations } from './migrate';
import { initRedis, closeRedis } from './services/block-segmentation-redis.service';

// ── PostgreSQL Connection ──────────────────────────────────────────
// Env overrides added for container deploys (vanadium failover tier);
// defaults preserve the native localhost configuration.
const pool = new Pool({
  host: process.env.PG_HOST || 'localhost',
  port: parseInt(process.env.PG_PORT || '5432', 10),
  user: process.env.PG_USER || 'pguser',
  password: process.env.PG_PASSWORD || process.env.PG_PASS || 'pgpass',
  database: process.env.PG_DB_NAME || 'nexus',
  options: '-c search_path=nebula',
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

// ── Express Setup ──────────────────────────────────────────────────
const app = express();
app.disable('x-powered-by');
const PORT = process.env.PORT ? parseInt(process.env.PORT) : 3101;

app.use(cors());
app.use(express.json({ limit: '5mb' }));  // raised for transcript docklang payloads

// ── API Routes ────────────────────────────────────────────────────
app.use('/api', createRoutes(pool));

// ── Health Check ───────────────────────────────────────────────────
async function healthHandler(_req: express.Request, res: express.Response) {
  try {
    const { rows } = await pool.query('SELECT 1 as ok');
    res.json({ status: 'ok', db: rows[0].ok === 1 });
  } catch (err: any) {
    res.status(503).json({ status: 'error', message: err.message });
  }
}

app.get('/health', healthHandler);
// Mount /api/health too — the Nebula UI proxy forwards /api/health here
app.get('/api/health', healthHandler);

// ── Redis init ──────────────────────────────────────────────────────
try {
  initRedis();
  console.log('[redis] block-segmentation client initialized (lazy connect)');
} catch (err: any) {
  console.warn('[redis] block-segmentation init failed (non-fatal):', err.message);
}

// ── Migrations + Start ─────────────────────────────────────────────
let server: ReturnType<typeof app.listen> | undefined;

// D-2026-08-16-007 (R5): sweep stale ACTIVE role leases → EXPIRED on start
// and periodically. Non-destructive (never RELEASED mid-session).
function sweepRoleLeases() {
  fetch(`http://localhost:${PORT}/api/role-leases/sweep`, { method: 'POST' })
    .then((r) => r.json())
    .then((body: any) => {
      if (body && body.swept > 0) console.log(`[role-leases] swept ${body.swept} stale lease(s)`);
    })
    .catch((err: any) => console.warn('[role-leases] sweep failed:', err.message));
}

async function start() {
  try {
    await runMigrations(pool);
  } catch (err: any) {
    console.error('nebula-srv: migration failure — refusing to start:', err.message);
    process.exit(1);
    return;
  }
  // G4 decommission (df8ff49d edge-monopoly): default bind is loopback;
    // LAN exposure must come from the control-edge (:8082), not this service.
  const BIND_HOST = process.env.BIND_HOST || "127.0.0.1";
  server = app.listen(Number(PORT), BIND_HOST, () => {
    console.log(`nebula-srv listening on http://${BIND_HOST}:${PORT}`);
    sweepRoleLeases();
    setInterval(sweepRoleLeases, 10 * 60 * 1000).unref();
  });

  // Handle listen-time errors (e.g. EADDRINUSE) cleanly so a port conflict
  // produces a one-line log line + non-zero exit instead of an unhandled
  // 'error' event that crashes the process with a stack trace. This also
  // lets systemd's Restart=on-failure behave predictably: a taken port is
  // a fatal-but-clean condition, not a crash.
  server.on('error', (err: NodeJS.ErrnoException) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`nebula-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    } else {
      console.error('nebula-srv: listen error:', err.message);
    }
    process.exit(1);
  });
}

start();

// ── Process-level safety net ─────────────────────────────────────
// Prevent unhandled errors from crashing the process.
// Redis connection errors, EPIPE on stale sockets, and other async
// I/O errors can fire after responses are sent; log them instead of
// killing the server. This is the last-resort safety net — route
// handlers should still use their own try/catch for granular errors.
process.on('uncaughtException', (err: Error & { code?: string }) => {
  // EADDRINUSE is a startup fatal — exit cleanly.
  if (err.code === 'EADDRINUSE') {
    console.error(`nebula-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  // EPIPE, ECONNRESET, ETIMEDOUT are connection-level noise — log and continue.
  if (err.code === 'EPIPE' || err.code === 'ECONNRESET' || err.code === 'ETIMEDOUT') {
    console.warn('[process] uncaughtException (connection noise):', err.code, err.message);
    return;
  }
  console.error('[process] uncaughtException:', err.message, err.stack?.split('\n').slice(0, 3).join('\n'));
});

// ── Unhandled rejection safety net ───────────────────────────────
// In Node.js v24, unhandled promise rejections exit the process.
// This handler catches them before the default behaviour fires so
// a single bad async path doesn't kill the server.
process.on('unhandledRejection', (reason: any) => {
  // PG pool connection errors come through as unhandled rejections
  // when a client 'error' event fires after release.
  const code = reason?.code;
  if (code === 'EPIPE' || code === 'ECONNRESET' || code === 'ETIMEDOUT' || code === 'ECONNREFUSED') {
    console.warn('[process] unhandledRejection (connection noise):', code, reason?.message ?? '');
    return;
  }
  console.error('[process] unhandledRejection:', reason?.message ?? require('util').inspect(reason, { depth: 2 }));
  if (reason?.stack) {
    console.error('[process]   stack:', reason.stack.split('\n').slice(0, 4).join('\n'));
  }
});

// ── Graceful shutdown ──────────────────────────────────────────────
process.on('SIGTERM', async () => {
  console.log('[server] SIGTERM received, shutting down...');
  await closeRedis();
  await pool.end();
  server?.close(() => process.exit(0));
});

process.on('SIGINT', async () => {
  console.log('[server] SIGINT received, shutting down...');
  await closeRedis();
  await pool.end();
  server?.close(() => process.exit(0));
});
