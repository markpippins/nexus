import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — canary port for the voyager port.
 *
 * The alias map below is the ENTIRE parity surface: it is extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/voyager-srv/openapi.yaml by `make apidocs-validate`
 * (MOLLECULER_MIRRORS) — so a path renamed here fails CI exactly like a
 * renamed Express route does. Alias order is significant: literal
 * `/by-id/:param` paths must precede `/:id`.
 *
 * WHITELIST TRAP (cost one debug cycle): moleculer-web matches whitelist masks
 * with a PATH matcher, where `*` does NOT cross a `.`. `["voyager.*"]` therefore
 * authorises only single-segment actions (`voyager.health`) and silently
 * rejects every grouped one (`voyager.scanEpochs.list`) — as a 404
 * ServiceNotFoundError, not a 403, which reads exactly like a missing action.
 * `voyager.**` is what spans the extra segments. The sibling apps never hit
 * this because their actions are all single-segment.
 *
 * Error envelope parity (incumbent routes.ts / index.ts):
 *   503 → {status:"error", message}   (health only, DB down)
 *   404 → {error:"<Thing> not found"}
 *   500 → {error:"<db message>"}
 * moleculer-web's default error body ({name,message,code,type}) is NOT that,
 * hence the explicit onError below.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4114,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/api",

            whitelist: ["voyager.**"],

            aliases: {
              "GET /health": "voyager.health",

              // ── scan epochs ─────────────────────────────────────────
              "GET /scan-epochs": "voyager.scanEpochs.list",
              "GET /scan-epochs/:id": "voyager.scanEpochs.get",

              // ── file observations (by-id BEFORE :id) ────────────────
              "GET /observations/files": "voyager.files.list",
              "GET /observations/files/by-id/:observationId": "voyager.files.byObservationId",
              "GET /observations/files/:id": "voyager.files.get",

              // ── directory observations ──────────────────────────────
              "GET /observations/directories": "voyager.directories.list",

              // ── topology (signals by-id pattern: :id last) ──────────
              "GET /topology/signals": "voyager.signals.list",
              "GET /topology/signals/:id": "voyager.signals.get",
              "GET /topology/edge-hints": "voyager.edgeHints.list",

              // ── entities (by-id BEFORE :id) ─────────────────────────
              "GET /entities": "voyager.entities.list",
              "GET /entities/by-id/:entityId": "voyager.entities.byEntityId",
              "GET /entities/:id": "voyager.entities.get",

              // ── metadata spans ──────────────────────────────────────
              "GET /spans": "voyager.spans.list",
              "GET /spans/:id": "voyager.spans.get",

              // ── stats ───────────────────────────────────────────────
              "GET /stats": "voyager.stats",
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              const message = (err as any)?.message ?? String(err);
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              // 503 keeps the incumbent's health-specific envelope; everything
              // else uses the {error} shape.
              const body = status === 503 ? { status: "error", message } : { error: message };
              res.end(JSON.stringify(body));
            },
          },

          // The incumbent serves GET /health OUTSIDE /api (index.ts) as well as
          // /api/health (routes.ts) — both are in the committed contract.
          {
            path: "/",
            whitelist: ["voyager.**"],
            aliases: {
              "GET /health": "voyager.health",
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
    });
  }
}
