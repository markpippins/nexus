import { describe, it, expect } from "vitest";
import {
  foldEvents,
  dbEventToRuntimeEvent,
  dbEventsToRuntimeEvents,
  type RuntimeEvent,
} from "./runtime-kernel.js";
import type { RuntimeEventType } from "./runtime-kernel.js";

/**
 * Conformance: deterministic fold under identical timestamps
 * (inspector G2 / architect plan 8261649 criterion: "TypeScript
 * conformance test generates two same-millisecond events and asserts
 * deterministic fold" — generalised to N same-millisecond events and
 * both tiebreaker classes).
 *
 * Event fixtures use FSM-legal chains (the kernel's reduce() throws on
 * illegal transitions, and with the deterministic sort the fold order —
 * not DB return order — decides legality).
 *
 * Hermetic: pure functions, no DB, no network.
 */

const T = "2026-09-19T12:00:00.000Z"; // one shared millisecond

const ev = (
  type: RuntimeEventType,
  opts: { ts?: string; seq?: number; id?: string } = {},
): RuntimeEvent => ({
  type,
  wrId: "wr-fold-1",
  timestamp: opts.ts ?? T,
  ...(opts.seq !== undefined ? { sequenceNumber: opts.seq } : {}),
  ...(opts.id !== undefined ? { eventId: opts.id } : {}),
});

/** Every permutation of the input array folds to the same state. */
function expectPermutationInvariant(events: RuntimeEvent[]): void {
  const reference = JSON.stringify(foldEvents("wr-fold-1", events));
  const seen = new Set<string>();
  const arr = [...events];
  for (let i = 0; i < 60; i++) {
    for (let j = arr.length - 1; j > 0; j--) {
      const k = Math.floor(Math.random() * (j + 1));
      [arr[j], arr[k]] = [arr[k], arr[j]];
    }
    seen.add(arr.map((e) => e.type + (e.sequenceNumber ?? "")).join(","));
    expect(JSON.stringify(foldEvents("wr-fold-1", [...arr]))).toBe(reference);
  }
  expect(seen.size).toBeGreaterThan(1); // shuffles really differed
}

