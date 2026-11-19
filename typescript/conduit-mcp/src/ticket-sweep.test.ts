import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildExpiryDetectionPayload,
  buildSweepLogRow,
  classifyTicketStatus,
  closureReasonEffective,
  closureReasonSource,
  deriveSweepDisposition,
  isDispositionRequired,
  isOpenGenerationStatus,
  SWEEP_DISPOSITIONS,
  TicketExpirySweeper,
  type SweepGenerationInput,
} from "./ticket-sweep";

describe("classifyTicketStatus (W-B2)", () => {
  it("classifies every live status and never folds closed into completed", () => {
    expect(classifyTicketStatus("completed")).toBe("terminal_outcome");
    expect(classifyTicketStatus("failed")).toBe("terminal_outcome");
    expect(classifyTicketStatus("cancelled")).toBe("terminal_outcome");
    expect(classifyTicketStatus("closed")).toBe("terminal_outcome");
    expect(classifyTicketStatus("expired")).toBe("attention_lost");
    expect(classifyTicketStatus("stale")).toBe("attention_lost");
    expect(classifyTicketStatus("open")).toBe("active");
    expect(classifyTicketStatus("claimed")).toBe("active");
  });

  it("throws instead of inventing an implicit bucket", () => {
    expect(() => classifyTicketStatus("superseded")).toThrow(/Unclassified ticket status/);
  });
});

describe("buildExpiryDetectionPayload (W-B2 AC3)", () => {
  it("emits all eight enriched fields with a typed false implies_completion", () => {
    const payload = buildExpiryDetectionPayload({
      id: "ticket-8261674-builder-abc",
      plan_id: "8261674",
      role: "builder",
      status: "claimed",
      expires_at: "2026-11-18T17:32:35.383+00:00",
      generation: 2,
    });

    expect(payload).toEqual({
      plan_id: "8261674",
      role: "builder",
      generation: 2,
      expires_at: "2026-11-18T17:32:35.383Z",
      from_status: "claimed",
      to_status: "expired",
      reason: "expiry_detection",
      implies_completion: false,
    });
    // Expiry must never be readable as completion — the field is a boolean, and
    // ticket identity stays in the event's aggregate_id rather than the payload.
    expect(typeof payload.implies_completion).toBe("boolean");
    expect(payload).not.toHaveProperty("ticket_id");
    expect(payload).not.toHaveProperty("to_status", "completed");
  });

  it("refuses a non-positional generation", () => {
    expect(() =>
      buildExpiryDetectionPayload({
        id: "ticket-8261674-builder-abc",
        plan_id: "8261674",
        role: "builder",
        status: "open",
        expires_at: null,
        generation: 0,
      }),
    ).toThrow(/positive integer generation/);
  });
});

describe("TicketExpirySweeper", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs stale detection before expiry detection", async () => {
    const calls: string[] = [];
    const detectStaleTickets = vi.fn(async () => {
      calls.push("stale");
      return 2;
    });
    const detectExpiredTickets = vi.fn(async () => {
      calls.push("expired");
      return 3;
    });
    const onResult = vi.fn();

    const sweeper = new TicketExpirySweeper(
      { detectStaleTickets, detectExpiredTickets },
      { onResult },
    );

    await expect(sweeper.runOnce()).resolves.toEqual({ stale: 2, expired: 3 });
    expect(calls).toEqual(["stale", "expired"]);
    expect(onResult).not.toHaveBeenCalled();
  });

  it("shares one in-flight pass across concurrent callers", async () => {
    let resolveStale!: (count: number) => void;
    const staleResult = new Promise<number>((resolve) => {
      resolveStale = resolve;
    });
    const detectStaleTickets = vi.fn(() => staleResult);
    const detectExpiredTickets = vi.fn().mockResolvedValue(4);
    const sweeper = new TicketExpirySweeper({ detectStaleTickets, detectExpiredTickets });

    const first = sweeper.runOnce();
    const second = sweeper.runOnce();
    expect(detectStaleTickets).toHaveBeenCalledTimes(1);
    expect(detectExpiredTickets).not.toHaveBeenCalled();

    resolveStale(1);
    await expect(Promise.all([first, second])).resolves.toEqual([
      { stale: 1, expired: 4 },
      { stale: 1, expired: 4 },
    ]);
    expect(detectExpiredTickets).toHaveBeenCalledTimes(1);
  });

  it("starts with an immediate pass and repeats on the interval", async () => {
    vi.useFakeTimers();
    const detectStaleTickets = vi.fn().mockResolvedValue(0);
    const detectExpiredTickets = vi.fn().mockResolvedValue(0);
    const onResult = vi.fn();
    const onError = vi.fn();
    const sweeper = new TicketExpirySweeper(
      { detectStaleTickets, detectExpiredTickets },
      { onResult, onError },
      1_000,
    );

    sweeper.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(detectStaleTickets).toHaveBeenCalledTimes(1);
    expect(detectExpiredTickets).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ stale: 0, expired: 0 });

    await vi.advanceTimersByTimeAsync(1_000);
    expect(detectStaleTickets).toHaveBeenCalledTimes(2);
    expect(detectExpiredTickets).toHaveBeenCalledTimes(2);
    expect(onResult).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();

    sweeper.stop();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(detectStaleTickets).toHaveBeenCalledTimes(2);
  });

  it("reports detector failures without leaving the interval unhandled", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const sweeper = new TicketExpirySweeper(
      {
        detectStaleTickets: vi.fn().mockRejectedValue(new Error("stale detector down")),
        detectExpiredTickets: vi.fn().mockResolvedValue(0),
      },
      { onError },
      1_000,
    );

    sweeper.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "stale detector down" }));

    sweeper.stop();
  });
});

