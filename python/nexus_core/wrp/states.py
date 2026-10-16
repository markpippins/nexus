"""
WRP state machine primitives — canonical adjacency matrix and receipt mapping.

# CANONICAL SOURCE
These tables are the canonical Python definition of the WRP (planning
lifecycle) state machine. They are kept in sync with:
    typescript/nebula-mcp/src/conduit-wrp-contract.ts (WRP states — mirror)
    typescript/conduit-mcp/src/receipts.ts           (receipt transitions)

Do not edit these tables independently — reconcile against the TypeScript
canonical if changes are needed. That reconciliation is enforced
mechanically by:
    python/nexus_core/wrp/tests/test_cross_language_contract.py

# NOT THE SAME MACHINE AS THE KERNEL (inspector finding D1, report 5d45f921)
There is a SECOND, deliberately separate state machine:
    typescript/conduit-mcp/src/runtime-kernel.ts
        — WR_STATUSES (DRAFT, VALIDATED, QUEUED, CLAIMED, ACKED, SETTLED,
          REJECTED, FAILED, NOOP, DEFERRED) and RUNTIME_EVENT_TYPES (WR_*)

That one is the kernel/execution lifecycle; this one is the planning
lifecycle. Neither is canonical over the other and they are deliberately NOT
merged — no rename was performed. They share exactly two names with
different meanings in each (QUEUED, FAILED). That collision set is pinned in
test_cross_language_contract.py, so any new shared name fails the guard and
forces a conscious decision.

# SEMANTICS
WRP_ADJACENCY_MATRIX: The 11-state WRP state machine defining which state
transitions are valid. Every transition must be registered here.

RECEIPT_TO_WRP_STATE: Maps Conduit receipt types to their corresponding
WRP states. A receipt type indicates "what happened"; the WRP state is
"what state the plan is now in" after the transition.
"""

from typing import Dict, Set


# ── The WRP adjacency matrix ──────────────────────────────────────────
# 11 states: CREATED → INTAKE → PLANNING → CRITIQUE → SPECIFICATION →
# APPROVED → QUEUED → EXECUTING → COMPLETED → ARCHIVED + FAILED

WRP_ADJACENCY_MATRIX: Dict[str, Set[str]] = {
    "CREATED":       {"INTAKE"},           # FAILED not reachable from CREATED per TS canonical
    "INTAKE":        {"PLANNING", "FAILED"},
    "PLANNING":      {"CRITIQUE", "FAILED"},
    "CRITIQUE":      {"PLANNING", "SPECIFICATION", "FAILED"},
    "SPECIFICATION": {"CRITIQUE", "APPROVED", "FAILED"},
    "APPROVED":      {"SPECIFICATION", "QUEUED", "FAILED"},
    "QUEUED":        {"EXECUTING", "FAILED"},
    "EXECUTING":     {"COMPLETED", "FAILED"},
    "COMPLETED":     {"ARCHIVED"},   # NB: FAILED not valid from COMPLETED — terminal success
    "ARCHIVED":      set(),
    "FAILED":        set(),
}


# ── Transition validation ─────────────────────────────────────────────


def is_valid_transition(from_state: str, to_state: str) -> bool:
    """Check if a WRP state transition is valid per the adjacency matrix."""
    allowed = WRP_ADJACENCY_MATRIX.get(from_state, set())
    return to_state in allowed


# ── Receipt type to WRP state mapping ─────────────────────────────────
# Maps each Conduit receipt type to the WRP state it represents.

RECEIPT_TO_WRP_STATE: Dict[str, str] = {
    "PLANNING":       "INTAKE",
    "PLAN_CREATE":    "PLANNING",
    "CRITIQUE":       "CRITIQUE",
    "CRITIQUE_PASS":  "SPECIFICATION",
    "CRITIQUE_REJECT": "PLANNING",
    "IMPLEMENTATION": "EXECUTING",
    "CCNF_EXECUTION": "EXECUTING",
    "REVIEW":         "APPROVED",
    "REVIEW_PASS":    "COMPLETED",
    "REVIEW_REJECT":  "EXECUTING",
    "BLOCK":          "FAILED",
    "PLAN_BLOCK":     "FAILED",
    "API_LIMIT":      "FAILED",
    "HOLD":           "QUEUED",
    "REQUEUED":       "QUEUED",
    "CANCELLED":      "ARCHIVED",
    "ABANDONED":      "FAILED",
}
