/**
 * STANDALONE broker config for side-by-side canary runs against aegis-srv.
 * Run from moleculer/aegis after `npm run build` (fleet convention: this
 * file stays at the twin root, uncompiled):
 *   SERVICE_PORT=4116 npx moleculer-runner --config moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/aegis.service.js
 */
module.exports = {
  namespace: "aegis",
  nodeID: "aegis-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
