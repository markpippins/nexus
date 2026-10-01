// ── index.ts ─────────────────────────────────────────────────────────────────
// Port of python/substance/main.py.
//
// Builds the Express app that replaces the FastAPI app, keeping the same
// service identity: port 3115, registry service id 117, 20s heartbeats, CORS
// wide open, /healthz liveness.

import cors from "cors";
import express, { type Express } from "express";

import { closeClient } from "./cache";
import { closePool, initPool } from "./db";
import { errorHandler } from "./http";
import { listenSegmentExpirations } from "./listener";
import { linksRouter } from "./routes/links";
import { segmentSetsRouter } from "./routes/segment-sets";

export const SERVICE_NAME = "substance";
export const SERVICE_ID = 117;
export const REGISTRY_URL = process.env.REGISTRY_URL || "http://localhost:8085";
export const DEFAULT_PORT = 3115;
export const HEARTBEAT_INTERVAL = 20; // seconds

/**
 * Periodically send a heartbeat to the service-registry (port 8085). A port of
 * the Python service's `_heartbeat_loop`.
 *
 * The registry flips a service to OFFLINE after 90s of missing heartbeats, so
 * 20s leaves room for two consecutive failures. A failed heartbeat is logged
 * and retried; it must never take the service down — Postgres is the source of
 * truth and a registry outage is not this service's problem to solve.
 *
 * This is deliberately self-contained rather than importing `heartbeat-client`:
 * that package is ESM-only and substance-srv compiles to CommonJS, and the
 * Python service carried its own loop anyway, so inlining is both the faithful
 * port and one less cross-package build-ordering dependency.
 */
export function startHeartbeatLoop(
  intervalSeconds: number = HEARTBEAT_INTERVAL,
): { stop: () => void } {
  const url = `${REGISTRY_URL}/api/v1/registry/heartbeat/${SERVICE_NAME}`;
  let stopped = false;

  const send = async (): Promise<boolean> => {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ serviceId: SERVICE_ID }),
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        console.log(`[heartbeat] ${SERVICE_NAME} OK (id=${SERVICE_ID})`);
        return true;
      }
      const body = await resp.text().catch(() => "");
      console.warn(`[heartbeat] ${SERVICE_NAME} ${resp.status}: ${body.slice(0, 200)}`);
      return false;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.warn(`[heartbeat] ${SERVICE_NAME} failed: ${message}`);
      return false;
    }
  };

  void send(); // fire immediately so the registry sees us at startup
  const timer = setInterval(() => {
    if (!stopped) {
      void send();
    }
  }, intervalSeconds * 1000);
  if (timer.unref) {
    timer.unref();
  }
  return {
    stop: () => {
      stopped = true;
      clearInterval(timer);
    },
  };
}

/**
 * Build the app. Exported separately from `start()` so the route table can be
 * asserted in tests without opening a socket or touching Postgres.
 */
export function createApp(): Express {
  const app = express();
  // CORS: allowlist via SUBSTANCE_CORS_ORIGINS (comma-separated). Default
  // "*" preserves the historical any-origin posture (the FastAPI app sent
  // ACAO: *); an explicit list tightens it with no code change. The
  // function form (rather than origin: "*") makes cors echo the request
  // origin and silently deny unlisted ones; `credentials: true` is dropped
  // — wildcard+credentials was already non-functional for credentialed
  // requests (browsers reject ACAO:* with credentials), and the
  // combination is the js/cors-permissive-configuration alert
  // (tools/security/backfill-ledger.yaml entry 1).
  const CORS_ORIGINS = (process.env.SUBSTANCE_CORS_ORIGINS ?? "*")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  app.use(
    cors({
      origin: (origin, cb) => {
        if (!origin || CORS_ORIGINS.includes("*") || CORS_ORIGINS.includes(origin)) return cb(null, true);
        return cb(null, false); // no-Origin (curl/same-origin) and allowlisted pass; others silent-deny
      },
    }),
  );
  app.use(express.json({ limit: "10mb" }));

  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok" });
  });

  // The segment-sets router carries prefix /segment-sets; the domain-links
  // router has no prefix (mounted at root), exactly as in the FastAPI app.
  app.use("/segment-sets", segmentSetsRouter);
  app.use("/", linksRouter);

  app.use(errorHandler());
  return app;
}

async function start(): Promise<void> {
  const port = Number.parseInt(process.env.SUBSTANCE_PORT || String(DEFAULT_PORT), 10);

  // Verify DB connectivity up front so a broken DSN fails fast under systemd.
  initPool();
  const { query } = await import("./db");
  await query("SELECT 1");
  console.log("[substance-srv] PostgreSQL connected (nebula.segment_sets)");
  console.log("[substance-srv] Redis client initialised (lazy connect)");

  const app = createApp();
  const server = app.listen(port, () => {
    console.log(`[substance-srv] REST API listening on http://localhost:${port}`);
    console.log(`[substance-srv] Health: http://localhost:${port}/healthz`);
    startHeartbeatLoop(HEARTBEAT_INTERVAL);
  });

  let stopping = false;
  const stop = (): boolean => {
    stopping = true;
    return stopping;
  };
  // Fire and forget: reconnects internally, and a dead listener is survivable
  // because the Redis TTL is the safety net.
  void listenSegmentExpirations(stop).catch((err) => {
    console.error("[substance-srv] listener exited:", err);
  });

  const shutdown = (signal: string) => {
    console.log(`[substance-srv] ${signal} received — draining`);
    stop();
    server.close(() => {
      void (async () => {
        try {
          await closePool();
        } catch {
          // Nothing left to do on a failing shutdown path.
        }
        closeClient();
        process.exit(0);
      })();
    });
    // Do not hang forever on a wedged connection.
    setTimeout(() => process.exit(0), 10_000).unref();
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));

  server.on("error", (err: NodeJS.ErrnoException) => {
    if (err.code === "EADDRINUSE") {
      console.error(
        `[substance-srv] port ${port} already in use, exiting (code EADDRINUSE)`,
      );
      process.exit(1);
    }
    console.error("[substance-srv] server error:", err.message);
  });
}

process.on("uncaughtException", (err: Error & { code?: string }) => {
  if (err.code === "EADDRINUSE") {
    console.error("[substance-srv] port already in use, exiting");
    process.exit(1);
  }
  console.error("[substance-srv] uncaughtException:", err.message);
});

if (require.main === module) {
  start().catch((err) => {
    console.error("[substance-srv] failed to start:", err);
    process.exit(1);
  });
}
