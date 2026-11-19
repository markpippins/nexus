/**
 * ticket-sweep.ts — periodic ticket lifecycle maintenance for conduit-mcp.
 *
 * The database detectors are deliberately injected so the scheduling and
 * single-flight contract remain DB-free and hermetically testable. The
 * watcher owns the production detector functions and the operational log.
 */

export interface TicketSweepResult {
  stale: number;
  expired: number;
}

export interface TicketSweepDetectors {
  detectStaleTickets(): Promise<number>;
  detectExpiredTickets(): Promise<number>;
}

export interface TicketSweepCallbacks {
  onResult?(result: TicketSweepResult): void;
  onError?(error: unknown): void;
}

/**
 * Ticket-state semantics (W-B2, plan 8261674).
 *
 * Every ticket row is classified exactly once:
 *  - `terminal_outcome` — the generation reached a decision. `closed` is its own
 *    member and is never folded into `completed` (ruling R-F6).
 *  - `attention_lost` — the generation was abandoned by expiry or staleness. It
 *    never implies completion: an `expired` ticket is not a finished one.
 *  - `active` — the generation is still in flight (`open` / `claimed`).
 *
 * An unknown status throws rather than defaulting: the census forbids implicit
 * buckets, so a new vocabulary member must be ruled on before it can be counted.
 */
export type TicketStatusClass = "terminal_outcome" | "attention_lost" | "active";

const TERMINAL_OUTCOME_STATUSES = new Set(["completed", "failed", "cancelled", "closed"]);
const ATTENTION_LOST_STATUSES = new Set(["expired", "stale"]);
const ACTIVE_STATUSES = new Set(["open", "claimed"]);

export function classifyTicketStatus(status: string): TicketStatusClass {
  if (TERMINAL_OUTCOME_STATUSES.has(status)) return "terminal_outcome";
  if (ATTENTION_LOST_STATUSES.has(status)) return "attention_lost";
  if (ACTIVE_STATUSES.has(status)) return "active";
  throw new Error(
    `Unclassified ticket status (W-B2 census forbids implicit buckets): ${status}`,
  );
}

/** Row shape the expiry detector hands to {@link buildExpiryDetectionPayload}. */
export interface ExpiryDetectionTicketRow {
  id: string;
  plan_id: string;
  role: string;
  /** The status the ticket held while it was still live — the event's `from_status`. */
  status: string;
  expires_at: string | Date | null;
  /** Derived positional generation (D1 §7.2): 1-based per (plan_id, role). */
  generation: number;
}

/**
 * The payload kernel.transition_event carries for `expiry_detection`.
 *
 * Eight fields, fixed shape: `implies_completion` is the typed boolean `false`,
 * never `"false"` and never absent — expiry must not make a plan look done.
 * `ticket_id` is deliberately absent; it is the event's `aggregate_id`.
 */
export interface ExpiryDetectionPayload {
  plan_id: string;
  role: string;
  generation: number;
  expires_at: string | null;
  from_status: string;
  to_status: "expired";
  reason: "expiry_detection";
  implies_completion: false;
}

export function buildExpiryDetectionPayload(
  row: ExpiryDetectionTicketRow,
): ExpiryDetectionPayload {
  if (!Number.isInteger(row.generation) || row.generation < 1) {
    throw new Error(
      `Expiry detection requires a positive integer generation: ${row.generation}`,
    );
  }
  return {
    plan_id: row.plan_id,
    role: row.role,
    generation: row.generation,
    expires_at: row.expires_at == null ? null : new Date(row.expires_at).toISOString(),
    from_status: row.status,
    to_status: "expired",
    reason: "expiry_detection",
    implies_completion: false,
  };
}

const DEFAULT_SWEEP_INTERVAL_MS = 60_000;

/**
 * Runs stale detection before expiry detection so a ticket that is both
 * claim-stale and past its expiry deadline is transitioned to `expired` in the
 * same pass. Overlapping interval ticks share one in-flight pass.
 */
export class TicketExpirySweeper {
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight: Promise<TicketSweepResult> | null = null;

