import { BrokerOptions } from "moleculer";

// NOTE (per moleculer/search precedent, verified live 2026-09-11):
// moleculer-runner NEVER loads this .ts — it auto-loads moleculer.config.js only.
// This .ts governs jest (imported directly) and `tsc` builds.
//
// TOPOLOGY: :4060 BELONGS ON THE NATS MESH (namespace "solscript") — same
// substrate as :4050 (search) and :4080 (nexus-broker), isolated discovery.
// This .ts stays `transporter: null` for dev/tests; the prod moleculer.config.js
// carries the NATS transporter (mirrors the search :4050 mesh join ruling
// dee39674). Namespace isolation keeps domains separate on the shared bus.

const brokerConfig: BrokerOptions = {
  namespace: "solscript",
  nodeID: "solscript-node-1",

  logger: {
    type: "Console",
    options: {
      level: "info",
      colors: true,
      moduleColors: false,
      formatter: "full",
      autoPadding: false,
    },
  },

  transporter: null, // dev/test only — prod flip is in moleculer.config.js

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: false,
    retries: 5,
    delay: 100,
    maxDelay: 1000,
    factor: 2,
    check: (err: any) => err && !!err.retryable,
  },

  maxCallLevel: 100,
  heartbeatInterval: 10,
  heartbeatTimeout: 30,

  tracking: {
    enabled: false,
    shutdownTimeout: 5000,
  },

  registry: {
    strategy: "RoundRobin",
    preferLocal: true,
  },

  circuitBreaker: {
    enabled: false,
    threshold: 0.5,
    minRequestCount: 20,
    windowTime: 60,
    halfOpenTime: 10 * 1000,
    check: (err: any) => err && err.code >= 500,
  },

  validator: true,
  metrics: { enabled: true },
  tracing: { enabled: false },

  internalServices: true,
  internalMiddlewares: true,
  hotReload: true,
};

export = brokerConfig;