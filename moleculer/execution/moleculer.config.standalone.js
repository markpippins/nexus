/**
 * STANDALONE broker config for side-by-side canary runs against execution-srv.
 * Run from moleculer/execution after `npm run build`:
 *   SERVICE_PORT=4110 npx moleculer-runner --config dist/moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/execution.service.js
 */
module.exports = {
  namespace: "execution",
  nodeID: "execution-standalone-1",
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
