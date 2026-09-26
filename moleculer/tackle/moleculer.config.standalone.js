/**
 * STANDALONE broker config — for side-by-side canary runs against the
 * incumbent (no NATS mesh registration). Use with moleculer-runner --config:
 *
 *   SERVICE_PORT=4410 npx moleculer-runner --config dist/moleculer.config.standalone.js
 *
 * (moleculer-runner consumes --config paths relative to CWD; run from the
 * moleculer/tackle directory.)
 */
module.exports = {
  namespace: "tackle",
  nodeID: "tackle-standalone-1",

  transporter: null,

  // SSE (/log/:sessionId) holds a response open up to 30s; keep the broker's
  // inter-service request timeout well clear of that window.
  requestTimeout: 45 * 1000,

  logger: {
    type: "Console",
    options: {
      level: "warn",
      colors: true,
    },
  },

  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
};
