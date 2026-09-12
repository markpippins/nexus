/**
 * PROD broker config — the ONLY config file moleculer-runner loads.
 *
 * The runner auto-loads `moleculer.config.js`/`.json` from cwd and NEVER
 * `moleculer.config.ts` (verified live 2026-09-11: boot logs
 * `Namespace: <not defined>` despite the .ts setting `namespace: "search"`).
 * The .ts file governs tests (imported directly) and `tsc` builds only.
 *
 * DELIBERATELY MINIMAL: this file enables ONLY what the canary needs
 * (request metrics for GET /api/traffic/counts). Every other setting stays
 * at moleculer defaults — exactly as the live service has always run.
 *
 * TOPOLOGY RULING (architect, CORRECTED 2026-09-12; supersedes the
 * standalone pin in commit 806eac5b and decision record 087e495b):
 *   :4050 BELONGS ON THE NATS MESH — membership is the target topology.
 * Evidence: NATS server is live fleet infrastructure on :4222 (7+ systemd
 * units subscribe: address-tts, cascade bridges, absorb-bus-mirror,
 * voyager-adapter; nexus-mesh-register/reconcile timers) — the mesh is
 * the fleet's transport substrate, not a hypothetical. nexus-broker's own
 * config/README document the growth path: "When the worker tier outgrows
 * one process, switch to a NATS/Redis transporter" — the tier now HAS two
 * moleculer brokers (:4080, :4050); that condition is met. :4050 was
 * authored mesh-ready (`namespace: "search"`, `nodeID: "search-node-1"`);
 * `transporter: null` was always "for now", never a ruling. Namespace
 * isolation ("search" vs nexus-broker's "nexus") means sharing NATS
 * transport does NOT merge discovery domains — mesh = shared transport,
 * namespaces = shared isolation, so joining is safe and reversible.
 *
 * MESH JOIN (executed per corrected ruling dee39674): transporter NATS +
 * authored namespace/nodeID below. `nats` npm dep installed (boot throws
 * without it — same crash-loop class as #210). Namespace "search" vs
 * nexus-broker's "nexus": shared transport, isolated discovery.
 * Keep both files' comments in sync when touching either.
 */
module.exports = {
  namespace: "search",
  nodeID: "search-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: "nats://localhost:4222",
    },
  },

  metrics: {
    enabled: true,
  },
};