describe("W-B3 sweep-log derivation (plan 8261675)", () => {
  const SWEPT = "2026-11-19T02:00:00Z";
  const PAST = "2026-11-18T00:00:00Z";
  const FUTURE = "2026-11-20T00:00:00Z";
  const base = (over: Partial<SweepGenerationInput> = {}): SweepGenerationInput => ({
    ticket_id: "ticket-test-builder-1",
    plan_id: "8261675",
    role: "builder",
    generation: 1,
    status: "open",
    created_at: PAST,
    expires_at: FUTURE,
    closed_at: null,
    closure_reason: null,
    has_expiry_event: false,
    ...over,
  });

  it("keeps the disposition vocabulary closed", () => {
    expect(SWEEP_DISPOSITIONS).toEqual([
      "expired_with_event",
      "expired_without_event_waived",
      "open_past_expiry_disposed",
      "no_disposition_required",
    ]);
  });

  it("derives S2 dispositions without writing to a ticket", () => {
    expect(deriveSweepDisposition(base({ status: "expired", has_expiry_event: true }), SWEPT)).toBe(
      "expired_with_event",
    );
    expect(deriveSweepDisposition(base({ status: "expired" }), SWEPT)).toBe(
      "expired_without_event_waived",
    );
    expect(deriveSweepDisposition(base({ status: "open", expires_at: PAST }), SWEPT)).toBe(
      "open_past_expiry_disposed",
    );
    expect(deriveSweepDisposition(base({ status: "completed", expires_at: PAST }), SWEPT)).toBe(
      "no_disposition_required",
    );
    expect(() => deriveSweepDisposition(base({ status: "bogus" }), SWEPT)).toThrow(
      /Unclassified ticket status/,
    );
  });

  it("requires a reason_source and never invents a closure reason", () => {
    expect(closureReasonSource({ closure_reason: "receipt:BLOCK", has_expiry_event: true })).toBe(
      "ticket",
    );
    expect(closureReasonEffective({ status: "expired", closure_reason: "receipt:BLOCK", has_expiry_event: true })).toBe(
      "receipt:BLOCK",
    );
    expect(closureReasonSource({ closure_reason: null, has_expiry_event: true })).toBe(
      "expiry_event",
    );
    expect(closureReasonEffective({ status: "expired", closure_reason: null, has_expiry_event: true })).toBe(
      "expiry_detection",
    );
    expect(closureReasonSource({ closure_reason: null, has_expiry_event: false })).toBe(
      "status_fallback",
    );
    expect(closureReasonEffective({ status: "cancelled", closure_reason: null, has_expiry_event: false })).toBe(
      "status:cancelled",
    );
  });

  it("fails closed when an expired generation has neither event nor waiver", () => {
    expect(() => buildSweepLogRow(base({ status: "expired" }), SWEPT, 1)).toThrow(
      /Unwaived event-absent expiry/,
    );
  });

  it("treats NULL expiry as never due and distinguishes it from not-yet-due", () => {
    expect(isDispositionRequired({ status: "open", expires_at: null }, SWEPT)).toBe(false);
    expect(isDispositionRequired({ status: "open", expires_at: FUTURE }, SWEPT)).toBe(false);
    expect(isDispositionRequired({ status: "open", expires_at: PAST }, SWEPT)).toBe(true);
    expect(isOpenGenerationStatus("stale")).toBe(true);
  });

  it("stamps a waiver ref only on waived expiries", () => {
    const waiver_ref = "6b42dd3f-ae62-4abf-8cce-fdc01c41032e";
    const row = buildSweepLogRow(base({ status: "expired", waiver_ref }), SWEPT, 1);
    expect(row.sweep_disposition).toBe("expired_without_event_waived");
    expect(row.waiver_ref).toBe(waiver_ref);
    expect(row.is_newest).toBe(true);
    const withEvent = buildSweepLogRow(base({ status: "expired", has_expiry_event: true, closure_reason: null }), SWEPT, 2);
    expect(withEvent.waiver_ref).toBeNull();
    expect(withEvent.is_newest).toBe(false);
  });
});
