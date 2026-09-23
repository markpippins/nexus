/**
 * STANDALONE broker config — the same broker as moleculer.config.js but with
 * the NATS transporter disabled, for local side-by-side canary comparison
 * against the incumbent typescript/kernel-srv (:8100).
 *
 * (the usual services glob is passed to moleculer-runner; see README).
 *
 * Why a separate file instead of an env switch: moleculer-runner parses the
 * `TRANSPORTER` environment variable itself and passes the raw string through,
 * so `TRANSPORTER=null` yields "'null' is not a valid transporter" rather than
 * a null transporter. Requiring the prod config keeps one source of truth.
 *
 * The HTTP gateway still binds SERVICE_PORT (default :4100) and every alias
 * resolves locally (registry.preferLocal), so nothing about the route surface
 * or the DB path differs from the mesh-joined run — only mesh registration is
 * skipped.
 */
const prod = require("./moleculer.config");

module.exports = Object.assign({}, prod, {
  transporter: null,
  nodeID: "kernel-standalone-1",
  metrics: { enabled: false },
  hotReload: false,
});
