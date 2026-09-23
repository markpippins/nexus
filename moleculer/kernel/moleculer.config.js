/**
 * PROD broker config — the ONLY config file moleculer-runner loads (it never
 * loads moleculer.config.ts; verified live per the search :4050 precedent).
 *
 * TOPOLOGY: :4100, the canary/deployment twin of typescript/kernel-srv
 * (:8100). Joins the NATS mesh in namespace "kernel" — same substrate as
 * search (:4050, namespace "search"), solscript (:4060) and cascade (:4106),
 * with isolated discovery. NATS is live fleet infrastructure on :4222; `nats`
 * is a dependency of this service.
 *
 * For a STANDALONE side-by-side canary against the incumbent (no mesh
 * registration), use the sibling `moleculer.config.standalone.js` via
 * `moleculer-runner --config` (see README for the exact command).
 * NOTE: do NOT try to force this with `TRANSPORTER=null` in the environment —
 * moleculer-runner consumes TRANSPORTER itself and passes the literal string
 * "null", which fails BrokerOptionsError. (Cost one debug cycle.)
 */
module.exports = {
  namespace: "kernel",
  nodeID: "kernel-node-1",

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
  hotReload: false,
};
