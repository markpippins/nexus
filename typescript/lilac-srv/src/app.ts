import express, { type Express, type NextFunction, type Request, type Response } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { SUPPORTED_CONTRACT_VERSION } from "./contract";
import type { Db } from "./repository";
import { LilacRepository } from "./repository";
import { buildHandlers, errorHandler, type RouteHandler } from "./routes";

export interface AppOptions {
  db: Db;
  contractVersion?: number;
  /** Disable the limiter in unit tests that hammer the surface. */
  limiter?: boolean;
}

/**
 * Build the app without binding a port, so the whole surface is exercisable
 * in-process (supertest) and a test can never pass by talking to whatever
 * happens to be listening on a dev machine.
 *
 * Route registration is deliberately LITERAL here rather than a loop over
 * ROUTE_TABLE: `tools/api-docs/extract_routes.py` reads literal
 * `app.<verb>("<path>")` calls from the entry module, and a service it cannot
 * see is a service whose openapi.yaml is never checked. ROUTE_TABLE remains the
 * single source of truth for the envelope and the contract test, and
 * `servesEveryRouteInTheTable` fails if the two ever disagree.
 */
export function createApp(opts: AppOptions): Express {
  const app = express();
  app.use(cors());
  app.use(express.json({ limit: "1mb" }));

  if (opts.limiter !== false) {
    app.use(
      rateLimit({
        windowMs: 60 * 1000,
        max: 600,
        standardHeaders: "draft-7",
        legacyHeaders: false,
        message: { outcome: "refused", code: "RATE_LIMITED" },
      }),
    );
  }

  const repo = new LilacRepository(opts.db);
  const handlers = buildHandlers(repo, opts.contractVersion ?? SUPPORTED_CONTRACT_VERSION);

  const wrap =
    (operationId: string) =>
    (req: Request, res: Response, next: NextFunction): void => {
      const handler: RouteHandler | undefined = handlers[operationId];
      if (!handler) {
        next(new Error(`no handler registered for ${operationId}`));
        return;
      }
      handler(req, res).catch(next);
    };

  app.get("/health", wrap("health"));
  app.get("/v1/meta", wrap("meta"));
  app.post("/v1/receipts", wrap("issueReceipt"));
  app.post("/v1/tickets", wrap("issueTicket"));
  app.post("/v1/tickets/:ticket_id/transitions", wrap("recordTransition"));
  app.post("/v1/fanouts", wrap("recordFanout"));
  app.get("/v1/tickets/:ticket_id/timeline", wrap("ticketTimeline"));
  app.get("/v1/parity", wrap("parityDiagnostics"));

  app.use(errorHandler);
  return app;
}
