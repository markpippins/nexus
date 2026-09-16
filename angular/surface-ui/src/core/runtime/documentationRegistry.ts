/**
 * Nexus Documentation & Manual Mode Registry
 *
 * Maps capabilities, widgets, roles, surfaces, and workflows to structured
 * documentation, video walkthrough metadata, schema fields, and key operations.
 *
 * Invariant: External to compiler; purely runtime-configurable.
 */

export interface DocumentationEntry {
  id: string;
  title: string;
  subtitle?: string;
  summary: string;
  capabilityContract?: string;
  category: "capability" | "surface" | "workflow" | "role";
  detailsMarkdown?: string;
  videoTitle?: string;
  videoDuration?: string;
  videoPoster?: string;
  keyActions?: Array<{ label: string; description: string; verb?: string }>;
  schemaFields?: Array<{ name: string; type: string; description: string }>;
  examplePayload?: Record<string, unknown>;
}

export type DocumentationRegistry = Record<string, DocumentationEntry>;

export const CANONICAL_DOCUMENTATION_REGISTRY: DocumentationRegistry = {
  KeyMetricMatrix: {
    id: "KeyMetricMatrix",
    title: "KeyMetricMatrix Capability",
    subtitle: "High-salience KPI summary grid with trend indicators",
    category: "capability",
    capabilityContract: "KeyMetricMatrix",
    summary:
      "Projects high-salience operational metrics (e.g. TPS, latency, quorum consensus percentage, active leases) into a responsive grid with status color coding and sparkline trends.",
    detailsMarkdown: `### Overview
The \`KeyMetricMatrix\` contract standardizes the display of operational telemetry. Each metric node contains an explicit value, optional unit, delta trend (+/-), and a severity status (\`nominal\`, \`warning\`, \`critical\`).

### Runtime Behavior
- **Inspection**: Clicking any metric emits an \`inspect\` event with the metric's key and current value.
- **Auto-formatting**: Automatically applies SI units (e.g., ms, req/s, %) based on unit definitions.
- **Contract Projection**: Adapters project raw telemetry payloads into the standardized \`metrics: MetricItem[]\` schema.`,
    videoTitle: "Working with KeyMetricMatrix Telemetry",
    videoDuration: "1m 45s",
    keyActions: [
      {
        label: "Inspect Metric",
        description: "Routes metric telemetry to inspector sidebar.",
        verb: "inspect",
      },
      {
        label: "Filter Surface",
        description: "Applies metric threshold filter to surrounding widgets.",
        verb: "filter",
      },
    ],
    schemaFields: [
      {
        name: "metrics",
        type: "Array<MetricItem>",
        description: "Collection of metric telemetry items.",
      },
      { name: "metrics[].id", type: "string", description: "Unique identifier for metric." },
      { name: "metrics[].label", type: "string", description: "Human-readable label." },
      { name: "metrics[].value", type: "number | string", description: "Current value." },
      {
        name: "metrics[].trend",
        type: "string",
        description: "Directional delta ('+4.2%', '-12ms').",
      },
      {
        name: "metrics[].status",
        type: "'nominal' | 'warning' | 'critical'",
        description: "Visual status indicator.",
      },
    ],
    examplePayload: {
      metrics: [
        { id: "throughput", label: "Quorum TPS", value: 14250, trend: "+12.4%", status: "nominal" },
        {
          id: "p99_latency",
          label: "P99 Latency",
          value: "18.4ms",
          trend: "-2.1ms",
          status: "nominal",
        },
        {
          id: "error_rate",
          label: "AST Parse Faults",
          value: "0.02%",
          trend: "0.00%",
          status: "nominal",
        },
      ],
    },
  },

  StatusBoard: {
    id: "StatusBoard",
    title: "StatusBoard Capability",
    subtitle: "Multi-stage pipeline and execution state tracker",
    category: "capability",
    capabilityContract: "StatusBoard",
    summary:
      "Visualizes sequential or concurrent execution pipelines, worker lease locks, task stages, and real-time state transitions.",
    detailsMarkdown: `### Overview
The \`StatusBoard\` contract visualizes multi-stage tasks such as compiler passes, distributed leases, and pipeline jobs. Stages transition through \`pending\`, \`active\`, \`completed\`, and \`failed\`.

### Interactivity
- **Stage Click**: Emits \`inspect\` or \`select\` event payload containing the stage ID and metadata.
- **ContractStateStore**: Live updates reflect worker progress in real time.`,
    videoTitle: "Pipeline State & Worker Coordination",
    videoDuration: "2m 10s",
    keyActions: [
      {
        label: "Select Stage",
        description: "Focuses pipeline stage and updates active selection.",
        verb: "select",
      },
      {
        label: "Inspect Worker",
        description: "Displays stage worker locks in InspectorPanel.",
        verb: "inspect",
      },
    ],
    schemaFields: [
      {
        name: "stages",
        type: "Array<PipelineStage>",
        description: "Ordered pipeline execution stages.",
      },
      { name: "stages[].name", type: "string", description: "Stage title (e.g. 'IR Lexer')." },
      {
        name: "stages[].status",
        type: "'pending' | 'active' | 'completed' | 'failed'",
        description: "Stage status.",
      },
    ],
    examplePayload: {
      stages: [
        { id: "stage-1", name: "DesignIR Verification", status: "completed", duration: "12ms" },
        { id: "stage-2", name: "Spatial Synthesis", status: "active", progress: 68 },
        { id: "stage-3", name: "Adapter Projection", status: "pending" },
      ],
    },
  },

  InspectorPanel: {
    id: "InspectorPanel",
    title: "InspectorPanel Capability",
    subtitle: "Detailed property and contract state inspection sheet",
    category: "capability",
    capabilityContract: "InspectorPanel",
    summary:
      "Secondary role surface that subscribes to cross-widget inspect events and displays deep metadata, JSON contracts, and actionable entity commands.",
    detailsMarkdown: `### Overview
The \`InspectorPanel\` serves as the primary secondary-role surface for deep dive analysis. It listens on the reactive EventBus for \`inspect\` events emitted by primary widgets.

### Capabilities
- **Acknowledge / Dismiss**: Allows operators to acknowledge alerts or dismiss inspection targets.
- **Contract Inspection**: Renders structured JSON state with copy and patch capabilities.`,
    videoTitle: "Deep Inspection and Cross-Role Interactions",
    videoDuration: "1m 30s",
    keyActions: [
      {
        label: "Acknowledge Alert",
        description: "Dispatches acknowledge event to originating widget.",
        verb: "acknowledge",
      },
      {
        label: "Dismiss Inspection",
        description: "Clears current inspection target.",
        verb: "dismiss",
      },
    ],
    schemaFields: [
      {
        name: "target",
        type: "Record<string, unknown>",
        description: "Active entity payload under inspection.",
      },
      { name: "selected", type: "boolean", description: "Whether an item is currently selected." },
      { name: "inspectedAt", type: "number", description: "Timestamp of last inspection action." },
    ],
    examplePayload: {
      target: { entityId: "ENT-104", type: "WorkerLease", status: "locked", ttl: "45s" },
      selected: true,
      inspectedAt: Date.now(),
    },
  },

  AuditStream: {
    id: "AuditStream",
    title: "AuditStream Capability",
    subtitle: "Chronological event log and system audit trail",
    category: "capability",
    capabilityContract: "AuditStream",
    summary:
      "Ambient-role stream that logs immutable system events, security authorizations, consensus votes, and compilation trace records.",
    detailsMarkdown: `### Overview
\`AuditStream\` provides an ambient footer or sidebar stream of discrete events. It guarantees chronological order and provides filter/search capabilities across log levels.`,
    videoTitle: "Monitoring Audit Trails & Security Logs",
    videoDuration: "1m 15s",
    keyActions: [
      {
        label: "Inspect Entry",
        description: "Inspects specific audit entry details.",
        verb: "inspect",
      },
    ],
    schemaFields: [
      {
        name: "events",
        type: "Array<AuditEvent>",
        description: "List of chronological audit log items.",
      },
    ],
    examplePayload: {
      events: [
        {
          id: "evt-1",
          timestamp: "12:40:01",
          severity: "info",
          message: "Quorum reached for schema rev #42",
        },
        {
          id: "evt-2",
          timestamp: "12:40:15",
          severity: "warn",
          message: "Worker node 0x7f lease lock renewed",
        },
      ],
    },
  },

  ConsensusMatrix: {
    id: "ConsensusMatrix",
    title: "ConsensusMatrix Capability",
    subtitle: "Multi-agent quorum deliberation and vote tallying",
    category: "capability",
    capabilityContract: "ConsensusMatrix",
    summary:
      "Renders multi-agent voting rounds, distributed quorum thresholds, proposal states, and consensus convergence metrics.",
    detailsMarkdown: `### Overview
Used for high-integrity multi-agent environments. Visualizes agent votes, proposed state mutations, and cryptographic signatures.`,
    videoTitle: "Quorum Deliberation & Voting Rounds",
    videoDuration: "2m 30s",
    keyActions: [
      { label: "Inspect Round", description: "Views vote breakdown for round.", verb: "inspect" },
      {
        label: "Compare Proposals",
        description: "Compares competing state proposals.",
        verb: "compare",
      },
    ],
    schemaFields: [
      { name: "proposals", type: "Array<Proposal>", description: "Active quorum proposals." },
      {
        name: "quorumReached",
        type: "boolean",
        description: "Whether consensus threshold is met.",
      },
    ],
    examplePayload: {
      proposals: [
        { id: "prop-1", title: "Promote AST v2", votesFor: 7, votesAgainst: 1, status: "accepted" },
      ],
      quorumReached: true,
    },
  },

  Timeline: {
    id: "Timeline",
    title: "Timeline Capability",
    subtitle: "Bitemporal and chronological event sequencer",
    category: "capability",
    capabilityContract: "Timeline",
    summary:
      "Visualizes chronological intervals, transaction time vs valid time revisions, and historical audit milestones.",
    detailsMarkdown: `### Overview
The \`Timeline\` widget supports bitemporal diffing and milestone tracking. Clicking any milestone updates the \`selectedEntity\` in \`InteractionContextStore\`.`,
    videoTitle: "Navigating Bitemporal Timeline Revisions",
    videoDuration: "1m 55s",
    keyActions: [
      { label: "Select Revision", description: "Updates active temporal slice.", verb: "select" },
      { label: "Inspect Diff", description: "Opens AST diff in inspector.", verb: "inspect" },
    ],
    schemaFields: [
      {
        name: "events",
        type: "Array<TimelineEvent>",
        description: "Chronological milestone entries.",
      },
    ],
    examplePayload: {
      events: [
        {
          id: "rev-101",
          timestamp: "2026-08-20 14:00",
          label: "Initial Schema Synthesis",
          author: "CompilerAgent",
        },
        {
          id: "rev-102",
          timestamp: "2026-08-21 09:30",
          label: "Spatial Grid Refactor",
          author: "Operator",
        },
      ],
    },
  },

  EntityCollection: {
    id: "EntityCollection",
    title: "EntityCollection Capability",
    subtitle: "Tabular entity inventory with row selection and filters",
    category: "capability",
    capabilityContract: "EntityCollection",
    summary:
      "High-density data grid with sorting, filtering, row selection, and cross-role inspection triggers.",
    detailsMarkdown: `### Overview
Renders collections of domain entities. Row selection immediately dispatches to \`InteractionContextStore.onRowSelect\`.`,
    videoTitle: "Entity Grid Filtering and Selection",
    videoDuration: "1m 20s",
    keyActions: [
      {
        label: "Select Row",
        description: "Selects entity and updates operator context.",
        verb: "select",
      },
      {
        label: "Inspect Entity",
        description: "Pushes entity record to inspector.",
        verb: "inspect",
      },
    ],
    schemaFields: [
      { name: "entities", type: "Array<EntityRecord>", description: "List of tabular records." },
    ],
    examplePayload: {
      entities: [
        { id: "ENT-101", name: "Lease Coordinator", status: "online", cpu: "14%", memory: "1.2GB" },
        { id: "ENT-104", name: "Compiler Worker #4", status: "busy", cpu: "89%", memory: "3.4GB" },
      ],
    },
  },

  WorkQueue: {
    id: "WorkQueue",
    title: "WorkQueue Capability",
    subtitle: "Active job queue with worker claims and prioritization",
    category: "capability",
    capabilityContract: "WorkQueue",
    summary:
      "Displays pending and executing asynchronous jobs, priority queues, and lease expirations.",
    detailsMarkdown: `### Overview
Tracks worker queues. Provides real-time job dispatch and status monitoring.`,
    videoTitle: "Work Queue Prioritization & Worker Leases",
    videoDuration: "1m 40s",
    keyActions: [
      { label: "Inspect Job", description: "Views job payload and stack trace.", verb: "inspect" },
    ],
    schemaFields: [{ name: "jobs", type: "Array<QueueJob>", description: "Active queue jobs." }],
    examplePayload: {
      jobs: [
        { id: "JOB-101", title: "Compile ViewSpec v3", priority: "high", status: "running" },
        { id: "JOB-102", title: "Emit Adapter Bindings", priority: "normal", status: "queued" },
      ],
    },
  },

  MetricSeries: {
    id: "MetricSeries",
    title: "MetricSeries Capability",
    subtitle: "Time-series line chart with anomaly callouts",
    category: "capability",
    capabilityContract: "MetricSeries",
    summary:
      "Interactive time-series charts displaying latency curves, memory utilization, and anomaly markers.",
    detailsMarkdown: `### Overview
Renders continuous time-series data with hover tooltips and range zoom.`,
    videoTitle: "Time-Series Anomaly Detection",
    videoDuration: "1m 50s",
    keyActions: [
      {
        label: "Inspect Point",
        description: "Drill down into time-slice latency.",
        verb: "inspect",
      },
    ],
    schemaFields: [
      {
        name: "points",
        type: "Array<{ time: string; value: number }>",
        description: "Time-series data points.",
      },
    ],
    examplePayload: {
      points: [
        { time: "12:00", value: 45 },
        { time: "12:05", value: 52 },
        { time: "12:10", value: 89 },
        { time: "12:15", value: 48 },
      ],
    },
  },
};

