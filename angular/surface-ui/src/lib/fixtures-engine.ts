import { CapabilityId } from "@/core/types/designIR";
import { CAPABILITY_REGISTRY } from "./capabilities-registry";

export type FixtureScenario = "nominal" | "empty" | "overflow" | "degraded" | "fuzz";

export interface FixturePreset {
  id: FixtureScenario;
  name: string;
  description: string;
  badge: string;
}

export const FIXTURE_PRESETS: FixturePreset[] = [
  {
    id: "nominal",
    name: "Nominal / Baseline",
    description: "Standard realistic production payload within healthy operating parameters.",
    badge: "HEALTHY",
  },
  {
    id: "empty",
    name: "Empty / Zero State",
    description: "Empty arrays, null values, or zero counts to verify first-run and cold-start UX.",
    badge: "ZERO STATE",
  },
  {
    id: "overflow",
    name: "Overflow / High Volume",
    description:
      "100+ entities, massive numerical spikes, and long strings to test layout wrapping.",
    badge: "STRESS TEST",
  },
  {
    id: "degraded",
    name: "Degraded / Errors",
    description: "Alert thresholds exceeded, failed execution states, error status badges.",
    badge: "ALERT STATE",
  },
  {
    id: "fuzz",
    name: "Random Fuzz Generator",
    description:
      "Dynamic stochastic generation with random noise for jitter and transition testing.",
    badge: "LIVE FUZZ",
  },
];

