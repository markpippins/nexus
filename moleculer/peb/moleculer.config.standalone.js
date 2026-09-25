/**
 * STANDALONE broker config for side-by-side canary runs against peb-srv.
 * Run from moleculer/peb after `npm run build`:
 *   SERVICE_PORT=4111 npx moleculer-runner --config dist/moleculer.config.standalone.js \
 *     dist/services/api.service.js dist/services/peb.service.js
 */
module.exports = {
  namespace: "peb",
  nodeID: "peb-standalone-1",
  transporter: null,
  logger: {
    type: "Console",
    options: { level: "warn", colors: true },
  },
  requestTimeout: 10 * 1000,
};
