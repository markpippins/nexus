import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the peb-srv twin.
 *
 * Dispatch-through-Express (tackle/conduit/execution/harness twin pattern):
 * the incumbent serves 23 routes (22 under /api/peb + root /health) with ADR
 * decision chains, transaction lineage, SSE governance-event streaming, and a
 * JSON catch-all 404. Every literal alias funnels through the real Express
 * app so parsing, responses, and envelope shapes stay verbatim.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is the
 * contract. Canary posture: reads + validation negatives ONLY — the four
 * write routes (POST /decisions, PATCH /decisions/:id, POST
 * /decisions/:id/supersede, POST /events/:receipt_id/replay) mutate
 * peb.decisions / peb.governance_events; canary POST/PATCH probes are
 * malformed-body negatives that 400 before any DB work.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4111,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["peb.**"],
            bodyParsers: { json: true },
            aliases: {
              "GET /health": "peb.dispatch",
              "GET /api/peb/health/circuit-breakers": "peb.dispatch",
              "GET /api/peb/health/entropy": "peb.dispatch",
              "GET /api/peb/health/violations/summary": "peb.dispatch",
              "GET /api/peb/events/stream": "peb.dispatch",
              "GET /api/peb/events": "peb.dispatch",
              "GET /api/peb/events/:receipt_id": "peb.dispatch",
              "POST /api/peb/events/:receipt_id/replay": "peb.dispatch",
              "GET /api/peb/binding-decisions": "peb.dispatch",
              "GET /api/peb/binding-decisions/authority/:decisionClass": "peb.dispatch",
              "GET /api/peb/transactions": "peb.dispatch",
              "GET /api/peb/transactions/:id": "peb.dispatch",
              "GET /api/peb/transactions/:id/lineage": "peb.dispatch",
              "GET /api/peb/decisions": "peb.dispatch",
              "GET /api/peb/decisions/next-number": "peb.dispatch",
              "POST /api/peb/decisions": "peb.dispatch",
              "GET /api/peb/decisions/:id": "peb.dispatch",
              "PATCH /api/peb/decisions/:id": "peb.dispatch",
              "POST /api/peb/decisions/:id/supersede": "peb.dispatch",
              "GET /api/peb/decisions/:id/chain": "peb.dispatch",
              "GET /api/peb/entities/:entity_id/capabilities": "peb.dispatch",
              "GET /api/peb/entities/:entity_id/capability-gap": "peb.dispatch",
              "GET /api/peb/state/:key/versions": "peb.dispatch",
              "GET /api/peb/state/:key/diff": "peb.dispatch",
              "GET /api/peb/traces/:id/tree": "peb.dispatch",
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
                // The incumbent's catch-all emits a JSON body ({error:{message:
                // "not_found"}}) via notFoundHandler — reproduce that shape for
                // gateway routing misses so parity holds on unmatched paths.
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.statusCode = 404;
                res.end(JSON.stringify({ error: { message: "not_found" } }));
                return;
              }
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              res.end(JSON.stringify({ error: { message } }));
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
