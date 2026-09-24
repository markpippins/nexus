import { afterEach, describe, expect, it, vi } from "vitest";

import { TicketExpirySweeper } from "./ticket-sweep";

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
