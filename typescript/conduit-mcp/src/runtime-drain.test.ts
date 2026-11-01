/**
 * Tick DRAIN semantics — one pass advances everything reachable and skips holds.
 *
 * Locks the behaviour that replaced the single-step tick:
 *
 *   1. `selectNextRunnable()` returned the same head-of-queue WR until it moved,
 *      so one unresolved WR starved every runnable WR behind it. The old tick
 *      reported that hold forever and never reached the rest of the queue.
 *   2. A pass visits each WR at most once — re-selecting a WR it just advanced
 *      would loop inside a single request.
 *
 * Held vs idle is a real distinction: a VALIDATED WR waiting on cascade
 * admission is idle (nothing to do, nothing wrong), while an unresolved or
 * unowned WR is held (actionable). Collapsing them would make every quiet queue
 * look like an incident.
 *
 * DB + enforcement bridge stubbed; the kernel and runtime-ops stay REAL.
 *
 * Usage:
 *   cd /home/codex/dev/nexus/typescript/conduit-mcp
 *   npx vitest run src/runtime-drain.test.ts
 */
import { describe, test, expect, vi, beforeEach } from "vitest";

const {
  gateWrTransitionMock,
  recordGovernedDecisionsMock,
  surfaceBlockerMock,
  getEventsMock,
  appendEventMock,
  selectNextRunnableMock,
} = vi.hoisted(() => ({
  gateWrTransitionMock: vi.fn(),
  recordGovernedDecisionsMock: vi.fn(),
  surfaceBlockerMock: vi.fn(),
  getEventsMock: vi.fn(),
  appendEventMock: vi.fn(),
  selectNextRunnableMock: vi.fn(),
}));

vi.mock("./cirsdm", () => ({
  gateWrTransition: gateWrTransitionMock,
  recordGovernedDecisions: recordGovernedDecisionsMock,
  surfaceBlocker: surfaceBlockerMock,
}));

vi.mock("./db", () => ({
  createTicketIfMissing: vi.fn(),
  getPlan: vi.fn(),
  getPlanById: vi.fn(),
  upsertPlan: vi.fn(),
  softDeletePlan: vi.fn(),
  checkpointWal: vi.fn(),
  cancelTicketsByPlan: vi.fn(),
  undeletePlan: vi.fn(),
  hardDeletePlan: vi.fn(),
  updateSessionHeartbeat: vi.fn(),
  createWorkRequest: vi.fn(),
  appendEvent: appendEventMock,
  getEvents: getEventsMock,
  getAllEvents: vi.fn(),
  selectNextRunnable: selectNextRunnableMock,
  listWorkRequestStates: vi.fn(),
  insertCompileVerdict: vi.fn(),
  computeCompileVerdictId: vi.fn(),
  runCompileGate: vi.fn(),
}));

vi.mock("./watcher", () => ({ PipelineWatcher: class {} }));

import { runTickDrain } from "./runtime-ops";

const WR_A = "aaaaaaaa-1111-4111-8111-111111111111";
const WR_B = "bbbbbbbb-2222-4222-8222-222222222222";
const WR_C = "cccccccc-3333-4333-8333-333333333333";

const ACCEPT = {
  state: "enforced",
  enforced: true,
  rules: [],
  violations: [],
  decisions: [],
  reject: false,
};

function ev(
  workRequestId: string,
  eventType: string,
  payload: any = {},
  seq = 1,
): any {
  return {
    event_id: `${workRequestId}-${eventType}-${seq}`,
    work_request_id: workRequestId,
    event_type: eventType,
    event_version: 1,
    correlation_id: null,
    causation_id: null,
    occurred_at: "2026-11-01T00:00:00Z",
    payload,
    actor_type: "system",
    actor_id: "",
    sequence_number: seq,
  };
}

const RESOLVED_OPS = { ipNodes: [], resolvedOps: ["op1"], registryVersion: "1" };

/** A WR the loop can actually advance: CLAIMED by w1. */
function claimedBy(id: string, workerId = "w1"): any[] {
  return [
    ev(id, "WR_SUBMITTED", { opTrace: RESOLVED_OPS }, 1),
    ev(id, "WR_VALIDATED", {}, 2),
    ev(id, "WR_QUEUED", { workerId }, 3),
  ];
}

/** A WR parked at VALIDATED with unresolved ops (D4 hold). */
function unresolved(id: string): any[] {
  return [
    ev(id, "WR_SUBMITTED", { opTrace: { ipNodes: [], resolvedOps: [], registryVersion: "UNRESOLVED" } }, 1),
  ];
}

/** A WR whose claim carries no owner (phantom claim). */
function claimedWithoutWorker(id: string): any[] {
  return [
    ev(id, "WR_SUBMITTED", { opTrace: RESOLVED_OPS }, 1),
    ev(id, "WR_VALIDATED", {}, 2),
    ev(id, "WR_QUEUED", {}, 3),
  ];
}

/** Per-WR event logs, keyed by work_request_uuid. Appends mutate them, so the
 *  fold after an advance sees the event that was just written (a mock that
 *  returned a frozen log would report `currentStatus` as the pre-advance
 *  status and quietly assert nothing). */
let logs: Record<string, any[]> = {};

function eventsFor(next: Record<string, any[]>) {
  logs = next;
  getEventsMock.mockImplementation(async (id: string) => logs[id] ?? []);
}

function rows(...ids: string[]) {
  const r = ids.map((id) => ({ work_request_uuid: id, wr_id: id.slice(0, 8) }));
  selectNextRunnableMock.mockResolvedValueOnce(undefined);
  // Rebuild the queue: n-1 rows then undefined to end the pass.
  selectNextRunnableMock.mockReset();
  for (const row of r) selectNextRunnableMock.mockResolvedValueOnce(row);
  selectNextRunnableMock.mockResolvedValue(undefined);
}

