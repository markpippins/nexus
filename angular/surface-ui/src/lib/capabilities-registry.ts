import { CapabilityId } from "@/core/types/designIR";
import type { WidgetType, SchemaNode, ApiEndpoint } from "./widget-types";
import type { Adapter } from "@/core/adapter/types";

export interface WidgetContractMeta {
  id: CapabilityId;
  name: string;
  description: string;
  defaultVariant?: string;
  variants: string[];
  sampleInput: Record<string, unknown>;
  inputSchema: Record<string, { type: string; required: boolean; description: string }>;
  outputEvents: Array<{ name: string; payloadType: string; description: string }>;
}

export const CAPABILITY_REGISTRY: Record<CapabilityId, WidgetContractMeta> = {
  MetricSeries: {
    id: "MetricSeries",
    name: "Metric Series",
    description:
      "Time-series or discrete numeric telemetry points with trend direction, status, and thresholds.",
    variants: ["sparkline", "area", "bar", "compact-trend"],
    sampleInput: {
      points: [
        { x: Date.now() - 180000, y: 45 },
        { x: Date.now() - 120000, y: 67 },
        { x: Date.now() - 60000, y: 82 },
        { x: Date.now(), y: 55 },
      ],
      unit: "ms",
      trend: "up",
      threshold: 80,
      status: "warn",
    },
    inputSchema: {
      points: {
        type: "Array<{ x: number|string, y: number }>",
        required: true,
        description: "Sequential coordinates",
      },
      unit: { type: "string", required: false, description: "Metric measurement unit" },
      trend: {
        type: "'up' | 'down' | 'flat'",
        required: false,
        description: "Calculated trajectory",
      },
      threshold: { type: "number", required: false, description: "SLO or alert boundary" },
      status: {
        type: "'ok' | 'warn' | 'error'",
        required: false,
        description: "Computed health state",
      },
    },
    outputEvents: [
      {
        name: "inspect",
        payloadType: "{ pointIndex: number, value: number }",
        description: "Fired when user clicks or hovers a point",
      },
      {
        name: "drilldown",
        payloadType: "{ range: [number, number] }",
        description: "Fired when user zooms in on a time window",
      },
    ],
  },
  EntityCollection: {
    id: "EntityCollection",
    name: "Entity Collection",
    description:
      "Tabular, grid, or list representation of structured domain entities with sorting, filtering, and selection.",
    variants: ["table", "card-grid", "compact-list"],
    sampleInput: {
      items: [
        {
          id: "req-01",
          name: "auth_token_refresh",
          status: "active",
          latency: 24,
          owner: "security-agent",
        },
        {
          id: "req-02",
          name: "spec_validator_ast",
          status: "completed",
          latency: 128,
          owner: "compiler-bot",
        },
        {
          id: "req-03",
          name: "lease_allocator",
          status: "pending",
          latency: 4,
          owner: "scheduler",
        },
      ],
      columns: [
        { key: "id", label: "Request ID", type: "string" },
        { key: "name", label: "Operation", type: "string" },
        { key: "status", label: "Status", type: "badge" },
        { key: "latency", label: "Latency (ms)", type: "number" },
      ],
      selection: "req-01",
    },
    inputSchema: {
      items: {
        type: "Array<Record<string, unknown>>",
        required: true,
        description: "List of domain objects",
      },
      columns: {
        type: "Array<{ key: string, label: string, type?: string }>",
        required: true,
        description: "Visible column headers",
      },
      selection: { type: "string | string[]", required: false, description: "Selected item ID(s)" },
      sort: {
        type: "{ key: string, direction: 'asc' | 'desc' }",
        required: false,
        description: "Active sort column",
      },
    },
    outputEvents: [
      {
        name: "select",
        payloadType: "{ itemId: string, item: object }",
        description: "Fired when a row or card is selected",
      },
      {
        name: "filter",
        payloadType: "{ column: string, query: string }",
        description: "Fired when filtering criteria change",
      },
      {
        name: "sort",
        payloadType: "{ key: string, direction: 'asc'|'desc' }",
        description: "Fired when column header clicked",
      },
    ],
  },
  StatusBoard: {
    id: "StatusBoard",
    name: "Status Board (Kanban)",
    description: "Multi-stage pipeline workflow cards organized into ordered status columns.",
    variants: ["kanban", "swimlanes", "stage-strip"],
    sampleInput: {
      stages: [
        { id: "DRAFT", label: "Draft & Intake" },
        { id: "COMPILED", label: "Compiled & Admitted" },
        { id: "READY", label: "Ready for Lease" },
        { id: "EXECUTING", label: "Executing" },
      ],
      items: [
        { id: "OQ-441", stage: "DRAFT", title: "Optimistic concurrency check", priority: "high" },
        {
          id: "SPEC-084",
          stage: "COMPILED",
          title: "Bitemporal spec revision migration",
          priority: "medium",
        },
        {
          id: "HARVEST-009",
          stage: "READY",
          title: "Schema compiler AST two-stage pipeline",
          priority: "high",
        },
        {
          id: "RUN-104",
          stage: "EXECUTING",
          title: "Lease worker allocation heartbeat",
          priority: "low",
        },
      ],
    },
    inputSchema: {
      stages: {
        type: "Array<{ id: string, label: string }>",
        required: true,
        description: "Ordered pipeline stages",
      },
      items: {
        type: "Array<{ id: string, stage: string, title: string, priority?: string }>",
        required: true,
        description: "Workflow cards",
      },
    },
    outputEvents: [
      {
        name: "changeStage",
        payloadType: "{ itemId: string, fromStage: string, toStage: string }",
        description: "Fired when card is dragged or transitioned",
      },
      {
        name: "inspect",
        payloadType: "{ itemId: string, item: object }",
        description: "Fired when card is clicked",
      },
    ],
  },
  Timeline: {
    id: "Timeline",
    name: "Timeline & Revisions",
    description:
      "Chronological or bitemporal stream of events, revisions, or audits with timestamps and validity intervals.",
    variants: ["vertical-stream", "gantt", "bitemporal-diff"],
    sampleInput: {
      events: [
        {
          id: "rev-3",
          timestamp: "2026-08-20T00:00:00Z",
          type: "MAJOR_REFACTOR",
          message: "Migrated plan status storage to nebula.plans schema.",
          isCurrent: true,
        },
        {
          id: "rev-2",
          timestamp: "2026-08-14T12:00:00Z",
          type: "AMENDMENT",
          message: "Added optimistic concurrency check during requirement move.",
          isCurrent: false,
        },
        {
          id: "rev-1",
          timestamp: "2026-08-01T08:30:00Z",
          type: "INITIAL_BASELINE",
          message: "Initial schema definition for two-stage requirement compiler.",
          isCurrent: false,
        },
      ],
    },
    inputSchema: {
      events: {
        type: "Array<{ id: string, timestamp: string|number, type: string, message: string }>",
        required: true,
        description: "Chronological event entries",
      },
    },
    outputEvents: [
      {
        name: "inspect",
        payloadType: "{ eventId: string, event: object }",
        description: "Fired when event node is selected",
      },
      {
        name: "compare",
        payloadType: "{ fromId: string, toId: string }",
        description: "Fired when two revisions are compared",
      },
    ],
  },
  KeyMetricMatrix: {
    id: "KeyMetricMatrix",
    name: "Key Metric Matrix (KPIs)",
    description:
      "Executive and operational summary cards with values, units, delta badges, and sparkline trends.",
    variants: ["bento-grid", "header-strip", "compact-tiles"],
    sampleInput: {
      metrics: [
        {
          id: "kpi-requests",
          label: "Active Requests",
          value: 11,
          unit: "reqs",
          trend: "up",
          status: "ok",
        },
        {
          id: "kpi-leases",
          label: "Active Leases",
          value: 1,
          unit: "leased",
          trend: "flat",
          status: "ok",
        },
        {
          id: "kpi-latency",
          label: "P99 Execution Time",
          value: 142,
          unit: "ms",
          trend: "down",
          status: "ok",
        },
        {
          id: "kpi-failures",
          label: "Degraded Runs",
          value: 0,
          unit: "err",
          trend: "flat",
          status: "ok",
        },
      ],
    },
    inputSchema: {
      metrics: {
        type: "Array<{ id: string, label: string, value: number|string, unit?: string, status?: string }>",
        required: true,
        description: "Metric tiles",
      },
    },
    outputEvents: [
      {
        name: "inspect",
        payloadType: "{ metricId: string }",
        description: "Fired when metric tile is clicked",
      },
      {
        name: "drilldown",
        payloadType: "{ metricId: string }",
        description: "Fired when metric detail is expanded",
      },
    ],
  },
  ConsensusMatrix: {
    id: "ConsensusMatrix",
    name: "Consensus & Deliberation Matrix",
    description:
      "Multi-agent or multi-model voting matrix with confidence scores, deliberations, and resolutions.",
    variants: ["vote-grid", "matrix-bars", "confidence-radar"],
    sampleInput: {
      items: [
        {
          id: "claim-01",
          label: "Schema Migration Safety",
          votes: [
            { agent: "Compiler Guard", value: 1.0, confidence: 0.95 },
            { agent: "Runtime Verifier", value: 0.9, confidence: 0.88 },
            { agent: "Security Auditor", value: 1.0, confidence: 0.98 },
          ],
        },
        {
          id: "claim-02",
          label: "Optimistic Concurrency Fallback",
          votes: [
            { agent: "Compiler Guard", value: 0.8, confidence: 0.75 },
            { agent: "Runtime Verifier", value: 1.0, confidence: 0.92 },
            { agent: "Security Auditor", value: 0.85, confidence: 0.8 },
          ],
        },
      ],
      resolution: { id: "claim-01", label: "Consensus Approved", confidence: 0.94 },
    },
    inputSchema: {
      items: {
        type: "Array<{ id: string, label: string, votes: Array<object> }>",
        required: true,
        description: "Voting subjects",
      },
      resolution: {
        type: "{ id: string, label: string, confidence: number }",
        required: false,
        description: "Resolved outcome",
      },
    },
    outputEvents: [
      {
        name: "inspect",
        payloadType: "{ itemId: string, item: object }",
        description: "Fired when deliberation row is clicked",
      },
      {
        name: "compare",
        payloadType: "{ agentA: string, agentB: string }",
        description: "Fired when agent votes are diffed",
      },
    ],
  },
  InspectorPanel: {
    id: "InspectorPanel",
    name: "Inspector & Entity Details",
    description:
      "Deep detail pane showing key-values, telemetry metadata, JSON payloads, and actions for an inspected entity.",
    variants: ["sidebar-drawer", "detail-sheet", "raw-json"],
    sampleInput: {
      target: {
        id: "SPEC-084",
        name: "Bitemporal Spec Revision Diff",
        validity: "2026-08-20T00:00:00Z -> Present",
        schema: "nebula.plans.v3",
        status: "ACTIVE",
        owner: "mpippins@gmail.com",
      },
      fields: [
        { key: "id", label: "Entity ID" },
        { key: "name", label: "Title" },
        { key: "validity", label: "Valid Interval" },
        { key: "status", label: "Lifecycle Status" },
      ],
    },
    inputSchema: {
      target: { type: "Record<string, unknown>", required: true, description: "Inspected object" },
      fields: {
        type: "Array<{ key: string, label: string }>",
        required: false,
        description: "Rendered fields",
      },
    },
    outputEvents: [
      {
        name: "acknowledge",
        payloadType: "{ entityId: string }",
        description: "Fired when user approves inspected state",
      },
      { name: "dismiss", payloadType: "{}", description: "Fired when inspector is closed" },
    ],
  },
  AuditStream: {
    id: "AuditStream",
    name: "Audit Stream & Security Log",
    description:
      "Append-only activity log capturing actor identities, timestamps, operations, and cryptographic receipts.",
    variants: ["terminal-stream", "table-log", "compact-badge-feed"],
    sampleInput: {
      entries: [
        {
          id: "aud-001",
          timestamp: Date.now() - 60000,
          actor: "agent:compiler",
          action: "COMPILED_VIEW_SPEC",
          details: { surface: "dashboard" },
        },
        {
          id: "aud-002",
          timestamp: Date.now() - 40000,
          actor: "agent:scheduler",
          action: "LEASE_ACQUIRED",
          details: { worker: "node-us-west" },
        },
        {
          id: "aud-003",
          timestamp: Date.now() - 10000,
          actor: "user:mpippins",
          action: "ADAPTER_PROJECTED",
          details: { adapter: "execHealthAdapter" },
        },
      ],
    },
    inputSchema: {
      entries: {
        type: "Array<{ id: string, timestamp: number|string, actor?: string, action: string }>",
        required: true,
        description: "Log entries",
      },
    },
    outputEvents: [
      {
        name: "inspect",
        payloadType: "{ entryId: string, entry: object }",
        description: "Fired when log entry is selected",
      },
      {
        name: "filter",
        payloadType: "{ actor?: string, action?: string }",
        description: "Fired when log is filtered",
      },
    ],
  },
  WorkQueue: {
    id: "WorkQueue",
    name: "Work Queue & Lease Switchboard",
    description:
      "Telemetry monitor and lease control for async work requests, active locks, and retry queues.",
    variants: ["pipeline-console", "queue-strip", "switchboard"],
    sampleInput: {
      items: [
        { id: "task-01", type: "SPEC_COMPILE", status: "RUNNING", createdAt: Date.now() - 45000 },
        {
          id: "task-02",
          type: "REST_ADAPTER_SYNC",
          status: "QUEUED",
          createdAt: Date.now() - 20000,
        },
        {
          id: "task-03",
          type: "FIXTURE_STRESS_TEST",
          status: "COMPLETED",
          createdAt: Date.now() - 100000,
        },
      ],
    },
    inputSchema: {
      items: {
        type: "Array<{ id: string, type: string, status: string, createdAt: number|string }>",
        required: true,
        description: "Queued work units",
      },
    },
    outputEvents: [
      {
        name: "acknowledge",
        payloadType: "{ taskId: string }",
        description: "Fired when task is acknowledged",
      },
      {
        name: "inspect",
        payloadType: "{ taskId: string }",
        description: "Fired when task details are opened",
      },
    ],
  },
  SurfaceContext: {
    id: "SurfaceContext",
    name: "Surface Context & Telemetry Sync",
    description:
      "Operational surface metadata defining real-time sync frequencies and reliability modes.",
    variants: ["status-bar", "badge"],
    sampleInput: {
      surfaceType: "dashboard",
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strong",
    },
    inputSchema: {
      surfaceType: {
        type: "'dashboard' | 'workbench' | 'inspector' | 'timelineView'",
        required: true,
        description: "Surface layout role",
      },
      timeSensitivity: {
        type: "'realTime' | 'nearRealTime' | 'batch' | 'historical'",
        required: true,
        description: "Refresh cadence",
      },
      reliabilityBias: {
        type: "'eventual' | 'strong' | 'strict'",
        required: true,
        description: "Consistency model",
      },
    },
    outputEvents: [],
  },
};

