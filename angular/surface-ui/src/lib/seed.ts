import { analyze } from "./analyze";
import { inferSchema } from "./schema";
import type { Widget } from "./widget-types";

const sparklineCode = `export default function Sparkline({ data, color = "#ffb74d" }) {
  const points = data?.points ?? [];
  if (!points.length) {
    return <div className="label-mono p-4 text-muted-foreground">no data</div>;
  }
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const range = max - min || 1;
  const width = 240;
  const height = 64;
  const coords = points.map((v, i) => {
    const x = (i / (points.length - 1 || 1)) * (width - 16) + 8;
    const y = height - 8 - ((v - min) / range) * (height - 16);
    return \`\${x},\${y}\`;
  }).join(" ");

  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="flex items-baseline justify-between">
        <span className="label-mono">{data?.label ?? "metric"}</span>
        <span className="font-mono text-sm font-semibold">{points[points.length - 1] ?? 0}</span>
      </div>
      <svg width={width} height={height} className="overflow-visible">
        <polyline
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={coords}
        />
      </svg>
    </div>
  );
}

// API: GET /api/metrics/sparkline
`;

const gaugeCode = `export default function Gauge({ value, max = 100, label = "utilization" }) {
  const val = typeof value === "object" ? (value?.value ?? 0) : Number(value || 0);
  const pct = Math.min(100, Math.max(0, (val / max) * 100));
  return (
    <div className="flex flex-col items-center gap-2 p-4">
      <div className="relative h-24 w-24">
        <svg viewBox="0 0 36 36" className="h-24 w-24 -rotate-90">
          <path
            className="text-border"
            strokeWidth="3.8"
            stroke="currentColor"
            fill="none"
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
          />
          <path
            className="text-primary transition-all duration-300"
            strokeDasharray={\`\${pct}, 100\`}
            strokeWidth="3.8"
            strokeLinecap="round"
            stroke="currentColor"
            fill="none"
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-base font-bold">{Math.round(pct)}%</span>
        </div>
      </div>
      <span className="label-mono">{label}</span>
    </div>
  );
}

// API: GET /api/metrics/gauge
`;

const inventoryCode = `export default function InventoryTable({ items = [] }) {
  const list = Array.isArray(items) ? items : (items?.items ?? []);
  return (
    <div className="w-full overflow-x-auto p-2">
      <table className="w-full text-left font-mono text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            <th className="py-2">item</th>
            <th className="py-2 text-right">stock</th>
            <th className="py-2 text-right">status</th>
          </tr>
        </thead>
        <tbody>
          {list.slice(0, 5).map((row, i) => (
            <tr key={i} className="border-b border-border/50">
              <td className="py-2 font-medium text-foreground">{row.name ?? \`item-\${i}\`}</td>
              <td className="py-2 text-right">{row.stock ?? row.count ?? 0}</td>
              <td className="py-2 text-right">
                <span className="rounded bg-accent/10 px-1.5 py-0.5 text-accent">
                  {row.status ?? "ok"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// API: GET /api/inventory/items
`;

