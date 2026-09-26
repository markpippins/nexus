/**
 * TS broker config — NOT loaded by moleculer-runner (it only reads
 * moleculer.config.js; kept for editor tooling and type-reference parity
 * with the sibling twins).
 */
import type { BrokerOptions } from "moleculer";

export const brokerConfig: BrokerOptions = {
  namespace: "tackle",
  nodeID: "tackle-node-1",

  transporter: {
    type: "NATS",
    options: {
      url: process.env.NATS_URL || "nats://localhost:4222",
    },
  },

  requestTimeout: 45 * 1000,

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

export default brokerConfig;