describe("deterministic fold (G2)", () => {
  it("two same-millisecond events fold identically regardless of input order", () => {
    const a = ev("WR_SUBMITTED", { seq: 2, id: "e-2" });
    const b = ev("WR_VALIDATED", { seq: 3, id: "e-3" });
    const s1 = JSON.stringify(foldEvents("wr-fold-1", [a, b]));
    const s2 = JSON.stringify(foldEvents("wr-fold-1", [b, a]));
    expect(s1).toBe(s2);
    const st = foldEvents("wr-fold-1", [a, b]);
    expect(st.lastEvent).toBe("WR_VALIDATED"); // seq 3 after seq 2
    expect(st.status).toBe("QUEUED");
  });

  it("full lifecycle at one timestamp is order-independent", () => {
    // Legal chain: SUBMITTED → VALIDATED → QUEUED → CLAIMED → ACKED
    const chain: RuntimeEventType[] = [
      "WR_SUBMITTED",
      "WR_VALIDATED",
      "WR_QUEUED",
      "WR_CLAIMED",
      "WR_ACKED",
    ];
    const events = chain.map((t, i) => ev(t, { seq: i + 1, id: `e-${i + 1}` }));
    expectPermutationInvariant(events);
    const st = foldEvents("wr-fold-1", events);
    expect(st.lastEvent).toBe("WR_ACKED"); // highest sequence wins
    expect(st.status).toBe("SETTLED");
  });

  it("sequence-number tiebreak orders DB rows by insert order", () => {
    // Rows returned in scrambled order; seq decides the fold chain
    // (legal chain SUBMITTED 5 -> VALIDATED 6 -> QUEUED 7).
    const events = [
      ev("WR_QUEUED", { seq: 7, id: "e-7" }),
      ev("WR_SUBMITTED", { seq: 5, id: "e-5" }),
      ev("WR_VALIDATED", { seq: 6, id: "e-6" }),
    ];
    expectPermutationInvariant(events);
    expect(foldEvents("wr-fold-1", events).lastEvent).toBe("WR_QUEUED");
  });

  it("missing sequence numbers tiebreak on eventId (lexicographic, documented)", () => {
    const events = [
      ev("WR_VALIDATED", { id: "b" }),
      ev("WR_SUBMITTED", { id: "a" }),
    ];
    expectPermutationInvariant(events);
    // 'a' < 'b' lexicographically → WR_SUBMITTED folds first
    expect(foldEvents("wr-fold-1", events).lastEvent).toBe("WR_VALIDATED");
  });

  it("sequenceNumber beats eventId when both present", () => {
    // seq order (SUBMITTED 1 -> VALIDATED 2) is the legal chain; id order
    // ('a' = VALIDATED first) would throw from DRAFT. Seq must win.
    const events = [
      ev("WR_SUBMITTED", { seq: 1, id: "z" }),
      ev("WR_VALIDATED", { seq: 2, id: "a" }),
    ];
    expectPermutationInvariant(events);
    const st = foldEvents("wr-fold-1", events);
    expect(st.lastEvent).toBe("WR_VALIDATED");
  });

  it("event-type tiebreak resolves fully keyless same-ms events", () => {
    // No seq, no id: the type component of the sort key makes the fold
    // canonical by type order (WR_SUBMITTED < WR_VALIDATED) regardless of
    // input order — the legal chain folds first from any permutation.
    const events = [ev("WR_SUBMITTED"), ev("WR_VALIDATED")];
    expectPermutationInvariant(events);
    expect(foldEvents("wr-fold-1", events).lastEvent).toBe("WR_VALIDATED");
  });

  it("different timestamps still dominate over tiebreakers", () => {
    // seq would order late(1) before early(99); timestamp wins — and the
    // resulting order (SUBMITTED early -> VALIDATED late) is the legal chain.
    const early = ev("WR_SUBMITTED", { ts: "2026-09-19T11:00:00.000Z", seq: 99 });
    const late = ev("WR_VALIDATED", { ts: T, seq: 1 });
    const s1 = JSON.stringify(foldEvents("wr-fold-1", [early, late]));
    const s2 = JSON.stringify(foldEvents("wr-fold-1", [late, early]));
    expect(s1).toBe(s2);
    expect(foldEvents("wr-fold-1", [early, late]).lastEvent).toBe(
      "WR_VALIDATED",
    );
  });

  it("dbEventToRuntimeEvent carries sequence_number and event_id through", () => {
    const rt = dbEventToRuntimeEvent({
      work_request_id: "wr-1",
      event_type: "WR_SUBMITTED",
      payload: { workerId: "w1" },
      occurred_at: T,
      sequence_number: 41,
      event_id: "ev-41",
    });
    expect(rt.sequenceNumber).toBe(41);
    expect(rt.eventId).toBe("ev-41");
    // and through the plural helper, string seq normalised
    const [only] = dbEventsToRuntimeEvents([
      {
        work_request_id: "wr-1",
        event_type: "WR_VALIDATED",
        payload: {},
        occurred_at: T,
        sequence_number: "42",
        event_id: "ev-42",
      },
    ]);
    expect(only.sequenceNumber).toBe(42);
    expect(only.eventId).toBe("ev-42");
  });

  it("db-shaped rows without sequence/id still fold deterministically", () => {
    const rows = [
      { work_request_id: "wr-1", event_type: "WR_VALIDATED", payload: {}, occurred_at: T },
      { work_request_id: "wr-1", event_type: "WR_SUBMITTED", payload: {}, occurred_at: T },
    ];
    const events = dbEventsToRuntimeEvents(rows);
    expectPermutationInvariant(events);
  });
});
