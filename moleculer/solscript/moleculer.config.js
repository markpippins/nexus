/**
 * PROD broker config — the ONLY config file moleculer-runner loads (it never
 * loads moleculer.config.ts; verified live per the search :4050 precedent).
 *
 * TOPOLOGY: :4060 BELONGS ON THE NATS MESH (namespace "solscript"). Same
 * substrate as search (:4050, namespace "search") and nexus-broker (:4080,
 * namespace "nexus") — shared transport, isolated discovery domains. NATS is
 * live fleet infrastructure on :4222; `nats` is a dependency of this service.
 */
module.exports = {
  namespace: "solscript",
  nodeID: "solscript-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: process.env.NATS_URL || "nats://localhost:4222",
    },
  },

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: false,
    retries: 5,
    delay: 100,
    maxDelay: 1000,
    factor: 2,
    check: (err) => err && !!err.retryable,
  },

  maxCallLevel: 100,
  heartbeatInterval: 10,
  heartbeatTimeout: 30,

  tracking: {
    enabled: false,
    shutdownTimeout: 5000,
  },

  registry: {
    strategy: "RoundRobin",
    preferLocal: true,
  },

  circuitBreaker: {
    enabled: false,
    threshold: 0.5,
    minRequestCount: 20,
    windowTime: 60,
    halfOpenTime: 10 * 1000,
    check: (err) => err && err.code >= 500,
  },

  validator: true,
  metrics: { enabled: true },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
  hotReload: true,
};