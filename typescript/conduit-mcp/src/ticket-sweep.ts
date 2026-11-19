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
