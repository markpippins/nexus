import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { errorHandler } from "./http.js";
import { linksRouter } from "./routes/links.js";
import { segmentSetsRouter } from "./routes/segment-sets.js";
import { initPool, getPool } from "./db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.ts so the moleculer gateway can dispatch into it.
 *
 * Mirrors the incumbent's createApp() (index.ts) exactly:
 *   - cors({ origin: "*", credentials: true }), express.json 10mb
 *   - /healthz liveness — NO DB probe (incumbent /healthz is static
 *     {status:"ok"}; the DB preflight lived in start(), which the broker
 *     replaces). Parity keeps the static envelope.
 *   - segment-sets router at /segment-sets; links router at root (the
 *     /{domain_type}/{domain_id}/segment-sets surface) — same mounting and
 *     route ORDER as the incumbent (from-segments before the UUID parser)
 *
 * Deviations from typescript/substance-srv/src/index.ts:
 *   - no app.listen / DB preflight / heartbeat loop / pg-notify listener /
 *     SIGTERM shutdown (the broker owns lifecycle; parity surface is
 *     HTTP-only). The heartbeat (registry :8085, id 117) and the
 *     segment_expired cache invalidation listener are process background
 *     subsystems, not HTTP contract; the twin leaves them to the incumbent
 *     and relies on the Redis TTL as the staleness bound, exactly as the
 *     listener itself documents.
 *   - DAY-ONE ADDITION: global rate limiter (300 req/min/IP — same posture
 *     as the PR #575 family; the incumbent carries none). Additive;
 *     envelopes below the 429 ceiling are byte-identical.
 */
const app: Express = express();

app.use(cors({ origin: "*", credentials: true }));
app.use(express.json({ limit: "10mb" }));

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { detail: "substance-srv rate limit exceeded" },
  }),
);

app.get("/healthz", (_req, res) => {
  res.json({ status: "ok" });
});

// Same mounting and order as the incumbent's createApp():
// segment-sets carries its prefix; the domain-links router is mounted at root.
app.use("/segment-sets", segmentSetsRouter);
app.use("/", linksRouter);

app.use(errorHandler());

export default app;

// Exported for the service module: the incumbent lazily initialises the pool
// on first query; the twin initialises it eagerly at service start so the
// first canary request behaves like a warm incumbent.
export function ensureDb(): void {
  try {
    getPool();
  } catch {
    initPool();
  }
}