  constructor(
    private readonly detectors: TicketSweepDetectors,
    private readonly callbacks: TicketSweepCallbacks = {},
    private readonly intervalMs: number = DEFAULT_SWEEP_INTERVAL_MS,
  ) {
    if (!Number.isInteger(intervalMs) || intervalMs <= 0) {
      throw new Error(`Ticket sweep interval must be a positive integer: ${intervalMs}`);
    }
  }

  async runOnce(): Promise<TicketSweepResult> {
    if (this.inFlight) return this.inFlight;

    const run = (async (): Promise<TicketSweepResult> => {
      const stale = await this.detectors.detectStaleTickets();
      const expired = await this.detectors.detectExpiredTickets();
      return { stale, expired };
    })();

    this.inFlight = run;
    try {
      return await run;
    } finally {
      if (this.inFlight === run) this.inFlight = null;
    }
  }

  private runAndReport(): void {
    void this.runOnce()
      .then((result) => this.callbacks.onResult?.(result))
      .catch((error) => this.callbacks.onError?.(error));
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => this.runAndReport(), this.intervalMs);
    // Sweep immediately on startup so an existing backlog does not wait for
    // the first interval tick.
    this.runAndReport();
  }

  stop(): void {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
  }
}

/* ── W-B3 (plan 8261675): generation-aware sweep disposition ─────────────
 *
 * The sweep log is an attestation surface, not a mutation surface. It records
 * one row per ticket *generation* — the positional identity derived once, per
 * D1 §7.2, as `row_number() OVER (PARTITION BY plan_id, role ORDER BY
 * created_at, id)` — and computes each row's disposition and effective closure
 * reason here, in the log. Nothing in this module writes to a ticket.
 *
 * Disposition vocabulary is closed (S2). An expired generation is either
 * covered by an `expiry_detection` event or waived by an architect ruling;
 * there is no silent third option. A waiver without a ruling ref throws
 * rather than being logged as covered — S3's whole point is that a gap may not
 * disappear without a name attached to it.
 */

/** Closed disposition vocabulary (S2). Order is the artifact's census order. */
export const SWEEP_DISPOSITIONS = [
  "expired_with_event",
  "expired_without_event_waived",
  "open_past_expiry_disposed",
  "no_disposition_required",
] as const;

export type SweepDisposition = (typeof SWEEP_DISPOSITIONS)[number];

/** Where the row's effective closure reason came from (S4). */
export type ClosureReasonSource = "ticket" | "expiry_event" | "status_fallback";

/** A derived generation row, before disposition is computed. */
export interface SweepGenerationInput {
  ticket_id: string;
  plan_id: string;
  role: string;
  /** Positional generation (1-based) per (plan_id, role), D1 §7.2. */
  generation: number;
  status: string;
  created_at: string | Date | null;
  expires_at: string | Date | null;
  closed_at: string | Date | null;
  closure_reason: string | null;
  /** True iff a kernel event with payload.reason = 'expiry_detection' names this ticket. */
  has_expiry_event: boolean;
  /** Architect-ruling ref; required only for expired rows with no event. */
  waiver_ref?: string | null;
}

/** One line of the W-B3 sweep log. */
export interface SweepLogRow extends Omit<SweepGenerationInput, "has_expiry_event"> {
  created_at: string | null;
  expires_at: string | null;
  closed_at: string | null;
  reason_source: ClosureReasonSource;
  closure_reason_effective: string;
  sweep_disposition: SweepDisposition;
  is_open_generation: boolean;
  is_newest: boolean;
  disposition_required: boolean;
  waiver_ref: string | null;
}

function asIso(value: string | Date | null): string | null {
  if (value == null) return null;
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`Sweep log received an unparseable timestamp: ${String(value)}`);
  }
  return d.toISOString();
}

/** A generation is "open" for projection purposes when it can still be worked. */
export function isOpenGenerationStatus(status: string): boolean {
  return status === "open" || status === "claimed" || status === "stale";
}

