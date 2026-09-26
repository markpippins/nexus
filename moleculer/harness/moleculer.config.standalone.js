/**
 * STANDALONE broker config for side-by-side canary runs against harness-srv.
 * Run from moleculer/harness after `npm run build`:
 *   SERVICE_PORT=4420 npx moleculer-runner --config dist/moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/harness.service.js
 */
module.exports = {
  namespace: "harness",
  nodeID: "harness-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },
  internalServices: true,
  internalMiddlewares: true,
};