const executionStateCode = `export default function ExecutionStateConsole({
  title = "Nebula Execution Switchboard",
  requests = {},
  leases = {},
  attempts = {},
  totalRequests = 11,
  activeLeases = 1
}) {
  const reqData = requests?.DRAFT !== undefined ? requests : { DRAFT: 3, COMPILED: 5, ADMITTED: 2, READY: 1 };
  const leaseData = leases?.ACTIVE !== undefined ? leases : { ACTIVE: 1, RELEASED: 4 };
  const attData = attempts?.RUNNING !== undefined ? attempts : { RUNNING: 1, SUCCEEDED: 3, FAILED: 1 };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-signal shadow-[0_0_8px_var(--color-signal)]" />
          <span className="font-semibold tracking-tight text-foreground">{title}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary border border-primary/20">
            {totalRequests} Total
          </span>
          <span className="rounded bg-signal/10 px-1.5 py-0.5 text-[10px] text-signal border border-signal/20">
            {activeLeases} Active Lease
          </span>
        </div>
      </div>

      <div className="space-y-1.5">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Request Pipeline</div>
        <div className="grid grid-cols-4 gap-1.5 text-center">
          <div className="rounded border border-border/50 bg-background/50 p-2">
            <div className="text-[10px] text-muted-foreground">DRAFT</div>
            <div className="text-base font-bold text-foreground">{reqData.DRAFT ?? 0}</div>
          </div>
          <div className="rounded border border-primary/30 bg-primary/5 p-2">
            <div className="text-[10px] text-primary">COMPILED</div>
            <div className="text-base font-bold text-primary">{reqData.COMPILED ?? 0}</div>
          </div>
          <div className="rounded border border-signal/30 bg-signal/5 p-2">
            <div className="text-[10px] text-signal">ADMITTED</div>
            <div className="text-base font-bold text-signal">{reqData.ADMITTED ?? 0}</div>
          </div>
          <div className="rounded border border-accent/40 bg-accent/10 p-2">
            <div className="text-[10px] text-accent">READY</div>
            <div className="text-base font-bold text-accent">{reqData.READY ?? 0}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 border-t border-border/40 pt-2 text-[11px]">
        <div className="flex items-center justify-between rounded bg-muted/40 px-2 py-1.5">
          <span className="text-muted-foreground">Active Leases:</span>
          <span className="font-bold text-signal">{leaseData.ACTIVE ?? 0} / {(leaseData.ACTIVE ?? 0) + (leaseData.RELEASED ?? 0)}</span>
        </div>
        <div className="flex items-center justify-between rounded bg-muted/40 px-2 py-1.5">
          <span className="text-muted-foreground">Attempts (Run/Pass/Fail):</span>
          <span className="font-bold text-foreground">{attData.RUNNING ?? 0} / {attData.SUCCEEDED ?? 0} / {attData.FAILED ?? 0}</span>
        </div>
      </div>
    </div>
  );
}

// API: GET /api/execution/state
`;

const cpfReadinessCode = `export default function CpfReadinessDial({
  title = "Compilation Readiness (CPF)",
  counts = {},
  threshold = 0.7,
  system = "nebula-core"
}) {
  const ready = counts?.ready ?? 5;
  const promoted = counts?.promoted ?? 3;
  const nearMiss = counts?.nearMiss ?? 2;
  const low = counts?.low ?? 10;
  const total = ready + promoted + nearMiss + low || 1;
  const readyPct = Math.round(((ready + promoted) / total) * 100);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{system}</span>
          <span className="font-semibold text-foreground">{title}</span>
        </div>
        <span className="rounded bg-signal/10 px-2 py-0.5 text-[10px] font-bold text-signal">
          ≥ {threshold} Threshold
        </span>
      </div>

      <div className="space-y-1.5">
        <div className="flex justify-between text-[11px]">
          <span className="text-muted-foreground">Promotable Velocity</span>
          <span className="font-bold text-signal">{readyPct}% Ready</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-border/40 flex">
          <div style={{ width: \`\${(promoted / total) * 100}%\` }} className="bg-primary" title="Promoted" />
          <div style={{ width: \`\${(ready / total) * 100}%\` }} className="bg-signal" title="Ready" />
          <div style={{ width: \`\${(nearMiss / total) * 100}%\` }} className="bg-accent" title="Near Miss" />
          <div style={{ width: \`\${(low / total) * 100}%\` }} className="bg-muted-foreground/30" title="Low" />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-1.5 text-center text-[10px]">
        <div className="rounded border border-signal/30 bg-signal/5 p-1.5">
          <div className="text-signal">Ready</div>
          <div className="text-sm font-bold text-signal">{ready}</div>
        </div>
        <div className="rounded border border-primary/30 bg-primary/5 p-1.5">
          <div className="text-primary">Promoted</div>
          <div className="text-sm font-bold text-primary">{promoted}</div>
        </div>
        <div className="rounded border border-accent/30 bg-accent/5 p-1.5">
          <div className="text-accent">Near Miss</div>
          <div className="text-sm font-bold text-accent">{nearMiss}</div>
        </div>
        <div className="rounded border border-border/40 bg-background/50 p-1.5">
          <div className="text-muted-foreground">Low</div>
          <div className="text-sm font-bold text-foreground">{low}</div>
        </div>
      </div>
    </div>
  );
}

// API: GET /api/cpf/count
`;

