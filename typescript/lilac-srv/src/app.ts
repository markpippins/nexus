import express, { type Express } from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { SUPPORTED_CONTRACT_VERSION } from "./contract";
import type { Db } from "./repository";
import { LilacRepository } from "./repository";
import { buildRouter, errorHandler } from "./routes";

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
  app.use(buildRouter(repo, opts.contractVersion ?? SUPPORTED_CONTRACT_VERSION));
  app.use(errorHandler);
  return app;
}
