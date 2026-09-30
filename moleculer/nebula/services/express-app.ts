import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { Pool } from "pg";
import { createRoutes } from "../src/routes.js";
import { initRedis, closeRedis } from "../src/services/block-segmentation-redis.service.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.ts so the moleculer gateway can dispatch into it.
 *
 * Mirrors typescript/nebula-srv/src/index.ts exactly:
 *   - cors(), express.json({ limit: "5mb" }) — raised for transcript
 *     docklang payloads (same comment as incumbent)
 *   - createRoutes(pool) mounted at /api (identical mount point)
 *   - /health AND /api/health — same handler, same DB probe, same 503
 *     error envelope ({ status: "error", message }) — the /api/health
 *     alias exists because the Nebula UI proxy forwards it (incumbent
 *     comment verbatim)
 *
 * The pool is the TWIN'S OWN (lazy-initialized on first ensureDb() call);
 * env overrides match the incumbent (PG_HOST/PG_PORT/PG_USER/PG_PASSWORD|PG_PASS/
 * PG_DB_NAME, search_path=nebula, max 10).
 *
 * Deviations from the incumbent (all lifecycle, not HTTP contract):
 *   - no app.listen / BIND_HOST logic (the broker owns the socket; G4
 *     loopback doctrine is enforced by the systemd unit, not the app)
 *   - no runMigrations() — CANARY RULE: the twin must NEVER mutate the
 *     schema. The incumbent's fail-closed migrate gate (Decision 23) runs
 *     in the incumbent process only.
 *   - no sweepRoleLeases() self-call loop (a 10-min POST to its own
 *     /api/role-leases/sweep — state mutation; incumbent process
 *     subsystem, deliberately not started; lease expiry is enforced by
 *     the incumbent regardless of which process serves reads)
 *   - no process-level safety nets / SIGTERM handlers (broker-owned)
 *   - DAY-ONE ADDITION: global rate limiter (300 req/min/IP — same
 *     posture as the PR #575 family; the incumbent carries only the
 *     narrow attestations limiter). Additive; envelopes below the 429
 *     ceiling are byte-identical.
 */

let pool: Pool | null = null;

/** Lazy pool init — called by the service's started() hook. */
export function ensureDb(): Pool {
  if (!pool) {
    pool = new Pool({
      host: process.env.PG_HOST || "localhost",
      port: parseInt(process.env.PG_PORT || "5432", 10),
      user: process.env.PG_USER || "pguser",
      password: process.env.PG_PASSWORD || process.env.PG_PASS || "pgpass",
      database: process.env.PG_DB_NAME || "nexus",
      options: "-c search_path=nebula",
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });
  }
  return pool;
}

export async function closeDb(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

const app: Express = express();

app.use(cors());
app.use(express.json({ limit: "5mb" })); // raised for transcript docklang payloads

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "nebula-srv rate limit exceeded" },
  }),
);

// ── API Routes ────────────────────────────────────────────────────
app.use("/api", createRoutes(ensureDb()));

// ── Health Check ───────────────────────────────────────────────────
async function healthHandler(_req: express.Request, res: express.Response) {
  try {
    const { rows } = await ensureDb().query("SELECT 1 as ok");
    res.json({ status: "ok", db: rows[0].ok === 1 });
  } catch (err: any) {
    res.status(503).json({ status: "error", message: err.message });
  }
}

app.get("/health", healthHandler);
// Mount /api/health too — the Nebula UI proxy forwards /api/health here
app.get("/api/health", healthHandler);

/** Graceful close for the broker's stopped() hook. */
export async function shutdown(): Promise<void> {
  await closeRedis();
  await closeDb();
}

export default app;
