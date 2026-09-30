/**
 * STANDALONE broker config for side-by-side canary runs against assembly-srv.
 * Run from moleculer/assembly after `npm run build` (fleet convention: this
 * file stays at the twin root, uncompiled):
 *   SERVICE_PORT=4107 npx moleculer-runner --config moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/assembly.service.js
 */
module.exports = {
  namespace: "assembly",
  nodeID: "assembly-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