const countsMatrixCode = `export default function EntityCountsMatrix({
  counts = {},
  title = "Nebula 13-Entity Schema Matrix"
}) {
  const data = Object.keys(counts).length > 0 ? counts : {
    threads: 42,
    requirements: 85,
    agendas: 3,
    candidates: 30,
    harvests: 25,
    openQuestions: 15,
    assessments: 8,
    observations: 20,
    agentRecords: 150,
    specifications: 6,
    plans: 18,
    users: 10
  };

  const total = Object.values(data).reduce((a, b) => a + Number(b || 0), 0);
  const items = Object.entries(data);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <span className="font-semibold text-foreground">{title}</span>
        <span className="rounded bg-primary/10 px-2 py-0.5 text-[10px] font-bold text-primary border border-primary/20">
          {total} Total Entities
        </span>
      </div>

      <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4">
        {items.map(([key, val]) => (
          <div key={key} className="flex flex-col justify-between rounded border border-border/40 bg-background/50 p-2">
            <span className="text-[10px] text-muted-foreground truncate" title={key}>{key}</span>
            <span className="text-sm font-bold text-foreground mt-0.5">{val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// API: GET /api/counts
`;

const openQuestionDeliberationCode = `export default function OpenQuestionDeliberation({
  title = "Deliberation Consensus Matrix",
  category = "CONFLICT",
  blocking = true,
  answers = []
}) {
  const answerList = Array.isArray(answers) && answers.length > 0 ? answers : [
    { role: "architect", answer: "Adopt bitemporal history with validity intervals", confidence: "HIGH", reasoning: "Preserves point-in-time state recovery without breaking schema FKs." },
    { role: "engineer", answer: "Ensure Redis cache can be fully recomputed from Postgres", confidence: "HIGH", reasoning: "Prevents stale graph state on crash restart." },
    { role: "topologist", answer: "Enforce level 3 hierarchy boundaries before compilation", confidence: "MEDIUM", reasoning: "Avoids cycle dependencies in graph edges." }
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <div className="flex items-center gap-2">
          {blocking ? (
            <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] font-bold text-destructive border border-destructive/20">
              BLOCKING
            </span>
          ) : (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">ADVISORY</span>
          )}
          <span className="font-semibold text-foreground truncate max-w-[200px]">{title}</span>
        </div>
        <span className="text-[10px] text-muted-foreground uppercase">{category}</span>
      </div>

      <div className="space-y-2">
        {answerList.map((item, idx) => (
          <div key={idx} className="rounded border border-border/40 bg-background/40 p-2 space-y-1">
            <div className="flex items-center justify-between">
              <span className="font-bold text-primary uppercase text-[10px]">{item.role}</span>
              <span className={\`rounded px-1.5 py-0.2 text-[9px] font-semibold \${
                item.confidence === "HIGH" ? "bg-signal/15 text-signal" : "bg-accent/15 text-accent"
              }\`}>
                {item.confidence} CONFIDENCE
              </span>
            </div>
            <p className="text-[11px] text-foreground">{item.answer}</p>
            {item.reasoning && (
              <p className="text-[10px] text-muted-foreground italic">↳ {item.reasoning}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// API: GET /api/open-questions/:id/answers
`;

