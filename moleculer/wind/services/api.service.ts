import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the wind-srv twin.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 87
 * registered endpoints (57 paths under /api + root /health) — scheduling /
 * DAG orchestration: workflows, nodes + requirements resolver, edges,
 * instances (advance/execute/pause/resume/run/stop), execution-requests
 * (attempts/dispatch/receipts), events + event-types, tickets, receipts,
 * offices, titles, v-roles, provider-contracts, tasks, outcomes, validate.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is the
 * contract. Every literal alias funnels through the real Express app so
 * parsing, responses, and envelope shapes stay verbatim (including
 * Express's default HTML 404 for unknown routes — the incumbent has no JSON
 * catch-all).
 *
 * The alias map is GENERATED from typescript/wind-srv/openapi.yaml (the
 * committed contract) — one alias per declared (method, path), so gateway
 * surface == contract surface by construction; check_drift.py holds the
 * equality from the other side.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4300,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["wind.**"],
            // Incumbent's parser limit (typescript/wind-srv/src/index.js:
            // express.json({ limit: '1mb' })) — the gateway parser must
            // match or oversized writes 413 here before the verbatim stack
            // ever sees them.
            bodyParsers: { json: { limit: "1mb" } },
            // moleculer-web 0.10.x does NOT put $req/$res into action meta on
            // its own (only passReqResToParams aliases get them, via params).
            // Dispatch-through-Express needs the REAL req/res objects inside
            // the action handler, so inject them here — this hook runs on the
            // gateway context BEFORE broker.call, and gateway context meta
            // propagates to the action context. (Discovered LIVE on the
            // resolution twin: without this, every aliased call 500s
            // DISPATCH_NO_REQRES; jest suites hit the Express app directly
            // and never see it.)
            onBeforeCall: (ctx: any, _route: any, req: any, res: any) => {
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },
            aliases: {
              "GET /api/edges": "wind.dispatch",
              "POST /api/edges": "wind.dispatch",

              "DELETE /api/edges/:id": "wind.dispatch",

              "GET /api/event-types": "wind.dispatch",
              "POST /api/event-types": "wind.dispatch",

              "GET /api/event-types/:eventType": "wind.dispatch",
              "DELETE /api/event-types/:eventType": "wind.dispatch",

              "GET /api/events": "wind.dispatch",
              "POST /api/events": "wind.dispatch",

              "POST /api/events/poll": "wind.dispatch",
              "GET /api/events/:id": "wind.dispatch",

              "GET /api/execution-requests": "wind.dispatch",
              "POST /api/execution-requests": "wind.dispatch",

              "GET /api/execution-requests/:id": "wind.dispatch",
              "GET /api/execution-requests/:id/attempts": "wind.dispatch",
              "POST /api/execution-requests/:id/attempts": "wind.dispatch",
              "POST /api/execution-requests/:id/dispatch": "wind.dispatch",
              "GET /api/execution-requests/:id/receipts": "wind.dispatch",
              "POST /api/execution-requests/:id/receipts": "wind.dispatch",

              "GET /api/instances": "wind.dispatch",
              "POST /api/instances": "wind.dispatch",

              "GET /api/instances/:id": "wind.dispatch",
              "POST /api/instances/:id/advance": "wind.dispatch",
              "POST /api/instances/:id/execute": "wind.dispatch",
              "POST /api/instances/:id/pause": "wind.dispatch",
              "POST /api/instances/:id/resume": "wind.dispatch",
              "POST /api/instances/:id/run": "wind.dispatch",
              "POST /api/instances/:id/stop": "wind.dispatch",

              "GET /api/nodes": "wind.dispatch",
              "POST /api/nodes": "wind.dispatch",

              "GET /api/nodes/capability/:key/resolve": "wind.dispatch",
              "GET /api/nodes/credential/:role": "wind.dispatch",
              "GET /api/nodes/:id": "wind.dispatch",
              "PUT /api/nodes/:id": "wind.dispatch",
              "DELETE /api/nodes/:id": "wind.dispatch",
              "GET /api/nodes/:id/requirements": "wind.dispatch",
              "POST /api/nodes/:id/requirements": "wind.dispatch",
              "GET /api/nodes/:id/requirements/current": "wind.dispatch",
              "PUT /api/nodes/:id/requirements/:reqId": "wind.dispatch",
              "GET /api/nodes/:id/resolve": "wind.dispatch",

              "GET /api/offices": "wind.dispatch",
              "POST /api/offices": "wind.dispatch",

              "GET /api/offices/:id": "wind.dispatch",
              "PUT /api/offices/:id": "wind.dispatch",
              "DELETE /api/offices/:id": "wind.dispatch",

              "GET /api/outcomes": "wind.dispatch",
              "POST /api/outcomes": "wind.dispatch",

              "GET /api/outcomes/:id": "wind.dispatch",
              "DELETE /api/outcomes/:id": "wind.dispatch",

              "GET /api/provider-contracts": "wind.dispatch",
              "POST /api/provider-contracts": "wind.dispatch",

              "GET /api/provider-contracts/:adapterId": "wind.dispatch",
              "GET /api/provider-contracts/:adapterId/credential-rotations": "wind.dispatch",
              "POST /api/provider-contracts/:adapterId/credential-rotations": "wind.dispatch",
              "POST /api/provider-contracts/:adapterId/deactivate": "wind.dispatch",
              "POST /api/provider-contracts/:adapterId/retire": "wind.dispatch",

              "GET /api/receipts": "wind.dispatch",

              "GET /api/receipts/:id": "wind.dispatch",

              "GET /api/tasks": "wind.dispatch",
              "POST /api/tasks": "wind.dispatch",

              "GET /api/tasks/:id": "wind.dispatch",
              "PUT /api/tasks/:id": "wind.dispatch",
              "DELETE /api/tasks/:id": "wind.dispatch",

              "GET /api/tickets": "wind.dispatch",

              "GET /api/tickets/:id": "wind.dispatch",
              "POST /api/tickets/:id/cancel": "wind.dispatch",
              "PUT /api/tickets/:id/status": "wind.dispatch",

              "GET /api/titles": "wind.dispatch",
              "POST /api/titles": "wind.dispatch",

              "GET /api/titles/:id": "wind.dispatch",
              "PUT /api/titles/:id": "wind.dispatch",
              "DELETE /api/titles/:id": "wind.dispatch",

              "GET /api/v-roles": "wind.dispatch",

              "GET /api/v-roles/:name": "wind.dispatch",

              "GET /api/validate/:version_id": "wind.dispatch",
              "POST /api/validate/:version_id/structure": "wind.dispatch",

              "GET /api/versions": "wind.dispatch",
              "POST /api/versions": "wind.dispatch",

              "GET /api/versions/:id": "wind.dispatch",
              "DELETE /api/versions/:id": "wind.dispatch",
              "POST /api/versions/:id/activate": "wind.dispatch",

              "GET /api/workflows": "wind.dispatch",
              "POST /api/workflows": "wind.dispatch",

              "GET /api/workflows/:id": "wind.dispatch",
              "PUT /api/workflows/:id": "wind.dispatch",
              "DELETE /api/workflows/:id": "wind.dispatch",

              "GET /health": "wind.dispatch",
              // Registered LAST: catch-alls into the verbatim Express app for
              // everything the literals don't name (unmatched paths anywhere,
              // GET-only routes hit with other methods). `(.*)` =
              // path-to-regexp 3.x catch-all; bare `/` is a separate alias.
              "* /": "wind.dispatch",
              "* /(.*)": "wind.dispatch",
            },
          },
        ],
      },
    });
  }
}
