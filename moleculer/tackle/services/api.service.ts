import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of tackle-srv (:4410).
 *
 * ARCHITECTURE (differs from the sibling twins, deliberately):
 *   tackle-srv is 86 routes across 13 Express routers + 2 index-level routes.
 *   Hand-shimming each handler into {status, body} actions would be a
 *   rewrite, not a port. Instead the twin carries the incumbent's REAL
 *   Express app (express-app.ts: verbatim index.ts minus listen/heartbeat)
 *   and every alias dispatches HTTP requests through it:
 *
 *     moleculer-web alias (literal map below)
 *       → onBeforeCall stashes the REAL req/res onto ctx.meta
 *         (moleculer-web sets req.$ctx = ctx; local calls pass meta by
 *         reference — the same mechanism the kernel twin's
 *         ctx.meta.$responseHeaders relies on)
 *       → tackle.dispatch routes via the express app's router
 *       → the VERBATIM route handler runs with a real Express req/res
 *
 *   Consequences, all deliberate parity wins:
 *     - Express query/param/body parsing, res.status().json() chains,
 *       404 {error} envelopes — all incumbent code paths, unmodified.
 *     - Unmatched routes hit Express finalhandler → byte-identical HTML
 *       error page (no onNotFound emulation needed; moleculer-web's
 *       headersSent guard at index.js:703/887 skips sendResponse once
 *       express wrote the response).
 *     - The SSE route (/log/:sessionId) works as-is: the handler writes
 *       headers and streams on the real res.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/tackle-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLECULER_MIRRORS) — a renamed alias fails CI exactly like
 * a renamed Express route.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is
 * the contract.
 *
 * Canary posture: reads + negatives ONLY. The incumbent has real write
 * paths (sessions/:id/kill → process.kill, projections render → file
 * writes, scheduler/roles/tasks mutations) — the write-canary ruling
 * forbids twin writes there, same posture as knowledge/draft.
 */


