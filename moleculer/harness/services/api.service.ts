import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the harness-srv twin.
 *
 * Dispatch-through-Express (tackle/conduit/execution twin pattern): the
 * incumbent's 8-route Express app (3 write/execute routes, 2 SSE streams,
 * job registry reads, health, sessions) runs verbatim behind one
 * harness.dispatch action. Every literal alias below is pinned to the
 * incumbent's committed openapi.yaml by the API drift gate.
 *
 * Canary posture: reads + validation negatives ONLY. POST /run and
 * POST /run-direct resolve context, then SPAWN agent processes (opencode/
 * ollama) and write PG events, governance receipts, and nebula records;
 * POST /jobs/:id/interrupt kills children by PID. None of those may run
 * from a canary. The canary's POST probes are malformed-body negatives that
 * return 400 before any admission/DB work.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4420,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["harness.**"],
            bodyParsers: { json: true },
            aliases: {
              "GET /health": "harness.dispatch",
              "GET /sessions": "harness.dispatch",
              "POST /run": "harness.dispatch",
              "POST /resolve-context": "harness.dispatch",
              "POST /run-direct": "harness.dispatch",
              "GET /jobs/:jobId": "harness.dispatch",
              "GET /jobs/:jobId/events": "harness.dispatch",
              "POST /jobs/:jobId/interrupt": "harness.dispatch",
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
                // The incumbent has NO catch-all 404 middleware: unmatched
                // routes fall through to Express finalhandler — HTML page
                // (kernel/draft/conduit finding). Reproduce byte-for-byte.
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(
                  `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${req.url}</pre>\n</body>\n</html>\n`
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
