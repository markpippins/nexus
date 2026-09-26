// execution-srv — Observability REST API over the PostgreSQL `execution` schema.
//
// This service is read-only. All endpoints perform SELECTs against the four
// durable nouns (execution.requests, execution.leases, execution.attempts,
// execution.receipts) and against the cross-schema lineage target
// vision.receipts. No mutation routes are mounted; if you need to write,
// conduit-mcp owns those.
//
// Pattern follows vision-srv (TypeScript + Express + pg.Pool).
//
// The Express app itself lives in ./app.ts (extracted verbatim so tests can
// import it without triggering listen/heartbeat — PR #575, tester review
// 269a6ca5). This file owns the process lifecycle only.

import { startHeartbeat } from 'heartbeat-client';
import { app, pool } from './app';

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