const agentTelemetryCode = `export default function AgentRecordTelemetry({
  title = "Agent Audit Telemetry Stream",
  records = [],
  roleFilter = "all"
}) {
  const list = Array.isArray(records) && records.length > 0 ? records : [
    { recordType: "report", role: "architect", title: "Level 1 System Boundary Validated", level: 1, visibilityScope: "builder", time: "2m ago" },
    { recordType: "engineering_log", role: "engineer", title: "Generated opcode sequence for REQ-8192", level: 2, visibilityScope: "builder", time: "5m ago" },
    { recordType: "assessment", role: "inspector", title: "Resolved dependency lattice cycle", level: 3, visibilityScope: "all", time: "12m ago" },
    { recordType: "decision", role: "planner", title: "Conduit PLN-104 admitted to Ready state", level: 4, visibilityScope: "all", time: "18m ago" }
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <span className="font-semibold text-foreground">{title}</span>
        <span className="text-[10px] text-muted-foreground uppercase">{roleFilter} scope</span>
      </div>

      <div className="space-y-1.5">
        {list.map((rec, i) => (
          <div key={i} className="flex items-center justify-between rounded border border-border/30 bg-background/50 px-2.5 py-1.5 hover:border-border/60 transition-colors">
            <div className="flex items-center gap-2 overflow-hidden">
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[9px] font-bold text-primary uppercase border border-primary/20">
                {rec.role}
              </span>
              <span className="truncate text-foreground text-[11px]">{rec.title}</span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0 text-[10px]">
              <span className="rounded bg-muted px-1 py-0.5 text-muted-foreground">L{rec.level}</span>
              <span className="text-muted-foreground">{rec.time ?? "recent"}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// API: GET /api/agent-records
`;

const crossRefLatticeCode = `export default function CrossReferenceLattice({
  title = "Cross-Reference Dependency Lattice",
  items = []
}) {
  const list = Array.isArray(items) && items.length > 0 ? items : [
    { relType: "req:blocks", sourceType: "requirement", targetType: "requirement", domain: "Requirement" },
    { relType: "spawns_plan", sourceType: "harvest_candidate", targetType: "plan", domain: "Agent" },
    { relType: "wrp:implements", sourceType: "plan", targetType: "work_request", domain: "WRP" },
    { relType: "kv:sourced_from", sourceType: "knowledge_entity", targetType: "harvest", domain: "Knowledge" }
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <span className="font-semibold text-foreground">{title}</span>
        <span className="rounded bg-signal/10 px-1.5 py-0.5 text-[10px] text-signal font-bold">
          4 Domains
        </span>
      </div>

      <div className="space-y-1.5">
        {list.map((edge, i) => (
          <div key={i} className="flex items-center justify-between rounded border border-border/40 bg-background/50 p-2">
            <div className="flex items-center gap-1.5">
              <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent font-semibold">
                {edge.domain}
              </span>
              <span className="text-[11px] font-bold text-foreground">{edge.relType}</span>
            </div>
            <div className="text-[10px] text-muted-foreground flex items-center gap-1">
              <span>{edge.sourceType}</span>
              <span>➔</span>
              <span>{edge.targetType}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// API: GET /api/cross-references
`;

const metricsSample = {
  label: "throughput",
  value: 74,
  points: [12, 18, 32, 45, 42, 60, 55, 74],
};

const inventorySample = {
  items: [
    { name: "flux-capacitor", stock: 12, status: "nominal" },
    { name: "warp-core", stock: 3, status: "low" },
    { name: "plasma-conduit", stock: 48, status: "nominal" },
  ],
};

const executionStateSample = {
  requests: { DRAFT: 3, COMPILED: 5, ADMITTED: 2, READY: 1 },
  leases: { ACTIVE: 1, RELEASED: 4 },
  attempts: { RUNNING: 1, SUCCEEDED: 3, FAILED: 1 },
  totalRequests: 11,
  activeLeases: 1,
};

const cpfSample = {
  counts: {
    ready: 5,
    promoted: 3,
    nearMiss: 2,
    low: 10,
  },
  threshold: 0.7,
  system: "nebula-core",
};

const countsMatrixSample = {
  counts: {
    threads: 42,
    requirements: 85,
    agendas: 3,
    candidates: 30,
    harvests: 25,
    openQuestions: 15,
    assessments: 8,
    observations: 20,
    agentRecords: 150,
    specifications: 6,
    plans: 18,
    users: 10,
  },
};

