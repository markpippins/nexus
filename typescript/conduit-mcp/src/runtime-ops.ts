/**
 * Runtime operations — the DB-facing side of the WorkRequest kernel.
 *
 * `runtime-kernel.ts` is pure: fold, decide, validate. This module is the only
 * place that turns those decisions into rows, and it exists because the logic
 * was duplicated between the MCP tool (`tools.ts`) and the REST route
 * (`index.ts`) — the two copies had already drifted, and a drain loop written
 * twice would drift immediately.
 *
 * Two operations:
 *
 *   runTickDrain()    advance every runnable WR in ONE pass, skipping held ones
 *   claimWorkRequest() take ownership of a QUEUED WR (workerId + lease)
 *
 * Why the drain skips held WRs rather than returning on them: `selectNextRunnable`
 * hands back the same head-of-queue WR until it moves, so the old single-step
 * tick reported the first hold forever and never reached the WRs behind it.
 */

import { appendEvent, getEvents, selectNextRunnable } from "./db";
import {
  gateWrTransition,
  recordGovernedDecisions,
  surfaceBlocker,
} from "./cirsdm";
import {
  cirsdmRejectedHoldReason,
  cirsdmUnavailableHoldReason,
  dbEventsToRuntimeEvents,
  decide,
  decideWrClaimAction,
  foldEvents,
  getOpTrace,
  isUnresolvedOps,
  noTransitionHoldReason,
  unresolvedHoldReason,
  unownedHoldReason,
  validateTransition,
  type RuntimeEventType,
  type WorkRequestState,
  type WorkRequestStatus,
} from "./runtime-kernel";

/** Default number of WRs a single drain pass may touch. */
export const DEFAULT_MAX_TICK_STEPS = 50;

/** Default lease recorded on a claim, in minutes. */
export const DEFAULT_LEASE_MINUTES = 30;

/**
 * What a pass did with one WR.
 *
 * `idle` and `held` are deliberately distinct. A VALIDATED WR waiting for the
 * cascade admission subscriber, a DRAFT never submitted, and a settled WR are
 * all *idle*: there is nothing for the loop to do and nothing wrong. Only a WR
 * that is blocked with something actionable to fix is *held*, so a caller can
 * tell "the queue is quiet" from "something needs a human".
 */
export type TickOutcome = "advanced" | "held" | "idle";

export interface TickStep {
  wrId: string;
  ticked: boolean;
  outcome: TickOutcome;
  event?: RuntimeEventType;
  previousStatus?: WorkRequestStatus;
  currentStatus?: WorkRequestState["status"];
  reason?: string;
  state?: WorkRequestState;
  decisions?: unknown[];
}

export interface TickDrainResult {
  steps: TickStep[];
  /** Steps that appended a transition event. */
  advanced: TickStep[];
  /** Blocked with something actionable to fix (unresolved ops, no owner, gate). */
  held: TickStep[];
  /** Nothing for the loop to do: handoff, unsubmitted, or terminal. */
  idle: TickStep[];
  /** false when the pass stopped at maxSteps with work still runnable. */
  drained: boolean;
}

/**
 * Advance ONE selected WR, or explain why it is held.
 *
 * A hold is not an error: it is a WR the loop declined to advance, and the
 * caller must be able to name it. Every hold therefore carries the WR id.
 */
async function stepOne(wr: {
  work_request_uuid: string;
  wr_id?: string;
}): Promise<TickStep> {
  const wrUuid = wr.work_request_uuid;
  const rawEvents = await getEvents(wrUuid);
  const events = dbEventsToRuntimeEvents(rawEvents);
  const state = foldEvents(wrUuid, events);

  // D4: never auto-advance an UNRESOLVED WR past VALIDATED.
  if (state.status === "VALIDATED" && isUnresolvedOps(getOpTrace(events))) {
    return {
      wrId: wrUuid,
      ticked: false,
      outcome: "held",
      previousStatus: state.status,
      reason: unresolvedHoldReason(wrUuid),
    };
  }

  const decision = decide(state);
  if (!decision) {
    const unowned =
      (state.status === "CLAIMED" || state.status === "ACKED") && !state.workerId;
    return {
      wrId: wrUuid,
      ticked: false,
      outcome: unowned ? "held" : "idle",
      previousStatus: state.status,
      reason: unowned
        ? unownedHoldReason(wrUuid, state.status)
        : noTransitionHoldReason(wrUuid, state.status),
    };
  }

  // T23 Step 8: CIR-SDM pre-row enforcement gate (fail-closed). A gate outage
  // HOLDS the tick (do not auto-advance) — mirror the D4 hold.
  try {
    const cirsdm = await gateWrTransition(events, decision.type, wrUuid);
    if (cirsdm.reject) {
      await recordGovernedDecisions(cirsdm.decisions);
      return {
        wrId: wrUuid,
        ticked: false,
        outcome: "held",
        previousStatus: state.status,
        reason: cirsdmRejectedHoldReason(
          wrUuid,
          cirsdm.decisions.map((d) => d.rule_id),
        ),
        decisions: cirsdm.decisions,
      };
    }
  } catch (e: any) {
    await surfaceBlocker(
      `enforcement unavailable for ${wrUuid} (${decision.type}): ${e.message}`,
    );
    return {
      wrId: wrUuid,
      ticked: false,
      outcome: "held",
      previousStatus: state.status,
      reason: cirsdmUnavailableHoldReason(wrUuid),
    };
  }

  await appendEvent(decision.wrId, decision.type, decision.payload as Record<string, unknown>);
  const rawNewEvents = await getEvents(wrUuid);
  const newState = foldEvents(wrUuid, dbEventsToRuntimeEvents(rawNewEvents));
  return {
    wrId: wrUuid,
    ticked: true,
    outcome: "advanced",
    event: decision.type,
    previousStatus: state.status,
    currentStatus: newState.status,
    state: newState,
  };
}

