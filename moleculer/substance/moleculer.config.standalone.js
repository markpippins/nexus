/**
 * STANDALONE broker config for side-by-side canary runs against substance-srv.
 * Run from moleculer/substance after `npm run build` (fleet convention: this
 * file stays at the twin root, uncompiled):
 *   SERVICE_PORT=4115 npx moleculer-runner --config moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/substance.service.js
 */
module.exports = {
  namespace: "substance",
  nodeID: "substance-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