export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4410,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/",

            // TRAP (see moleculer/README.md): path-style masks, `**` spans.
            whitelist: ["tackle.**"],

            // moleculer-web runs its own json bodyParser by default; the
            // express app ALSO has express.json() — body-parser's _body
            // guard makes the second parse a no-op, so keeping the
            // incumbent's middleware stack is safe (verified in
            // body-parser/lib/read.js:46).
            bodyParsers: { json: true },

            aliases: {
"GET /audit-trail": "tackle.dispatch",
            "GET /audit-trail/recent": "tackle.dispatch",
            "GET /config/ai": "tackle.dispatch",
            "POST /config/ai/bundle": "tackle.dispatch",
            "DELETE /config/ai/bundle/:id": "tackle.dispatch",
            "GET /config/ai/bundle/:id": "tackle.dispatch",
            "GET /config/ai/bundles": "tackle.dispatch",
            "GET /config/ai/bundles/:role": "tackle.dispatch",
            "POST /config/ai/bundles/:role": "tackle.dispatch",
            "POST /config/ai/harness": "tackle.dispatch",
            "DELETE /config/ai/harness/:id": "tackle.dispatch",
            "GET /config/ai/harness/:id": "tackle.dispatch",
            "GET /config/ai/harnesses": "tackle.dispatch",
            "POST /config/ai/import": "tackle.dispatch",
            "POST /config/ai/model": "tackle.dispatch",
            "DELETE /config/ai/model/:id": "tackle.dispatch",
            "GET /config/ai/model/:id": "tackle.dispatch",
            "GET /config/ai/models": "tackle.dispatch",
            "POST /config/ai/provider": "tackle.dispatch",
            "DELETE /config/ai/provider/:id": "tackle.dispatch",
            "GET /config/ai/provider/:id": "tackle.dispatch",
            "GET /config/ai/providers": "tackle.dispatch",
            "GET /config/ai/resolve/:role": "tackle.dispatch",
            "POST /config/ai/role": "tackle.dispatch",
            "DELETE /config/ai/role/:role": "tackle.dispatch",
            "GET /config/ai/role/:role": "tackle.dispatch",
            "GET /config/ai/roles": "tackle.dispatch",
            "POST /config/ai/seed-defaults": "tackle.dispatch",
            "POST /config/ai/test": "tackle.dispatch",
            "GET /config/ai/tool-access": "tackle.dispatch",
            "POST /config/ai/tool-access": "tackle.dispatch",
            "PATCH /config/ai/tool-access/:id": "tackle.dispatch",
            "GET /config/ai/tool-access/:role": "tackle.dispatch",
            "POST /config/ai/tool-access/seed": "tackle.dispatch",
            "GET /config/ai/validate": "tackle.dispatch",
            "POST /config/ai/verify": "tackle.dispatch",
            "GET /config/ai/verify/:sessionId": "tackle.dispatch",
            "POST /config/ai/verify/purge-unverified": "tackle.dispatch",
            "GET /config/failure-recovery": "tackle.dispatch",
            "POST /config/failure-recovery": "tackle.dispatch",
            "GET /health": "tackle.dispatch",
            "GET /health/history": "tackle.dispatch",
            "GET /health/metrics": "tackle.dispatch",
            "POST /health/simulate-load": "tackle.dispatch",
            "GET /log/:sessionId": "tackle.dispatch",
            "DELETE /logs": "tackle.dispatch",
            "GET /logs": "tackle.dispatch",
            "POST /logs/emit": "tackle.dispatch",
            "DELETE /memory/assign": "tackle.dispatch",
            "POST /memory/assign": "tackle.dispatch",
            "POST /memory/check-since": "tackle.dispatch",
            "GET /memory/procedure/:slug": "tackle.dispatch",
            "GET /memory/procedures/:role": "tackle.dispatch",
            "POST /memory/refresh": "tackle.dispatch",
            "GET /memory/role-updates": "tackle.dispatch",
            "GET /projections": "tackle.dispatch",
            "POST /projections": "tackle.dispatch",
            "DELETE /projections/:id": "tackle.dispatch",
            "GET /projections/:id": "tackle.dispatch",
            "PUT /projections/:id": "tackle.dispatch",
            "POST /projections/:id/render": "tackle.dispatch",
            "GET /projections/drift": "tackle.dispatch",
            "POST /projections/render-all": "tackle.dispatch",
            "GET /prompts": "tackle.dispatch",
            "POST /prompts": "tackle.dispatch",
            "GET /prompts/:role": "tackle.dispatch",
            "GET /prompts/:role/:slug": "tackle.dispatch",
            "GET /roles": "tackle.dispatch",
            "POST /roles": "tackle.dispatch",
            "DELETE /roles/:id": "tackle.dispatch",
            "GET /roles/:id": "tackle.dispatch",
            "POST /roles/provision": "tackle.dispatch",
            "GET /roles/readiness/:name": "tackle.dispatch",
            "GET /scheduler": "tackle.dispatch",
            "POST /scheduler": "tackle.dispatch",
            "DELETE /scheduler/:id": "tackle.dispatch",
            "GET /scheduler/:id": "tackle.dispatch",
            "PATCH /scheduler/:id": "tackle.dispatch",
            "GET /scheduler/due": "tackle.dispatch",
            "GET /sessions": "tackle.dispatch",
            "POST /sessions/:sessionId/kill": "tackle.dispatch",
            "GET /tasks": "tackle.dispatch",
            "POST /tasks": "tackle.dispatch",
            "DELETE /tasks/:task_slug": "tackle.dispatch",
            "GET /tasks/:task_slug": "tackle.dispatch",
            "GET /tasks/inspector/dispatch": "tackle.dispatch",
            },

            onBeforeCall(ctx: any, route: any, req: any, res: any) {
              // Hand the REAL req/res to the dispatch action. Local broker
              // calls pass ctx.meta by reference, so mutations here are
              // visible inside the action handler.
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },

            onError(req: any, res: any, err: any) {
              const status = Number.isInteger((err as any)?.code) && (err as any).code >= 400 && (err as any).code < 600 ? (err as any).code : 500;
              const message = (err as any)?.message ?? String(err);
              // The gateway's own routing miss (no alias matched) surfaces
              // here as moleculer-web's NotFoundError via send404 →
              // sendError. The incumbent is Express, whose finalhandler
              // emits the default HTML error page for unmatched routes —
              // reproduce it byte-for-byte (kernel/draft/role-memory
              // finding).
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                const full = String(req?.url ?? req?.originalUrl ?? "");
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(
                  `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${full}</pre>\n</body>\n</html>\n`
                );
                return;
              }
              // Dispatch failures before any handler ran — emit the
              // incumbent's 500 envelope shape.
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
