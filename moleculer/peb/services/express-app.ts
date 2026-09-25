import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { routes } from "./routes/index.js";
import { errorHandler, notFoundHandler } from "./error-handler.js";
import { dsnInfo } from "./db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.js so the moleculer gateway can dispatch into it.
 *
 * Deviations from typescript/peb-srv/src/index.js:
 *   - no app.listen / heartbeat / process-level handlers (broker owns
 *     lifecycle; parity surface is HTTP-only)
 *   - imports carry .js extensions (NodeNext compilation)
 *   - DAY-ONE ADDITION: global rate limiter. The incumbent carries 23 open
 *     js/missing-rate-limiting alerts (they would otherwise mirror onto this
 *     twin and trip the CodeQL alert-delta gate, exactly as they did on
 *     #531/#555/#559/#570). Same 300 req/min/IP posture as the CodeQL
 *     remediation that landed on tackle/conduit/execution/harness (PR #575).
 *     Incumbent hardening tracked separately; the limiter is additive and
 *     does not alter any envelope below the 429 ceiling.
 */
const PORT = process.env.PEB_SRV_PORT || 3111;

const app: Express = express();

app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: { message: "peb-srv rate limit exceeded" } },
  }),
);

app.get("/health", (_req, res) => res.json({ ok: true, dsn: dsnInfo, port: PORT }));

app.use("/api/peb", routes);

app.use(notFoundHandler);
app.use(errorHandler);

export default app;
