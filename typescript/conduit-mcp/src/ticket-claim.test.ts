/**
 * Ticket-claim decision tests (walk-through D3, 2026-09-22).
 *
 * decideClaimAction is the pure core of claim_ticket; these are hermetic —
 * no DB, no HTTP. The SQL paths (claimTicket/releaseTicket) are verified
 * live against the real ticket store on deploy.
 */
import { describe, expect, it } from "vitest";
import { decideClaimAction } from "./db";

const NOW = Date.parse("2026-09-22T19:00:00Z");
const BASE = {
  requestedSessionId: "sess-B",
  nowMs: NOW,
  staleMinutes: 30,
};

describe("decideClaimAction", () => {
  it("claims an unclaimed open ticket", () => {
    const d = decideClaimAction({ ...BASE, ticketStatus: "open", claimedSessionId: null, lastActivity: null });
    expect(d.action).toBe("claim");
  });

  it("claims a stale (reset) ticket", () => {
    const d = decideClaimAction({ ...BASE, ticketStatus: "stale", claimedSessionId: null, lastActivity: null });
    expect(d.action).toBe("claim");
  });

  it("refuses nothing-to-claim statuses", () => {
    for (const s of ["completed", "failed", "expired", "cancelled", "abandoned"]) {
      const d = decideClaimAction({ ...BASE, ticketStatus: s, claimedSessionId: null, lastActivity: null });
      expect(d.action).toBe("none");
    }
  });

  it("refreshes idempotently for the holding session", () => {
    const d = decideClaimAction({
      ...BASE,
      ticketStatus: "claimed",
      claimedSessionId: "sess-B",
      lastActivity: "2026-09-22T18:40:00Z", // 20m old — would conflict for anyone else
    });
    expect(d.action).toBe("refresh");
  });

  it("refuses a fresh claim held by another session, with holder details", () => {
    const d = decideClaimAction({
      ...BASE,
      ticketStatus: "claimed",
      claimedSessionId: "sess-A",
      lastActivity: "2026-09-22T18:50:00Z", // 10m old < 30m window
    });
    expect(d.action).toBe("conflict");
    expect(d.holder).toBe("sess-A");
    expect(d.holderSince).toBe("2026-09-22T18:50:00Z");
  });

  it("takes over a stale claim held by another session", () => {
    const d = decideClaimAction({
      ...BASE,
      ticketStatus: "claimed",
      claimedSessionId: "sess-A",
      lastActivity: "2026-09-22T18:10:00Z", // 50m old > 30m window
    });
    expect(d.action).toBe("takeover");
    expect(d.holder).toBe("sess-A");
  });

  it("treats unknown freshness as conflict (safe default — never silently steals)", () => {
    const d = decideClaimAction({
      ...BASE,
      ticketStatus: "claimed",
      claimedSessionId: "sess-A",
      lastActivity: null,
    });
    expect(d.action).toBe("conflict");
  });

  it("honors a custom staleness window", () => {
    const d = decideClaimAction({
      ...BASE,
      staleMinutes: 120,
      ticketStatus: "claimed",
      claimedSessionId: "sess-A",
      lastActivity: "2026-09-22T18:10:00Z", // 50m — stale under 30m, fresh under 120m
    });
    expect(d.action).toBe("conflict");
  });
});
