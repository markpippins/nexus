module.exports = {
  namespace: "conduit",
  nodeID: "conduit-twin-1",
  transporter: "NATS",
  nats: { servers: process.env.NATS_URL || "nats://localhost:4222" },
  logger: { type: "Console", options: { level: "info", colors: true } },
  requestTimeout: 30 * 1000,
  validator: true,
  metrics: { enabled: true },
  tracing: { enabled: true, exporter: "Console" },
};
