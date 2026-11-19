import { getLatestReceiptType } from "./db";

// Allowed transitions: from → to (v067: added PROPOSED, PLANNING)
const ALLOWED: Record<string, string[]> = {
  // Anything can be created or proposed:
  "": ["PLAN_CREATE", "BLOCK", "PROPOSED"],
  // After creation, builder can implement or block:
  PLAN_CREATE: ["IMPLEMENTATION", "BLOCK", "CRITIQUE"],
  // After implementation, reviewer can pass or reject:
  IMPLEMENTATION: ["REVIEW_PASS", "REVIEW_REJECT", "REVIEW"],
  // Rejection → builder re-implements, or manual REVIEW_PASS override
  REVIEW_REJECT: ["IMPLEMENTATION", "REVIEW_PASS"],
  // After pass, plan is done. Block from any state:
  BLOCK: ["IMPLEMENTATION"], // unblock by implementing
  // REVIEW_PASS is terminal — no further receipts for THIS plan.
  // But a revision creates a NEW plan with a PLANNING receipt.
  REVIEW_PASS: [],
  // Proposed plans can be promoted to planning:
  PROPOSED: ["PLANNING"],
  // Planning plans can be finalized (pending) or sent back to proposed:
  PLANNING: ["PLAN_CREATE", "PROPOSED", "PLAN_BLOCK"],
  // Reviewer starts reviewing after implementation:
  REVIEW: ["REVIEW_PASS", "REVIEW_REJECT"],
  // Critic starts critique after plan creation:
  CRITIQUE: ["CRITIQUE_PASS", "CRITIQUE_REJECT"],
  // Critic outcomes:
  CRITIQUE_PASS: ["IMPLEMENTATION"], // critique passed → builder can implement
  CRITIQUE_REJECT: ["PLAN_CREATE"], // critique failed → back to planning
  // Planner block:
  PLAN_BLOCK: ["IMPLEMENTATION"], // unblock via implementation
  // Requeued plans can be re-dispatched:
  REQUEUED: ["PLAN_CREATE", "IMPLEMENTATION"],
  // Cancelled / abandoned plans can be resurrected:
  CANCELLED: ["PLAN_CREATE"],
  ABANDONED: ["PLAN_CREATE"],
};

export async function validateReceipt(
  planId: string,
  newType: string,
): Promise<{ valid: boolean; error?: string }> {
  if (newType === "BLOCK" || newType === "API_LIMIT" || newType === "CANCELLED" || newType === "ABANDONED") {
    // BLOCK and API_LIMIT are always allowed (override any state)
    return { valid: true };
  }

  const currentType = await getLatestReceiptType(planId) || "";
  const allowed = ALLOWED[currentType] || [];

  if (!allowed.includes(newType)) {
    return {
      valid: false,
      error: `Cannot issue ${newType} receipt: current state is ${currentType || "none"}. Allowed: ${allowed.join(", ") || "none (terminal state)"}`,
    };
  }

  return { valid: true };
}
