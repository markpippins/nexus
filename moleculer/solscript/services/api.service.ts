import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) for the SOLScript facade.
 *
 * Exposes the FULL surface at /api/solscript/* (contract
 * typespec/v1/solscript/typescript/operations.tsp): reads AND the
 * transition-entity write, which commits/refuses/rejects through the
 * interpreter. This is the DISTRIBUTED deployment (DB lives elsewhere).
 * The JVM mobile projection (nexus-core-solscript) stays read-only (405).
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
              // ── Write surface (FULLY LIVE on this deployment) ────
              // transition-entity commits/refuses/rejects through the
              // interpreter. This is the distributed deployment (DB lives
              // elsewhere); the JVM mobile projection stays read-only (405).
              "POST /solscript/transition-entity": "solscript.transitionEntity",
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
      },
    });
  }
}