import { describe, test, expect, vi, beforeEach } from "vitest";

// Handler-level test for the CIR-SDM enforcement gate (T23 Step 8): the tool
// paths runtime_transition + runtime_tick with a STUBBED enforcement bridge
// (fake reject/accept) + stubbed DB. Verifies the pre-row gate behavior:
//   - reject → CIR_SDM_REJECTED (transition) / ticked:false (tick), no append
//   - accept → appendEvent proceeds
//   - bridge outage → fail-closed (held) + surfaceBlocker
// The pure state machine (runtime-kernel) stays REAL.

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

vi.mock("./watcher", () => ({
  PipelineWatcher: class {},
}));

import { registerToolHandlers } from "./tools";

const WR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const WR_ID = "wr-1";

function row(eventId: string, eventType: string, payload: any = {}): any {
  return {
    event_id: eventId,
    work_request_id: WR_UUID,
    event_type: eventType,
    event_version: 1,
    correlation_id: null,
    causation_id: null,
    occurred_at: "2026-08-01T00:00:00Z",
    payload,
    actor_type: "system",
    actor_id: "",
    sequence_number: 1,
  };
}

const SUBMITTED = row("e1", "WR_SUBMITTED", {
  opTrace: { ipNodes: ["n1"], resolvedOps: ["op1"], registryVersion: "v1" },
});

const ACCEPT = {
  state: "enforced",
  enforced: true,
  rules: ["cir-sdm.one-way-gate"],
  violations: [],
  decisions: [],
  reject: false,
};

const DECISION = {
  violation_id: "abc123",
  rule_id: "cir-sdm.one-way-gate",
  rule_version: "2",
  severity: "blocking",
  event_id: "e6",
  cer_id: null,
  description: "illegal WR transition",
  detected_at: null,
  blocking: true,
};

const REJECT = {
  ...ACCEPT,
  violations: [DECISION],
  decisions: [DECISION],
  reject: true,
};

const handlers = registerToolHandlers({} as any, () => {});

describe("runtime_transition — CIR-SDM gate (stub bridge)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    appendEventMock.mockResolvedValue(undefined);
  });

  test("accept → appendEvent proceeds", async () => {
    getEventsMock.mockResolvedValue([SUBMITTED]);
    gateWrTransitionMock.mockResolvedValue(ACCEPT);
    const result = await handlers.runtime_transition({
      wrId: WR_ID,
      type: "WR_VALIDATED",
    });
    expect(result.ok).toBe(true);
    expect(appendEventMock).toHaveBeenCalledWith(WR_ID, "WR_VALIDATED", {});
    expect(recordGovernedDecisionsMock).not.toHaveBeenCalled();
  });

  test("reject → CIR_SDM_REJECTED, no append, decision recorded", async () => {
    getEventsMock.mockResolvedValue([SUBMITTED]);
    gateWrTransitionMock.mockResolvedValue(REJECT);
    await expect(
      handlers.runtime_transition({ wrId: WR_ID, type: "WR_VALIDATED" }),
    ).rejects.toMatchObject({ error: { code: "CIR_SDM_REJECTED" } });
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(recordGovernedDecisionsMock).toHaveBeenCalledWith([DECISION]);
  });

  test("bridge outage → CIR_SDM_UNAVAILABLE (fail-closed)", async () => {
    getEventsMock.mockResolvedValue([SUBMITTED]);
    gateWrTransitionMock.mockRejectedValue(new Error("python missing"));
    await expect(
      handlers.runtime_transition({ wrId: WR_ID, type: "WR_VALIDATED" }),
    ).rejects.toMatchObject({ error: { code: "CIR_SDM_UNAVAILABLE" } });
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(surfaceBlockerMock).toHaveBeenCalled();
  });
});

describe("runtime_tick — CIR-SDM gate (stub bridge)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    appendEventMock.mockResolvedValue(undefined);
    selectNextRunnableMock.mockResolvedValue({
      work_request_uuid: WR_UUID,
      wr_id: WR_ID,
    });
    // After runtime_claim, the auto-chain starts at CLAIMED: a QUEUED WR is
    // claimed deliberately (it no longer auto-claims), so the tick's decision
    // here is CLAIMED → ACKED (WR_CLAIMED). The claim event carries the owner,
    // without which the tick would refuse to ack on the worker's behalf.
    getEventsMock.mockResolvedValue([
      SUBMITTED,
      row("e2", "WR_VALIDATED"),
      row("e3", "WR_QUEUED", { workerId: "w1" }),
    ]);
  });

  test("accept → auto-advance proceeds (ticked)", async () => {
    gateWrTransitionMock.mockResolvedValue(ACCEPT);
    const result = await handlers.runtime_tick();
    expect(result.ticked).toBe(true);
    expect(appendEventMock).toHaveBeenCalled();
  });

  test("reject → ticked:false, no append, decision recorded", async () => {
    gateWrTransitionMock.mockResolvedValue(REJECT);
    const result = await handlers.runtime_tick();
    expect(result.ticked).toBe(false);
    expect(result.reason).toContain("CIR_SDM_REJECTED");
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(recordGovernedDecisionsMock).toHaveBeenCalledWith([DECISION]);
  });

  test("bridge outage → held (fail-closed) + blocker surfaced", async () => {
    gateWrTransitionMock.mockRejectedValue(new Error("python missing"));
    const result = await handlers.runtime_tick();
    expect(result.ticked).toBe(false);
    expect(result.reason).toContain("CIR_SDM_UNAVAILABLE");
    expect(appendEventMock).not.toHaveBeenCalled();
    expect(surfaceBlockerMock).toHaveBeenCalled();
  });
});
