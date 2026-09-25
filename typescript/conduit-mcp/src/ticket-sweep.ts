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
