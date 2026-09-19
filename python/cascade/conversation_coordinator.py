"""conversation_coordinator.py — Determines interactive turn outcomes.

Modeled on cascade/coordinator.py: merges conversation state dimensions
and applies organizational doctrine to decide whether a conversation
should continue, delegate to another role, or close.

Doctrine rules:
    R1 — Lease governance (hard stop):
         lease.remaining_units <= 0 OR lease.expired → CLOSED
         (granular since #7: CLOSED_LEASE_REVOKED / _EXHAUSTED / _EXPIRED
         from the persisted release_reason; close codes in #8)
    R2 — Turn limit (prevent infinite loops):
         turn_count >= max_turns → CLOSED
    R3 — Agent-declared completion (explicit close signal):
         response contains CONVERSATION_CLOSED → CLOSED_BY_AGENT
    R4 — Idle timeout (no user activity):
         idle_ms > idle_timeout_ms → CLOSED_IDLE
    R5 — Delegation detected (agent-to-agent handoff):
         response contains DELEGATE <role>: → DELEGATE
    R6 — Open questions remain → CONTINUE
    Default — conservative close (don't popcorn) → CLOSED_NATURAL
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# Outcome constants
OUTCOME_CONTINUE        = "CONTINUE"
OUTCOME_DELEGATE        = "DELEGATE"
OUTCOME_CLOSED          = "CLOSED"
OUTCOME_CLOSED_LEASE    = "CLOSED_LEASE"          # legacy aggregate (no release_reason)
OUTCOME_CLOSED_LEASE_REVOKED    = "CLOSED_LEASE_REVOKED"
OUTCOME_CLOSED_LEASE_EXHAUSTED  = "CLOSED_LEASE_EXHAUSTED"
OUTCOME_CLOSED_LEASE_EXPIRED    = "CLOSED_LEASE_EXPIRED"
OUTCOME_CLOSED_TURNS    = "CLOSED_TURNS"
OUTCOME_CLOSED_AGENT    = "CLOSED_BY_AGENT"
OUTCOME_CLOSED_IDLE     = "CLOSED_IDLE"
OUTCOME_CLOSED_NATURAL  = "CLOSED_NATURAL"

TERMINAL_OUTCOMES = frozenset([
    OUTCOME_CLOSED, OUTCOME_CLOSED_LEASE,
    OUTCOME_CLOSED_LEASE_REVOKED, OUTCOME_CLOSED_LEASE_EXHAUSTED,
    OUTCOME_CLOSED_LEASE_EXPIRED,
    OUTCOME_CLOSED_TURNS, OUTCOME_CLOSED_AGENT, OUTCOME_CLOSED_IDLE,
    OUTCOME_CLOSED_NATURAL,
])

# Structured close codes (f0706646 #8) — controlled vocabulary persisted on
# duality.session_watches.closed_reason (V130) and carried in the
# watch.status envelope so closure is queryable without parsing free text.
CLOSE_CODE_LEASE_REVOKED   = "lease_revoked"
CLOSE_CODE_LEASE_EXHAUSTED = "lease_exhausted"
CLOSE_CODE_LEASE_EXPIRED   = "lease_expired"
CLOSE_CODE_TURNS           = "turns"
CLOSE_CODE_AGENT           = "agent"
CLOSE_CODE_IDLE            = "idle"
CLOSE_CODE_NATURAL         = "natural"
# Explicit no-granularity code (V185, inspector G3): the legacy aggregate
# OUTCOME_CLOSED_LEASE covers revoked/exhausted/expired equally, and
# session_watches persists no release_reason evidence — 'unknown' says
# exactly that instead of a false specific claim.
CLOSE_CODE_UNKNOWN         = "unknown"

# Pattern: DELEGATE <role>: <instruction>
_DELEGATE_RE = re.compile(r'DELEGATE\s+(\w[\w-]*)\s*:\s*(.+)', re.IGNORECASE)

# Pattern: CONVERSATION_CLOSED (agent declares completion)
_CLOSED_RE = re.compile(r'\bCONVERSATION_CLOSED\b', re.IGNORECASE)

# Pattern: signal that the agent is asking a question (→ continue)
_QUESTION_RE = re.compile(r'\?\s*$', re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class ConversationResolution:
    """Result of applying doctrine to conversation state."""
    outcome: str
    reason: str
    delegate_target: str | None = None    # set when outcome == DELEGATE
    delegate_instruction: str | None = None


def is_terminal(outcome: str) -> bool:
    """True when the outcome closes the thread."""
    return outcome in TERMINAL_OUTCOMES


def parse_delegation(response_text: str) -> dict[str, str] | None:
    """Extract DELEGATE <role>: <instruction> from agent output.

    Returns {'target': role, 'instruction': text} or None.
    """
    m = _DELEGATE_RE.search(response_text)
    if m:
        return {"target": m.group(1).lower(), "instruction": m.group(2).strip()}
    return None


def has_open_questions(response_text: str) -> bool:
    """True when the agent's response appears to be asking for input."""
    return bool(_QUESTION_RE.search(response_text))


