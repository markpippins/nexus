/**
 * PROD broker config — the ONLY config file moleculer-runner loads (it never
 * loads moleculer.config.ts; verified live per the search :4050 precedent).
 *
 * TOPOLOGY: :4501, the canary/deployment twin of typescript/tackle-prompt-sync-srv
 * (:3501). Joins the NATS mesh in namespace "prompt-sync" — same substrate
 * as search (:4050), solscript (:4060), cascade (:4106), kernel (:4100),
 * knowledge (:4109), draft (:4170), role-memory (:4150), semantics (:4160)
 * and tackle (:4410), with isolated discovery. NATS is live fleet
 * infrastructure on :4222; `nats` is a dependency of this service.
 *
 * For a STANDALONE side-by-side canary against the incumbent (no mesh
 * registration), use the sibling `moleculer.config.standalone.js` via
 * `moleculer-runner --config` (see README for the exact command).
 * NOTE: do NOT try to force this with `TRANSPORTER=null` in the environment —
 * moleculer-runner consumes TRANSPORTER itself and passes the literal string
 * "null", which fails BrokerOptionsError. (Cost one debug cycle.)
 */
module.exports = {
  namespace: "prompt-sync",
  nodeID: "prompt-sync-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: process.env.NATS_URL || "nats://localhost:4222",
    },
  },

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: true,
    retries: 3,
    delay: 100,
    maxDelay: 1000,
    factor: 2,
    check: (err) => err && !!err.retryable,
  },

  logger: {
    type: "Console",
    options: {
      level: "info",
      colors: true,
    },
  },

  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
};
