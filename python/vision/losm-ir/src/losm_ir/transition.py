from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ValidationResult:
    """Immutable result of a transition validation.

    Attributes:
        allowed: True if from_state->to_state is a legal transition.
        reason: Human-readable explanation when not allowed, or None.
    """
    allowed: bool
    reason: Optional[str] = None

    def __iter__(self):
        return iter((self.allowed, self.reason))


# Canonical transition table.
# Keys: current state. Values: set of allowed next states.
# States: NEW, INTAKE, PLAN_GENERATION, PLAN_REVIEW, PLAN_APPROVAL_GATE,
#         SPEC_GENERATION, EXECUTION, VALIDATION, BLOCKED, COMPLETION, FAILED,
#         PROPOSED, TESTED, VALIDATED, STABLE
VALID_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"INTAKE", "FAILED", "BLOCKED"},
    "INTAKE": {"PLAN_GENERATION", "FAILED", "BLOCKED"},
    "PLAN_GENERATION": {"PLAN_REVIEW", "FAILED", "BLOCKED"},
    "PLAN_REVIEW": {"PLAN_APPROVAL_GATE", "PLAN_GENERATION", "FAILED", "BLOCKED"},
    "PLAN_APPROVAL_GATE": {"SPEC_GENERATION", "PLAN_GENERATION", "FAILED", "BLOCKED"},
    "SPEC_GENERATION": {"EXECUTION", "FAILED", "BLOCKED"},
    "EXECUTION": {"VALIDATION", "FAILED", "BLOCKED"},
    "VALIDATION": {"COMPLETION", "EXECUTION", "PLAN_GENERATION", "FAILED", "BLOCKED"},
    "BLOCKED": {"NEW", "INTAKE", "PLAN_GENERATION", "PLAN_REVIEW", "PLAN_APPROVAL_GATE",
                "SPEC_GENERATION", "EXECUTION", "VALIDATION", "FAILED", "COMPLETION"},
    "COMPLETION": set(),
    "FAILED": set(),
    "PROPOSED": {"TESTED", "DEPRECATED"},
    "TESTED": {"VALIDATED", "DEPRECATED"},
    "VALIDATED": {"STABLE", "DEPRECATED"},
    "STABLE": {"DEPRECATED"},
}


def validate_transition(from_state: str, to_state: str) -> ValidationResult:
    """Pure function. Given two states, returns whether the transition is legal.

    Args:
        from_state: The current state (must be a key in VALID_TRANSITIONS).
        to_state: The desired next state.

    Returns:
        ValidationResult with allowed=True/False and a reason for failures.
    """
    allowed_targets = VALID_TRANSITIONS.get(from_state)
    if allowed_targets is None:
        return ValidationResult(
            allowed=False,
            reason=f"Unknown from_state: '{from_state}'"
        )
    if to_state in allowed_targets:
        return ValidationResult(allowed=True)
    allowed_list = ", ".join(sorted(allowed_targets))
    return ValidationResult(
        allowed=False,
        reason=f"'{from_state}' -> '{to_state}' is not a legal transition. "
               f"Allowed targets: {allowed_list}"
    )


class TransitionError(Exception):
    """Raised when a caller attempts an invalid transition."""
    pass


__all__ = [
    "ValidationResult",
    "VALID_TRANSITIONS",
    "validate_transition",
    "TransitionError",
]