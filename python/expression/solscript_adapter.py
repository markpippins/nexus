"""Expression E7 — live SOLScript ResolutionInterpreter adapter.

Replaces the E4 callback-only seam with the real in-memory
``ResolutionInterpreter`` (python/SOLScript). The adapter:

- builds a pure evaluation callable bound to one interpreter instance;
- maps the interpreter's ``(Disposition, all_passed, context_status)``
  triple into the E4 wire outcome vocabulary;
- treats context-gate outcomes (``context_required``,
  ``context_mismatch``) as explicit ``unevaluable``/``refused`` results
  instead of exceptions;
- never mutates interpreter state, never invokes transitions, and never
  persists — the E6 writer remains the only persistence path.

The interpreter is loaded by the caller (database_loader or in-memory
fixtures); this adapter owns no interpreter construction, keeping
evaluation side-effect free from Expression's perspective.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .evaluator import evaluate_bundle, replay_evaluation

# Make python/SOLScript importable when the workspace layout is present.
_SOLSCRIPT_ROOT = Path(__file__).resolve().parents[1] / "SOLScript"
if (_SOLSCRIPT_ROOT / "solscript" / "interpreter.py").exists():
    _solscript_path = str(_SOLSCRIPT_ROOT)
    if _solscript_path not in sys.path:
        sys.path.insert(0, _solscript_path)

INTERPRETER_REVISION = "solscript-resolution-interpreter-v33-e84"


def _disposition_to_outcome(disposition: Any) -> str:
    """Map a live Disposition enum/value into the E4 wire vocabulary."""
    name = getattr(disposition, "name", None) or str(disposition)
    value = getattr(disposition, "value", None) or name
    mapping = {
        "ASSERTED": "asserted",
        "Asserted": "asserted",
        "DISPUTED": "disputed",
        "Disputed": "disputed",
        "REJECTED": "rejected",
        "Rejected": "rejected",
        "PENDING": "pending",
        "Pending": "pending",
        "PROPOSED": "advisory",
        "Proposed": "advisory",
        "STALE": "stale",
        "Stale": "stale",
        "RETRACTED": "refused",
        "Retracted": "refused",
    }
    outcome = mapping.get(name) or mapping.get(value)
    if outcome is None:
        # Fail closed on unknown dispositions.
        return "refused"
    return outcome


_CONTEXT_STATUS_OUTCOME = {
    "not_scoped": None,  # type has no required dimensions; evaluate on merits
    "unframed_required": "unevaluable",
    "scoped": None,  # fall through to the disposition
    "context_required": "unevaluable",
    "context_mismatch": "refused",
}


def solscript_evaluator(
    interpreter: Any,
    *,
    context: dict[str, Any] | None = None,
    interpreter_revision: str = INTERPRETER_REVISION,
) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """Build an E4-compatible evaluator bound to a live interpreter.

    The returned callable has the exact shape E4 expects: it takes the
    (deep-copied) proposition candidate and the pinned request and returns
    a normalized result dictionary. It never mutates the interpreter.
    """
    evaluate_proposition = interpreter.evaluate_proposition

    def evaluator(candidate: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        prop_id = candidate.get("proposition_id") or ""
        prop = None
        getter = getattr(interpreter, "get_proposition", None)
        if callable(getter):
            prop = getter(prop_id)
        if prop is None:
            return {
                "disposition": "pending",
                "reason_code": "proposition_not_in_interpreter",
                "reason": (
                    f"Proposition {prop_id!r} is not registered in the live "
                    "interpreter; register it via the ontology loader first."
                ),
            }
        try:
            disposition, all_passed, context_status = evaluate_proposition(
                prop, deepcopy(context)
            )
        except ValueError as exc:
            # Unknown context key (or malformed context) is fail-closed.
            return {
                "disposition": "refused",
                "reason_code": "invalid_context",
                "reason": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 — evaluation must not crash the envelope
            return {
                "disposition": "refused",
                "reason_code": "interpreter_error",
                "reason": str(exc),
            }

        gated = _CONTEXT_STATUS_OUTCOME.get(context_status)
        if gated is not None:
            return {
                "disposition": gated,
                "reason_code": f"context_{context_status}",
                "reason": f"Context gate returned {context_status!r}",
            }

        outcome = _disposition_to_outcome(disposition)
        result: dict[str, Any] = {
            "disposition": outcome,
            "authority_status": "advisory" if outcome in {"asserted", "rejected", "disputed"} else "evaluation_only",
        }
        if outcome in {"asserted", "disputed"}:
            result["all_passed"] = bool(all_passed)
        return result

    return evaluator


def evaluate_bundle_with_interpreter(
    bundle: dict[str, Any],
    interpreter: Any,
    *,
    read_set: dict[str, Any],
    ontology_revision: str,
    authority_owner: str = "resolution",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one E4 evaluation envelope through the live interpreter."""
    return evaluate_bundle(
        bundle,
        read_set=read_set,
        evaluator=solscript_evaluator(interpreter, context=context),
        evaluator_revision=INTERPRETER_REVISION,
        ontology_revision=ontology_revision,
        authority_owner=authority_owner,
    )


def replay_bundle_with_interpreter(
    bundle: dict[str, Any],
    prior: dict[str, Any],
    interpreter: Any,
    *,
    read_set: dict[str, Any],
    ontology_revision: str,
    authority_owner: str = "resolution",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic replay through the live interpreter."""
    return replay_evaluation(
        bundle,
        prior,
        read_set=read_set,
        evaluator=solscript_evaluator(interpreter, context=context),
        evaluator_revision=INTERPRETER_REVISION,
        ontology_revision=ontology_revision,
        authority_owner=authority_owner,
    )
