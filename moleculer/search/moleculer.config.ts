import { BrokerOptions } from "moleculer";

// NOTE (verified live 2026-09-11): moleculer-runner NEVER loads this file —
// it auto-loads moleculer.config.js/.json only (boot logs prove it:
// `Namespace: <not defined>`). This .ts governs jest (imported directly)
// and `tsc` builds. The committed moleculer.config.js carries the ONLY
// prod-effective settings (currently: request metrics for the traffic
// canary).
//
// TOPOLOGY RULING (architect, CORRECTED 2026-09-12; supersedes commit
// 806eac5b and decision record 087e495b): :4050 BELONGS ON THE NATS MESH.
// The fleet is already a NATS meshed substrate (:4222 live; address-tts,
// cascade bridges, absorb-bus-mirror, voyager-adapter all subscribe;
// nexus-mesh-register/reconcile timers). nexus-broker's config/README
// document "when the worker tier outgrows one process, switch to a
// NATS/Redis transporter" — the tier now has two brokers (:4080, :4050).
// This .ts stays airborne `transporter: null` for dev/tests; the prod
// moleculer.config.js carries the flip as a SEPARATE staged follow-up
// (adds `nats` npm dep → restart → verify NATSDiscovery before declaring
// join). Namespace "search" isolates discovery from nexus-broker's "nexus"
// on the shared bus — transport joins, domains stay isolated.
// Keep the .js and this file's comments in sync when touching either.

const brokerConfig: BrokerOptions = {
  namespace: "search",
  nodeID: "search-node-1",
  
  logger: {
    type: "Console",
    options: {
      level: "info",
      colors: true,
      moduleColors: false,
      formatter: "full",
      autoPadding: false
    }
  },

  transporter: null, // dev/test only — prod flip is staged separately (see header ruling)

  requestTimeout: 10 * 1000,
  retryPolicy: {
    enabled: false,
    retries: 5,
    delay: 100,
    maxDelay: 1000,
    factor: 2,
    check: (err: any) => err && !!err.retryable
  },

  maxCallLevel: 100,
  heartbeatInterval: 10,
  heartbeatTimeout: 30,

  tracking: {
    enabled: false,
    shutdownTimeout: 5000,
  },

  disableBalancer: false,

  registry: {
    strategy: "RoundRobin",
    preferLocal: true
  },

  circuitBreaker: {
    enabled: false,
    threshold: 0.5,
    minRequestCount: 20,
    windowTime: 60,
    halfOpenTime: 10 * 1000,
    check: (err: any) => err && err.code >= 500
  },

  bulkhead: {
    enabled: false,
    concurrency: 10,
    maxQueueSize: 100,
  },

  validator: true,

  // M1 traffic canary: built-in request metrics feed api.trafficCounts
  // (GET /api/traffic/counts). Enabled explicitly (default off) — negligible
  // overhead for this service, platform-maintained counters.
  metrics: {
    enabled: true,
  },

  tracing: {
    enabled: false,
  },

  internalServices: true,
  internalMiddlewares: true,

  hotReload: true,
};

export = brokerConfig;