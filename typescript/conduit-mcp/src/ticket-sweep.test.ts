import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildExpiryDetectionPayload,
  classifyTicketStatus,
  TicketExpirySweeper,
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
