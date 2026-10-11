import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the substance-srv twin.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 11
 * registered endpoints (8 paths: /healthz, the /segment-sets families, and
 * the root-mounted /{domain_type}/{domain_id}/segment-sets domain-links
 * surface). FastAPI-shaped envelopes ({detail} errors, 422 validation) pass
 * through the verbatim stack unchanged.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is the
 * contract.
 *
 * The alias map is GENERATED from typescript/substance-srv/openapi.yaml (the
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
        port: process.env.SERVICE_PORT || 4115,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["substance.**"],
            bodyParsers: { json: true },
            // moleculer-web 0.10.x does NOT populate ctx.meta.$req/$res from the
            // route; only this alias hook hands the REAL Express req/res to the
            // action context, which `substance.dispatch` requires. (Live-confirmed
            // 2026-10-11 per Ruling 38G §4: without this, every aliased call 500s
            // DISPATCH_NO_REQRES; jest suites hit the Express app directly and
            // never see it. Canonical form: moleculer/wind/services/api.service.ts.)
            onBeforeCall: (ctx: any, _route: any, req: any, res: any) => {
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },
            aliases: {
              "GET /healthz": "substance.dispatch",

              "GET /segment-sets": "substance.dispatch",
              "POST /segment-sets": "substance.dispatch",
              "POST /segment-sets/from-segments": "substance.dispatch",
              "GET /segment-sets/:segment_set_id": "substance.dispatch",
              "PATCH /segment-sets/:segment_set_id": "substance.dispatch",
              "POST /segment-sets/:segment_set_id/members": "substance.dispatch",
              "DELETE /segment-sets/:segment_set_id/members/:segment_id": "substance.dispatch",

              "GET /:domain_type/:domain_id/segment-sets": "substance.dispatch",
              "POST /:domain_type/:domain_id/segment-sets": "substance.dispatch",
              "DELETE /:domain_type/:domain_id/segment-sets/:segment_set_id": "substance.dispatch"
            },
          },
        ],
      },
    });
  }
}
