/**
 * STANDALONE broker config for side-by-side canary runs against conduit-srv.
 * Run from moleculer/conduit after `npm run build`:
 *   SERVICE_PORT=4104 npx moleculer-runner --config dist/moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/conduit.service.js
 */
module.exports = {
  namespace: "conduit",
  nodeID: "conduit-standalone-1",
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
