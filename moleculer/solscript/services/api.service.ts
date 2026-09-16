import { Service, ServiceBroker, Errors } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) for the SOLScript facade.
 *
 * Exposes the read surface at /api/solscript/* (contract
 * typespec/v1/solscript/typescript/operations.tsp). Read actions proxy to the
 * solscript service. The WRITE action (transition-entity) is deliberately NOT
 * aliased to a live action — it is answered with 405 by this gateway's onError
 * handler, preserving the readonly-by-default posture at the REST tier.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4060,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/api",

            whitelist: [
              "solscript.*",
              "api.*",
            ],

            aliases: {
              // ── Read surface (live) ─────────────────────────────
              "POST /solscript/evaluate-proposition": "solscript.evaluateProposition",
              "POST /solscript/check-rule": "solscript.checkRule",
              "POST /solscript/check-transition-guard": "solscript.checkTransitionGuard",
              "POST /solscript/execute-query": "solscript.executeQuery",
              "GET /solscript/health": "solscript.health",
              // ── Write surface (read-only posture → 405) ─────────
              // transition-entity is a WRITE (mutates entity state). The
              // readonly-by-default posture disallows it at the REST tier: it
              // routes to a gateway action that throws 405 (never reaches the
              // interpreter, which fully supports transitions in the library).
              "POST /solscript/transition-entity": "api.transitionReadOnly405",
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
        health: {
          async handler() {
            return {
              status: "ok",
              service: "solscript-gateway",
              namespace: "solscript",
              timestamp: new Date().toISOString(),
            };
          },
        },

        // Readonly-by-default write posture: mutating surface → 405.
        transitionReadOnly405: {
          params: {
            "**": { type: "any", optional: true },
          },
          async handler() {
            throw new Errors.MoleculerError(
              "nexus-core is read-only: POST /api/solscript/transition-entity mutates entity state. "
              + "Disallowed in the read-only projection; write path (JetStream) not yet implemented.",
              405, "METHOD_NOT_ALLOWED");
          },
        },
      },
    });
  }
}