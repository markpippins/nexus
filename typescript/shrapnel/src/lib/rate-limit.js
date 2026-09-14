import { rateLimit } from 'express-rate-limit';

// Positive-integer env override helper (exported for unit tests).
export function parsePositiveIntEnv(name, dflt) {
  const n = Number.parseInt(process.env[name] ?? '', 10);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

const json = { error: { message: 'too_many_requests' } };

// ── Global API limiter ───────────────────────────────────────────────────
// Mounted app-wide before every /api route in src/index.js. Exists to close
// the CodeQL js/missing-rate-limiting class (the service performs database
// access on every route) and to bound accidental load on the EAV store.
//
// Defaults are deliberately generous for LAN-internal consumers; tune with
// SHRAPNEL_RATE_WINDOW_MS / SHRAPNEL_RATE_LIMIT. The store is per-process
// memory — correct for the single-instance systemd deployment this service
// runs under (see README "Rate limiting").
export const apiLimiter = rateLimit({
  windowMs: parsePositiveIntEnv('SHRAPNEL_RATE_WINDOW_MS', 60_000),
  limit: parsePositiveIntEnv('SHRAPNEL_RATE_LIMIT', 300),
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: json,
});

// ── Write limiter ────────────────────────────────────────────────────────
// Stricter budget for mutating endpoints (object create/delete, encode,
// field create, revision creation, classification). Skips GET/HEAD/OPTIONS
// so it can be mounted on routers that mix reads and writes.
export const writeLimiter = rateLimit({
  windowMs: parsePositiveIntEnv('SHRAPNEL_WRITE_RATE_WINDOW_MS', 60_000),
  limit: parsePositiveIntEnv('SHRAPNEL_WRITE_RATE_LIMIT', 60),
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: json,
  skip: (req) => ['GET', 'HEAD', 'OPTIONS'].includes(req.method),
});
