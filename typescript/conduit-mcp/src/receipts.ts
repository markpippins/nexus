import { getReceiptsRaw, getLatestReceiptType } from "./conduit-client";

// Allowed transitions: from → to (v018: removed PROPOSED, added HOLD)
// v019: added CCNF_EXECUTION — sub-event within implementation phase
// v020 (plan 0016): added the artifact-critique edge — a recurring CRITIQUE
//   may follow IMPLEMENTATION. The ALLOWED table stays per-receipt-type; the
//   POSITION of a CRITIQUE (admission vs artifact) is resolved by
//   validateReceipt from the LAST NON-CRITIQUE receipt, never from
//   ticket.objective (which is descriptive-only). No new CRITIQUE_ADMISSION /
//   CRITIQUE_ARTIFACT / CRITIQUE_ARTIFACT_STATE receipt types or states.
const ALLOWED: Record<string, string[]> = {
  // Anything can be created, or start from a requirement idea:
  // PLANNING is for revise_plan (fresh revision plans start with PLANNING receipt):
  "": ["PLAN_CREATE", "BLOCK", "PLANNING"],
  // After creation, builder can implement, hold, or route to critique (admission):
  PLAN_CREATE: ["IMPLEMENTATION", "BLOCK", "CRITIQUE", "HOLD", "PLAN_CREATE"],
  // After implementation, reviewer can pass, reject, or hold; and (plan 0016)
  // the ARTIFACT critique edge — a second Critic review before the Reviewer.
  // CCNF_EXECUTION is a sub-event that records CCNF conformance runs.
  IMPLEMENTATION: ["REVIEW_PASS", "REVIEW_REJECT", "REVIEW", "HOLD", "CCNF_EXECUTION", "CRITIQUE"],
  // CCNF execution returns to implementation or chains another run:
  CCNF_EXECUTION: ["IMPLEMENTATION", "CCNF_EXECUTION", "HOLD"],
  // Rejection → builder re-implements, or manual REVIEW_PASS override, or hold:
  REVIEW_REJECT: ["IMPLEMENTATION", "REVIEW_PASS", "HOLD"],
  // Block → requeue (append-only unblock), implement (legacy edge), or hold:
  BLOCK: ["REQUEUED", "IMPLEMENTATION", "HOLD"],
  // REVIEW_PASS is terminal — no further receipts for THIS plan.
  // But a revision creates a NEW plan with a PLANNING receipt.
  REVIEW_PASS: [],
  // Planning plans can be finalized (pending) or blocked:
  PLANNING: ["PLAN_CREATE", "PLAN_BLOCK", "HOLD"],
  // Hold can be released back to pending, or canceled:
  HOLD: ["PLAN_CREATE", "CANCELLED", "ABANDONED"],
  // Reviewer starts reviewing after implementation:
  REVIEW: ["REVIEW_PASS", "REVIEW_REJECT", "HOLD"],
  // Critic starts critique (admission after PLAN_CREATE, or — plan 0016 —
  // artifact after IMPLEMENTATION). The POSITION is resolved by
  // validateReceipt from the last non-CRITIQUE receipt.
  CRITIQUE: ["CRITIQUE_PASS", "CRITIQUE_REJECT", "HOLD"],
  // Critic outcomes — position-aware routing (admission vs artifact) is
  // resolved by validateReceipt from the last non-CRITIQUE receipt.
  CRITIQUE_PASS: ["IMPLEMENTATION", "REVIEW", "HOLD"],
  CRITIQUE_REJECT: ["PLAN_CREATE", "IMPLEMENTATION", "HOLD"],
  // Planner block → requeue (append-only unblock), implement, or hold:
  PLAN_BLOCK: ["REQUEUED", "IMPLEMENTATION", "HOLD"], // unblock or hold
  // Requeued plans can be re-dispatched:
  REQUEUED: ["PLAN_CREATE", "IMPLEMENTATION"],
  // Cancelled / abandoned plans can be requeued or resurrected:
  CANCELLED: ["REQUEUED", "PLAN_CREATE"],
  ABANDONED: ["REQUEUED", "PLAN_CREATE"],
  // API_LIMIT is always valid (like BLOCK) — handled as special case in validateReceipt
  API_LIMIT: [],
};

/**
 * The critique-family receipt types. All three (CRITIQUE and its two outcomes)
 * share a single canonical WRP state; the distinction is a ROUTING decision
 * resolved from the last non-CRITIQUE receipt — never a new state/receipt type.
 */
const CRITIQUE_FAMILY = new Set(["CRITIQUE", "CRITIQUE_PASS", "CRITIQUE_REJECT"]);

