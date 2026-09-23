import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of kernel-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/kernel-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLLECULER_MIRRORS) — a renamed alias fails CI exactly like a
 * renamed Express route.
 *
 * Route shape mirrors the incumbent's mounts (index.ts):
 *   app.use('/api/kernel', routes)  → everything under /api/kernel/**
 *   app.get('/health')              → health handler
 *   app.get('/api/health')          → same handler
 *
 * Error envelope parity (incumbent routes.ts helpers):
 *   400 {status:"error", message}
 *   404 {status:"error", message}
 *   403 {status:"error", message}      (PG 45000 from kernel triggers)
 *   500 {status:"error", code:"<KERNEL_*_FAILED>", message}
 *   503 {status:"error", message}      (health only)
 */
/**
 * Reconstruct the full request path the way Express's finalhandler prints it.
 * moleculer-web strips the route prefix from req.url before dispatching, and
 * send404/sendError dispatch to route.onError with req.$route set — so the
 * original path is route.path + the stripped remainder. (Trailing "/" on the
 * route path is dropped because the remainder is already slash-prefixed.)
 */
function expressFullUrl(req: any): string {
  const base = String(req?.$route?.path ?? "").replace(/\/$/, "");
  return base + String(req?.url ?? req?.originalUrl ?? "");
}

function expressNotFoundHtml(req: any): string {
  return `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${expressFullUrl(req)}</pre>\n</body>\n</html>\n`;
}

export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4100,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/api/kernel",

            // TRAP (see moleculer/README.md): `kernel.*` does NOT match
            // grouped actions — moleculer-web masks are path-style. `**` spans.
            whitelist: ["kernel.**"],

            aliases: {
              "POST /transitions": "kernel.transitions.create",
              "GET /transitions/:event_id": "kernel.transitions.get",
              "GET /transitions/:event_id/causality": "kernel.transitions.causality",

              "POST /receipts": "kernel.receipts.create",
              "GET /receipts/:id/chain": "kernel.receipts.chain",
              "GET /plans/:plan_number/receipts": "kernel.plans.receipts",

              "GET /aggregates/:aggregate_type/:aggregate_id/events": "kernel.aggregates.events",

              "GET /policy/active": "kernel.policy.active",
              "GET /policy/maturity": "kernel.policy.maturity",

              "GET /health/recent-events": "kernel.health.recentEvents",
              "GET /health/receipt-integrity": "kernel.health.receiptIntegrity",

              // SSE — the action returns a Node Readable stream; moleculer-web
              // pipes it and applies ctx.meta.$responseHeaders set inside.
              "GET /events/stream": "kernel.eventsStream",
            },

            // Unmatched aliases bypass onError entirely (moleculer-web routes
            // them through onNotFound). The incumbent is Express, whose
            // finalhandler emits the default HTML error page for unmatched
            // routes — reproduce it byte-for-byte (see canary case
            // unknown-route-404).
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              const message = (err as any)?.message ?? String(err);
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              // Parity with the incumbent routes.ts helpers: 500 envelopes
              // carry the KERNEL_*_FAILED code (MoleculerError .type); the
              // 4xx validation/refusal envelopes do not.
              if (status === 500) {
                res.end(
                  JSON.stringify({
                    status: "error",
                    code: (err as any)?.type || "INTERNAL_ERROR",
                    message,
                  })
                );
                return;
              }
              // The gateway's own routing miss surfaces as moleculer-web's
              // built-in NotFoundError (name "NotFoundError", message "Not
              // found"). The incumbent is Express, whose finalhandler emits
              // the default HTML error page for unmatched routes — reproduce
              // it byte-for-byte. Action-level 404s (kernel.service.ts's
              // notFound helper) are MoleculerError instances and keep the
              // JSON envelope.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              res.end(JSON.stringify({ status: "error", message }));
            },
          },

          // Incumbent health mounts (index.ts): /health and /api/health share
          // the same handler — 200 {status,db,pgNotify,subscribers} / 503 error.
          {
            path: "/",
            whitelist: ["kernel.**", "api.**"],
            aliases: {
              "GET /health": "kernel.health",
              "GET /api/health": "kernel.health",
            },
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },
            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              const message = (err as any)?.message ?? String(err);
              // Same Express-HTML emulation for the gateway's own NotFoundError
              // at the root route (see the /api/kernel route's onError).
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              res.end(JSON.stringify({ status: "error", message }));
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
