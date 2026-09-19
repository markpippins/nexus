/**
 * Runtime Kernel — the deterministic state machine + decision loop for WorkRequest lifecycle.
 *
 * This is NOT an execution engine. It is a causal loop:
 *
 *   event_log → fold → state → decide → new event → event_log
 *
 * Architecture:
 *   - WorkRequest lifecycle follows an irreversible state machine
 *   - State = fold(event_log), never direct mutation
 *   - The `decide()` function selects what happens next
 *   - Compiler output is validated against execution-field leakage
 *
 * State machine:
 *
 *   DRAFT ──→ VALIDATED ──→ QUEUED ──→ CLAIMED ──→ ACKED ──→ SETTLED
 *     │           │           │           │
 *     ↓           ↓           ↓           ↓
 *   REJECTED    REJECTED   DEFERRED     FAILED
 *                                       ACKED ──→ NOOP
 *
 * Terminal states: SETTLED, REJECTED, FAILED, NOOP, DEFERRED
 */

import type { SweepScope } from "./sweep-scope";

// ── Status enum ────────────────────────────────────────────────────

export const WR_STATUSES = [
  "DRAFT",
  "VALIDATED",
  "QUEUED",
  "CLAIMED",
  "ACKED",
  "SETTLED",
  "REJECTED",
  "FAILED",
  "NOOP",
  "DEFERRED",
] as const;

export type WorkRequestStatus = (typeof WR_STATUSES)[number];

// ── Runtime event types ────────────────────────────────────────────

export const RUNTIME_EVENT_TYPES = [
  "WR_SUBMITTED",
  "WR_VALIDATED",
  "WR_QUEUED",
  "WR_CLAIMED",
  "WR_ACKED",
  "WR_SETTLED",
  "WR_REJECTED",
  "WR_FAILED",
  "WR_NOOP",
  "WR_DEFERRED",
] as const;

export type RuntimeEventType = (typeof RUNTIME_EVENT_TYPES)[number];

export interface RuntimeEvent {
  type: RuntimeEventType;
  wrId: string;
  timestamp?: string;
  payload?: Record<string, unknown>;
  /** DB sequence number (work_request_events.sequence_number) — deterministic
   *  fold tiebreaker for identical timestamps (inspector G2, plan 8261649). */
  sequenceNumber?: number;
  /** DB event id — secondary tiebreaker when sequence numbers are absent
   *  (e.g. in-memory events) so equal-timestamp folds are order-independent. */
  eventId?: string;
}

// ── WorkRequest state (folded from events) ─────────────────────────

export interface WorkRequestState {
  wrId: string;
  status: WorkRequestStatus;
  version: number; // event count
  workerId?: string;
  reason?: string;
  error?: string;
  lastEvent: RuntimeEventType;
  lastTimestamp: string;
  createdAt: string;
  /** D6: held-for-normalization marker — compiled-and-PASSing but deliberately
   * unreleased. Distinct from "never compiled" and "failed". */
  normalization_pending?: boolean;
  /** Opaque sonar metadata passed through compiler intent.inputs (e.g.
   * { issueKeys, hotspotKeys, ruleKeys, component, severity, batch }). Set
   * at WR_SUBMITTED; read by the Builder for commit/PR content and by the
   * Reviewer for the post-merge completion match. Never folded into the
   * mutation surface. */
  sonar?: Record<string, unknown>;
}

// ── Compiler output (what the compiler is allowed to emit) ─────────
// This is the contract boundary: NO execution fields allowed.

/**
 * Canonical compiler intent inputs (D1). `deliverable` / `outputs` are the
 * first-class output path/kind for read-only/recon nodes — never folded into
 * the mutation surface. Everything else is passed through opaquely.
 */
export interface CompilerIntentInputs {
  deliverable?: string;
  outputs?: string[];
  /** D7: pinned sweep scope (canonical input — feeds entityKey). */
  scope?: SweepScope;
  [key: string]: unknown;
}

export interface CompilerOutput {
  wrId: string;
  intent: {
    type: string;
    inputs: CompilerIntentInputs;
    objective: string;
  };
  constraints: {
    deterministic: boolean;
    maxRetries?: number;
    timeoutPolicy?: string;
    resourceHints?: string[];
  };
  opTrace: {
    ipNodes: string[];
    resolvedOps: string[];
    registryVersion: string;
  };
  /** D6: mark a WR held-for-normalization (advisory; no conduit plan change). */
  normalization_pending?: boolean;
}

// ── Transition table ───────────────────────────────────────────────
// Maps current status → allowed next event types (and their resulting status).
//
// ADR-006: VALIDATED → QUEUED is still a valid manual transition (via transition API),
// but the Runtime Kernel no longer auto-advances past VALIDATED.
// The cascade admission subscriber handles VALIDATED → ADMITTED → READY
// in the execution.requests domain.