/**
 * S2's disposition-required set: every expired/stale generation, plus any
 * open/claimed generation whose `expires_at` is already behind `swept_at`.
 * `expires_at IS NULL` is never due, so it is reported as not required rather
 * than treated as due (a bare `NULL < swept_at` predicate would exclude it).
 */
export function isDispositionRequired(
  row: Pick<SweepGenerationInput, "status" | "expires_at">,
  sweptAt: string | Date,
): boolean {
  if (row.status === "expired" || row.status === "stale") return true;
  if (row.status !== "open" && row.status !== "claimed") return false;
  const expiresAt = asIso(row.expires_at);
  if (expiresAt == null) return false;
  return Date.parse(expiresAt) < Date.parse(asIso(sweptAt) as string);
}

/** S4: `ticket` when the row carries a reason; `expiry_event` when an event does; else the status. */
export function closureReasonSource(
  row: Pick<SweepGenerationInput, "closure_reason" | "has_expiry_event">,
): ClosureReasonSource {
  if (row.closure_reason != null && row.closure_reason !== "") return "ticket";
  if (row.has_expiry_event) return "expiry_event";
  return "status_fallback";
}

/** S4: computed in the log and never written back to the ticket. */
export function closureReasonEffective(
  row: Pick<SweepGenerationInput, "status" | "closure_reason" | "has_expiry_event">,
): string {
  switch (closureReasonSource(row)) {
    case "ticket":
      return row.closure_reason as string;
    case "expiry_event":
      return "expiry_detection";
    case "status_fallback":
      return `status:${row.status}`;
  }
}

/**
 * S2/S3 disposition. Fails closed:
 *  - an unknown status throws (the W-B2 census forbids implicit buckets);
 *  - an expired row with no expiry event and no `waiver_ref` throws — an
 *    unwaived loss is never logged as covered (S3).
 */
export function deriveSweepDisposition(
  row: Pick<SweepGenerationInput, "status" | "expires_at" | "has_expiry_event" | "waiver_ref">,
  sweptAt: string | Date,
): SweepDisposition {
  classifyTicketStatus(row.status);

  if (row.status === "expired") {
    return row.has_expiry_event ? "expired_with_event" : "expired_without_event_waived";
  }
  if (isOpenGenerationStatus(row.status) && isDispositionRequired(row, sweptAt)) {
    return "open_past_expiry_disposed";
  }
  return "no_disposition_required";
}

/**
 * Build one sweep-log row. `swept_at` is the single cutoff stamped on the
 * artifact; the log's dispositions are derived against it, so two runs with
 * the same cutoff produce identical rows.
 */
export function buildSweepLogRow(
  row: SweepGenerationInput,
  sweptAt: string | Date,
  /** Highest generation per (plan_id, role) — makes `is_newest` auditable. */
  newestGeneration: number,
): SweepLogRow {
  if (!Number.isInteger(row.generation) || row.generation < 1) {
    throw new Error(`Sweep log requires a positive integer generation: ${row.generation}`);
  }
  const disposition = deriveSweepDisposition(row, sweptAt);
  const waiverRef = row.waiver_ref ?? null;

  if (disposition === "expired_without_event_waived" && !waiverRef) {
    throw new Error(
      `Unwaived event-absent expiry (S3): ticket ${row.ticket_id} has no expiry_detection event ` +
        `and no architect waiver_ref`,
    );
  }

  const stampedWaiver =
    disposition === "expired_without_event_waived" ? waiverRef : null;

  return {
    ticket_id: row.ticket_id,
    plan_id: row.plan_id,
    role: row.role,
    generation: row.generation,
    status: row.status,
    created_at: asIso(row.created_at),
    expires_at: asIso(row.expires_at),
    closed_at: asIso(row.closed_at),
    closure_reason: row.closure_reason,
    reason_source: closureReasonSource(row),
    closure_reason_effective: closureReasonEffective(row),
    sweep_disposition: disposition,
    is_open_generation: isOpenGenerationStatus(row.status),
    is_newest: row.generation === newestGeneration,
    disposition_required: isDispositionRequired(row, sweptAt),
    waiver_ref: stampedWaiver,
  };
}