const openQuestionSample = {
  title: "Bitemporal Migration vs Snapshot Tables",
  category: "CONFLICT",
  blocking: true,
  answers: [
    {
      role: "architect",
      answer: "Adopt bitemporal history with validity intervals",
      confidence: "HIGH",
      reasoning: "Preserves point-in-time state recovery without breaking schema FKs.",
    },
    {
      role: "engineer",
      answer: "Ensure Redis cache can be fully recomputed from Postgres",
      confidence: "HIGH",
      reasoning: "Prevents stale graph state on crash restart.",
    },
    {
      role: "topologist",
      answer: "Enforce level 3 hierarchy boundaries before compilation",
      confidence: "MEDIUM",
      reasoning: "Avoids cycle dependencies in graph edges.",
    },
  ],
};

const agentTelemetrySample = {
  roleFilter: "all",
  records: [
    {
      recordType: "report",
      role: "architect",
      title: "Level 1 System Boundary Validated",
      level: 1,
      visibilityScope: "builder",
      time: "2m ago",
    },
    {
      recordType: "engineering_log",
      role: "engineer",
      title: "Generated opcode sequence for REQ-8192",
      level: 2,
      visibilityScope: "builder",
      time: "5m ago",
    },
    {
      recordType: "assessment",
      role: "inspector",
      title: "Resolved dependency lattice cycle",
      level: 3,
      visibilityScope: "all",
      time: "12m ago",
    },
    {
      recordType: "decision",
      role: "planner",
      title: "Conduit PLN-104 admitted to Ready state",
      level: 4,
      visibilityScope: "all",
      time: "18m ago",
    },
  ],
};

const planKanbanCode = `export default function ConduitPlanKanban({
  title = "Conduit Implementation Plan Board",
  plans = [],
  activeFilter = "all"
}) {
  const defaultPlans = [
    {
      id: "PLN-101",
      title: "Bitemporal Migration Engine",
      goal: "Implement validity intervals on audit tables without breaking existing foreign keys",
      status: "in_progress",
      files_affected: ["src/db/schema.ts", "src/services/bitemporal.ts", "migrations/004_bitemporal.sql"],
      acceptance_criteria: ["Valid_from and valid_until correctly closed on update", "Zero downtime migration"],
      dependencies: ["PLN-089"],
      sizeBytes: 4210
    },
    {
      id: "PLN-102",
      title: "Redis Subsystem Invalidation Hook",
      goal: "Flush snapshot segment cache on upstream Postgres transaction commit",
      status: "backlog",
      files_affected: ["src/cache/redis.ts", "src/services/segmentation.ts"],
      acceptance_criteria: ["Idempotent key purge", "Latency < 5ms"],
      dependencies: [],
      sizeBytes: 2840
    },
    {
      id: "PLN-103",
      title: "CPF Readiness Scoring Pipeline",
      goal: "Automate harvest candidate scoring based on intent resolution threshold >= 0.7",
      status: "accepted",
      files_affected: ["src/services/cpf.ts", "src/routes/cpf.ts"],
      acceptance_criteria: ["Evaluates 4 bands", "Dispatches promotion webhooks"],
      dependencies: ["PLN-101"],
      sizeBytes: 3120
    },
    {
      id: "PLN-104",
      title: "Opcode Trace Journaler",
      goal: "Record stage 2 compilation execution receipts into agent_records",
      status: "done",
      files_affected: ["src/compiler/stage2.ts", "src/routes/execution.ts"],
      acceptance_criteria: ["Structured JSON receipt output", "Deterministic hash matching"],
      dependencies: [],
      sizeBytes: 5120
    }
  ];

  const planList = Array.isArray(plans) && plans.length > 0 ? plans : defaultPlans;
  const statusColors = {
    backlog: "bg-muted text-muted-foreground border-border",
    in_progress: "bg-primary/10 text-primary border-primary/30",
    accepted: "bg-accent/15 text-accent border-accent/30",
    done: "bg-signal/15 text-signal border-signal/30",
    archived: "bg-border/30 text-muted-foreground border-border/40"
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-surface/80 p-3.5 font-mono text-xs shadow-xs">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-primary shadow-[0_0_8px_var(--color-primary)]" />
          <span className="font-semibold text-foreground tracking-tight">{title}</span>
        </div>
        <div className="flex items-center gap-1.5 text-[10px]">
          <span className="rounded bg-muted/60 px-2 py-0.5 text-muted-foreground border border-border/40">
            {planList.length} Active Plans
          </span>
        </div>
      </div>

      <div className="space-y-2.5">
        {planList.map((plan) => {
          const badgeClass = statusColors[plan.status] || "bg-muted text-muted-foreground";
          return (
            <div
              key={plan.id}
              className="group rounded-md border border-border/40 bg-background/60 p-2.5 hover:border-border transition-colors space-y-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-background px-1.5 py-0.5 text-[10px] font-bold text-foreground border border-border">
                    {plan.id}
                  </span>
                  <span className="font-semibold text-foreground text-[11px] leading-tight">
                    {plan.title}
                  </span>
                </div>
                <span className={"rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider border " + badgeClass}>
                  {plan.status.replace("_", " ")}
                </span>
              </div>

              <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed">
                {plan.goal}
              </p>

              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/30 pt-1.5 text-[10px]">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span>📁 {plan.files_affected?.length ?? 0} files</span>
                  <span>✓ {plan.acceptance_criteria?.length ?? 0} criteria</span>
                </div>
                {plan.dependencies && plan.dependencies.length > 0 && (
                  <span className="text-accent text-[9px]">
                    deps: {plan.dependencies.join(", ")}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// API: GET /api/plans
`;

