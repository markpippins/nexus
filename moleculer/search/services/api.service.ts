import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";
import { trafficSnapshot } from "./traffic-counter";

export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4050,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/api",
            
            whitelist: [
              "google-search.*",
              "api.*"
            ],

            aliases: {
              "POST /search/simple": "google-search.simpleSearch",
              "GET /health": "api.health",
              "GET /traffic/counts": "api.trafficCounts"
            },

            bodyParsers: {
              json: {
                strict: false,
                limit: "1MB"
              },
              urlencoded: {
                extended: true,
                limit: "1MB"
              }
            },

            cors: {
              origin: "*",
              methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
              allowedHeaders: "*",
              credentials: false,
              maxAge: 3600
            }
          }
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
              timestamp: new Date().toISOString(),
              service: "moleculer-search"
            };
          }
        },

        // M1 traffic canary: cumulative per-action invocation counts since
        // boot ({startedAt, total, counts}). The zero-traffic observation
        // for cutover sign-off polls this alongside the gateway
        // GET /api/v1/broker/traffic/counts: legacy search counts must stay
        // flat while these carry the traffic. In-memory: a restart resets
        // the window (see startedAt).
        trafficCounts: {
          async handler() {
            return trafficSnapshot();
          }
        }
      }
    });
  }
}