/**
 * STANDALONE broker config for side-by-side canary runs against wind-srv.
 * Run from moleculer/wind after `npm run build` (fleet convention: this
 * file stays at the twin root, uncompiled):
 *   SERVICE_PORT=4118 npx moleculer-runner --config moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/wind.service.js
 */
module.exports = {
  namespace: "wind",
  nodeID: "wind-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
