/**
 * TypeScript mirror of moleculer.config.js — kept for editor support and
 * documentation. moleculer-runner NEVER loads this file (it loads
 * moleculer.config.js only; verified live per the search :4050 precedent).
 */
export default {
  namespace: "role-memory",
  nodeID: "role-memory-node-1",

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

  logger: {
    type: "Console",
    options: {
      level: "info",
      colors: true,
    },
  },

  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
};
