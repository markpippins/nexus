/**
 * /state ticket display — newest ticket per (plan, role) (plan 8261649 follow-up).
 *
 * Defect this locks: the watcher built `ticketStatuses` by assigning every row
 * in query order, so an older expired ticket overwrote the fresh open ticket
 * minted by a re-arm. The plan card then reported `expired` for a plan whose
 * DB state was `open`, hiding it from anyone dispatching off /state.
 *
 * The SQL now orders created_at DESC (id DESC as tiebreak) and the pure
 * collapse keeps the first row per role. These tests pin that contract without
 * a database.
 */

import { describe, expect, it } from "vitest";
import { newestTicketPerRole, type TicketRowForStatus } from "./watcher";

function row(over: Partial<TicketRowForStatus>): TicketRowForStatus {
  return {
    plan_id: "8261650",
    role: "builder",
    status: "open",
    id: "ticket-a",
    created_at: "2026-11-10 15:36:34.426+00",
    expires_at: "2026-11-11 15:36:34.426+00",
    objective: "plan objective",
    claimed_at: null,
    session_id: null,
    ...over,
  };
}

describe("newestTicketPerRole", () => {
  it("keeps the fresh open ticket, not the older expired one", () => {
    // Newest-first, as the SQL returns it.
    const map = newestTicketPerRole([
      row({ id: "ticket-new", status: "open" }),
      row({
        id: "ticket-old",
        status: "expired",
        created_at: "2026-09-19 00:47:58.016+00",
        expires_at: "2026-09-23 18:20:49.343+00",
      }),
    ]);

    expect(map.get("8261650")!.builder.status).toBe("open");
    expect(map.get("8261650")!.builder.id).toBe("ticket-new");
  });

  it("reports a claimed newest ticket with its session", () => {
    const map = newestTicketPerRole([
      row({
        id: "ticket-claimed",
        status: "claimed",
        claimed_at: "2026-11-10 15:36:27+00",
        session_id: "worker-conduit-worker-1",
      }),
      row({ id: "ticket-old", status: "expired", created_at: "2026-09-19 00:47:58+00" }),
    ]);

    expect(map.get("8261650")!.builder).toMatchObject({
      status: "claimed",
      id: "ticket-claimed",
      claimedSession: "worker-conduit-worker-1",
    });
  });

  it("keeps roles and plans independent", () => {
    const map = newestTicketPerRole([
      row({ plan_id: "8261649", role: "builder", id: "b-new", status: "open" }),
      row({ plan_id: "8261649", role: "builder", id: "b-old", status: "expired" }),
      row({ plan_id: "8261649", role: "reviewer", id: "r-new", status: "open" }),
      row({ plan_id: "8261661", role: "builder", id: "other", status: "open" }),
    ]);

    expect(map.get("8261649")!.builder.id).toBe("b-new");
    expect(map.get("8261649")!.reviewer.id).toBe("r-new");
    expect(map.get("8261661")!.builder.id).toBe("other");
    expect(Object.keys(map.get("8261649")!)).toEqual(["builder", "reviewer"]);
  });

  it("omits null optional fields instead of emitting null", () => {
    const map = newestTicketPerRole([
      row({ expires_at: null, objective: null, claimed_at: null, session_id: null }),
    ]);
    const card = map.get("8261650")!.builder;

    expect(card.expires_at).toBeUndefined();
    expect(card.objective).toBeUndefined();
    expect(card.claimedAt).toBeUndefined();
    expect(card.claimedSession).toBeUndefined();
  });
});
