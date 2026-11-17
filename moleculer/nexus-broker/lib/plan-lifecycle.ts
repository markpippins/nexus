/**
 * Plan-lifecycle admission — can this plan still accept a harness run?
 *
 * `worker.harness.run` writes a PLAN_CREATE lifecycle receipt for its
 * `wind_task_id` (the conduit plan id) at walk start. Conduit validates that
 * receipt against the plan's *current* state before persisting it:
 *
 *   POST /vision/receipts               (typescript/conduit-mcp/src/index.ts)
 *     → validateReceipt(planId, type)   (typescript/conduit-mcp/src/receipts.ts)
 *         const currentType = (await getLatestReceiptType(planId)) || ""
 *         if (!(ALLOWED[currentType] || []).includes(newType)) → 400
 *
 * For a plan whose latest receipt is REVIEW_PASS the map reads
 * `REVIEW_PASS: []` ("terminal — no further receipts for THIS plan"), so every
 * run submitted against that plan produced
 *
 *   [worker.harness] governance receipt PLAN_CREATE for <plan> rejected: HTTP
 *   400: {"ok":false,"error":"Cannot issue PLAN_CREATE receipt: current state
 *   is REVIEW_PASS. Allowed: none (terminal state)"}
 *
 * and then carried on: the execution request + attempt rows were created and
 * the agent ran against a closed plan whose lifecycle ledger had already
 * refused the run. A rejection that only reaches the log is not a decision.
 *
 * This module makes the same decision ahead of time so the run can be refused
 * before any request/attempt row exists, and with a message an operator can
 * act on.
 *
 * Contract with the ledger (test-pinned, see tests/plan-lifecycle.test.js):
 *  - mirror ALLOWED's PLAN_CREATE edges exactly;
 *  - default-deny, because the ledger does `ALLOWED[state] || []`;
 *  - read the same view and ordering the ledger reads for "current state".
 *
 * Keep this table in lockstep with `ALLOWED` in
 * typescript/conduit-mcp/src/receipts.ts — the test parses that file and fails
 * on any divergence in either direction.
 */

export const PLAN_CREATE_RECEIPT_TYPE = "PLAN_CREATE";

/**
 * States (previous latest receipt type) from which the ledger permits a
 * PLAN_CREATE transition. `""` is the fresh-plan state — a plan with no
 * receipts yet — because the ledger does `getLatestReceiptType(planId) || ""`
 * and `ALLOWED[""]` includes PLAN_CREATE.
 */
export const PLAN_CREATE_ALLOWED_FROM: ReadonlySet<string> = new Set([
  "", // fresh plan (no receipts yet)
  "PLAN_CREATE", // append-only unblock re-create
  "PLANNING", // revise_plan → pending
  "HOLD", // released hold
  "CRITIQUE_REJECT", // admission critique sent the plan back to pending
  "REQUEUED", // unblock / re-dispatch
  "CANCELLED", // resurrect
  "ABANDONED", // resurrect
]);

/**
 * The ledger's own read for "current state": latest receipt for the plan.
 * `db_adapter` uses this ordering in its terminal-plan guard
 * (`create_attempt`), and conduit-mcp's `getLatestReceiptType` reads the same
 * `nebula.receipts_unified` view (C5 projection over resolution.receipt).
 * `id` breaks same-timestamp ties deterministically, which is why it is
 * included rather than relying on `created_at` alone.
 */
export const LATEST_PLAN_RECEIPT_SQL =
  "SELECT type FROM nebula.receipts_unified WHERE plan_id = $1 ORDER BY created_at DESC, id DESC LIMIT 1";

export interface PlanSubmissionDecision {
  allowed: boolean;
  /** Latest receipt type used for the decision (null = plan has no receipts). */
  latest_receipt_type: string | null;
  /** HTTP status to refuse with when `allowed` is false. */
  http_status: number;
  /** Stable machine-readable reason. */
  reason: "PLAN_ACCEPTS_WORK" | "PLAN_STATE_REFUSED";
  /** Operator-facing explanation; empty when allowed. */
  message: string;
}

export function normalizePlanReceiptType(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const type = raw.trim().toUpperCase();
  return type.length > 0 ? type : null;
}

/** True when the ledger would accept PLAN_CREATE as the plan's next receipt. */
export function isPlanCreatePermitted(latestReceiptType: unknown): boolean {
  const state = normalizePlanReceiptType(latestReceiptType) ?? "";
  return PLAN_CREATE_ALLOWED_FROM.has(state);
}

/** Human-readable list of the states that still accept PLAN_CREATE. */
export function planCreateAcceptedFrom(): string[] {
  return Array.from(PLAN_CREATE_ALLOWED_FROM)
    .filter((s) => s.length > 0)
    .sort();
}

/**
 * Decide whether a harness run may be submitted for this plan.
 *
 * Default-deny mirrors the ledger: a state the map does not list (including an
 * unknown receipt type) permits nothing. Only a plan with no receipts is
 * treated as fresh and allowed.
 */
export function decidePlanSubmission(
  planId: string,
  latestReceiptType: unknown,
): PlanSubmissionDecision {
  const latest = normalizePlanReceiptType(latestReceiptType);
  if (isPlanCreatePermitted(latest)) {
    return {
      allowed: true,
      latest_receipt_type: latest,
      http_status: 200,
      reason: "PLAN_ACCEPTS_WORK",
      message: "",
    };
  }

  const state = latest as string;
  const hint =
    state === "REVIEW_PASS"
      ? "REVIEW_PASS is terminal: the ledger accepts no further receipts for this plan, so the work cannot be attached to it. Submit it on a fresh plan (create a new wind task) instead of re-running this one."
      : `Advance the plan first (a permitted transition such as REQUEUED), or submit this work on a fresh plan (new wind task).`;

  return {
    allowed: false,
    latest_receipt_type: latest,
    http_status: 409,
    reason: "PLAN_STATE_REFUSED",
    message:
      `plan ${planId} cannot accept harness work: its latest ledger receipt is ${state}, ` +
      `and ${PLAN_CREATE_RECEIPT_TYPE} is not a permitted transition from ${state} ` +
      `(the ledger would reject it with HTTP 400). ${PLAN_CREATE_RECEIPT_TYPE} is accepted only from: ` +
      `${planCreateAcceptedFrom().join(", ")}, or from a plan with no receipts yet. ${hint}`,
  };
}
