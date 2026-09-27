import express from "express";
import type { Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import routes from "./routes.js";
import { pool } from "./db.js";

dotenv.config({ path: "../../.env" });
dotenv.config({ path: ".env" });

/**
 * The incumbent's Express app, verbatim — built by this module instead of
 * index.ts so the moleculer gateway can dispatch into it.
 *
 * Deviations from typescript/aegis-srv/src/index.ts:
 *   - no app.listen / schema preflight / process-level handlers (the broker
 *     owns lifecycle; parity surface is HTTP-only)
 *   - imports carry .js extensions (NodeNext compilation)
 *   - DAY-ONE ADDITION: global rate limiter. The incumbent currently carries
 *     a js/resource-exhaustion alert in tlc-runner.ts (user-controlled TLC
 *     timeout reaching a child-process timer) that would mirror onto this
 *     twin via the verbatim copy and trip the CodeQL alert-delta gate. The
 *     limiter itself does not address that site — it hardens the surface the
 *     way PR #575 did for tackle/conduit/execution/harness; the tlc-runner
 *     clamp mirrors the incumbent-side fix (see services/tlc-runner.ts
 *     header). Both are additive; envelopes below the 429 ceiling are
 *     byte-identical.
 */
const PORT = process.env.AEGIS_SRV_PORT
  ? parseInt(process.env.AEGIS_SRV_PORT, 10)
  : 3116;

const app: Express = express();

app.use(cors());
app.use(express.json({ limit: "5mb" }));

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "aegis-srv rate limit exceeded" },
  }),
);

app.get("/health", (_req, res) => res.json({ ok: true, service: "aegis-srv" }));

app.use("/api", routes);

// 404 handler (verbatim)
app.use((_req, res) => res.status(404).json({ error: "not found" }));

// Error handler (verbatim)
app.use((err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error("[aegis-srv] handler error:", err?.message);
  res.status(500).json({ error: "internal server error", message: err?.message });
});

export default app;