/**
 * Run ONE tick of the decision loop — as a DRAIN.
 *
 * Advances every runnable WR it can reach, skipping (not returning on) WRs that
 * are held, so an unresolved or unowned WR no longer starves everything behind
 * it. Each WR is visited at most once per pass: a WR that was just advanced has
 * a fresh decision waiting, and re-selecting it would loop forever inside one
 * request.
 */
export async function runTickDrain(
  opts: { maxSteps?: number } = {},
): Promise<TickDrainResult> {
  const maxSteps = Math.max(1, opts.maxSteps ?? DEFAULT_MAX_TICK_STEPS);
  const steps: TickStep[] = [];
  const visited = new Set<string>();
  let drained = false;

  for (let i = 0; i < maxSteps; i++) {
    const wr = await selectNextRunnable([...visited]);
    if (!wr) {
      drained = true;
      break;
    }
    const key = wr.work_request_uuid;
    // Defensive: if the selector ignores the exclusion (a mock, a future
    // rewrite) this is the same WR again, and continuing would spin.
    if (visited.has(key)) {
      drained = true;
      break;
    }
    visited.add(key);
    steps.push(await stepOne(wr));
  }

  return {
    steps,
    advanced: steps.filter((s) => s.outcome === "advanced"),
    held: steps.filter((s) => s.outcome === "held"),
    idle: steps.filter((s) => s.outcome === "idle"),
    drained,
  };
}

// ── Claim ─────────────────────────────────────────────────────────

export interface ClaimResult {
  ok: true;
  wrId: string;
  action: "claim" | "refresh";
  workerId: string;
  state: WorkRequestState;
}

export class ClaimError extends Error {
  code: "WR_CLAIM_CONFLICT" | "WR_CLAIM_REFUSED";
  constructor(code: ClaimError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "ClaimError";
  }
}

/**
 * Claim a QUEUED WorkRequest for a worker, or explain why it cannot be claimed.
 *
 * A claim is an explicit act, unlike the tick: it records WHO holds the WR and
 * for how long (`leaseUntil`). Without this, a queued WR had no owner and the
 * only thing that ever moved it was the tick auto-claiming on nobody's behalf.
 */
export async function claimWorkRequest(args: {
  wrId: string;
  workerId: string;
  leaseMinutes?: number;
}): Promise<ClaimResult> {
  const { wrId, workerId } = args;
  const leaseMinutes = args.leaseMinutes ?? DEFAULT_LEASE_MINUTES;

  const rawEvents = await getEvents(wrId);
  if (rawEvents.length === 0) {
    throw new ClaimError("WR_CLAIM_REFUSED", `WorkRequest ${wrId} not found`);
  }
  const events = dbEventsToRuntimeEvents(rawEvents);
  const state = foldEvents(wrId, events);
  const decision = decideWrClaimAction({
    status: state.status,
    holderWorkerId: state.workerId,
    requestedWorkerId: workerId,
  });

  if (decision.action === "conflict") {
    throw new ClaimError(
      "WR_CLAIM_CONFLICT",
      `${wrId} is ${decision.reason}`,
    );
  }
  if (decision.action === "none") {
    throw new ClaimError("WR_CLAIM_REFUSED", `${wrId}: ${decision.reason}`);
  }
  if (decision.action === "refresh") {
    // Same worker, already held: no event. The FSM has no CLAIMED→CLAIMED
    // no-op, and appending a second claim would put a false history in the
    // ledger for something that did not happen.
    return { ok: true, wrId, action: "refresh", workerId, state };
  }

  // QUEUED → CLAIMED is carried by the WR_QUEUED event in this FSM.
  validateTransition(state.status, "WR_QUEUED");

  // Same governed-write posture as every other transition: the CIR-SDM gate
  // runs before the row is written, and fails closed.
  try {
    const cirsdm = await gateWrTransition(events, "WR_QUEUED", wrId);
    if (cirsdm.reject) {
      await recordGovernedDecisions(cirsdm.decisions);
      throw new ClaimError(
        "WR_CLAIM_REFUSED",
        `CIR_SDM_REJECTED: ${cirsdm.decisions.map((d) => d.rule_id).join(", ")}`,
      );
    }
  } catch (e: any) {
    if (e instanceof ClaimError) throw e;
    await surfaceBlocker(`enforcement unavailable for ${wrId} (claim): ${e.message}`);
    throw new ClaimError(
      "WR_CLAIM_REFUSED",
      `CIR_SDM_UNAVAILABLE: cannot claim ${wrId} (fail-closed): ${e.message}`,
    );
  }

  const now = new Date();
  const leaseUntil = new Date(now.getTime() + leaseMinutes * 60_000).toISOString();
  await appendEvent(wrId, "WR_QUEUED", {
    workerId,
    claimedAt: now.toISOString(),
    leaseUntil,
    leaseMinutes,
  });

  const rawNewEvents = await getEvents(wrId);
  const newState = foldEvents(wrId, dbEventsToRuntimeEvents(rawNewEvents));
  return { ok: true, wrId, action: "claim", workerId, state: newState };
}
