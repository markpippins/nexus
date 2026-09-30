import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { routes } from "./routes/index.js";
import { errorHandler } from "./error-handler.js";
import { pool } from "./db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.js so the moleculer gateway can dispatch into it.
 *
 * Deviations from typescript/wind-srv/src/index.js:
 *   - no app.listen / heartbeat-client / process-level handlers / shutdown
 *     coordinator (the broker owns lifecycle; parity surface is HTTP-only)
 *   - no rover scheduler / event-processor / nats-listener startup: those are
 *     BACKGROUND subsystems of the incumbent process (nebula.harvest polling,
 *     NATS publishing, pg-notify listeners). They are not part of the HTTP
 *     contract; the route modules never import them. The twin deliberately
 *     leaves them out so a canary run cannot double-publish harvest.created
 *     events or double-poll the wind.events queue alongside the incumbent.
 *   - imports carry .js extensions (NodeNext compilation; verbatim files kept
 *     plain-JS under allowJs, peb-twin style)
 *   - DAY-ONE ADDITION: global rate limiter. The incumbent rate-limits only
 *     three write surfaces (execution, provider-contracts, node-requirements
 *     POSTs); the remaining 54 paths would trip the CodeQL alert-delta gate
 *     exactly as they did on #531/#555/#559/#570. Same 300 req/min/IP posture
 *     as the PR #575 remediation family. Additive; envelopes below the 429
 *     ceiling are byte-identical.
 *   - the incumbent /health probes the DB (503 on failure); preserved verbatim.
 */
const PORT = process.env.WIND_SRV_PORT
  ? parseInt(process.env.WIND_SRV_PORT, 10)
  : 3300;

const app: Express = express();

app.use(cors());
app.use(express.json({ limit: "1mb" }));

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "wind-srv rate limit exceeded" },
  }),
);

app.get("/health", async (_req, res) => {
  try {
    const client = await pool.connect();
    await client.query("SELECT 1");
    client.release();
    res.json({ ok: true, schema: "wind" });
  } catch (err: any) {
    res.status(503).json({ ok: false, error: err?.message });
  }
});

app.use("/api", routes);

// Error handler (verbatim import — the same module the incumbent mounts).
// NOTE: the incumbent has NO JSON catch-all 404 (unlike aegis-srv) — unknown
// routes fall through to Express's default HTML 404. Parity keeps that.
app.use(errorHandler);

export default app;