interface TransitionEntry {
  event: RuntimeEventType;
  nextStatus: WorkRequestStatus;
}

const TRANSITION_TABLE: Record<WorkRequestStatus, TransitionEntry[]> = {
  DRAFT: [
    { event: "WR_SUBMITTED", nextStatus: "VALIDATED" },
    { event: "WR_REJECTED", nextStatus: "REJECTED" },
  ],
  VALIDATED: [
    { event: "WR_VALIDATED", nextStatus: "QUEUED" },  // manual only — no auto-advance
    { event: "WR_REJECTED", nextStatus: "REJECTED" },
  ],
  QUEUED: [
    { event: "WR_QUEUED", nextStatus: "CLAIMED" },
    { event: "WR_DEFERRED", nextStatus: "DEFERRED" },
  ],
  CLAIMED: [
    { event: "WR_CLAIMED", nextStatus: "ACKED" },
    { event: "WR_FAILED", nextStatus: "FAILED" },
  ],
  ACKED: [
    { event: "WR_ACKED", nextStatus: "SETTLED" },
    { event: "WR_FAILED", nextStatus: "FAILED" },
    { event: "WR_NOOP", nextStatus: "NOOP" },
  ],
  // Terminal states — no transitions out
  SETTLED: [],
  REJECTED: [],
  FAILED: [],
  NOOP: [],
  DEFERRED: [],
};

// ── Status → event mapping for `decide()` ─────────────────────────
//
// ADR-006: Vision boundary at VALIDATED.
// VALIDATED is the handoff point — Vision produces VALIDATED WorkRequests
// and stops. The cascade admission subscriber (Python pipeline) handles
// VALIDATED → ADMITTED → READY in execution.requests.
// The Runtime Kernel does NOT auto-advance past VALIDATED.

const DECISION_MAP: Partial<Record<WorkRequestStatus, RuntimeEventType>> = {
  // VALIDATED intentionally omitted — Vision stops here (ADR-006)
  QUEUED: "WR_QUEUED",
  CLAIMED: "WR_CLAIMED",
  ACKED: "WR_ACKED",
};

// ══════════════════════════════════════════════════════════════════
//  Core Kernel Functions
// ══════════════════════════════════════════════════════════════════

/**
 * Validate that a transition is allowed by the state machine.
 * Returns the new status if valid, or throws if invalid.
 */
export function validateTransition(
  currentStatus: WorkRequestStatus,
  event: RuntimeEventType,
): WorkRequestStatus {
  const allowed = TRANSITION_TABLE[currentStatus];
  if (!allowed || allowed.length === 0) {
    throw new Error(
      `INVALID_TRANSITION: ${currentStatus} is a terminal state — no transitions allowed`,
    );
  }
  const entry = allowed.find((t) => t.event === event);
  if (!entry) {
    const allowedEvents = allowed.map((t) => t.event).join(", ");
    throw new Error(
      `INVALID_TRANSITION: ${event} not allowed from ${currentStatus}. Allowed: ${allowedEvents}`,
    );
  }
  return entry.nextStatus;
}

/**
 * D4: an opTrace with no resolved ops is "UNRESOLVED" — a first-class valid
 * state for storage/compare/classify/HELD, but NOT admissible to execution.
 * A missing opTrace is treated as unresolved (fail-closed).
 */
export function isUnresolvedOps(opTrace?: {
  ipNodes?: string[];
  resolvedOps?: string[];
  registryVersion?: string;
}): boolean {
  if (!opTrace) return true;
  if (opTrace.registryVersion === "UNRESOLVED") return true;
  if (!opTrace.resolvedOps || opTrace.resolvedOps.length === 0) return true;
  return false;
}

/**
 * Extract the opTrace from a WR's event log (the WR_SUBMITTED event payload).
 * Returns undefined when the WR has no submission event yet.
 */
export function getOpTrace(
  events: RuntimeEvent[],
): { ipNodes?: string[]; resolvedOps?: string[]; registryVersion?: string } | undefined {
  for (const e of events) {
    if (e.type === "WR_SUBMITTED" && e.payload?.opTrace) {
      return e.payload.opTrace as {
        ipNodes?: string[];
        resolvedOps?: string[];
        registryVersion?: string;
      };
    }
  }
  return undefined;
}

/**
 * Validate a transition with D4 op-resolution awareness.
 *
 * An UNRESOLVED WR may be stored/HELD at VALIDATED, but the explicit
 * VALIDATED→QUEUED advance is rejected with `UNRESOLVED_OPS` until its ops are
 * resolved (registry pin). Delegates to :func:`validateTransition` otherwise.
 *
 * CP-4 forward note (architect checklist delta 9bf28fce): when the registry
 * pin later resolves an UNRESOLVED WR's ops, the upgrade is APPEND-ONLY — the
 * pin emits a new resolution event; it NEVER rewrites the WR's historical
 * event log or its existing identity. The acceptance path here must not
 * preclude that (this guard rejects only the *explicit* VALIDATED→QUEUED
 * advance, never the append of a resolution event).
 */
