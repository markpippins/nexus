/**
 * runtime_claim — a QUEUED WorkRequest gets an owner, or a named refusal.
 *
 * Locks the replacement for the phantom claim. Before this, the tick's
 * auto-transition walked QUEUED → CLAIMED → ACKED → SETTLED with no worker and
 * no execution behind any of it (measured live: three ticks, WR settled,
 * nothing ran). A claim now records WHO holds the WR and for how long, and the
 * tick refuses to ack or settle a WR that has no owner.
 *
 * Tested invariants:
 *   AC1 — QUEUED claims: appends WR_QUEUED carrying workerId + leaseUntil.
 *   AC2 — folded state after a claim carries workerId/claimedAt/leaseUntil.
 *   AC3 — same worker re-claiming is a refresh: no event appended.
 *   AC4 — another worker's claim is a conflict naming the holder, no append.
 *   AC5 — a non-QUEUED WR is refused (SETTLED cannot be claimed).
 *   AC6 — a CIR-SDM rejection refuses the claim and appends nothing.
 *   AC7 — an unowned CLAIMED WR is not auto-advanced by decide().
 *   AC8 — a claimed WR IS auto-advanced by decide() (claim unblocks the chain).
 *   AC9 — a phantom claim (no workerId in the claim event) records no owner.
 *
 * DB + enforcement bridge stubbed; kernel and runtime-ops stay REAL.
 *
 * Usage:
 *   cd /home/codex/dev/nexus/typescript/conduit-mcp
 *   npx vitest run src/runtime-claim.test.ts
 */
import { describe, test, expect, vi, beforeEach } from "vitest";

const {
  gateWrTransitionMock,
  recordGovernedDecisionsMock,
  surfaceBlockerMock,
  getEventsMock,
  appendEventMock,
} = vi.hoisted(() => ({
  gateWrTransitionMock: vi.fn(),
  recordGovernedDecisionsMock: vi.fn(),
  surfaceBlockerMock: vi.fn(),
  getEventsMock: vi.fn(),
  appendEventMock: vi.fn(),
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
  selectNextRunnable: vi.fn(),
  listWorkRequestStates: vi.fn(),
  insertCompileVerdict: vi.fn(),
  computeCompileVerdictId: vi.fn(),
  runCompileGate: vi.fn(),
}));

vi.mock("./watcher", () => ({ PipelineWatcher: class {} }));

import { ClaimError, claimWorkRequest } from "./runtime-ops";
import { decide, foldEvents, dbEventsToRuntimeEvents } from "./runtime-kernel";

const WR = "aaaaaaaa-1111-4111-8111-111111111111";
const RESOLVED_OPS = { ipNodes: [], resolvedOps: ["op1"], registryVersion: "1" };

const ACCEPT = {
  state: "enforced",
  enforced: true,
  rules: [],
  violations: [],
  decisions: [],
  reject: false,
};

const DECISION = {
  violation_id: "v1",
  rule_id: "cir-sdm.one-way-gate",
  rule_version: "2",
  severity: "blocking",
  event_id: "e3",
  cer_id: null,
  description: "illegal WR transition",
  detected_at: null,
  blocking: true,
};

