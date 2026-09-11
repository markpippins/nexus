import { BrokerOptions } from "moleculer";

export const testBrokerConfig: BrokerOptions = {
  namespace: "test",
  nodeID: "test-node",
  logger: false,
  transporter: null,
  requestTimeout: 5000,
  retryPolicy: {
    enabled: false,
    retries: 0,
  },
  registry: {
    strategy: "RoundRobin",
    preferLocal: true,
  },
  // NOTE: keep internalMiddlewares ON (prod default). Setting it false
  // silently disables the Validator middleware, so action `params` are
  // never checked and missing-query tests pass for the wrong reason
  // (any rejection satisfies rejects.toThrow). Suite must prove what
  // production enforces.
  internalMiddlewares: true,
  internalServices: false,
};
