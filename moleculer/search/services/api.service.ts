import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

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
              "POST /search/force": "google-search.forceSearch",
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

        // M1 traffic canary: cumulative per-action request counts since
        // boot, derived from the built-in metrics registry (no custom
        // state, no cross-file imports — prod runs .ts source directly
        // under Node native type-stripping, which cannot resolve
        // extensionless relative imports). Shape: {startedAt, total,
        // counts}. Pairs with the gateway GET
        // /api/v1/broker/traffic/counts for the zero-traffic observation.
        // In-memory by design: a restart resets the window (see startedAt).
        trafficCounts: {
          async handler(ctx: any) {
            const startedAt = new Date(Date.now() - process.uptime() * 1000).toISOString();
            const counts: Record<string, number> = {};
            let total = 0;
            try {
              const list: any = await ctx.call("$node.metrics");
              const metrics = Array.isArray(list) ? list : [];
              for (const metric of metrics) {
                if (!metric || typeof metric.name !== "string") continue;
                if (metric.name !== "moleculer.request.total") continue;
                const values = Array.isArray(metric.values) ? metric.values : [];
                for (const point of values) {
                  const action = point && point.labels && point.labels.action;
                  const value = point && typeof point.value === "number" ? point.value : 0;
                  if (typeof action === "string" && action.length > 0) {
                    counts[action] = (counts[action] ?? 0) + value;
                    total += value;
                  }
                }
              }
            } catch (err) {
              // Metrics unavailable: report an explicitly empty window
              // rather than failing the observation endpoint.
            }
            return { startedAt, total, counts };
          }
        }
      }
    });
  }
}