def resolve_conversation_outcome(
    watch: dict[str, Any],
    lease: dict[str, Any] | None,
    last_agent_response: str | None,
    now_ms: int | None = None,
) -> ConversationResolution:
    """Apply doctrine to conversation state.

    Args:
        watch: Row from duality.session_watches (thread_id, role, max_turns,
               turn_count, idle_timeout_ms, last_activity, status).
        lease: Row from tackle.role_leases (id, role, budget_units,
               consumed_units, status, window_end, expires_at).
        last_agent_response: The agent's last response text (may be None
                             if this is the first turn).
        now_ms: Current timestamp in ms (default: time.time()*1000).

    Returns:
        ConversationResolution with outcome and reason.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # ── R1: Lease governance — hard stop ──────────────────────────
    if lease is None:
        return ConversationResolution(
            outcome=OUTCOME_CLOSED_LEASE,
            reason="No active role lease",
        )

    lease_status = lease.get("status", "")
    if lease_status in ("EXPIRED", "RELEASED"):
        # f0706646 #7 — separate the release paths: the API layer already
        # persists release_reason ∈ {revoked, exhausted, expired} on the
        # lease row; surface it as distinct structured outcomes instead of a
        # single aggregate. Missing release_reason (e.g. an old row or a
        # synthetic lease) falls back to the legacy aggregated outcome.
        release_reason = lease.get("release_reason") or ""
        r = release_reason.lower()
        if lease_status == "RELEASED" and r == "revoked":
            return ConversationResolution(
                outcome=OUTCOME_CLOSED_LEASE_REVOKED,
                reason="Role lease revoked",
            )
        if r == "exhausted":
            return ConversationResolution(
                outcome=OUTCOME_CLOSED_LEASE_EXHAUSTED,
                reason="Role lease exhausted (released)",
            )
        if r == "expired" or lease_status == "EXPIRED":
            return ConversationResolution(
                outcome=OUTCOME_CLOSED_LEASE_EXPIRED,
                reason=f"Role lease expired (status={lease_status})",
            )
        return ConversationResolution(
            outcome=OUTCOME_CLOSED_LEASE,
            reason=f"Role lease status={lease_status}",
        )

    budget = lease.get("budget_units") or 0
    consumed = lease.get("consumed_units") or 0
    remaining = max(0, budget - consumed)
    if budget > 0 and remaining <= 0:
        return ConversationResolution(
            outcome=OUTCOME_CLOSED_LEASE_EXHAUSTED,
            reason=f"Role lease exhausted ({consumed}/{budget} units consumed)",
        )

    expires_at = lease.get("expires_at")
    if expires_at:
        if isinstance(expires_at, str):
            expires_at = datetime.datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            )
        if expires_at.timestamp() * 1000 < now_ms:
            return ConversationResolution(
                outcome=OUTCOME_CLOSED_LEASE_EXPIRED,
                reason=f"Role lease expired at {expires_at.isoformat()}",
            )

    # ── R2: Turn limit — prevent infinite loops ──────────────────
    max_turns = watch.get("max_turns", 20)
    turn_count = watch.get("turn_count", 0)
    if turn_count >= max_turns:
        return ConversationResolution(
            outcome=OUTCOME_CLOSED_TURNS,
            reason=f"Turn limit reached ({turn_count}/{max_turns})",
        )

    # ── R3: Agent-declared completion ────────────────────────────
    if last_agent_response and _CLOSED_RE.search(last_agent_response):
        return ConversationResolution(
            outcome=OUTCOME_CLOSED_AGENT,
            reason="Agent declared CONVERSATION_CLOSED",
        )

    # ── R4: Idle timeout — no user activity ──────────────────────
    idle_timeout_ms = watch.get("idle_timeout_ms", 300000)
    last_activity = watch.get("last_activity")
    if last_activity:
        if isinstance(last_activity, str):
            last_activity = datetime.datetime.fromisoformat(
                last_activity.replace("Z", "+00:00")
            )
        idle_ms = now_ms - (last_activity.timestamp() * 1000)
        if idle_ms > idle_timeout_ms:
            return ConversationResolution(
                outcome=OUTCOME_CLOSED_IDLE,
                reason=f"Idle timeout ({idle_ms}ms > {idle_timeout_ms}ms)",
            )

    # ── R5: Delegation detected ──────────────────────────────────
    if last_agent_response:
        delegation = parse_delegation(last_agent_response)
        if delegation:
            return ConversationResolution(
                outcome=OUTCOME_DELEGATE,
                reason=f"Agent delegated to {delegation['target']}",
                delegate_target=delegation["target"],
                delegate_instruction=delegation["instruction"],
            )

    # ── R6: Open questions remain — continue ─────────────────────
    if last_agent_response and has_open_questions(last_agent_response):
        return ConversationResolution(
            outcome=OUTCOME_CONTINUE,
            reason="Open questions remain in agent response",
        )

    # ── Default: conservative close — don't popcorn ──────────────
    return ConversationResolution(
        outcome=OUTCOME_CLOSED_NATURAL,
        reason="Conversation reached natural end (no open questions, no delegation)",
    )


# ── Public API ──────────────────────────────────────────────────────

def should_continue(resolution: ConversationResolution) -> bool:
    """True when the conversation should keep going."""
    return resolution.outcome == OUTCOME_CONTINUE


def should_delegate(resolution: ConversationResolution) -> bool:
    """True when the conversation hands off to another role."""
    return resolution.outcome == OUTCOME_DELEGATE


# ── Structured close codes (f0706646 #8) ─────────────────────────────

_CLOSE_CODE_BY_OUTCOME = {
    OUTCOME_CLOSED_LEASE_REVOKED:   CLOSE_CODE_LEASE_REVOKED,
    OUTCOME_CLOSED_LEASE_EXHAUSTED: CLOSE_CODE_LEASE_EXHAUSTED,
    OUTCOME_CLOSED_LEASE_EXPIRED:   CLOSE_CODE_LEASE_EXPIRED,
    OUTCOME_CLOSED_TURNS:           CLOSE_CODE_TURNS,
    OUTCOME_CLOSED_AGENT:           CLOSE_CODE_AGENT,
    OUTCOME_CLOSED_IDLE:            CLOSE_CODE_IDLE,
    OUTCOME_CLOSED_NATURAL:         CLOSE_CODE_NATURAL,
    # Legacy aggregates (pre-#7, no granularity):
    #   OUTCOME_CLOSED -> 'natural' (conservative, unchanged)
    #   OUTCOME_CLOSED_LEASE -> 'unknown' (V185, inspector G3): the lease
    #   aggregate covers revoked/exhausted/expired equally; persisting
    #   'lease_expired' asserted a specificity the data never had.
    OUTCOME_CLOSED:                 CLOSE_CODE_NATURAL,
    OUTCOME_CLOSED_LEASE:           CLOSE_CODE_UNKNOWN,
}


def close_code_for_outcome(outcome: str) -> str:
    """Map a terminal outcome to its structured close code.

    Vocab (V130 CHECK as extended by V185): lease_revoked, lease_exhausted,
    lease_expired, turns, agent, idle, natural, unknown. Unknown outcomes
    fall back to ``natural``. The legacy aggregates carry no granularity:
    OUTCOME_CLOSED -> ``natural`` (conservative), OUTCOME_CLOSED_LEASE ->
    ``unknown`` (V185, inspector G3 — no false specificity in the
    governance column).
    """
    return _CLOSE_CODE_BY_OUTCOME.get(outcome, CLOSE_CODE_NATURAL)
