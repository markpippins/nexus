"""watch_gate.py — SOL-framed watch admission gate for cascade subscribers.

Replaces the inline SQL ``WHERE status='active'`` in ``_query_watches``
with a frame-scoped SOL proposition.  Each watch is evaluated against
``"watch may consume event"``, which checks:

- ``status = 'active'``  (SOL assertion — the admission boundary)
- ``turn_count < max_turns``  (pre-flight guard, when max_turns > 0)
- idle timeout not exceeded   (pre-flight guard, when idle_timeout_ms > 0)

The proposition is framed on ``execution_backend`` — the watch's own
backend, one of the V096 governed values (``operator`` / ``harness`` /
``freebuff``).  The shared build state is **never mutated on the
evaluation path** (inspector C2 / plan 8261648): every evaluation
deep-copies the built state into a per-call context and frames only the
copy, so any governed backend is in scope and an unknown backend fails
closed with ``context_mismatch`` — and concurrent evaluations can never
observe each other's frame.

Interface::

    from cascade.watch_gate import evaluate_watch_admission

    admitted, reason = evaluate_watch_admission(watch, now_ms=...)

Advisory record-then-act (PEB-forward Phase 1): every evaluation outcome
is recorded into ``peb.transactions`` via ``cascade.peb_admission``
before the caller acts.  Recording is best-effort and can never flip the
outcome.
"""

from __future__ import annotations

import copy
from typing import Any

_IMPORTED = False


def _import_solscript() -> None:
    global _IMPORTED
    if _IMPORTED:
        return
    import sys, os
    _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    _IMPORTED = True


# ── Fixed IDs ─────────────────────────────────────────────────────────

_WATCH_CONCEPT_ID      = "watch-gate:SessionWatch"
_STATUS_ATTR_ID        = "watch-gate:status"
_MAX_TURNS_ATTR_ID     = "watch-gate:max_turns"
_TURN_COUNT_ATTR_ID    = "watch-gate:turn_count"
_IDLE_TIMEOUT_ATTR_ID  = "watch-gate:idle_timeout_ms"
_LAST_ACTIVITY_ATTR_ID = "watch-gate:last_activity"
_BACKEND_DIM_ID        = "watch-gate:execution_backend"
_BACKEND_OPERATOR       = "watch-gate:backend:operator"
_BACKEND_HARNESS        = "watch-gate:backend:harness"
_BACKEND_FREEBUFF       = "watch-gate:backend:freebuff"
_PROPOSITION_ID        = "watch-gate:may-consume-event"
_FRAME_ID_PREFIX       = "watch-gate:may-consume-event:frame:"
_ENTITY_ID             = "watch-gate:watch-entity"
_ASSERTION_ID          = "watch-gate:assertion"
_STATUS_EQ_EXPR_ID     = "watch-gate:expr:status-eq"
_STATUS_REF_EXPR_ID    = "watch-gate:expr:status-ref"
_ACTIVE_LIT_EXPR_ID    = "watch-gate:expr:active-lit"

# The governed execution_backend values (V096 CHECK constraint on
# duality.session_watches).  A watch on any of these is in scope; any
# other value fails closed with context_mismatch.
_GOVERNED_BACKENDS = ("operator", "harness", "freebuff")
_BACKEND_VALUE_ID = {
    "operator": _BACKEND_OPERATOR,
    "harness": _BACKEND_HARNESS,
    "freebuff": _BACKEND_FREEBUFF,
}

# ── Shared immutable state (built once, never mutated) ───────────────
#
# C2 (inspector report 5d45f921 / architect plan 8261648): the evaluation
# path must never write module-level state. _STATE is built once by
# _build() and only ever READ afterward; each evaluation deep-copies it
# and mutates only its private copy. The pre-built PropositionFrameValue
# objects are handed to the copy by reference — the interpreter only
# reads them.

_STATE: dict[str, Any] | None = None


# ── Builder (matches sol_gate.py pattern exactly) ─────────────────────

