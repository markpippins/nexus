import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of
 * tackle-prompt-sync-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/tackle-prompt-sync-srv/openapi.yaml by
 * `make apidocs-validate` (check_drift.MOLECULER_MIRRORS) — a renamed alias
 * fails CI exactly like a renamed Express route.
 *
 * Route shape mirrors the incumbent's index.ts (a flat Express app,
 * no prefixes):
 *   GET /health
 *   GET /prompts/:role
 *   GET /prompt/:role/:slug
 *   GET /tasks/:role
 *   POST /refresh
 *
 * NO AUTH GATE: the incumbent has no auth middleware (plain Express with
 * express.json(), no cors either — parity is the contract).
 *
 * Envelope parity (prompt-sync.service.ts + onError):
 *   200 [] / cards / indexes / refresh result
 *   404 {error:"Prompt not found"}        (role/slug miss)
 *   500 {error}                           (action throwers)
 *   503 {status:"error", message}         (health when Redis unreachable —
 *     the incumbent's health catch is res.status(503).json with a structured
 *     body; the action throws tagged PS_HEALTH and onError emits it)
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
        port: process.env.SERVICE_PORT || 4501,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/",

            // TRAP (see moleculer/README.md): path-style masks, `**` spans.
            whitelist: ["prompt-sync.**"],

            aliases: {
              "GET /health": "prompt-sync.health",
              "GET /prompts/:role": "prompt-sync.prompts",
              "GET /prompt/:role/:slug": "prompt-sync.prompt",
              "GET /tasks/:role": "prompt-sync.tasks",
              "POST /refresh": "prompt-sync.refresh",
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
              // found") — same finalhandler-HTML parity as onNotFound.
              // Action-level 404s (PS_NOT_FOUND) are MoleculerError instances
              // and keep the JSON {error} envelope.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              // Health 503: the incumbent's health catch emits the structured
              // body {status:"error", message} (NOT the plain {error} shape).
              if ((err as any)?.type === "PS_HEALTH") {
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.statusCode = 503;
                res.end(JSON.stringify({ status: "error", message }));
                return;
              }
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
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
