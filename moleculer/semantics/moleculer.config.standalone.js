/**
 * STANDALONE broker config — for side-by-side canary runs against the
 * incumbent (no NATS mesh registration). Use with moleculer-runner --config:
 *
 *   SERVICE_PORT=4160 npx moleculer-runner --config dist/moleculer.config.standalone.js
 *
 * (moleculer-runner consumes --config paths relative to CWD; run from the
 * moleculer/semantics directory.)
 */
module.exports = {
  namespace: "semantics",
  nodeID: "semantics-standalone-1",

  transporter: null,

  logger: {
    type: "Console",
    options: {
      level: "warn",
      colors: true,
    },
  },

  requestTimeout: 10 * 1000,

  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
};