describe("runTickDrain", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    logs = {};
    appendEventMock.mockImplementation(async (id: string, type: string, payload: any) => {
      logs[id] = logs[id] ?? [];
      logs[id].push(ev(id, type, payload, logs[id].length + 1));
    });
    gateWrTransitionMock.mockResolvedValue(ACCEPT);
  });

  test("advances every runnable WR in a single pass", async () => {
    eventsFor({ [WR_A]: claimedBy(WR_A), [WR_B]: claimedBy(WR_B), [WR_C]: claimedBy(WR_C) });
    rows(WR_A, WR_B, WR_C);

    const result = await runTickDrain();

    expect(result.advanced).toHaveLength(3);
    expect(result.held).toHaveLength(0);
    expect(result.drained).toBe(true);
    expect(appendEventMock).toHaveBeenCalledTimes(3);
    // Each WR visited once: CLAIMED -> ACKED
    expect(result.advanced.map((s) => s.previousStatus)).toEqual(["CLAIMED", "CLAIMED", "CLAIMED"]);
    expect(result.advanced.map((s) => s.currentStatus)).toEqual(["ACKED", "ACKED", "ACKED"]);
  });

  test("skips a held WR and advances the one behind it", async () => {
    eventsFor({ [WR_A]: unresolved(WR_A), [WR_B]: claimedBy(WR_B) });
    rows(WR_A, WR_B);

    const result = await runTickDrain();

    expect(result.steps).toHaveLength(2);
    expect(result.held).toHaveLength(1);
    expect(result.held[0].wrId).toBe(WR_A);
    expect(result.held[0].reason).toContain("UNRESOLVED_OPS");
    expect(result.held[0].reason).toContain(WR_A);
    expect(result.advanced).toHaveLength(1);
    expect(result.advanced[0].wrId).toBe(WR_B);
    // Only the runnable WR was written to
    expect(appendEventMock).toHaveBeenCalledTimes(1);
    expect(appendEventMock).toHaveBeenCalledWith(WR_B, "WR_CLAIMED", expect.anything());
  });

  test("a pass with only a blocked WR reports it by id and does not append", async () => {
    eventsFor({ [WR_A]: unresolved(WR_A) });
    rows(WR_A);

    const result = await runTickDrain();

    expect(result.advanced).toHaveLength(0);
    expect(result.held).toHaveLength(1);
    expect(appendEventMock).not.toHaveBeenCalled();
  });

  test("an unowned CLAIMED WR is held, not acked", async () => {
    eventsFor({ [WR_A]: claimedWithoutWorker(WR_A) });
    rows(WR_A);

    const result = await runTickDrain();

    expect(result.held).toHaveLength(1);
    expect(result.held[0].reason).toContain("UNOWNED");
    expect(appendEventMock).not.toHaveBeenCalled();
  });

  test("a VALIDATED handoff is idle, not held", async () => {
    // Resolved ops, still awaiting cascade admission — nothing to do, nothing wrong.
    eventsFor({ [WR_A]: [ev(WR_A, "WR_SUBMITTED", { opTrace: RESOLVED_OPS }, 1)] });
    rows(WR_A);

    const result = await runTickDrain();

    expect(result.held).toHaveLength(0);
    expect(result.idle).toHaveLength(1);
    expect(result.idle[0].reason).toContain("no automatic transition");
  });

  test("visits each WR at most once even if the selector ignores the exclusion", async () => {
    eventsFor({ [WR_A]: claimedBy(WR_A) });
    // Always the same row — a selector that does not honour `exclude`.
    selectNextRunnableMock.mockResolvedValue({ work_request_uuid: WR_A, wr_id: "aaaaaaaa" });

    const result = await runTickDrain({ maxSteps: 10 });

    expect(result.steps).toHaveLength(1);
    expect(result.drained).toBe(true);
    expect(appendEventMock).toHaveBeenCalledTimes(1);
  });

  test("the selector is told which WRs were already visited", async () => {
    eventsFor({ [WR_A]: claimedBy(WR_A), [WR_B]: claimedBy(WR_B) });
    rows(WR_A, WR_B);

    await runTickDrain();

    expect(selectNextRunnableMock.mock.calls[0][0]).toEqual([]);
    expect(selectNextRunnableMock.mock.calls[1][0]).toEqual([WR_A]);
  });

  test("respects maxSteps and reports a truncated pass", async () => {
    eventsFor({ [WR_A]: claimedBy(WR_A), [WR_B]: claimedBy(WR_B), [WR_C]: claimedBy(WR_C) });
    rows(WR_A, WR_B, WR_C);

    const result = await runTickDrain({ maxSteps: 2 });

    expect(result.advanced).toHaveLength(2);
    expect(result.drained).toBe(false);
  });

  test("an enforcement-gate outage holds that WR and still drains the rest", async () => {
    eventsFor({ [WR_A]: claimedBy(WR_A), [WR_B]: claimedBy(WR_B) });
    rows(WR_A, WR_B);
    gateWrTransitionMock
      .mockRejectedValueOnce(new Error("python missing"))
      .mockResolvedValue(ACCEPT);

    const result = await runTickDrain();

    expect(result.held).toHaveLength(1);
    expect(result.held[0].reason).toContain("CIR_SDM_UNAVAILABLE");
    expect(result.held[0].reason).toContain(WR_A);
    expect(result.advanced).toHaveLength(1);
    expect(surfaceBlockerMock).toHaveBeenCalled();
  });
});
