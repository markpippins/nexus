import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of knowledge-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/knowledge-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLECULER_MIRRORS) — a renamed alias fails CI exactly like a
 * renamed Express route.
 *
 * Route shape mirrors the incumbent's mounts (index.ts):
 *   app.use('/knowledge', knowledgeRouter)  → everything under /knowledge/**
 *   app.get('/')                            → root index (JSON)
 *   app.get('/health')                      → health handler
 *
 * NO AUTH GATE: the incumbent is CORS-only (unlike draft-srv's
 * X-Nexus-Internal Security Pass Alpha) — do not add a gate the incumbent
 * does not have; parity is the contract.
 *
 * Envelope parity (knowledge.service.ts throwers + onError):
 *   400 {error}            (validation)
 *   404 {error}            (misses)
 *   500 {error, message}   (transport)
 *   503 {status:"unhealthy", error}   (health only)
 * Unmatched routes: the incumbent is Express — finalhandler HTML page
 * (kernel-port finding).
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4109,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/knowledge",

            // TRAP (see moleculer/README.md): `knowledge.*` does NOT match
            // grouped actions — moleculer-web masks are path-style. `**` spans.
            whitelist: ["knowledge.**"],

            aliases: {
              "GET /entities": "knowledge.entities.list",
              "POST /entities": "knowledge.entities.create",
              "DELETE /entities": "knowledge.entities.purge",
              "GET /entities/:section/:entity_id": "knowledge.entities.get",
              "PUT /entities/:section/:entity_id": "knowledge.entities.update",
              "DELETE /entities/:section/:entity_id": "knowledge.entities.delete",
              "GET /entities/:section/:entity_id/relations": "knowledge.entities.relations",

              "GET /edges": "knowledge.edges.list",
              "POST /edges": "knowledge.edges.create",
              "DELETE /edges/:id": "knowledge.edges.delete",

              "GET /cross-references": "knowledge.xrefs.list",
              "POST /cross-references": "knowledge.xrefs.create",
              "DELETE /cross-references/:id": "knowledge.xrefs.delete",

              "GET /migrations": "knowledge.migrations.list",
              "GET /summary": "knowledge.summary",
            },

            // Unmatched aliases bypass onError entirely (moleculer-web routes
            // them through onNotFound). The incumbent is Express, whose
            // finalhandler emits the default HTML error page — reproduce it
            // byte-for-byte (kernel-port finding).
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              // The gateway's own routing miss surfaces as moleculer-web's
              // built-in NotFoundError (name "NotFoundError"). Render the
              // Express finalhandler HTML page byte-for-byte.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              const data = (err as any)?.data ?? {};
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

          // Incumbent root mounts (index.ts): app.get('/') JSON index and
          // app.get('/health') health handler share this route.
          {
            path: "/",
            whitelist: ["knowledge.**"],
            aliases: {
              "GET /": "knowledge.root",
              "GET /health": "knowledge.health",
            },
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },
            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              const data = (err as any)?.data ?? {};
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

/**
 * Reconstruct the full request path the way Express's finalhandler prints it.
 * moleculer-web strips the route prefix from req.url before dispatching; the
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
