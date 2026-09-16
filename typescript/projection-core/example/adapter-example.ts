import { AdapterRuntime } from "../src/adapter/runtime";
import { Adapter } from "../src/adapter/types";

const mockResponse = {
  metrics: {
    health: {
      samples: [
        { timestamp: Date.now() - 180000, value: 45, status: "OK" },
        { timestamp: Date.now() - 120000, value: 67, status: "OK" },
        { timestamp: Date.now() - 60000, value: 82, status: "WARN" },
        { timestamp: Date.now(), value: 55, status: "OK" },
      ],
    },
  },
};

const metricAdapter: Adapter = {
  id: "execHealthAdapter",
  source: { type: "mock", mock: mockResponse },
  outputContract: "MetricSeries",
  steps: [
    { op: "select", args: { path: "metrics.health.samples" } },
    {
      op: "map",
      args: {
        fields: {
          x: "timestamp",
          y: "value",
        },
      },
    },
    { op: "sortBy", args: { key: "x", direction: "asc" } },
    {
      op: "semanticMap",
      args: {
        field: "status",
        map: {
          OK: "ok",
          WARN: "warn",
          ERROR: "error",
        },
      },
    },
    { op: "default", args: { value: 0 } },
  ],
};

const runtime = new AdapterRuntime();
runtime.register(metricAdapter);

async function main() {
  const result = await runtime.execute("execHealthAdapter", mockResponse);
  console.log("=== Adapter Pipeline Result ===");
  console.log(JSON.stringify(result, null, 2));
}

main().catch(console.error);