export function validateTransitionWithOps(
  currentStatus: WorkRequestStatus,
  event: RuntimeEventType,
  opTrace?: {
    ipNodes?: string[];
    resolvedOps?: string[];
    registryVersion?: string;
  },
): WorkRequestStatus {
  if (
    currentStatus === "VALIDATED" &&
    event === "WR_VALIDATED" &&
    isUnresolvedOps(opTrace)
  ) {
    throw new Error(
      "UNRESOLVED_OPS: cannot advance VALIDATED→QUEUED with unresolved opcodes " +
      "(registryVersion UNRESOLVED or empty resolvedOps)",
    );
  }
  return validateTransition(currentStatus, event);
}

/**
 * Reduce: apply a single event to a state, producing a new state.
 * This is a PURE function — no side effects, no I/O.
 */
export function reduce(
  state: WorkRequestState,
  event: RuntimeEvent,
): WorkRequestState {
  const nextStatus = validateTransition(state.status, event.type);
  const timestamp = event.timestamp || new Date().toISOString();

  return {
    ...state,
    status: nextStatus,
    version: state.version + 1,
    lastEvent: event.type,
    lastTimestamp: timestamp,
    workerId:
      event.type === "WR_CLAIMED"
        ? (event.payload?.workerId as string) || state.workerId
        : state.workerId,
    reason:
      event.type === "WR_REJECTED" || event.type === "WR_DEFERRED"
        ? (event.payload?.reason as string) || undefined
        : undefined,
    error:
      event.type === "WR_FAILED"
        ? (event.payload?.error as string) || undefined
        : undefined,
    normalization_pending:
      event.type === "WR_SUBMITTED"
        ? (event.payload?.normalization_pending as boolean) || state.normalization_pending
        : state.normalization_pending,
    sonar:
      event.type === "WR_SUBMITTED"
        ? ((event.payload?.intent as { inputs?: Record<string, unknown> } | undefined)
            ?.inputs?.sonar as Record<string, unknown> | undefined)
        : state.sonar,
  };
}

/**
 * Fold: reduce an array of events (in chronological order) into a state.
 * Starts from the initial DRAFT state if no events.
 */
export function foldEvents(
  wrId: string,
  events: RuntimeEvent[],
): WorkRequestState {
  // Deterministic ordering (inspector G2 / plan 8261649): primary key is
  // timestamp; ties break on sequenceNumber (DB insert order), then eventId,
  // then type — so identical-timestamp events fold to ONE canonical state
  // regardless of the order rows arrive from the database. The final
  // Array.prototype.sort stability guarantee (ES2019+) makes equal keys
  // keep input order; the explicit keys make equal-timestamp inputs
  // order-independent.
  const sortKey = (e: RuntimeEvent): string =>
    `${String(new Date(e.timestamp || 0).getTime()).padStart(15, "0")}:` +
    `${e.sequenceNumber === undefined ? "~" : String(e.sequenceNumber).padStart(15, "0")}:` +
    `${e.eventId ?? "~"}:${e.type}`;
  const sorted = [...events].sort(
    (a, b) => (sortKey(a) < sortKey(b) ? -1 : sortKey(a) > sortKey(b) ? 1 : 0),
  );

  const initialState: WorkRequestState = {
    wrId,
    status: "DRAFT",
    version: 0,
    lastEvent: sorted.length > 0 ? sorted[sorted.length - 1].type : "WR_SUBMITTED",
    lastTimestamp: sorted.length > 0 ? sorted[sorted.length - 1].timestamp || "" : "",
    createdAt: sorted.length > 0 ? sorted[0].timestamp || "" : new Date().toISOString(),
  };

  return sorted.reduce((state, event) => reduce(state, event), initialState);
}

/**
 * Decide: given a state, determine the next event to emit (or null).
 * This is what makes the system ALIVE — it selects the next forward motion.
 */
export function decide(state: WorkRequestState): RuntimeEvent | null {
  const nextEventType = DECISION_MAP[state.status];
  if (!nextEventType) return null;

  const now = new Date().toISOString();
  const event: RuntimeEvent = {
    type: nextEventType,
    wrId: state.wrId,
    timestamp: now,
    payload: {},
  };

  // Attach context for automatic transitions
  if (nextEventType === "WR_VALIDATED") {
    event.payload = { validatedAt: now };
  } else if (nextEventType === "WR_QUEUED") {
    event.payload = { queuedAt: now };
  } else if (nextEventType === "WR_CLAIMED") {
    event.payload = { claimedAt: now };
  } else if (nextEventType === "WR_ACKED") {
    event.payload = { acknowledgedAt: now };
  }

  return event;
}