def _build() -> None:
    global _STATE

    _import_solscript()
    from SOLScript.solscript import (                   # type: ignore[import-untyped]
        Concept, ConceptAttribute, Entity,
        Expression, ExpressionKind, Operator,
        FrameDimension, FrameDimensionValue,
        Proposition, PropositionFrameValue,
        ResolutionInterpreter, Rule, RuleType, Severity,
        Disposition,
    )

    interp = ResolutionInterpreter()

    # ── SessionWatch concept ────────────────────────────────────────

    watch_concept = Concept(
        id=_WATCH_CONCEPT_ID,
        name="SessionWatch",
        description="Duality session watch — subscribes to forum thread events",
    )
    interp.add_concept(watch_concept)

    status_attr = ConceptAttribute(
        id=_STATUS_ATTR_ID,
        concept_id=_WATCH_CONCEPT_ID,
        name="status",
        description="Watch lifecycle status",
        value_type="text",
        is_state_attribute=True,
        allowed_values=["active", "paused", "closed"],
    )
    watch_concept.attributes[status_attr.id] = status_attr

    max_turns_attr = ConceptAttribute(
        id=_MAX_TURNS_ATTR_ID,
        concept_id=_WATCH_CONCEPT_ID,
        name="max_turns",
        description="Maximum turns before watch expires (0 = unlimited)",
        value_type="integer",
        is_state_attribute=False,
    )
    watch_concept.attributes[max_turns_attr.id] = max_turns_attr

    turn_count_attr = ConceptAttribute(
        id=_TURN_COUNT_ATTR_ID,
        concept_id=_WATCH_CONCEPT_ID,
        name="turn_count",
        description="Turns consumed so far",
        value_type="integer",
        is_state_attribute=False,
    )
    watch_concept.attributes[turn_count_attr.id] = turn_count_attr

    idle_timeout_attr = ConceptAttribute(
        id=_IDLE_TIMEOUT_ATTR_ID,
        concept_id=_WATCH_CONCEPT_ID,
        name="idle_timeout_ms",
        description="Max idle time in milliseconds (0 = no timeout)",
        value_type="integer",
        is_state_attribute=False,
    )
    watch_concept.attributes[idle_timeout_attr.id] = idle_timeout_attr

    last_activity_attr = ConceptAttribute(
        id=_LAST_ACTIVITY_ATTR_ID,
        concept_id=_WATCH_CONCEPT_ID,
        name="last_activity",
        description="Timestamp of last activity",
        value_type="timestamp",
        is_state_attribute=False,
    )
    watch_concept.attributes[last_activity_attr.id] = last_activity_attr

    # ── execution_backend frame dimension (V96 governed values) ─────

    backend_dim = FrameDimension(
        id=_BACKEND_DIM_ID,
        name="execution_backend",
        description="Which execution backend the watch was created on",
        value_kind="governed_reference",
    )
    interp.add_frame_dimension(backend_dim)

    for value, vid in _BACKEND_VALUE_ID.items():
        interp.add_frame_dimension_value(FrameDimensionValue(
            id=vid,
            dimension_id=_BACKEND_DIM_ID,
            value=value,
        ))

    # ── Assertion: status = 'active' ────────────────────────────
    # Turn-count and idle-timeout checks remain as pre-flight guards
    # (those need conditional semantics; the SOL assertion covers
    # the frame-scoped status check).

    status_eq = Expression(
        id=_STATUS_EQ_EXPR_ID,
        kind=ExpressionKind.OPERATOR,
        operator=Operator.EQ,
        return_type="boolean",
        operands=[
            Expression(
                id=_STATUS_REF_EXPR_ID,
                kind=ExpressionKind.ATTRIBUTE_REF,
                return_type="text",
                attribute_id=_STATUS_ATTR_ID,
            ),
            Expression(
                id=_ACTIVE_LIT_EXPR_ID,
                kind=ExpressionKind.LITERAL,
                return_type="text",
                literal_value="active",
            ),
        ],
    )

    assertion = Rule(
        id=_ASSERTION_ID,
        name="watch is active",
        rule_type=RuleType.INVARIANT,
        severity=Severity.HARD,
        expression=status_eq,
        concept_id=_WATCH_CONCEPT_ID,
    )
    interp.rules[assertion.id] = assertion

    # ── Proposition ─────────────────────────────────────────────────

    prop = Proposition(
        id=_PROPOSITION_ID,
        title="watch may consume event",
        description="An active session watch may consume cascade events",
        asset_concept_id=_WATCH_CONCEPT_ID,
        subject_entity_id=_ENTITY_ID,
        disposition=Disposition.PENDING,
        assertions=[assertion],
    )
    interp.add_proposition(prop)

    # One frame value per governed backend. The interpreter ANDs every
    # frame value, so a single proposition can only be scoped to ONE
    # value per dimension at a time — we swap the active frame value per
    # call to the watch's own backend (see evaluate_watch_admission).
    pfv_by_backend: dict[str, PropositionFrameValue] = {}
    for value, value_id in _BACKEND_VALUE_ID.items():
        pfv_by_backend[value] = PropositionFrameValue(
            id=f"{_FRAME_ID_PREFIX}{value}",
            proposition_id=_PROPOSITION_ID,
            dimension_id=_BACKEND_DIM_ID,
            reference_value_id=value_id,
            scalar_value=None,
        )

    _STATE = {
        "interp": interp,
        "prop": prop,
        "pfv_by_backend": pfv_by_backend,
    }


# ── Public API ────────────────────────────────────────────────────────

def evaluate_watch_admission(
    watch: dict[str, Any] | None,
    now_ms: int | None = None,
    enforce_preflights: bool = True,
) -> tuple[bool, str]:
    """Evaluate whether a watch may consume an event.

    Returns ``(admitted, reason)``.

    Args:
        watch: Dict from _query_watches with keys: status, max_turns,
               turn_count, idle_timeout_ms, last_activity, execution_backend.
        now_ms: Current epoch milliseconds for idle timeout check.
        enforce_preflights: When True (default), the deterministic
            turn-count and idle-timeout guards are enforced.  Callers that
            delegate those to the conversation coordinator (which closes
            the watch with a guarded transition) pass False so only the
            frame-scoped SOL admission boundary decides.
    """
    outcome = _evaluate_watch_admission(
        watch, now_ms=now_ms, enforce_preflights=enforce_preflights,
    )
    _record_admission(watch, outcome)
    return outcome


