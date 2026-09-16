import { CapabilityId } from "../types/designIR";

/**
 * Runtime Fixture & Mock Data Provider
 * Isolated strictly to the runtime/Studio test layer.
 * The compiler never imports or calls this.
 */
export function generateRuntimeMockData(
  capability: CapabilityId,
  seedKey: string = "default",
): unknown {
  const baseTime = 1771600000000;
  const seed = seedKey.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);

  switch (capability) {
    case "MetricSeries":
      return {
        points: Array.from({ length: 16 }, (_, i) => ({
          x: baseTime - (15 - i) * 60000,
          y: Math.sin((i + seed) * 0.5) * 30 + 50,
        })),
        unit: "ms",
        status: "ok",
      };
    case "EntityCollection":
      return {
        items: Array.from({ length: 8 }, (_, i) => ({
          id: `ENT-${100 + i}`,
          name: `Cluster Worker Node ${String.fromCharCode(65 + (i % 6))}-${i + 1}`,
          status: i % 3 === 0 ? "healthy" : i % 3 === 1 ? "syncing" : "idle",
          latency: 12 + ((i * 7 + seed) % 45),
          leaseHold: `worker-${(i % 4) + 1}`,
        })),
        columns: [
          { key: "id", label: "NODE ID" },
          { key: "name", label: "CLUSTER IDENTIFIER" },
          { key: "status", label: "STATUS" },
          { key: "latency", label: "LATENCY (ms)" },
          { key: "leaseHold", label: "ACTIVE LEASE" },
        ],
      };
    case "StatusBoard":
      return {
        stages: [
          { id: "pending", label: "Pending Intake" },
          { id: "compiling", label: "Synthesizing Layout" },
          { id: "verified", label: "Contract Verified" },
        ],
        items: [
          {
            id: "REQ-01",
            stage: "compiling",
            title: "IR Spatial Grid Allocator",
            priority: "high",
          },
          {
            id: "REQ-02",
            stage: "pending",
            title: "Action Interpreter Dispatcher",
            priority: "medium",
          },
          {
            id: "REQ-03",
            stage: "verified",
            title: "Deterministic ID Seed Synthesizer",
            priority: "high",
          },
        ],
      };
    case "Timeline":
      return {
        events: [
          {
            id: "EVT-1",
            timestamp: baseTime - 450000,
            type: "info",
            message: "DesignIR AST parsed and validated",
          },
          {
            id: "EVT-2",
            timestamp: baseTime - 320000,
            type: "info",
            message: "Capability contract schemas resolved",
          },
          {
            id: "EVT-3",
            timestamp: baseTime - 60000,
            type: "info",
            message: "Reactive EventBus routing table compiled",
          },
        ],
      };
    case "KeyMetricMatrix":
      return {
        metrics: [
          { id: "m1", label: "Quorum Consensus", value: "99.98%", status: "ok" },
          { id: "m2", label: "Adapter Latency", value: "4.2 ms", unit: "ms", status: "ok" },
          { id: "m3", label: "AST Parse Rate", value: "1,420 ops/s", status: "ok" },
          { id: "m4", label: "Contract State Stores", value: "8 active", status: "ok" },
        ],
      };
    case "ConsensusMatrix":
      return {
        items: [
          {
            id: "POL-01",
            topic: "AST Node Immutability Invariant",
            status: "PASSED",
            votesFor: 7,
            votesAgainst: 0,
          },
          {
            id: "POL-02",
            topic: "Event State Routing via ContractStateStore",
            status: "PASSED",
            votesFor: 6,
            votesAgainst: 1,
          },
        ],
      };
    case "InspectorPanel":
      return {
        target: { id: "ACTIVE-NODE", role: "main", capability: "StatusBoard", status: "BOUND" },
        fields: [
          { key: "id", label: "IDENTIFIER" },
          { key: "role", label: "SPATIAL ROLE" },
          { key: "capability", label: "CAPABILITY" },
          { key: "status", label: "CONTRACT STATUS" },
        ],
      };
    case "AuditStream":
      return {
        entries: [
          {
            id: "AUD-1",
            timestamp: "18:24:02",
            actor: "compiler",
            action: "Synthesized ViewSpec program AST",
          },
          {
            id: "AUD-2",
            timestamp: "18:24:05",
            actor: "runtime",
            action: "Initialized ContractStateStores for 4 nodes",
          },
        ],
      };
    case "WorkQueue":
      return {
        items: [
          {
            id: "JOB-101",
            title: "Project REST payload -> MetricSeriesContract",
            status: "RUNNING",
            progress: 85,
          },
          {
            id: "JOB-102",
            title: "Compile Deliberation IR document",
            status: "QUEUED",
            progress: 0,
          },
        ],
      };
    default:
      return { surfaceType: "dashboard" };
  }
}
