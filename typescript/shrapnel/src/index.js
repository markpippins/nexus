import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import { routes } from './routes/index.js';
import { errorHandler, notFoundHandler } from './error-handler.js';
import { dsnInfo } from './db.js';
import { apiLimiter } from './lib/rate-limit.js';
import { startHeartbeat } from 'heartbeat-client';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const app = express();
const PORT = process.env.SHRAPNEL_SRV_PORT || 3110;

// ── Process-level safety net ─────────────────────────────────────
process.on('uncaughtException', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`shrapnel-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === 'EPIPE' || err.code === 'ECONNRESET' || err.code === 'ETIMEDOUT') {
    console.warn('[shrapnel] uncaughtException (connection noise):', err.code, err.message);
    return;
  }
  console.error('[shrapnel] uncaughtException:', err.message, err.stack?.split('\n').slice(0, 3).join('\n'));
});

app.use(cors());
app.use(express.json({ limit: '4mb' }));

// Global rate limit for every /api route (see lib/rate-limit.js). Mounted
// before routing so all handlers are covered — closes the CodeQL
// js/missing-rate-limiting class for this service.
app.use(apiLimiter);

app.get('/health', (_req, res) => res.json({ ok: true, dsn: dsnInfo, port: PORT }));

app.use('/api', routes);

app.use(notFoundHandler);
app.use(errorHandler);

const server = app.listen(PORT, () => {
  console.log(`shrapnel-srv listening on http://localhost:${PORT}  (dsn: ${dsnInfo})`);

  startHeartbeat({
    // registry.services row: id=54, name='shrapnel' (registered 2026-07-29).
    // Previously hardcoded as 117, which did not match the registry row —
    // heartbeats would upsert the wrong record. Aligned to the existing row.
    serviceId: 54,
    serviceName: 'shrapnel',
    interval: 30,
    log: (...args) => console.log(new Date().toISOString(), '[heartbeat shrapnel]', ...args),
  });
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`shrapnel-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
  } else {
    console.error('shrapnel-srv: listen error:', err.message);
  }
  process.exit(1);
});

process.on('SIGINT', () => {
  console.log('[shrapnel] SIGINT received, shutting down...');
  server.close(() => process.exit(0));
});
process.on('SIGTERM', () => {
  console.log('[shrapnel] SIGTERM received, shutting down...');
  server.close(() => process.exit(0));
});

export { app, server };
