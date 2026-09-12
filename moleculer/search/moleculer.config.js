/**
 * PROD broker config — the ONLY config file moleculer-runner loads.
 *
 * The runner auto-loads `moleculer.config.js`/`.json` from cwd and NEVER
 * `moleculer.config.ts` (verified live 2026-09-11: boot logs
 * `Namespace: <not defined>` despite the .ts setting `namespace: "search"`).
 * The .ts file governs tests (imported directly) and `tsc` builds only.
 *
 * DELIBERATELY MINIMAL: this file enables ONLY what the canary needs
 * (request metrics for GET /api/traffic/counts) plus the topology pin.
 * Every other setting stays at moleculer defaults — exactly as the live
 * service has always run.
 *
 * TOPOLOGY RULING (architect, 2026-09-12, record/PR #211): :4050 is
 * STANDALONE — `transporter: null` is pinned here explicitly. This is not
 * a behavior change: live boot logs prove the runner defaults already run
 * LocalDiscoverer (no NATS attach: `Discoverer: LocalDiscoverer`). The pin
 * makes the status quo deterministic and immune to runner-default drift.
 * The service has no moleculer-to-moleculer consumers (registry-client is
 * HTTP/axios to :8085; callers hit the HTTP envelope) — nothing would use
 * a NATS mesh, and joining one would only add discovery noise.
 * Keep both files' comments in sync when touching either.
 */
module.exports = {
  transporter: null, // STANDALONE pin — architect ruling, PR #211 (2026-09-12)
  metrics: {
    enabled: true,
  },
};
