import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the conduit-srv twin.
 *
 * Dispatch-through-Express is deliberate: the incumbent has 20 routes across
 * eight Express routers, including a long-lived SSE endpoint and write routes
 * with status/envelope quirks. Every literal alias funnels through the real
 * Express app so parsing, responses, streaming, and finalhandler stay verbatim.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4104,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["conduit.**"],
            bodyParsers: { json: true },
            aliases: {
              "GET /": "conduit.dispatch",
              "GET /health": "conduit.dispatch",
              "GET /workflows": "conduit.dispatch",
              "POST /tickets/detect": "conduit.dispatch",
              "GET /tickets/lineage/:planId": "conduit.dispatch",
              "GET /tokens/plan/:planId": "conduit.dispatch",
              "GET /tokens/role/:role": "conduit.dispatch",
              "GET /tokens/ticket/:ticketId": "conduit.dispatch",
              "GET /config/cron": "conduit.dispatch",
              "GET /config/failure-recovery": "conduit.dispatch",
              "POST /config/failure-recovery": "conduit.dispatch",
              "GET /log/:sessionId": "conduit.dispatch",
              "POST /governance/replay": "conduit.dispatch",
              "GET /governance/events": "conduit.dispatch",
              "POST /vision/work-requests": "conduit.dispatch",
              "GET /vision/work-requests": "conduit.dispatch",
              "GET /vision/work-requests/:id": "conduit.dispatch",
              "GET /vision/receipts": "conduit.dispatch",
              "GET /wr/:id/projection-drift": "conduit.dispatch",
              "GET /wr/drift-scan": "conduit.dispatch",
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
