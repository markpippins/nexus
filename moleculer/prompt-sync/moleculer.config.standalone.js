/**
 * STANDALONE broker config — for side-by-side canary runs against the
 * incumbent (no NATS mesh registration). Use with moleculer-runner --config:
 *
 *   SERVICE_PORT=4501 npx moleculer-runner --config dist/moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/prompt-sync.service.js
 *
 * (moleculer-runner consumes --config paths relative to CWD; run from the
 * moleculer/prompt-sync directory. Positional service files land in
 * Args.sub — verified per the tackle-port boot mechanics.)
 */
module.exports = {
  namespace: "prompt-sync",
  nodeID: "prompt-sync-standalone-1",

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
