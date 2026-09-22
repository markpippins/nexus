"""RoleLease — class-level frame requirement for a "may consume work" proposition.

Demonstrates the E8.4 frame discipline end-to-end on the role-lease
authority identity (the lease the Duality/PEB admission chain keys on):

  * A `RoleLease` concept with a state attribute (`status`) and budget
    accounting (`budget_units`, `consumed_units`).
  * A governed `channel` frame dimension (interactive / opencode / ollama) —
    the same vocabulary the real tackle.role_leases uses.
  * A proposition `"role may consume work"` that is FRAMED on
    `channel = interactive`, with assertions over the lease entity
    (ACTIVE + budget remaining).

The E8.4 gate then refuses to evaluate the claim unless the supplied context
matches its required semantic type frame — exactly the fail-closed behavior
the Duality subscriber audit found missing on the operator/harness backends:

    context               → (disposition, all_passed, context_status)
    --------------------    ------------------------------------------
    none                  → (None, False, 'context_required')   refuse
    channel=opencode      → (None, False, 'context_mismatch')   refuse
    channel=interactive   → (Asserted, True, 'scoped')          evaluate
    interactive + budget exhausted
                          → (Rejected, False, 'scoped')         evaluate, fail

Usage::

    python -m solscript.examples.role_lease
"""

from __future__ import annotations

import uuid
from typing import Any, Tuple

from .. import (
    Concept,
    ConceptAttribute,
    ConceptStateTransition,
    Disposition,
    Entity,
    Expression,
    ExpressionKind,
    FrameDimension,
    FrameDimensionValue,
    Operator,
    Proposition,
    PropositionFrameValue,
    ResolutionInterpreter,
    Rule,
    RuleType,
    Severity,
)


def _uid() -> str:
    return str(uuid.uuid4())


def _attr(
    concept: Concept,
    name: str,
    value_type: str,
    *,
    is_state: bool = False,
    allowed: list[str] | None = None,
) -> ConceptAttribute:
    """Register a ConceptAttribute on `concept` and return it."""
    attr = ConceptAttribute(
        id=_uid(),
        concept_id=concept.id,
        name=name,
        description=f"{concept.name}.{name}",
        value_type=value_type,
        is_state_attribute=is_state,
        allowed_values=allowed or [],
    )
    concept.attributes[attr.id] = attr
    return attr


def _attr_ref(attr: ConceptAttribute) -> Expression:
    return Expression(
        id=_uid(),
        kind=ExpressionKind.ATTRIBUTE_REF,
        return_type=attr.value_type,
        attribute_id=attr.id,
    )


def _lit(value: Any, value_type: str) -> Expression:
    return Expression(
        id=_uid(),
        kind=ExpressionKind.LITERAL,
        return_type=value_type,
        literal_value=value,
    )


def _lt(left: Expression, right: Expression) -> Expression:
    return Expression(
        id=_uid(),
        kind=ExpressionKind.OPERATOR,
        operator=Operator.LT,
        return_type="boolean",
        operands=[left, right],
    )


