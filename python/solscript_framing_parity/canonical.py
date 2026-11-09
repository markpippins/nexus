"""Canonical, comparison-safe outcome values for the framing parity guard.

Each runtime returns a different carrier type (Python tuple, TypeScript
``[Disposition|null, boolean, string]``, SQL ``(text, boolean, text)``) and
reports refusals differently (Python/TypeScript return ``False`` for
``all_passed``; SQL returns ``NULL``). To compare them honestly we reduce every
result to one canonical shape *before* comparison:

* ``{"kind": "outcome", "disposition": ..., "all_passed": ..., "context_status": ...}``
* ``{"kind": "error", "error": "<error-class>"}``

Error messages carry runtime-specific prefixes and prose, so only the error
*class* is compared (see :func:`classify_error`). The raw message is kept on
the side for diagnostics and never participates in the parity comparison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# Error classes shared by all three runtimes.
UNKNOWN_FRAME_DIMENSION = "unknown_frame_dimension"
CONTEXT_NOT_OBJECT = "context_not_object"
UNRECOGNIZED_VALUE_KIND = "unrecognized_value_kind"
UNCLASSIFIED = "unclassified_error"


class RuntimeUnavailable(RuntimeError):
    """A runtime adapter cannot attest its rows because its toolchain is absent.

    Distinct from a parity failure: nothing was proven wrong, the proof simply
    could not be produced. The guard maps this to its own exit code so that a
    CI job never reads "cannot attest" as "the contract is violated".
    """

#: Substrings that identify each error class in the three implementations.
#: Python raises ``ValueError``, TypeScript raises ``Error`` and PL/pgSQL
#: raises ``EXCEPTION``; all three emit the same phrase for the same class.
_ERROR_SIGNATURES = (
    ("names no known frame_dimension", UNKNOWN_FRAME_DIMENSION),
    ("must be a JSON object", CONTEXT_NOT_OBJECT),
    ("must be a dict", CONTEXT_NOT_OBJECT),
    ("has unrecognized value_kind", UNRECOGNIZED_VALUE_KIND),
)


def classify_error(message: object) -> str:
    """Reduce a runtime error message to a stable, comparable error class."""
    text = str(message or "")
    for signature, error_class in _ERROR_SIGNATURES:
        if signature in text:
            return error_class
    return UNCLASSIFIED


@dataclass(frozen=True)
class Outcome:
    """Canonical result of one ``evaluate_proposition`` call."""

    disposition: Optional[str] = None
    all_passed: bool = False
    context_status: Optional[str] = None
    error: Optional[str] = None
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        if self.error is not None and self.disposition is not None:
            raise ValueError("an Outcome is either an outcome or an error, not both")

    @property
    def kind(self) -> str:
        return "error" if self.error is not None else "outcome"

    def to_json(self) -> dict[str, Any]:
        if self.error is not None:
            return {"kind": "error", "error": self.error}
        return {
            "kind": "outcome",
            "disposition": self.disposition,
            "all_passed": self.all_passed,
            "context_status": self.context_status,
        }

    def comparison_key(self) -> str:
        """Stable string used for equality/difference checks."""
        return json.dumps(self.to_json(), sort_keys=True)

    def render(self) -> str:
        if self.error is not None:
            return f"error:{self.error}"
        disposition = self.disposition if self.disposition is not None else "null"
        passed = "pass" if self.all_passed else "fail"
        return f"{disposition}/{passed}/{self.context_status}"


def outcome_from_python(result: tuple) -> Outcome:
    """Canonicalise a Python ``(disposition, all_passed, context_status)`` tuple."""
    disposition, all_passed, context_status = result
    value = getattr(disposition, "value", disposition)
    return Outcome(
        disposition=None if value is None else str(value),
        all_passed=bool(all_passed),
        context_status=None if context_status is None else str(context_status),
    )
