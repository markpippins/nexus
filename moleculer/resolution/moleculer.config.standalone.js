/**
 * STANDALONE broker config for side-by-side canary runs against resolution-srv.
 * Run from moleculer/resolution after `npm run build` (fleet convention: this
 * file stays at the twin root, uncompiled):
 *   SERVICE_PORT=4171 npx moleculer-runner --config moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/resolution.service.js
 */
module.exports = {
  namespace: "resolution",
  nodeID: "resolution-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