def build_role_lease_interpreter() -> Tuple[ResolutionInterpreter, Proposition, Entity]:
    """Build the RoleLease example and return (interpreter, proposition, lease).

    The returned proposition is framed on `channel = interactive`; the lease
    entity starts ACTIVE with budget 5 / consumed 0.  A guarded `consume`
    transition is also registered on the RoleLease concept.
    """
    interp = ResolutionInterpreter()

    # ── 1. RoleLease concept + accounting attributes ──────────────────
    lease_concept = Concept(
        id=_uid(),
        name="RoleLease",
        description="Time-bounded role authority identity issued by the lease subsystem",
    )
    interp.add_concept(lease_concept)

    status = _attr(
        lease_concept, "status", "text", is_state=True,
        allowed=["ACTIVE", "EXPIRED", "RELEASED"],
    )
    _attr(lease_concept, "channel", "text")
    budget = _attr(lease_concept, "budget_units", "integer")
    consumed = _attr(lease_concept, "consumed_units", "integer")

    # ── 2. governed frame dimension: channel ──────────────────────────
    channel_dim = FrameDimension(
        id=_uid(),
        name="channel",
        description="execution channel the lease authorizes (interactive/opencode/ollama)",
        value_kind="governed_reference",
        scalar_type=None,
    )
    interp.add_frame_dimension(channel_dim)

    val_interactive = FrameDimensionValue(
        id=_uid(), dimension_id=channel_dim.id, value="interactive",
    )
    val_opencode = FrameDimensionValue(
        id=_uid(), dimension_id=channel_dim.id, value="opencode",
    )
    interp.add_frame_dimension_value(val_interactive)
    interp.add_frame_dimension_value(val_opencode)

    # ── 3. the lease entity ───────────────────────────────────────────
    lease = interp.add_entity_by_concept_name(
        "RoleLease",
        {
            "status": "ACTIVE",
            "channel": "interactive",
            "budget_units": 5,
            "consumed_units": 0,
        },
        external_id="lease-engineer-interactive",
    )

    # ── 4. assertion: status = ACTIVE AND consumed < budget ───────────
    status_eq = Expression(
        id=_uid(),
        kind=ExpressionKind.OPERATOR,
        operator=Operator.EQ,
        return_type="boolean",
        operands=[_attr_ref(status), _lit("ACTIVE", "text")],
    )
    assert_expr = Expression(
        id=_uid(),
        kind=ExpressionKind.OPERATOR,
        operator=Operator.AND,
        return_type="boolean",
        operands=[status_eq, _lt(_attr_ref(consumed), _attr_ref(budget))],
    )
    assertion = Rule(
        id=_uid(),
        name="lease active and budget remaining",
        rule_type=RuleType.INVARIANT,
        expression=assert_expr,
        severity=Severity.HARD,
        concept_id=lease_concept.id,
    )
    lease_concept.invariants.append(assertion)
    interp.rules[assertion.id] = assertion

    # ── 5. guarded consume transition (the lease lifecycle) ───────────
    consume_guard = Rule(
        id=_uid(),
        name="budget remaining before consume",
        rule_type=RuleType.GUARD,
        expression=_lt(_attr_ref(consumed), _attr_ref(budget)),
        severity=Severity.HARD,
        concept_id=lease_concept.id,
    )
    consume = ConceptStateTransition(
        id=_uid(),
        concept_id=lease_concept.id,
        from_value="ACTIVE",
        to_value="ACTIVE",
        name="consume",
        notes="consume one unit; guard enforces budget remaining",
        guards=[consume_guard],
    )
    lease_concept.state_transitions.append(consume)
    interp.state_transitions[consume.id] = consume

    # ── 6. the framed proposition: "role may consume work" ────────────
    prop = Proposition(
        id=_uid(),
        title="engineer may consume work",
        description="The engineer's interactive lease authorizes another unit of work",
        asset_concept_id=lease_concept.id,
        subject_entity_id=lease.id,
        disposition=Disposition.PENDING,
        assertions=[assertion],
        semantic_type_id="role-lease-consume",
    )
    interp.add_proposition(prop)
    # E8.4: channel is required by the semantic type; the instance frame
    # commitment alone must not be the source of the requirement.
    interp.register_semantic_type_required_dimension(
        "role-lease-consume", channel_dim.id
    )

    frame = PropositionFrameValue(
        id=_uid(),
        proposition_id=prop.id,
        dimension_id=channel_dim.id,
        reference_value_id=val_interactive.id,
        scalar_value=None,
    )
    interp.add_proposition_frame_value(frame)

    return interp, prop, lease


def _show(label: str, outcome: Tuple[Disposition | None, bool, str]) -> None:
    disposition, all_passed, status = outcome
    print(
        f"  {label:<34} → "
        f"disposition={str(disposition):<10} all_passed={all_passed!s:<5} "
        f"context_status={status!r}"
    )


def main() -> None:
    interp, prop, lease = build_role_lease_interpreter()

    print("=== RoleLease: frame-scoped 'may consume work' ===\n")
    print(
        f"Proposition: {prop.title!r} framed on channel=interactive "
        f"(lease {lease.external_id}: budget {lease.attributes['budget_units']}, "
        f"consumed {lease.attributes['consumed_units']})\n"
    )

    print("Evaluations of the SAME proposition under different contexts:\n")

    # 1. No context → refuse (framed but nothing supplied)
    _show("no context", interp.evaluate_proposition(prop))

    # 2. Wrong channel → refuse (context contradicts declared dimension)
    _show("context channel=opencode",
          interp.evaluate_proposition(prop, context={"channel": "opencode"}))

    # 3. Matching context → evaluate; assertions pass → Asserted
    _show("context channel=interactive",
          interp.evaluate_proposition(prop, context={"channel": "interactive"}))

    # 4. Matching context, budget exhausted → evaluate; assertion fails → Rejected
    lease.attributes["consumed_units"] = 5
    _show("interactive + budget exhausted",
          interp.evaluate_proposition(prop, context={"channel": "interactive"}))

    print("\nThe two refusals (context_required / context_mismatch) wrote no "
          "disposition — the claim was never evaluated in a context it does "
          "not bind to. That is the fail-closed admission boundary the "
          "operator/harness Duality backends currently lack.")

    print("\n--- Guarded 'consume' transition (budget now exhausted) ---")
    consume = next(
        t for t in interp.state_transitions.values() if t.name == "consume"
    )
    ok, results = interp.check_transition_guard(consume, lease)
    print(f"  guard: {[(r['rule_name'], r['passed']) for r in results]}")
    print(f"  admitted: {ok}  (consumed={lease.attributes['consumed_units']} "
          f"== budget={lease.attributes['budget_units']} → blocked)")


if __name__ == "__main__":
    main()