export function getCapabilityForWidget(widgetId: string, widgetName: string): CapabilityId {
  const idLower = widgetId.toLowerCase();
  const nameLower = widgetName.toLowerCase();

  if (
    idLower.includes("sparkline") ||
    nameLower.includes("sparkline") ||
    idLower.includes("gauge") ||
    nameLower.includes("gauge")
  ) {
    return "MetricSeries";
  }
  if (
    idLower.includes("revision") ||
    nameLower.includes("revision") ||
    idLower.includes("timeline") ||
    nameLower.includes("diff")
  ) {
    return "Timeline";
  }
  if (
    idLower.includes("request-status") ||
    nameLower.includes("request status") ||
    idLower.includes("kanban")
  ) {
    return "StatusBoard";
  }
  if (
    idLower.includes("execution") ||
    nameLower.includes("execution") ||
    idLower.includes("switchboard")
  ) {
    return "WorkQueue";
  }
  if (
    idLower.includes("consensus") ||
    nameLower.includes("consensus") ||
    idLower.includes("deliberation")
  ) {
    return "ConsensusMatrix";
  }
  if (
    idLower.includes("inventory") ||
    nameLower.includes("inventory") ||
    idLower.includes("table") ||
    idLower.includes("cross-ref")
  ) {
    return "EntityCollection";
  }
  if (idLower.includes("kpi") || nameLower.includes("kpi") || idLower.includes("matrix")) {
    return "KeyMetricMatrix";
  }
  return "EntityCollection";
}