/**
 * Select next runnable — priority for decision loop:
 *   QUEUED (ready to claim) > CLAIMED (ready to ack)
 *
 * ADR-006: VALIDATED removed — Vision stops here, cascade admission handles the rest.
 */
export function getDecisionPriority(status: WorkRequestStatus): number {
  switch (status) {
    case "QUEUED":    return 3;
    case "CLAIMED":   return 2;
    case "ACKED":     return 1;
    default:          return 0;
  }
}

/**
 * Validate compiler output — enforce the contract boundary.
 * Throws if any execution-semantics fields leak into compiler output.
 */
const FORBIDDEN_COMPILER_FIELDS = [
  "status",
  "scheduled",
  "worker",
  "workerId",
  "executionPlan",
  "retryCount",
  "retryStrategy",
  "runId",
  "executionOrder",
  "dagSchedule",
  "queuePosition",
];

// D3: identity is owned by the EMISSION BOUNDARY (nexus_core.wrp.identity /
// compile.py), never the compiler. A compiler emits the derivation rule +
// canonical inputs; a pre-computed entityKey in compiler output is a contract
// violation (any compiler change would otherwise silently change identities).
const FORBIDDEN_IDENTITY_FIELDS = [
  "entityKey",
  "entity_key",
  "identity",
];

export function validateCompilerOutput(output: unknown): asserts output is CompilerOutput {
  if (!output || typeof output !== "object") {
    throw new Error("Compiler output must be a non-null object");
  }

  const obj = output as Record<string, unknown>;

  // Check for leaked execution fields at the top level
  for (const field of FORBIDDEN_COMPILER_FIELDS) {
    if (field in obj) {
      throw new Error(
        `COMPILER_LEAK: Field "${field}" is forbidden in compiler output. ` +
        `Execution semantics belong to the Runtime, not the Compiler.`,
      );
    }
  }

  // D3: reject pre-computed identity (entityKey/identity/entity_key).
  for (const field of FORBIDDEN_IDENTITY_FIELDS) {
    if (field in obj) {
      throw new Error(
        `IDENTITY_LEAK: Field "${field}" is forbidden in compiler output. ` +
        `entityKey is derived at the emission boundary, not pre-computed by ` +
        `the compiler.`,
      );
    }
  }

  // Validate required structure
  if (!obj.wrId || typeof obj.wrId !== "string") {
    throw new Error("Compiler output must have a string wrId");
  }
  if (!obj.intent || typeof obj.intent !== "object") {
    throw new Error("Compiler output must have an intent object");
  }
  if (!obj.constraints || typeof obj.constraints !== "object") {
    throw new Error("Compiler output must have a constraints object");
  }
  if (!obj.opTrace || typeof obj.opTrace !== "object") {
    throw new Error("Compiler output must have an opTrace object");
  }

  const intent = obj.intent as Record<string, unknown>;
  if (!intent.type || typeof intent.type !== "string") {
    throw new Error("Compiler output intent must have a string type");
  }
  if (!intent.objective || typeof intent.objective !== "string") {
    throw new Error("Compiler output intent must have a string objective");
  }
}

/**
 * Create a WR_SUBMITTED event from validated compiler output.
 */
export function compilerOutputToEvent(output: CompilerOutput): RuntimeEvent {
  return {
    type: "WR_SUBMITTED",
    wrId: output.wrId,
    timestamp: new Date().toISOString(),
    payload: {
      intent: output.intent,
      constraints: output.constraints,
      opTrace: output.opTrace,
      normalization_pending: output.normalization_pending,
    },
  };
}

/**
 * Convert a DB event row (snake_case) to a RuntimeEvent (camelCase).
 */
export function dbEventToRuntimeEvent(row: {
  work_request_id: string;
  event_type: string;
  payload: any;
  occurred_at: string;
  sequence_number?: number | string;
  event_id?: string;
}): RuntimeEvent {
  return {
    type: row.event_type as RuntimeEventType,
    wrId: row.work_request_id,
    timestamp: row.occurred_at,
    payload: typeof row.payload === "object" ? row.payload : {},
    sequenceNumber:
      row.sequence_number === undefined || row.sequence_number === null
        ? undefined
        : Number(row.sequence_number),
    eventId: row.event_id || undefined,
  };
}

export function dbEventsToRuntimeEvents(rows: any[]): RuntimeEvent[] {
  return rows.map(dbEventToRuntimeEvent);
}

/**
 * Create initial DRAFT state for a new work request (before submission).
 */
export function createDraftState(wrId: string): WorkRequestState {
  return {
    wrId,
    status: "DRAFT",
    version: 0,
    lastEvent: "WR_SUBMITTED",
    lastTimestamp: new Date().toISOString(),
    createdAt: new Date().toISOString(),
  };
}
