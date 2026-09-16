import { ServiceBroker } from "moleculer";

/**
 * Test broker config — off the live :4060 (gateway NOT started in config tests,
 * so no HTTP bind). The solscript service starts its own interpreter in-memory.
 */
export const testBrokerConfig: any = {
  namespace: "solscript-test",
  nodeID: "solscript-test-node",
  transporter: null,
  logger: { type: "Console", options: { level: "error" } },
  validator: true,
  metrics: { enabled: false },
  tracing: { enabled: false },
};

/** Start a fresh test broker with a given service, returning {broker, service}. */
export async function startBroker(serviceCtor: any): Promise<{ broker: ServiceBroker; service: any }> {
  const broker = new ServiceBroker(testBrokerConfig);
  const service = broker.createService(serviceCtor) as any;
  await broker.start();
  return { broker, service };
}