def _evaluate_watch_admission(
    watch: dict[str, Any] | None,
    now_ms: int | None = None,
    enforce_preflights: bool = True,
) -> tuple[bool, str]:
    """Internal evaluation core (wrapped by evaluate_watch_admission)."""
    if _STATE is None:
        _build()

    from SOLScript.solscript import Entity  # type: ignore[import-untyped]

    assert _STATE is not None

    if watch is None:
        return False, "no watch provided"

    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)

    # ── Pre-flight: turn-count gate ──────────────────────────────
    if enforce_preflights:
        max_turns = int(watch.get("max_turns") or 0)
        turn_count = int(watch.get("turn_count") or 0)
        if max_turns > 0 and turn_count >= max_turns:
            return False, f"turn limit exhausted ({turn_count}/{max_turns})"

        # ── Pre-flight: idle timeout ─────────────────────────────
        idle_ms = int(watch.get("idle_timeout_ms") or 0)
        last_activity = watch.get("last_activity")
        if idle_ms > 0 and last_activity is not None:
            import datetime
            if isinstance(last_activity, str):
                last_activity = datetime.datetime.fromisoformat(
                    last_activity.replace("Z", "+00:00")
                )
            elif not isinstance(last_activity, datetime.datetime):
                last_activity = None
            if last_activity is not None:
                elapsed = now_ms - int(last_activity.timestamp() * 1000)
                if elapsed > idle_ms:
                    return False, f"idle timeout exceeded ({elapsed}ms > {idle_ms}ms)"

    # ── Build entity ─────────────────────────────────────────────
    entity = Entity(
        id=_ENTITY_ID,
        concept_id=_WATCH_CONCEPT_ID,
        attributes={
            "status":          str(watch.get("status", "")),
            "max_turns":       str(int(watch.get("max_turns") or 0)),
            "turn_count":      str(int(watch.get("turn_count") or 0)),
            "idle_timeout_ms": str(int(watch.get("idle_timeout_ms") or 0)),
            "last_activity":   str(now_ms),
        },
        external_id=str(watch.get("id", "")),
    )

    # ── Frame the proposition to the watch's own backend ─────────
    backend = str(watch.get("execution_backend") or "")
    pfv = _STATE["pfv_by_backend"].get(backend)
    if pfv is None:
        # Unknown / ungoverned backend — fail closed.  The SQL WHERE
        # already excludes non-active rows; this is the frame-scoped
        # admission boundary for anything that slips past it.
        return False, f"context_mismatch (backend={backend or 'unknown'})"

    # ── Per-call evaluation context (C2) ─────────────────────────
    # The shared _STATE is never mutated: every call deep-copies it,
    # frames the COPY's proposition to this call's backend, and
    # registers the entity on the COPY's interpreter. Concurrent
    # evaluations can therefore never observe each other's frame or
    # entity.
    try:
        state = copy.deepcopy(_STATE)
    except Exception:  # noqa: BLE001 — fail closed, never raise out of a gate
        return False, "evaluation context clone failed"
    interp = state["interp"]
    prop = state["prop"]
    prop.frame_values = [pfv]
    interp.entities[_ENTITY_ID] = entity
    context = {"execution_backend": backend}

    # ── SOL evaluation ───────────────────────────────────────────
    try:
        disposition, all_passed, context_status = interp.evaluate_proposition(
            prop,
            context=context,
        )
    except Exception:
        return False, "SOL evaluation failed"

    if context_status == "context_required":
        return False, "context_required"
    if context_status == "context_mismatch":
        return False, f"context_mismatch (backend={backend})"

    if all_passed:
        return True, ""

    status = str(watch.get("status", ""))
    return False, f"assertion failed (status={status})"


def evaluate_watch_admission_bool(
    watch: dict[str, Any] | None,
    now_ms: int | None = None,
    enforce_preflights: bool = True,
) -> bool:
    """Boolean-only variant."""
    admitted, _ = evaluate_watch_admission(
        watch, now_ms=now_ms, enforce_preflights=enforce_preflights,
    )
    return admitted


def _record_admission(
    watch: dict[str, Any] | None,
    outcome: tuple[bool, str],
) -> None:
    """Advisory record-then-act (PEB-forward Phase 1).

    Best-effort: a recording failure never flips the gate outcome and
    never raises (see cascade.peb_admission.record_gate_outcome)."""
    if watch is None:
        return
    admitted, reason = outcome
    try:
        from cascade.peb_admission import record_gate_outcome
    except Exception:  # noqa: BLE001 — advisory path
        return
    try:
        record_gate_outcome(
            gate="watch_gate.evaluate_watch_admission",
            entity_id=str(watch.get("id", "")),
            admitted=admitted,
            reason=reason,
            payload={"watch": watch, "reason": reason},
        )
    except Exception:  # noqa: BLE001 — advisory path must never raise
        pass