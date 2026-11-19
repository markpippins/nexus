// Pipeline state types

export type PlanStatus =
  | "pending"
  | "active"
  | "completed"
  | "blocked"
  | "proposed"
  | "planning";

export interface PlanCard {
  fileName: string;
  planNumber: string;
  baseName: string;
  title: string;
  project: string;
  createdAt: string; // ISO date
  movedAt?: string;
  completedAt?: string;
  blockReason?: string;
  goal?: string;
  filesAffected?: string[];
  acceptanceCriteria?: string[];
  dependencies?: string[];
  promptRef?: string; // prompt number this plan was spawned from
  priority?: number;
  /** Per-role ticket detail: role → { status, id, created_at, expires_at, objective } */
  ticketStatuses?: Record<string, { status: string; id: string; created_at: string; expires_at?: string; objective?: string }>;
  /** Derived status from the receipt chain (PLAN_CREATE, IMPLEMENTATION, REVIEW_PASS, etc.) */
  derivedStatus?: string;
}

export interface BuilderStatus {
  pid: number | null;
  status: "idle" | "running" | "stale" | "killed" | "error";
  startedAt?: string;
  lastActivity?: string;
  elapsedSeconds?: number;
  lastLogLine?: string;
  workflowId?: string;
  runId?: string;
}

export interface CircuitBreaker {
  tripped: boolean;
  retryAfter?: number;
  reason?: string;
  paused: boolean;
}

export interface PipelineState {
  plans: {
    pending: PlanCard[];
    active: PlanCard[];
    completed: PlanCard[];
    blocked: PlanCard[];
    archived: PlanCard[];
    proposed: PlanCard[];
    planning: PlanCard[];
  };
  builder: BuilderStatus;
  circuitBreaker: CircuitBreaker;
  agents: AgentState[];
  receiptStats?: { type: string; count: number }[];
  prompts: any[];
  lastUpdated: string;
}

// SSE event types
export type PipelineEventType =
  | "plan_created"
  | "plan_moved"
  | "plan_file_added" // file appeared on disk (content sync, not state change)
  | "plan_file_removed" // file deleted from disk (content sync, not state change)
  | "plan_state_changed" // receipt was issued, state changed (authoritative)
  | "plan_archived"
  | "plan_deleted"
  | "builder_update"
  | "circuit_breaker_update"
  | "agent_update"
  | "state_full";

export interface PipelineEvent {
  type: PipelineEventType;
  data: any;
  timestamp: string;
}

export interface ParsedPlan {
  fileName: string;
  planNumber: string;
  baseName: string;
  title: string;
  project: string;
  goal: string;
  filesAffected: string[];
  acceptanceCriteria: string[];
  dependencies: string[];
}

// Agent heartbeat types (v034)
export type AgentRole =
  | "planner"
  | "builder"
  | "reviewer"
  | "critic"
  | "analyst"
  | "architect";
export type AgentStatus = "idle" | "working" | "blocked" | "stale" | "gone";

export interface AgentState {
  role: AgentRole;
  status: AgentStatus;
  detail: string | null;
  pid: number | null;
  lastHeartbeat: string | null;
}

// Analytics types (v037)
export interface TimeSeriesPoint {
  label: string;
  value: number;
}
export interface PipelineMetrics {
  totalPlansCompleted: number;
  totalPlansPending: number;
  totalPlansActive: number;
  totalPlansBlocked: number;
  totalBuildersLaunched: number;
  totalBuildersKilled: number;
  averagePlanLifetimeSeconds: number;
  builderStalenessRate: number;
  circuitBreakerTrips: number;
  throughputSparkline: number[];
  throughputAvg: number;
  planAgeDistribution: { bucket: string; count: number }[];
}

// Receipt types (v056)
export type ReceiptType =
  | "PLAN_CREATE"
  | "IMPLEMENTATION"
  | "REVIEW_PASS"
  | "REVIEW_REJECT"
  | "BLOCK"
  | "PROPOSED"
  | "PLANNING"
  | "REVIEW"
  | "CRITIQUE"
  | "CRITIQUE_PASS"
  | "CRITIQUE_REJECT"
  | "PLAN_BLOCK";

export interface IssueReceiptInput {
  plan_id: string; // required: plan number e.g. "0053"
  type: ReceiptType; // required
  agent_role: string; // required: planner|builder|reviewer|watchdog
  session_id?: string; // optional: builder-20260605-120000
  artifact_path?: string; // optional: relative path to proof file
  summary?: string; // optional: one-line description
  metadata?: Record<string, any>; // optional: arbitrary JSON
}
