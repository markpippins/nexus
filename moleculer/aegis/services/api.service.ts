import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the aegis-srv twin.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 66
 * registered routes (65 under /api + root /health) — registries CRUD,
 * revisions, Phase-A validate, TLC model-check, wind compilations, and six
 * table-CRUD families — with a JSON catch-all 404 ({error:"not found"}).
 * Every literal alias funnels through the real Express app so parsing,
 * responses, and envelope shapes stay verbatim.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is the
 * contract. Canary posture: reads + validation negatives ONLY — the 40
 * write routes mutate aegis.* rows (and model-check spawns java/TLC); the
 * canary's POST/PATCH/DELETE probes are malformed/UUID-absent negatives
 * that reject before any DB or spawn work.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4116,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["aegis.**"],
            bodyParsers: { json: true },
            aliases: {
              "GET /health": "aegis.dispatch",

              "GET /api/registries": "aegis.dispatch",
              "POST /api/registries": "aegis.dispatch",
              "GET /api/registries/name/:name": "aegis.dispatch",
              "GET /api/registries/:id": "aegis.dispatch",
              "PATCH /api/registries/:id": "aegis.dispatch",
              "DELETE /api/registries/:id": "aegis.dispatch",
              "GET /api/registries/:id/revisions": "aegis.dispatch",
              "POST /api/registries/:id/revisions": "aegis.dispatch",
              "GET /api/registries/:id/revisions/:rid": "aegis.dispatch",
              "POST /api/registries/:id/validate": "aegis.dispatch",
              "POST /api/registries/:id/model-check": "aegis.dispatch",
              "GET /api/registries/:id/validation-results": "aegis.dispatch",
              "GET /api/registries/:id/model-check-results": "aegis.dispatch",
              "GET /api/registries/:id/wind-compilations": "aegis.dispatch",
              "GET /api/registries/:id/wind-compilations/:cid": "aegis.dispatch",
              "POST /api/registries/:id/wind-compilations": "aegis.dispatch",

              "GET /api/registries/:id/constants": "aegis.dispatch",
              "POST /api/registries/:id/constants": "aegis.dispatch",
              "GET /api/registries/:id/constants/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/constants/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/constants/:cid": "aegis.dispatch",

              "GET /api/registries/:id/properties": "aegis.dispatch",
              "POST /api/registries/:id/properties": "aegis.dispatch",
              "GET /api/registries/:id/properties/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/properties/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/properties/:cid": "aegis.dispatch",

              "GET /api/registries/:id/temporal-properties": "aegis.dispatch",
              "POST /api/registries/:id/temporal-properties": "aegis.dispatch",
              "GET /api/registries/:id/temporal-properties/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/temporal-properties/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/temporal-properties/:cid": "aegis.dispatch",

              "GET /api/registries/:id/relationship-mappings": "aegis.dispatch",
              "POST /api/registries/:id/relationship-mappings": "aegis.dispatch",
              "GET /api/registries/:id/relationship-mappings/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/relationship-mappings/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/relationship-mappings/:cid": "aegis.dispatch",

              "GET /api/registries/:id/execution-log": "aegis.dispatch",
              "POST /api/registries/:id/execution-log": "aegis.dispatch",
              "GET /api/registries/:id/execution-log/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/execution-log/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/execution-log/:cid": "aegis.dispatch",

              "GET /api/registries/:id/variables": "aegis.dispatch",
              "POST /api/registries/:id/variables": "aegis.dispatch",
              "GET /api/registries/:id/variables/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/variables/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/variables/:cid": "aegis.dispatch",

              "GET /api/registries/:id/states": "aegis.dispatch",
              "POST /api/registries/:id/states": "aegis.dispatch",
              "GET /api/registries/:id/states/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/states/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/states/:cid": "aegis.dispatch",

              "GET /api/registries/:id/transitions": "aegis.dispatch",
              "POST /api/registries/:id/transitions": "aegis.dispatch",
              "GET /api/registries/:id/transitions/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/transitions/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/transitions/:cid": "aegis.dispatch",

              "GET /api/registries/:id/invariants": "aegis.dispatch",
              "POST /api/registries/:id/invariants": "aegis.dispatch",
              "GET /api/registries/:id/invariants/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/invariants/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/invariants/:cid": "aegis.dispatch",

              "GET /api/registries/:id/attribute-mappings": "aegis.dispatch",
              "POST /api/registries/:id/attribute-mappings": "aegis.dispatch",
              "GET /api/registries/:id/attribute-mappings/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/attribute-mappings/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/attribute-mappings/:cid": "aegis.dispatch",

              "GET /api/registries/:id/concept-mappings": "aegis.dispatch",
              "POST /api/registries/:id/concept-mappings": "aegis.dispatch",
              "GET /api/registries/:id/concept-mappings/:cid": "aegis.dispatch",
              "PATCH /api/registries/:id/concept-mappings/:cid": "aegis.dispatch",
              "DELETE /api/registries/:id/concept-mappings/:cid": "aegis.dispatch",
            },
            onBeforeCall(ctx: any, _route: any, req: any, res: any) {
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },
            onError(req: any, res: any, err: any) {
              const status =
                Number.isInteger((err as any)?.code) &&
                (err as any).code >= 400 &&
                (err as any).code < 600
                  ? (err as any).code
                  : 500;
              const message = (err as any)?.message ?? String(err);
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                // The incumbent's catch-all emits a JSON body
                // ({error:"not found"}) via its app-level 404 middleware —
                // reproduce that shape for gateway routing misses.
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.statusCode = 404;
                res.end(JSON.stringify({ error: "not found" }));
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