export function generateCapabilityFixture(
  capabilityId: CapabilityId,
  scenario: FixtureScenario,
  seed = Date.now(),
): Record<string, unknown> {
  const meta = CAPABILITY_REGISTRY[capabilityId] || CAPABILITY_REGISTRY.EntityCollection;

  switch (scenario) {
    case "nominal":
      return JSON.parse(JSON.stringify(meta.sampleInput));

    case "empty":
      switch (capabilityId) {
        case "MetricSeries":
          return { points: [], unit: "ms", status: "ok", trend: "flat" };
        case "EntityCollection":
          return { items: [], columns: meta.sampleInput.columns, selection: null };
        case "StatusBoard":
          return {
            stages: [
              { id: "TODO", label: "To Do" },
              { id: "IN_PROGRESS", label: "In Progress" },
              { id: "DONE", label: "Done" },
            ],
            items: [],
          };
        case "Timeline":
          return { events: [] };
        case "KeyMetricMatrix":
          return {
            metrics: [
              { id: "m1", label: "Active Requests", value: 0, unit: "reqs", status: "ok" },
              { id: "m2", label: "Active Leases", value: 0, unit: "leases", status: "ok" },
            ],
          };
        case "ConsensusMatrix":
          return { items: [], resolution: null };
        case "InspectorPanel":
          return { target: null, fields: [] };
        case "AuditStream":
          return { entries: [] };
        case "WorkQueue":
          return { items: [] };
        default:
          return {};
      }

    case "overflow":
      switch (capabilityId) {
        case "MetricSeries":
          return {
            points: Array.from({ length: 60 }, (_, i) => ({
              x: seed - (59 - i) * 10000,
              y: Math.sin(i / 4) * 400 + 500 + Math.random() * 50,
            })),
            unit: "req/s",
            trend: "up",
            threshold: 800,
            status: "ok",
          };
        case "EntityCollection":
          return {
            columns: [
              { key: "id", label: "ID", type: "string" },
              { key: "name", label: "Operation Title (Long Identifier)", type: "string" },
              { key: "status", label: "Lifecycle Status", type: "badge" },
              { key: "latency", label: "P99 Latency (ms)", type: "number" },
              { key: "owner", label: "Assigned Agent Subsystem", type: "string" },
            ],
            items: Array.from({ length: 40 }, (_, i) => ({
              id: `ENTITY-LONG-ID-HASH-${(1000 + i).toString(16).toUpperCase()}`,
              name: `Super-Scale-Distributed-Transaction-Shard-${i}-Worker-Sync-Loop-Cluster-Alpha`,
              status: ["active", "completed", "pending", "syncing"][i % 4],
              latency: Math.floor(Math.random() * 1200) + 50,
              owner: `agent-${i % 8}.cluster.internal`,
            })),
            selection: "ENTITY-LONG-ID-HASH-3E8",
          };
        case "StatusBoard":
          return {
            stages: [
              { id: "BACKLOG", label: "Backlog (24)" },
              { id: "DRAFT", label: "Draft & Intake (18)" },
              { id: "COMPILED", label: "Compiled AST (35)" },
              { id: "EXECUTING", label: "Active Shards (12)" },
              { id: "VERIFIED", label: "Consensus Verified (50)" },
            ],
            items: Array.from({ length: 30 }, (_, i) => ({
              id: `TASK-OVERFLOW-${i + 1}`,
              stage: ["BACKLOG", "DRAFT", "COMPILED", "EXECUTING", "VERIFIED"][i % 5],
              title: `High-throughput batch job #${i + 1} with deep execution trace`,
              priority: ["high", "medium", "low"][i % 3],
            })),
          };
        case "Timeline":
          return {
            events: Array.from({ length: 25 }, (_, i) => ({
              id: `rev-${25 - i}`,
              timestamp: new Date(seed - (25 - i) * 86400000).toISOString(),
              type: ["MAJOR_REFACTOR", "AMENDMENT", "SECURITY_HOTFIX", "SCHEMA_MIGRATION"][i % 4],
              message: `Revision #${25 - i}: Automated reconciliation of schema diff and lattice nodes.`,
              isCurrent: i === 24,
            })),
          };
        case "KeyMetricMatrix":
          return {
            metrics: Array.from({ length: 12 }, (_, i) => ({
              id: `kpi-${i}`,
              label: `Cluster Node ${i} Throughput`,
              value: Math.floor(Math.random() * 950000) + 50000,
              unit: "iops",
              trend: i % 2 === 0 ? "up" : "down",
              status: i === 3 ? "warn" : "ok",
            })),
          };
        default:
          return meta.sampleInput;
      }

    case "degraded":
      switch (capabilityId) {
        case "MetricSeries":
          return {
            points: [
              { x: seed - 180000, y: 35 },
              { x: seed - 120000, y: 78 },
              { x: seed - 60000, y: 145 },
              { x: seed, y: 210 },
            ],
            unit: "err/s",
            trend: "up",
            threshold: 50,
            status: "error",
          };
        case "EntityCollection":
          return {
            columns: meta.sampleInput.columns,
            items: [
              {
                id: "ERR-001",
                name: "deadlock_detector",
                status: "FAILED",
                latency: 5400,
                owner: "scheduler",
              },
              {
                id: "ERR-002",
                name: "lease_timeout_expirator",
                status: "DEGRADED",
                latency: 3200,
                owner: "lease-guard",
              },
              {
                id: "ERR-003",
                name: "consensus_quorum_checker",
                status: "UNRESPONSIVE",
                latency: 9999,
                owner: "auditor",
              },
            ],
          };
        case "StatusBoard":
          return {
            stages: [
              { id: "BLOCKED", label: "Blocked & Quarantine" },
              { id: "RETRYING", label: "Retry Loop" },
              { id: "FAILED", label: "Fatal Errors" },
            ],
            items: [
              {
                id: "ERR-991",
                stage: "BLOCKED",
                title: "Storage quorum unreachable on us-west",
                priority: "high",
              },
              {
                id: "ERR-992",
                stage: "RETRYING",
                title: "Optimistic lease conflict on slot 4",
                priority: "high",
              },
              {
                id: "ERR-993",
                stage: "FAILED",
                title: "AST compilation syntax exception",
                priority: "high",
              },
            ],
          };
        case "Timeline":
          return {
            events: [
              {
                id: "err-1",
                timestamp: new Date(seed - 60000).toISOString(),
                type: "FATAL_CRASH",
                message: "Kernel process panic on shard 03",
              },
              {
                id: "err-2",
                timestamp: new Date(seed - 120000).toISOString(),
                type: "QUORUM_LOST",
                message: "Raft leader election split brain detected",
              },
            ],
          };
        case "KeyMetricMatrix":
          return {
            metrics: [
              { id: "m1", label: "Error Rate", value: "34.8%", status: "error", trend: "up" },
              { id: "m2", label: "P99 Latency", value: "4,210 ms", status: "error", trend: "up" },
              { id: "m3", label: "Healthy Nodes", value: "1 / 8", status: "warn", trend: "down" },
            ],
          };
        default:
          return meta.sampleInput;
      }

    case "fuzz":
      switch (capabilityId) {
        case "MetricSeries":
          return {
            points: Array.from({ length: 15 }, (_, i) => ({
              x: seed - (14 - i) * 15000,
              y: Math.floor(Math.random() * 100),
            })),
            unit: ["ms", "req/s", "%", "MB"][Math.floor(Math.random() * 4)],
            status: (["ok", "warn", "error"] as const)[Math.floor(Math.random() * 3)],
            trend: (["up", "down", "flat"] as const)[Math.floor(Math.random() * 3)],
          };
        case "EntityCollection":
          return {
            columns: meta.sampleInput.columns,
            items: Array.from({ length: 6 }, (_, i) => ({
              id: `fuzz-${Math.floor(Math.random() * 9000 + 1000)}`,
              name: `Random_Op_${Math.random().toString(36).substring(7)}`,
              status: ["active", "pending", "completed", "error"][Math.floor(Math.random() * 4)],
              latency: Math.floor(Math.random() * 500),
              owner: `bot-${Math.floor(Math.random() * 5)}`,
            })),
          };
        default:
          return meta.sampleInput;
      }
  }
}
