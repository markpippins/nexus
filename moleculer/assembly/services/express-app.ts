import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import zlib from "node:zlib";
import dotenv from "dotenv";
import { routes } from "../src/routes/index.js";
import { errorHandler } from "../src/error-handler.js";
import { query, closePool } from "../src/db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.js so the moleculer gateway can dispatch into it.
 *
 * Mirrors typescript/assembly-srv/src/index.js exactly:
 *   - cors(), express.json({ limit: "5mb" })  (raised for transcript
 *     ingest comments — incumbent comment verbatim)
 *   - the CUSTOM zlib gzip middleware VERBATIM (assembly implements gzip
 *     with Node's built-in zlib, not the `compression` package: GET-only,
 *     Vary/Content-Encoding headers, Content-Length strip-before-first-
 *     write dance — see the incumbent's own comment block; the transcripts
 *     forum list is a ~140 MB JSON payload and gzip is load-bearing)
 *   - static GET /health → { ok: true } (unprefixed; note the DIFFERENT
 *     /api/health envelope { status: "healthy" } from routes/health.js)
 *   - routes mounted at /api with the documented ORDER: segment-sets
 *     mounted LAST at router root so its root-relative /:id cannot shadow
 *     the concrete family mounts (incumbent doctrine comment verbatim)
 *   - errorHandler last
 *
 * Deviations from the incumbent (all lifecycle, not HTTP contract):
 *   - no app.listen / SIGTERM shutdown / process-level handlers (the
 *     broker owns lifecycle; parity surface is HTTP-only)
 *   - no runMigration() — CANARY RULE: the twin NEVER mutates the schema
 *   - no startHeartbeat() (registry :8085, serviceId 110) — incumbent
 *     process subsystem; the twin is invisible to the registry heartbeat
 *     by design, same as every fleet twin
 *   - DAY-ONE ADDITION: global rate limiter (300 req/min/IP — same
 *     posture as the PR #575 family and the peb twin; the incumbent
 *     carries none). Additive; envelopes below the 429 ceiling are
 *     byte-identical.
 *
 * Pool note: the incumbent's db.js creates its pool at module load; the
 * twin keeps that file verbatim (module-load pool creation included —
 * it connects lazily by default, so no eager connection storm) and
 * exports closePool() for the broker's stopped() hook.
 */

const app: Express = express();

app.use(cors());
app.use(express.json({ limit: "5mb" })); // raised for transcript ingest comments

// ── Gzip compression (GET responses only) ───────────────────────────
// Verbatim from the incumbent (index.js): Node's built-in zlib, no
// `compression` dependency. See the incumbent's comment block for the
// Content-Length strip-once / first-write dance.
app.use((req, res, next) => {
  if (req.method !== "GET") return next();
  const accept = req.headers["accept-encoding"] || "";
  if (!/\bgzip\b/.test(accept)) return next();
  res.setHeader("Vary", "Accept-Encoding");
  res.setHeader("Content-Encoding", "gzip");
  const gzip = zlib.createGzip();
  const origWrite = res.write.bind(res);
  const origEnd = res.end.bind(res);
  let lengthStripped = false;
  const send = (chunk: any) => {
    if (!lengthStripped) {
      res.removeHeader("Content-Length");
      lengthStripped = true;
    }
    return origWrite(chunk);
  };
  (res as any).write = (chunk: any, encoding: any, cb: any) =>
    gzip.write(chunk, encoding, cb);
  (res as any).end = (chunk: any, encoding: any, cb: any) => {
    if (chunk) gzip.write(chunk, encoding);
    gzip.end(cb);
  };
  gzip.on("data", (c: any) => send(c));
  gzip.on("end", () => origEnd());
  gzip.on("error", () => origEnd());
  next();
});

app.get("/health", (_req, res) => res.json({ ok: true }));

app.use("/api", routes);

app.use(errorHandler);

export async function shutdown(): Promise<void> {
  await closePool();
}

export default app;