/**
 * The unblock receipt family — the states a plan is in when an operator asks
 * to reopen it.
 *
 * Unblocking MUST NOT delete these rows. `execution.receipts` is the canonical
 * store for plan-backed plans and it is append-only (trg_receipts_immutable,
 * architect ruling fcec95a2 #2), so the only reachable transition is
 * `BLOCK|PLAN_BLOCK|CANCELLED|ABANDONED → REQUEUED`. The old delete-then-
 * PLAN_CREATE path could never work for a plan-backed plan: the DELETE hit
 * `vision.receipts` (0 rows) and PLAN_CREATE was then refused by this map,
 * leaving the plan pending but un-receipted.
 */
export const UNBLOCK_RECEIPT_TYPES = [
  "BLOCK",
  "PLAN_BLOCK",
  "CANCELLED",
  "ABANDONED",
] as const;

const UNBLOCK_FAMILY = new Set<string>(UNBLOCK_RECEIPT_TYPES);

/** Read-only view of the transition map (hermetic tests; no network). */
export function allowedTransitionsFrom(currentType: string): string[] {
  return [...(ALLOWED[currentType] || [])];
}

/**
 * Which receipt reopens a plan, given its latest receipt type.
 *
 * `REQUEUED` for the unblock family — the append-only path, and the only one
 * that works against the immutable canonical store. `PLAN_CREATE` otherwise
 * (no receipts yet, or a synthetic/vision-only plan), preserving the previous
 * behaviour where it is still valid.
 */
export function unblockReceiptTypeFor(
  latest: string | null | undefined,
): "REQUEUED" | "PLAN_CREATE" {
  return latest && UNBLOCK_FAMILY.has(latest) ? "REQUEUED" : "PLAN_CREATE";
}

/** Resolve the unblock transition from the live latest receipt type. */
export async function chooseUnblockReceiptType(
  planId: string,
): Promise<"REQUEUED" | "PLAN_CREATE"> {
  return unblockReceiptTypeFor(await getLatestReceiptType(planId));
}

/**
 * Resolve the position of a critique-family receipt: "admission" (after
 * PLAN_CREATE) vs "artifact" (after IMPLEMENTATION). SINGLE source of truth is
 * the LAST NON-CRITIQUE receipt in plan history — never ticket.objective.
 *
 * Returns the routing basis: { position, basis } where basis is the last
 * non-CRITIQUE receipt type that determined the position.
 */
export async function resolveCritiquePosition(
  planId: string,
): Promise<{ position: "admission" | "artifact" | "unknown"; basis: string | null }> {
  const data = await getReceiptsRaw(planId);
  const receipts = data?.receipts || [];
  // Chronological scan (receipts already come newest-first per the raw endpoint);
  // find the most recent receipt NOT in the critique family.
  const nonCritique = receipts.find((r: any) => !CRITIQUE_FAMILY.has(r?.type));
  const basis = nonCritique?.type ?? null;
  if (basis === "PLAN_CREATE") return { position: "admission", basis };
  if (basis === "IMPLEMENTATION") return { position: "artifact", basis };
  return { position: "unknown", basis };
}

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

/**
 * C1 gate 1 (Lilac plan 8261639): declaring-producer provenance fields for
 * receipt writes issued by this (TypeScript front-door) channel. Spread into
 * every insertReceipt metadata blob so the persistence layer can stamp
 * declaring producer/source channel vs the physical writer process, per the
 * C2 contract draft (producer identity, contract_version, correlation id).
 * Values use the producer-registry naming proposed in the C2 draft.
 */
export function receiptProvenanceMetadata(
  correlationId: string,
): Record<string, unknown> {
  return {
    producer_id: "conduit-mcp",
    source_channel: "conduit-mcp-http",
    contract_version: "1",
    correlation_id: correlationId,
  };
}

/**
 * Resolve the routing target for a critique-family OUTCOME receipt
 * (CRITIQUE_PASS / CRITIQUE_REJECT) based on the position of the CRITIQUE it
 * follows. INVARIANT (plan 0016 AC1): routing resolves ONLY from the last
 * non-CRITIQUE receipt in plan history — ticket.objective MUST NEVER be read.
 *
 *   admission:  CRITIQUE_PASS → IMPLEMENTATION,  CRITIQUE_REJECT → PLAN_CREATE
 *   artifact:   CRITIQUE_PASS → REVIEW,          CRITIQUE_REJECT → IMPLEMENTATION
 */
export async function resolveCritiqueOutcome(
  planId: string,
  outcome: "CRITIQUE_PASS" | "CRITIQUE_REJECT",
): Promise<{ position: "admission" | "artifact" | "unknown"; target: string | null }> {
  const { position } = await resolveCritiquePosition(planId);
  if (position === "admission") {
    return { position, target: outcome === "CRITIQUE_PASS" ? "IMPLEMENTATION" : "PLAN_CREATE" };
  }
  if (position === "artifact") {
    return { position, target: outcome === "CRITIQUE_PASS" ? "REVIEW" : "IMPLEMENTATION" };
  }
  return { position, target: null };
}