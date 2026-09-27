/**
 * PROD broker config — the ONLY config file moleculer-runner loads (it never
 * loads moleculer.config.ts; verified live per the search :4050 precedent).
 *
 * TOPOLOGY: :4410, the canary/deployment twin of typescript/tackle-srv
 * (:3410). Joins the NATS mesh in namespace "tackle" — same substrate as
 * search (:4050), solscript (:4060), cascade (:4106), kernel (:4100),
 * knowledge (:4109), draft (:4170), role-memory (:4150) and semantics
 * (:4160), with isolated discovery. NATS is live fleet infrastructure on
 * :4222; `nats` is a dependency of this service.
 *
 * NOTE: :3104 is conduit-srv, NOT tackle-srv — the incumbent's own health
 * shape ({"status":"ok","port":3410,...}) and its process cwd
 * (typescript/tackle-srv) pin the incumbent to :3410. The +1000 band puts
 * the twin on :4410.
 *
 * For a STANDALONE side-by-side canary against the incumbent (no mesh
 * registration), use the sibling `moleculer.config.standalone.js` via
 * `moleculer-runner --config` (see README for the exact command).
 * NOTE: do NOT try to force this with `TRANSPORTER=null` in the environment —
 * moleculer-runner consumes TRANSPORTER itself and passes the literal string
 * "null", which fails BrokerOptionsError. (Cost one debug cycle.)
 */
module.exports = {
  namespace: "tackle",
  nodeID: "tackle-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: process.env.NATS_URL || "nats://localhost:4222",
    },
  },

  // SSE (/log/:sessionId) holds a response open up to 30s; the incumbent
  // has no request timeout at all. Keep the broker's inter-service request
  // timeout well clear of the SSE window.
  requestTimeout: 45 * 1000,
  retryPolicy: {
    enabled: true,
    retries: 3,
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
