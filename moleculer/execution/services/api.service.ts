import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the execution-srv twin.
 *
 * Dispatch-through-Express is deliberate: the incumbent serves 19 routes
 * (18 under /api/execution + root /health) with paginated lists, lifecycle
 * aggregations, integrity scans, governed witnessed-run projections, and a
 * read-only JSON catch-all 404. Every literal alias funnels through the real
 * Express app so parsing, responses, and envelope shapes stay verbatim.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is the
 * contract. Read-only: every route is a SELECT.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4110,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["execution.**"],
            bodyParsers: { json: true },
            aliases: {
              "GET /health": "execution.dispatch",
              "GET /api/execution/requests": "execution.dispatch",
              "GET /api/execution/requests/:id/state": "execution.dispatch",
              "GET /api/execution/requests/:id/attempts": "execution.dispatch",
              "GET /api/execution/requests/:id/receipts/lineage": "execution.dispatch",
              "GET /api/execution/leases": "execution.dispatch",
              "GET /api/execution/leases/stale": "execution.dispatch",
              "GET /api/execution/leases/:id/lifecycle": "execution.dispatch",
              "GET /api/execution/attempts": "execution.dispatch",
              "GET /api/execution/receipts": "execution.dispatch",
              "GET /api/execution/receipts/:id/pipeline-origin": "execution.dispatch",
              "GET /api/execution/witnessed-runs": "execution.dispatch",
              "GET /api/execution/witnessed-runs/diagnostics": "execution.dispatch",
              "GET /api/execution/projections/witnessed-runs": "execution.dispatch",
              "GET /api/execution/metrics": "execution.dispatch",
              "GET /api/execution/health": "execution.dispatch",
              "GET /api/execution/health/integrity-scan": "execution.dispatch",
              "GET /api/execution/health/by-executor": "execution.dispatch",
              "GET /api/execution/health/status-distribution": "execution.dispatch",
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
                // The incumbent's catch-all emits a JSON body (NOT the
                // finalhandler HTML page — it registers an app-level 404
                // middleware). Reproduce that shape for gateway routing
                // misses so parity holds on unmatched paths too.
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.statusCode = 404;
                res.end(
                  JSON.stringify({
                    error: "not_found",
                    hint: "execution-srv is read-only. Available endpoints live under /api/execution and /health. See REST API.md for the full catalog.",
                  })
                );
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
