"""The shared framing matrix and the pinned cross-runtime parity contract.

One matrix, three runtimes. Every case is fed to Python, SQL and TypeScript with
identical inputs (same frame dimensions, same instance frame values, same
caller context). Expected results are pinned here: either a single outcome that
*all three* runtimes must produce (``Agree``) or an explicit per-runtime map of
known divergences (``Diverge``) that cites a follow-up identifier from
:data:`FOLLOW_UPS`.

Nothing in this module may be relaxed silently. If a runtime starts agreeing
where a divergence is pinned — or diverging where agreement is pinned — the
guard fails and the parity contract has to be re-verified by a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .canonical import Outcome, UNKNOWN_FRAME_DIMENSION

#: Runtime identifiers, in report order.
RUNTIMES: Tuple[str, ...] = ("python", "typescript", "sql")

RUNTIME_LABELS: Mapping[str, str] = {
    "python": "Python (E8.4 type-level gate)",
    "typescript": "TypeScript (instance frame-count gate)",
    "sql": "SQL (resolution.evaluate_proposition, v32)",
}


# ── Fixed fixture ────────────────────────────────────────────────────────────
# Deterministic ids so every runtime builds the same graph. SQL needs real
# uuid values; Python and TypeScript accept the same strings unchanged.
FIXTURE: Mapping[str, Any] = {
    "semantic_type_id": "aaaaaaaa-0000-4000-8000-000000000001",
    "entity_id": "aaaaaaaa-0000-4000-8000-000000000002",
    "asset_concept_id": "aaaaaaaa-0000-4000-8000-000000000003",
    "proposition_id": "aaaaaaaa-0000-4000-8000-000000000004",
}

#: Frame dimensions registered in every runtime before evaluation.
#: ``values`` maps a governed-reference *value name* to its deterministic id.
DIMENSIONS: Mapping[str, Mapping[str, Any]] = {
    "channel": {
        "id": "aaaaaaaa-0000-4000-8000-000000000010",
        "name": "channel",
        "value_kind": "governed_reference",
        "scalar_type": None,
        "values": {
            "interactive": "aaaaaaaa-0000-4000-8000-000000000020",
            "opencode": "aaaaaaaa-0000-4000-8000-000000000021",
        },
    },
    "tenant": {
        "id": "aaaaaaaa-0000-4000-8000-000000000011",
        "name": "tenant",
        "value_kind": "governed_reference",
        "scalar_type": None,
        "values": {
            "acme": "aaaaaaaa-0000-4000-8000-000000000022",
        },
    },
}


# ── Follow-ups (residual divergences, deliberately NOT fixed here) ───────────
FOLLOW_UPS: Mapping[str, Mapping[str, str]] = {
    "F1": {
        "title": "`unframed_required` is a Python-only context status",
        "runtimes": "python vs typescript, sql",
        "summary": (
            "The Python E8.4 gate evaluates *type-level* required dimensions "
            "(semantic_type_required_dimensions) before any context check and "
            "reports the dedicated status `unframed_required` when the "
            "instance does not commit every required dimension. SQL v32 and "
            "the TypeScript interpreter have no type-level requirement input "
            "at all: they gate on the *instance* frame count, so they report "
            "`context_required` for the same proposition."
        ),
        "next_step": (
            "Decide whether SQL/TypeScript gain type-level required-dimension "
            "inputs, or whether Python retires the `unframed_required` label "
            "and folds it into `context_required`. Either change is an "
            "admission-affecting behaviour change and must not ride along "
            "with this shadow-only guard."
        ),
        "status": "open",
    },
    "F2": {
        "title": "Unknown context keys on unframed propositions",
        "runtimes": "python vs typescript, sql",
        "summary": (
            "When the caller supplies context for a proposition with zero "
            "instance frame values, Python validates the keys unconditionally "
            "and raises on an unknown dimension name. SQL and TypeScript "
            "short-circuit at 'no frame values' -> `not_scoped` and never "
            "inspect the context, so an unknown key is silently ignored."
        ),
        "next_step": (
            "Align the three runtimes on one rule (validate-always or "
            "validate-only-when-framed). This is a caller-contract change and "
            "needs its own unit; the guard currently asserts both behaviours "
            "so neither can change unnoticed."
        ),
        "status": "open",
    },
    "F3": {
        "title": "SQL D2 framed-union-required open question",
        "runtimes": "python vs typescript, sql",
        "summary": (
            "SQL v32 (and the TypeScript port) require the whole *union* of "
            "declared instance frame dimensions to be supplied and matched by "
            "the caller: a supplementary frame value therefore refuses with "
            "`context_required` when context is absent, and relabels the "
            "proposition `scoped` when context matches. Python's E8.4 gate "
            "requires only *type-level required* dimensions; supplementary "
            "instance values are optional (absent context -> `not_scoped`, "
            "matching context -> still `not_scoped`). The disposition agrees "
            "for the matching case; the status label does not."
        ),
        "next_step": (
            "Resolve the D2 open question: is the gate framed-union-required "
            "(SQL/TS) or type-required (Python)? Whichever wins, the losing "
            "runtimes need an explicit migration; until then this guard pins "
            "the divergence rather than averaging it away."
        ),
        "status": "open",
    },
}


# ── Matrix model ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class InstanceFrame:
    """One instance-level frame commitment (``proposition_frame_value`` row)."""

    dimension: str
    reference_value: str


@dataclass(frozen=True)
class Agree:
    """All three runtimes must produce exactly this outcome."""

    outcome: Outcome
    kind: str = field(default="agree", init=False)


@dataclass(frozen=True)
class Diverge:
    """The runtimes are known (and asserted) to disagree; see ``follow_up``."""

    outcomes: Mapping[str, Outcome]
    follow_up: str
    kind: str = field(default="divergence", init=False)

    def __post_init__(self) -> None:
        missing = [rt for rt in RUNTIMES if rt not in self.outcomes]
        if missing:
            raise ValueError(f"divergence is missing runtimes: {missing}")
        if self.follow_up not in FOLLOW_UPS:
            raise ValueError(f"unknown follow-up identifier: {self.follow_up}")
        distinct = {outcome.comparison_key() for outcome in self.outcomes.values()}
        if len(distinct) == 1:
            raise ValueError("a divergence expectation must actually diverge")


@dataclass(frozen=True)
class Case:
    """One row of the shared matrix."""

    id: str
    description: str
    required_dimensions: Tuple[str, ...]
    instance_frames: Tuple[InstanceFrame, ...]
    context: Optional[Mapping[str, str]]
    expectation: Any  # Agree | Diverge
    plan_clause: str = ""

    def expected_for(self, runtime: str) -> Outcome:
        if self.expectation.kind == "agree":
            return self.expectation.outcome
        return self.expectation.outcomes[runtime]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "required_dimensions": list(self.required_dimensions),
            "instance_frames": [
                {
                    "dimension": frame.dimension,
                    "reference_value": frame.reference_value,
                }
                for frame in self.instance_frames
            ],
            "context": dict(self.context) if self.context is not None else None,
        }


def _ch(value: str = "interactive") -> InstanceFrame:
    return InstanceFrame(dimension="channel", reference_value=value)


MATRIX: Tuple[Case, ...] = (
    Case(
        id="zero_required_unframed",
        description="Zero required dimensions, no instance frame values, no context",
        required_dimensions=(),
        instance_frames=(),
        context=None,
        expectation=Agree(Outcome("Asserted", True, "not_scoped")),
        plan_clause="zero-required unframed",
    ),
    Case(
        id="supplementary_absent_context",
        description=(
            "Zero required dimensions, one supplementary instance frame value, "
            "no caller context"
        ),
        required_dimensions=(),
        instance_frames=(_ch(),),
        context=None,
        expectation=Diverge(
            {
                "python": Outcome("Asserted", True, "not_scoped"),
                "typescript": Outcome(None, False, "context_required"),
                "sql": Outcome(None, False, "context_required"),
            },
            follow_up="F3",
        ),
        plan_clause="supplementary absent context",
    ),
    Case(
        id="supplementary_matching_context",
        description=(
            "Zero required dimensions, one supplementary instance frame value, "
            "matching caller context"
        ),
        required_dimensions=(),
        instance_frames=(_ch(),),
        context={"channel": "interactive"},
        expectation=Diverge(
            {
                "python": Outcome("Asserted", True, "not_scoped"),
                "typescript": Outcome("Asserted", True, "scoped"),
                "sql": Outcome("Asserted", True, "scoped"),
            },
            follow_up="F3",
        ),
        plan_clause="supplementary matching context",
    ),
    Case(
        id="supplementary_contradicting_context",
        description=(
            "Zero required dimensions, one supplementary instance frame value, "
            "contradicting caller context"
        ),
        required_dimensions=(),
        instance_frames=(_ch(),),
        context={"channel": "opencode"},
        expectation=Agree(Outcome(None, False, "context_mismatch")),
        plan_clause="supplementary contradicting context",
    ),
    Case(
        id="incomplete_instance_no_context",
        description=(
            "Type requires channel+tenant, instance commits channel only, "
            "no caller context"
        ),
        required_dimensions=("channel", "tenant"),
        instance_frames=(_ch(),),
        context=None,
        expectation=Diverge(
            {
                "python": Outcome(None, False, "unframed_required"),
                "typescript": Outcome(None, False, "context_required"),
                "sql": Outcome(None, False, "context_required"),
            },
            follow_up="F1",
        ),
        plan_clause="non-empty incomplete instance",
    ),
    Case(
        id="incomplete_instance_partial_context",
        description=(
            "Type requires channel+tenant, instance commits channel only, "
            "caller context covers the only committed dimension — Python still "
            "refuses on the missing type requirement, SQL/TS have no such input "
            "and evaluate normally"
        ),
        required_dimensions=("channel", "tenant"),
        instance_frames=(_ch(),),
        context={"channel": "interactive"},
        expectation=Diverge(
            {
                "python": Outcome(None, False, "unframed_required"),
                "typescript": Outcome("Asserted", True, "scoped"),
                "sql": Outcome("Asserted", True, "scoped"),
            },
            follow_up="F1",
        ),
        plan_clause="non-empty incomplete instance (context supplied)",
    ),
    Case(
        id="complete_instance_no_context",
        description=(
            "Type requires channel, instance commits channel, no caller context"
        ),
        required_dimensions=("channel",),
        instance_frames=(_ch(),),
        context=None,
        expectation=Agree(Outcome(None, False, "context_required")),
        plan_clause="complete instance without context",
    ),
    Case(
        id="complete_instance_matching_context",
        description=(
            "Type requires channel, instance commits channel, matching caller "
            "context"
        ),
        required_dimensions=("channel",),
        instance_frames=(_ch(),),
        context={"channel": "interactive"},
        expectation=Agree(Outcome("Asserted", True, "scoped")),
        plan_clause="matching context",
    ),
    Case(
        id="complete_instance_mismatching_context",
        description=(
            "Type requires channel, instance commits channel, mismatching "
            "caller context"
        ),
        required_dimensions=("channel",),
        instance_frames=(_ch(),),
        context={"channel": "opencode"},
        expectation=Agree(Outcome(None, False, "context_mismatch")),
        plan_clause="mismatching context",
    ),
    Case(
        id="unframed_unknown_context_key",
        description=(
            "Zero required dimensions, no instance frame values, caller "
            "context names an unknown frame dimension"
        ),
        required_dimensions=(),
        instance_frames=(),
        context={"bogus": "whatever"},
        expectation=Diverge(
            {
                "python": Outcome(error=UNKNOWN_FRAME_DIMENSION),
                "typescript": Outcome("Asserted", True, "not_scoped"),
                "sql": Outcome("Asserted", True, "not_scoped"),
            },
            follow_up="F2",
        ),
        plan_clause="unknown-key handling for unframed propositions",
    ),
    Case(
        id="framed_unknown_context_key",
        description=(
            "One supplementary instance frame value, caller context names an "
            "unknown frame dimension in addition to the declared one"
        ),
        required_dimensions=(),
        instance_frames=(_ch(),),
        context={"channel": "interactive", "bogus": "whatever"},
        expectation=Agree(Outcome(error=UNKNOWN_FRAME_DIMENSION)),
        plan_clause="unknown-key handling for framed propositions (control)",
    ),
)


def matrix_payload() -> Dict[str, Any]:
    """JSON-serialisable payload consumed by the TypeScript and SQL runtimes."""
    return {
        "fixture": dict(FIXTURE),
        "dimensions": {
            name: {
                "id": spec["id"],
                "name": spec["name"],
                "value_kind": spec["value_kind"],
                "scalar_type": spec["scalar_type"],
                "values": dict(spec["values"]),
            }
            for name, spec in DIMENSIONS.items()
        },
        "cases": [case.to_payload() for case in MATRIX],
    }


def divergence_ids() -> Tuple[str, ...]:
    return tuple(
        case.id for case in MATRIX if case.expectation.kind == "divergence"
    )


def agreement_ids() -> Tuple[str, ...]:
    return tuple(case.id for case in MATRIX if case.expectation.kind == "agree")
