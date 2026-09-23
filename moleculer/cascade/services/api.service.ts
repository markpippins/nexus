import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of cascade-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/cascade-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLECULER_MIRRORS) — a renamed alias fails CI exactly like a
 * renamed Express route.
 *
 * Route shape mirrors the incumbent's mounts:
 *   app.use('/cascade', routes)  → everything under /cascade/**
 *   app.get('/')                 → the root {name,version,port} index
 *
 * Error envelope parity (incumbent routes.ts): 404 {error}, 400 {error},
 * 500 {error}, 503 {status:"error", error} (health only).
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4106,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/cascade",

            // TRAP (see moleculer/README.md): `cascade.*` does NOT match
            // grouped actions — moleculer-web masks are path-style. `**` spans.
            whitelist: ["cascade.**"],

            aliases: {
              "GET /events": "cascade.events.list",
              "GET /events/:id/lineage": "cascade.events.lineage",
              "GET /events/:id/children": "cascade.events.children",
              "GET /events/:id": "cascade.events.get",

              "GET /lineage": "cascade.lineage",
              "GET /analytics": "cascade.analytics",

              "GET /subscribers": "cascade.subscribers.list",
              "GET /subscribers/:pattern": "cascade.subscribers.get",
              "PATCH /subscribers/:pattern": "cascade.subscribers.update",

              "GET /assessments": "cascade.assessments.list",
              "GET /health": "cascade.health",
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              const message = (err as any)?.message ?? String(err);
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              // 503 keeps the incumbent health envelope; everything else {error}.
              const body =
                status === 503 ? { status: "error", error: message } : { error: message };
              res.end(JSON.stringify(body));
            },
          },

          // Incumbent root index: GET / → {name,version,port}.
          {
            path: "/",
            whitelist: ["cascade.**", "api.**"],
            aliases: {
              "GET /": "api.index",
            },
            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              res.end(JSON.stringify({ error: (err as any)?.message ?? String(err) }));
            },
          },
        ],

        log4XXResponses: false,
        logRequestParams: "info",
        logResponseData: "info",
      },

      actions: {
        // GET / — the incumbent's root index handler (index.ts).
        index: {
          handler() {
            return { name: "cascade-srv", version: "1.0.0", port: 3106 };
          },
        },
      },
    });
  }
}
