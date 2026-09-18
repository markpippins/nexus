import { BrokerOptions } from "moleculer";

/**
 * nexus-broker — Moleculer worker-tier broker (D-2026-08-14-002).
 *
 * Hosts the process-spawning worker services re-homed from the Express
 * fleet (harness-srv, execution-srv — Wave 4; pty-srv RETIRED M3 — replaced
 * by worker.pty-transport on :3130) plus any internal actions the two
 * AdonisJS edges call via the bus.
 *
 * Topology note: `transporter: null` runs all worker services in-process
 * (single-broker mode). When the worker tier outgrows one process, switch
 * to a NATS/Redis transporter without touching service code.
 */
const brokerConfig: BrokerOptions = {
  namespace: "nexus",
  nodeID: "nexus-broker-1",

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

  transporter: null, // in-process worker tier for now

  requestTimeout: 30 * 1000,
  retryPolicy: {
    enabled: false,
    retries: 5,
    delay: 100,
    maxDelay: 2000,
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

  disableBalancer: false,

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

  bulkhead: {
    enabled: false,
    concurrency: 10,
    maxQueueSize: 100,
  },

  validator: true,

  metrics: {
    enabled: false,
  },

  tracing: {
    enabled: false,
  },

  internalServices: true,
  internalMiddlewares: true,

  hotReload: true,
};

export = brokerConfig;
