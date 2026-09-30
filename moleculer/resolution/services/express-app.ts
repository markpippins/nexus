import express from "express";
import type { Express } from "express";
import cors from "cors";
import dotenv from "dotenv";
import { loadEnv } from "../src/env.js";
import { globalLimiter } from "../src/limiter.js";
import { healthRouter } from "../src/routes/health.js";
import { resolutionRouter } from "../src/routes/resolution.js";
import { closePool } from "../src/db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });
loadEnv();

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.ts so the moleculer gateway can dispatch into it.
 *
 * Mirrors typescript/resolution-srv/src/index.ts exactly:
 *   - cors(), express.json() (default 100kb — the incumbent raises no
 *     limit; a read-only surface needs none)
 *   - globalLimiter mounted BEFORE every route (incumbent doctrine:
 *     covers /health and /api uniformly — this is the incumbent's OWN
 *     limiter, copied verbatim from limiter.ts; no day-one addition
 *     needed, resolution-srv is already the hardened pattern)
 *   - /health from routes/health.ts (DB probe + missing-tables degraded
 *     envelope); /api carries the resolution router (meta, per-table
 *     registry routes, 405 method guard)
 *
 * Deviations from the incumbent (all lifecycle, not HTTP contract):
 *   - no app.listen / process-level handlers (the broker owns lifecycle)
 *   - no startHeartbeat() (env-gated service-registry heartbeat,
 *     RESOLUTION_SRV_SERVICE_ID — incumbent process subsystem)
 *   - no DB preflight in start() (the incumbent fails fast on broken
 *     DSN at boot; the twin's pool is module-load lazy, and the
 *     /api/health envelope surfaces connectivity per-request, which is
 *     the contract the canary observes)
 *
 * NOTE: resolution-srv ships NO migrations — resolution.* is
 * producer-owned schema; the twin therefore cannot mutate the schema by
 * construction (stronger than the usual canary never-migrates rule).
 */

const app: Express = express();

app.use(cors());
app.use(express.json());

// ── Global rate limit (covers /health and /api uniformly) ────────────
app.use(globalLimiter);

// ── Health ───────────────────────────────────────────────────────────
app.use("/health", healthRouter);

// ── Resolution API ───────────────────────────────────────────────────
app.use("/api", resolutionRouter);

export async function shutdown(): Promise<void> {
  await closePool();
}

export default app;
