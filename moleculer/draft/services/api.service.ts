import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of draft-srv.
 *
 * The alias map is the ENTIRE parity surface: checked against
 * typescript/draft-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLLECULER_MIRRORS) — a renamed alias fails CI exactly like a
 * renamed Express route.
 *
 * Route shape mirrors the incumbent (index.ts):
 *   app.use('/api', dbWorkbenchRoutes())  → /api/db/*
 *   app.get('/api/health')                → liveness (secret-exempt)
 *
 * Security Pass Alpha (audit C1, decision 22fe12bc) is REPLICATED: the
 * X-Nexus-Internal fleet secret is enforced on every route except the
 * liveness probe, fail-closed (503 when unconfigured, 403 on mismatch). The
 * data-explorer-ui server proxy already sends the header, so callers are
 * unchanged. Envelope parity per route family is in draft.service.ts.
 *
 * NOTE: unlike the incumbent (loopback bind), the gateway binds 0.0.0.0 —
 * the canary twin is probed from its own port; the secret gate still guards
 * every data route.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4170,
        ip: process.env.SERVICE_IP || "0.0.0.0",

        routes: [
          {
            path: "/api",

            // TRAP (see moleculer/README.md): masks are path-style — use `**`.
            whitelist: ["draft.**"],

            // Security Pass Alpha — fleet secret gate. Route options hook:
            // onBeforeCall runs after alias resolution but before the action
            // (moleculer-web 0.10 has no onRequest route option — an
            // `onRequest` key is silently ignored, which is how the first
            // canary run got 200s past the gate). Throwing here routes
            // through onError, which renders the incumbent envelopes.
            // The unmatched-route path is gated in onNotFound below, which
            // reproduces the incumbent's app.use ordering (gate precedes
            // routing entirely).
            onBeforeCall(ctx: any, route: any, req: any, res: any, alias: any) {
              const secret = process.env.NEXUS_INTERNAL_SECRET;
              const { Errors } = require("moleculer");
              // Liveness exemption (incumbent: req.path === '/api/health').
              const full =
                String(req?.$route?.path ?? "").replace(/\/$/, "") + String(req?.url ?? "");
              if (req.method === "GET" && full.replace(/\/+$/, "") === "/api/health") {
                return;
              }
              if (!secret) {
                throw new Errors.MoleculerError(
                  "service misconfigured: missing internal secret", 503, "MISCONFIGURED");
              }
              if ((req.headers || {})['x-nexus-internal'] !== secret) {
                throw new Errors.MoleculerError("forbidden", 403, "FORBIDDEN");
              }
            },

            aliases: {
              "GET /health": "draft.health",
              "GET /db/engines": "draft.db.engines",
              "POST /db/test-connection": "draft.db.testConnection",
              "POST /db/databases": "draft.db.databases",
              "POST /db/schemas": "draft.db.schemas",
              "POST /db/query": "draft.db.query",
            },

            onNotFound(req: any, res: any) {
              // The secret gate precedes routing in the incumbent (app.use
              // order), so an unauthenticated request to an unknown path is a
              // 403, not a 404. With the header, unknown routes get Express's
              // finalhandler HTML page (kernel-port finding).
              const secret = process.env.NEXUS_INTERNAL_SECRET;
              if (!secret || (req.headers || {})['x-nexus-internal'] !== secret) {
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.statusCode = 403;
                res.end(JSON.stringify({ error: "forbidden" }));
                return;
              }
              const base = String(req?.$route?.path ?? "").replace(/\/$/, "");
              const full = base + String(req?.url ?? req?.originalUrl ?? "");
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(
                `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${full}</pre>\n</body>\n</html>\n`
              );
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              // Unmatched routes arrive here as moleculer-web's built-in
              // NotFoundError (send404 -> sendError -> onError; onNotFound
              // only fires when NO route matched the prefix at all — the
              // kernel-port finding again). The gate precedes routing in the
              // incumbent, so unauthenticated misses are 403; authenticated
              // misses get Express's finalhandler HTML page.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                const secret = process.env.NEXUS_INTERNAL_SECRET;
                if (!secret || (req.headers || {})['x-nexus-internal'] !== secret) {
                  res.setHeader("Content-Type", "application/json; charset=utf-8");
                  res.statusCode = 403;
                  res.end(JSON.stringify({ error: "forbidden" }));
                  return;
                }
                const base = String(req?.$route?.path ?? "").replace(/\/$/, "");
                const full = base + String(req?.url ?? req?.originalUrl ?? "");
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(
                  `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${full}</pre>\n</body>\n</html>\n`
                );
                return;
              }
              const data = (err as any)?.data ?? {};
              // Action errors carry the route-family envelope in .data
              // ({success,message} for test-connection, {error} elsewhere).
              const envelope = (data as any).envelope;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              if (envelope && typeof envelope === "object") {
                res.end(JSON.stringify(envelope));
                return;
              }
              res.end(JSON.stringify({ error: (err as any)?.message ?? String(err) }));
            },
          },
        ],

        log4XXResponses: false,
        logRequestParams: "info",
        logResponseData: "info",
      },
    });
  }
}