const crossRefSample = {
  items: [
    {
      relType: "req:blocks",
      sourceType: "requirement",
      targetType: "requirement",
      domain: "Requirement",
    },
    {
      relType: "spawns_plan",
      sourceType: "harvest_candidate",
      targetType: "plan",
      domain: "Agent",
    },
    {
      relType: "wrp:implements",
      sourceType: "plan",
      targetType: "work_request",
      domain: "WRP",
    },
    {
      relType: "kv:sourced_from",
      sourceType: "knowledge_entity",
      targetType: "harvest",
      domain: "Knowledge",
    },
  ],
};

const planKanbanSample = {
  title: "Conduit Implementation Plan Board",
  activeFilter: "all",
  plans: [
    {
      id: "PLN-101",
      title: "Bitemporal Migration Engine",
      goal: "Implement validity intervals on audit tables without breaking existing foreign keys",
      status: "in_progress",
      files_affected: [
        "src/db/schema.ts",
        "src/services/bitemporal.ts",
        "migrations/004_bitemporal.sql",
      ],
      acceptance_criteria: [
        "Valid_from and valid_until correctly closed on update",
        "Zero downtime migration",
      ],
      dependencies: ["PLN-089"],
      sizeBytes: 4210,
    },
    {
      id: "PLN-102",
      title: "Redis Subsystem Invalidation Hook",
      goal: "Flush snapshot segment cache on upstream Postgres transaction commit",
      status: "backlog",
      files_affected: ["src/cache/redis.ts", "src/services/segmentation.ts"],
      acceptance_criteria: ["Idempotent key purge", "Latency < 5ms"],
      dependencies: [],
      sizeBytes: 2840,
    },
    {
      id: "PLN-103",
      title: "CPF Readiness Scoring Pipeline",
      goal: "Automate harvest candidate scoring based on intent resolution threshold >= 0.7",
      status: "accepted",
      files_affected: ["src/services/cpf.ts", "src/routes/cpf.ts"],
      acceptance_criteria: ["Evaluates 4 bands", "Dispatches promotion webhooks"],
      dependencies: ["PLN-101"],
      sizeBytes: 3120,
    },
    {
      id: "PLN-104",
      title: "Opcode Trace Journaler",
      goal: "Record stage 2 compilation execution receipts into agent_records",
      status: "done",
      files_affected: ["src/compiler/stage2.ts", "src/routes/execution.ts"],
      acceptance_criteria: ["Structured JSON receipt output", "Deterministic hash matching"],
      dependencies: [],
      sizeBytes: 5120,
    },
  ],
};

