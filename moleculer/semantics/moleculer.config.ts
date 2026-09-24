/**
 * TypeScript mirror of moleculer.config.js — kept for editor support and
 * documentation. moleculer-runner NEVER loads this file (it loads
 * moleculer.config.js only; verified live per the search :4050 precedent).
 */
export default {
  namespace: "semantics",
  nodeID: "semantics-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: process.env.NATS_URL || "nats://localhost:4222",
    },
  },

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: true,
    retries: 3,
  },
};