/**
 * Resolves documentation entry for given runtime targets
 */
export function resolveDocumentation(target: {
  capabilityId?: string;
  widgetType?: string;
  surfaceId?: string;
  roleId?: string;
  workflowId?: string;
}): DocumentationEntry {
  if (target.capabilityId && CANONICAL_DOCUMENTATION_REGISTRY[target.capabilityId]) {
    return CANONICAL_DOCUMENTATION_REGISTRY[target.capabilityId];
  }

  // Check matching by widget contract / capability
  for (const entry of Object.values(CANONICAL_DOCUMENTATION_REGISTRY)) {
    if (
      entry.capabilityContract &&
      (entry.capabilityContract === target.capabilityId ||
        entry.id.toLowerCase() === (target.widgetType || "").toLowerCase())
    ) {
      return entry;
    }
  }

  // Fallback generic documentation
  return {
    id: "generic-runtime-help",
    title: target.capabilityId || target.roleId || "Nexus Runtime Component",
    subtitle: "Contextual Runtime Manual",
    category: "capability",
    summary:
      "This runtime component is governed by pure DesignIR compilation and reactive ContractStateStore state projection.",
    detailsMarkdown: `### Nexus Vision Runtime Component
- **Surface**: \`${target.surfaceId || "Active Surface"}\`
- **Role**: \`${target.roleId || "Primary Role"}\`
- **Capability**: \`${target.capabilityId || "Standard Contract"}\`

All event mutations route strictly through the \`ActionInterpreter\` and update the \`InteractionContextStore\`.`,
    videoTitle: "Nexus Vision Architecture Tour",
    videoDuration: "1m 00s",
    keyActions: [
      {
        label: "Inspect Node",
        description: "Opens component contract in inspector.",
        verb: "inspect",
      },
    ],
  };
}