function build(
  id: string,
  name: string,
  description: string,
  tags: string[],
  code: string,
  sample: unknown,
  type: Widget["type"] = "react-component",
): Widget {
  const { componentName, inputs, endpoints } = analyze(code);
  const mocks: Widget["mocks"] = {};
  if (endpoints[0]) {
    mocks[endpoints[0].signature] = {
      schema: inferSchema(sample),
      seed: sample,
    };
  }
  return {
    id,
    name,
    description,
    tags,
    type,
    code,
    componentName,
    inputs,
    endpoints,
    mocks,
  };
}

export const SEED_WIDGETS: Widget[] = [
  build(
    "nebula-plan-kanban",
    "Conduit Implementation Plan Kanban",
    "Lifecycle status board and affected files inspector for Conduit implementation plans.",
    ["plans", "kanban", "implementation", "nebula"],
    planKanbanCode,
    planKanbanSample,
    "control-surface",
  ),
  build(
    "nebula-execution-state",
    "Execution Switchboard",
    "Telemetry monitor and lease control for Nebula work requests and active leases.",
    ["execution", "leases", "nebula", "requests"],
    executionStateCode,
    executionStateSample,
    "control-surface",
  ),
  build(
    "nebula-cpf-readiness",
    "CPF Compilation Readiness Dial",
    "Visualizes candidate readiness bands (ready, promoted, nearMiss, low) for conduit compilation.",
    ["cpf", "candidates", "readiness", "metrics"],
    cpfReadinessCode,
    cpfSample,
    "data-vis",
  ),
  build(
    "nebula-counts-matrix",
    "13-Entity Schema Matrix",
    "Real-time aggregate inventory across all 13 core Nebula database tables and knowledge nodes.",
    ["database", "counts", "knowledge", "matrix"],
    countsMatrixCode,
    countsMatrixSample,
    "data-vis",
  ),
  build(
    "nebula-open-question-deliberation",
    "Deliberation Consensus Matrix",
    "Role-based deliberation and confidence scoring for unresolved architectural open questions.",
    ["governance", "questions", "roles", "consensus"],
    openQuestionDeliberationCode,
    openQuestionSample,
    "interactive-tool",
  ),
  build(
    "nebula-agent-telemetry",
    "Agent Audit Telemetry Stream",
    "Stream of agent audit records filtered by role, abstraction level (L1–L4), and visibility scope.",
    ["agents", "audit", "telemetry", "records"],
    agentTelemetryCode,
    agentTelemetrySample,
    "react-component",
  ),
  build(
    "nebula-cross-references",
    "Cross-Reference Dependency Lattice",
    "Interactive dependency graph mapping relationships across WRP, Agent, Knowledge, and Requirement domains.",
    ["graph", "crossref", "dependencies", "lattice"],
    crossRefLatticeCode,
    crossRefSample,
    "canvas-element",
  ),
  build(
    "seed-sparkline",
    "Sparkline Chart",
    "Compact polyline rendering for time-series metrics.",
    ["metrics", "chart"],
    sparklineCode,
    metricsSample,
    "data-vis",
  ),
  build(
    "seed-gauge",
    "Circular Gauge",
    "Radial progress indicator for percentage telemetry.",
    ["metrics", "gauge"],
    gaugeCode,
    metricsSample,
    "control-surface",
  ),
  build(
    "seed-inventory",
    "Inventory Monitor",
    "Tabular live status for stocked relic inventory.",
    ["inventory", "table"],
    inventoryCode,
    inventorySample,
    "react-component",
  ),
];

export const seedWidgets = SEED_WIDGETS;