function ev(eventType: string, payload: any = {}, seq = 1): any {
  return {
    event_id: `${eventType}-${seq}`,
    work_request_id: WR,
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

/** QUEUED: submitted (resolved) + validated. */
const QUEUED = [
  ev("WR_SUBMITTED", { opTrace: RESOLVED_OPS }, 1),
  ev("WR_VALIDATED", {}, 2),
];

/** CLAIMED by `workerId` (ownership recorded on the claim event). */
const claimedBy = (workerId: string) => [
  ...QUEUED,
  ev("WR_QUEUED", { workerId, claimedAt: "2026-11-01T00:00:00Z" }, 3),
];

const SETTLED = [
  ...claimedBy("w1"),
  ev("WR_CLAIMED", { workerId: "w1" }, 4),
  ev("WR_ACKED", { workerId: "w1" }, 5),
];

describe("claimWorkRequest", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    appendEventMock.mockResolvedValue(undefined);
    gateWrTransitionMock.mockResolvedValue(ACCEPT);
  });

  test("AC1 — a QUEUED WR can be claimed, recording the owner", async () => {
    getEventsMock.mockResolvedValue(QUEUED);

    const result = await claimWorkRequest({ wrId: WR, workerId: "w1" });

    expect(result.action).toBe("claim");
    expect(appendEventMock).toHaveBeenCalledTimes(1);
    const [wrArg, typeArg, payloadArg] = appendEventMock.mock.calls[0];
    expect(wrArg).toBe(WR);
    expect(typeArg).toBe("WR_QUEUED"); // QUEUED→CLAIMED in this FSM
    expect(payloadArg.workerId).toBe("w1");
    expect(typeof payloadArg.claimedAt).toBe("string");
    expect(typeof payloadArg.leaseUntil).toBe("string");
    expect(payloadArg.leaseMinutes).toBe(30);
  });

  test("AC2 — the fold carries workerId, claimedAt and leaseUntil", async () => {
    const state = foldEvents(
      WR,
      dbEventsToRuntimeEvents([
        ...QUEUED,
        ev("WR_QUEUED", {
          workerId: "w9",
          claimedAt: "2026-11-01T01:00:00Z",
          leaseUntil: "2026-11-01T01:30:00Z",
        }, 3),
      ]),
    );
    expect(state.status).toBe("CLAIMED");
    expect(state.workerId).toBe("w9");
    expect(state.claimedAt).toBe("2026-11-01T01:00:00Z");
    expect(state.leaseUntil).toBe("2026-11-01T01:30:00Z");
  });

  test("AC3 — the same worker re-claiming is a refresh with no new event", async () => {
    getEventsMock.mockResolvedValue(claimedBy("w1"));

    const result = await claimWorkRequest({ wrId: WR, workerId: "w1" });

    expect(result.action).toBe("refresh");
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(gateWrTransitionMock).not.toHaveBeenCalled();
  });

  test("AC4 — another worker's claim is a conflict that names the holder", async () => {
    getEventsMock.mockResolvedValue(claimedBy("w1"));

    await expect(
      claimWorkRequest({ wrId: WR, workerId: "w2" }),
    ).rejects.toMatchObject({ code: "WR_CLAIM_CONFLICT" });
    await expect(
      claimWorkRequest({ wrId: WR, workerId: "w2" }),
    ).rejects.toThrow(/w1/);
    expect(appendEventMock).not.toHaveBeenCalled();
  });

  test("AC5 — a settled WR cannot be claimed", async () => {
    getEventsMock.mockResolvedValue(SETTLED);

    const err = await claimWorkRequest({ wrId: WR, workerId: "w1" }).catch((e) => e);
    expect(err).toBeInstanceOf(ClaimError);
    expect(err.code).toBe("WR_CLAIM_REFUSED");
    expect(err.message).toContain("SETTLED");
    expect(appendEventMock).not.toHaveBeenCalled();
  });

  test("AC6 — a CIR-SDM rejection refuses the claim and appends nothing", async () => {
    getEventsMock.mockResolvedValue(QUEUED);
    gateWrTransitionMock.mockResolvedValue({ ...ACCEPT, violations: [DECISION], decisions: [DECISION], reject: true });

    const err = await claimWorkRequest({ wrId: WR, workerId: "w1" }).catch((e) => e);
    expect(err).toBeInstanceOf(ClaimError);
    expect(err.message).toContain("CIR_SDM_REJECTED");
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(recordGovernedDecisionsMock).toHaveBeenCalledWith([DECISION]);
  });

  test("AC7 — an unowned CLAIMED WR is not auto-advanced", () => {
    // Claim event with no workerId: the phantom claim that used to walk to SETTLED.
    const state = foldEvents(
      WR,
      dbEventsToRuntimeEvents([...QUEUED, ev("WR_QUEUED", {}, 3)]),
    );
    expect(state.status).toBe("CLAIMED");
    expect(state.workerId).toBeUndefined();
    expect(decide(state)).toBeNull();
  });

  test("AC8 — a claimed WR is auto-advanced (the claim unblocks the chain)", () => {
    const state = foldEvents(WR, dbEventsToRuntimeEvents(claimedBy("w1")));
    const decision = decide(state);
    expect(decision?.type).toBe("WR_CLAIMED");
    // The ack is attributed to the holder, not to nobody
    expect(decision?.payload?.workerId).toBe("w1");
  });

  test("AC9 — a phantom claim records no owner", () => {
    const state = foldEvents(
      WR,
      dbEventsToRuntimeEvents([...QUEUED, ev("WR_QUEUED", {}, 3)]),
    );
    expect(state.workerId).toBeUndefined();
    expect(state.claimedAt).toBeUndefined();
    expect(state.leaseUntil).toBeUndefined();
  });
});
