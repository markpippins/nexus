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
 * In particular this does NOT apply the .ts file's `transporter: null`:
 * whether :4050 belongs on the NATS mesh is an undecided topology question
 * (architect), and flipping it here would change fleet behavior silently.
 * Keep both files' comments in sync when touching either.
 */
module.exports = {
  metrics: {
    enabled: true,
  },
};
