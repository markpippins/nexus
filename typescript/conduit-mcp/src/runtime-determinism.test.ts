/**
 * Fold determinism for eventless WRs + identifiable UNRESOLVED_OPS holds.
 *
 * Locks two defects found while taking the WR runtime's status (record
 * 90a4c9f4):
 *
 *   1. `foldEvents` stamped `createdAt = new Date()` when a WR had no events,
 *      so its folded state changed on every read of the same immutable (empty)
 *      input — and it rendered an eventless WR as freshly submitted. All six
 *      legacy WRs carried events=0 and reported `createdAt` = the moment you
 *      asked.
 *
 *   2. Both tick entry points returned the bare string
 *      "UNRESOLVED_OPS: held at VALIDATED, not auto-advancing". Because
 *      `selectNextRunnable()` returns the same head-of-queue WR every tick, one
 *      unresolved WR blocks every resolved WR behind it — and the reason named
 *      the condition but not the WR, so the blocker was unactionable.
 *
 * Pure and DB-free.
 *
 * Usage:
 *   cd /home/codex/dev/nexus/typescript/conduit-mcp
 *   npx vitest run src/runtime-determinism.test.ts
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  foldEvents,
  unresolvedHoldReason,
  type RuntimeEvent,
} from "./runtime-kernel.js";

afterEach(() => {
  vi.useRealTimers();
});

describe("fold determinism (eventless)", () => {
  it("AC1 — an eventless WR folds with an empty createdAt, not the wall clock", () => {
    const state = foldEvents("wr-empty", []);
    expect(state.createdAt).toBe("");
    expect(state.status).toBe("DRAFT");
    expect(state.version).toBe(0);
    expect(state.lastTimestamp).toBe("");
  });

  it("AC2 — the same empty log folds identically at different wall-clock times", () => {
    vi.useFakeTimers();

    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));
    const first = JSON.stringify(foldEvents("wr-empty", []));

    vi.setSystemTime(new Date("2026-11-01T20:24:03.487Z"));
    const second = JSON.stringify(foldEvents("wr-empty", []));

    vi.setSystemTime(new Date("2027-06-15T12:34:56.789Z"));
    const third = JSON.stringify(foldEvents("wr-empty", []));

    expect(second).toBe(first);
    expect(third).toBe(first);
  });

  it("AC3 — a WR with events still takes createdAt from its first event", () => {
    const events: RuntimeEvent[] = [
      {
        type: "WR_SUBMITTED",
        wrId: "wr-real",
        timestamp: "2026-08-12T22:51:11.270Z",
        payload: { opTrace: { resolvedOps: ["READ_FILE"], registryVersion: "1" } },
      },
    ];
    const state = foldEvents("wr-real", events);
    expect(state.createdAt).toBe("2026-08-12T22:51:11.270Z");
    expect(state.status).toBe("VALIDATED");
    expect(state.version).toBe(1);
  });

  it("AC4 — eventless and submitted WRs are now distinguishable", () => {
    const empty = foldEvents("wr-empty", []);
    const submitted = foldEvents("wr-real", [
      {
        type: "WR_SUBMITTED",
        wrId: "wr-real",
        timestamp: "2026-08-12T22:51:11.270Z",
        payload: {},
      },
    ]);
    // Distinguishable on the timestamp the fold carries forward, rather than
    // both looking like they were created at read time.
    expect(empty.createdAt).toBe("");
    expect(submitted.createdAt).not.toBe("");
  });
});

describe("UNRESOLVED_OPS hold reason", () => {
  it("AC5 — names the blocking WR", () => {
    const reason = unresolvedHoldReason("0ec94c16-0451-4c66-86d7-d017f317851a");
    expect(reason).toContain("0ec94c16-0451-4c66-86d7-d017f317851a");
  });

  it("AC6 — keeps the UNRESOLVED_OPS: prefix callers and logs match on", () => {
    expect(unresolvedHoldReason("wr-1")).toMatch(/^UNRESOLVED_OPS: /);
  });

  it("AC7 — holds ARE reported with both condition and subject", () => {
    const reason = unresolvedHoldReason("wr-abc");
    expect(reason).toContain("VALIDATED");
    expect(reason).toContain("wr-abc");
  });
});
