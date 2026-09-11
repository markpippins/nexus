import { BrokerOptions } from "moleculer";
import { trafficCounterMiddleware } from "./services/traffic-counter";

const brokerConfig: BrokerOptions = {
  namespace: "search",
  nodeID: "search-node-1",
  
  logger: {
    type: "Console",
    options: {
      level: "info",
      colors: true,
      moduleColors: false,
      formatter: "full",
      autoPadding: false
    }
  },

  transporter: null, // No external transporter for now (standalone mode)

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: false,
    retries: 5,
    delay: 100,
    maxDelay: 1000,
    factor: 2,
    check: (err: any) => err && !!err.retryable
  },

  maxCallLevel: 100,
  heartbeatInterval: 10,
  heartbeatTimeout: 30,

  tracking: {
    enabled: false,
    shutdownTimeout: 5000,
  },

  disableBalancer: false,

  registry: {
    strategy: "RoundRobin",
    preferLocal: true
  },

  circuitBreaker: {
    enabled: false,
    threshold: 0.5,
    minRequestCount: 20,
    windowTime: 60,
    halfOpenTime: 10 * 1000,
    check: (err: any) => err && err.code >= 500
  },

  bulkhead: {
    enabled: false,
    concurrency: 10,
    maxQueueSize: 100,
  },

  validator: true,

  metrics: {
    enabled: false,
  },

  tracing: {
    enabled: false,
  },

  internalServices: true,
  internalMiddlewares: true,

  // M1 traffic canary: count local action invocations per action name.
  // Queryable at GET /api/traffic/counts (api.trafficCounts).
  middlewares: [trafficCounterMiddleware()],

  hotReload: true,
};

export = brokerConfig;