import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of role-memory-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/role-memory-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLECULER_MIRRORS) — a renamed alias fails CI exactly like
 * a renamed Express route.
 *
 * Route shape mirrors the incumbent's mounts (index.ts — a flat Express
 * app, no prefixes):
 *   GET /health
 *   GET /procedures/:role
 *   GET /procedure/:slug
 *   POST /refresh
 *
 * NO AUTH GATE: the incumbent has no auth middleware (plain CORS-less
 * Express with express.json()) — parity is the contract.
 *
 * Envelope parity (role-memory.service.ts + onError):
 *   200 [] / cards / indexes / refresh result
 *   404 {error:"Procedure not found"}     (slug miss)
 *   500 {error}                           (action throwers)
 *   503 {error}                           (health when Redis unreachable —
 *     the incumbent's health catch is res.status(503).json({...}) with a
 *     structured body; the action throws and onError emits the envelope)
 * Unmatched routes: the incumbent is Express — finalhandler HTML page
 * (kernel-port finding).
 */

/**
 * Reconstruct the full request path the way Express's finalhandler prints it.
 * (kernel-port finding: moleculer-web strips the route prefix from req.url;
 * original path is route.path + the stripped remainder.)
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
        port: process.env.SERVICE_PORT || 4150,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/",

            // TRAP (see moleculer/README.md): path-style masks, `**` spans.
            whitelist: ["role-memory.**"],

            aliases: {
              "GET /health": "role-memory.health",
              "GET /procedures/:role": "role-memory.procedures",
              "GET /procedure/:slug": "role-memory.procedure",
              "POST /refresh": "role-memory.refresh",
            },

            // Unmatched aliases bypass onError entirely (moleculer-web routes
            // them through onNotFound). The incumbent is Express, whose
            // finalhandler emits the default HTML error page for unmatched
            // routes — reproduce it byte-for-byte (kernel-port finding).
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              const message = (err as any)?.message ?? String(err);
              // The gateway's own routing miss surfaces as moleculer-web's
              // built-in NotFoundError (name "NotFoundError", message "Not
              // found"). The incumbent is Express, whose finalhandler emits
              // the default HTML error page for unmatched routes — reproduce
              // it byte-for-byte (kernel/draft finding). Action-level 404s
              // (RM_NOT_FOUND) are MoleculerError instances and keep the
              // JSON {error} envelope.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              // Incumbent catch blocks emit {error} envelopes; the health
              // 503 emits its structured body — parity via {error} is what
              // the incumbent's non-health catch blocks produce, and health
              // 503 in the incumbent carries the structured fields, so the
              // gateway passes the health error through as-is when marked.
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              if ((err as any)?.type === "RM_HEALTH") {
                res.end(
                  JSON.stringify({
                    status: "error",
                    redis: "unreachable",
                    lastUpdated: null,
                    procedureCount: 0,
                    roleIndexCount: 0,
                    stale: true,
                    message,
                  })
                );
                return;
              }
              res.end(JSON.stringify({ error: message }));
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
