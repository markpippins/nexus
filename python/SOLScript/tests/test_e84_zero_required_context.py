"""E8.4 regression: zero type-level requirements evaluate as ``not_scoped``.

The empty required-dimension set is always a subset of the instance
dimension set, so the ``not_scoped`` branch must be selected explicitly
when the type declares no required dimensions. Supplementary instance
frame values may participate in evaluation, but they must not relabel the
proposition ``scoped`` and must not cause refusal when the caller supplies
no context or a matching context. Nonempty requirements keep their
unframed_required / context_required / context_mismatch discipline, and a
contradicting supplementary context still refuses.

These are in-memory checks only; no database evaluator is exercised.
"""

from __future__ import annotations

import uuid

from solscript import (
    Disposition,
    Entity,
    FrameDimension,
    FrameDimensionValue,
    Proposition,
    PropositionFrameValue,
    ResolutionInterpreter,
)


def _uid() -> str:
    return str(uuid.uuid4())


def _build_interpreter(*, semantic_type_id: "str | None" = None):
    interp = ResolutionInterpreter()
    entity = Entity(id=_uid(), concept_id=_uid(), attributes={})
    interp.add_entity(entity)
    prop = Proposition(
        id=_uid(),
        title="E8.4 frame-scope regression",
        description=None,
        asset_concept_id=entity.concept_id,
        subject_entity_id=entity.id,
        disposition=Disposition.PENDING,
        semantic_type_id=semantic_type_id,
    )
    interp.add_proposition(prop)
    return interp, prop


def _register_channel(interp: ResolutionInterpreter):
    """Register a governed-reference dimension named ``channel``."""
    dim = FrameDimension(
        id=_uid(),
        name="channel",
        description=None,
        value_kind="governed_reference",
    )
    value = FrameDimensionValue(
        id=_uid(), dimension_id=dim.id, value="interactive"
    )
    interp.frame_dimensions[dim.id] = dim
    interp.frame_dimension_values[value.id] = value
    return dim, value


def _add_supplementary_channel(interp, prop, dim, value) -> None:
    interp.add_proposition_frame_value(
        PropositionFrameValue(
            id=_uid(),
            proposition_id=prop.id,
            dimension_id=dim.id,
            reference_value_id=value.id,
        )
    )


class TestZeroTypeRequirements:
    def test_unframed_zero_requirements_is_not_scoped(self) -> None:
        interp, prop = _build_interpreter()
        disposition, all_passed, status = interp.evaluate_proposition(prop)
        assert (disposition, all_passed, status) == (
            Disposition.ASSERTED,
            True,
            "not_scoped",
        )

    def test_supplementary_value_with_absent_context_is_not_scoped(self) -> None:
        interp, prop = _build_interpreter()
        dim, value = _register_channel(interp)
        _add_supplementary_channel(interp, prop, dim, value)

        missing = interp.evaluate_proposition(prop)
        empty = interp.evaluate_proposition(prop, context={})
        assert missing == (Disposition.ASSERTED, True, "not_scoped")
        assert empty == (Disposition.ASSERTED, True, "not_scoped")

    def test_supplementary_value_with_matching_context_is_not_scoped(self) -> None:
        interp, prop = _build_interpreter()
        dim, value = _register_channel(interp)
        _add_supplementary_channel(interp, prop, dim, value)

        disposition, all_passed, status = interp.evaluate_proposition(
            prop, context={"channel": "interactive"}
        )
        assert (disposition, all_passed, status) == (
            Disposition.ASSERTED,
            True,
            "not_scoped",
        )

    def test_supplementary_value_with_contradicting_context_refuses(self) -> None:
        interp, prop = _build_interpreter()
        dim, value = _register_channel(interp)
        _add_supplementary_channel(interp, prop, dim, value)

        disposition, all_passed, status = interp.evaluate_proposition(
            prop, context={"channel": "opencode"}
        )
        assert (disposition, all_passed, status) == (
            None,
            False,
            "context_mismatch",
        )


class TestNonEmptyTypeRequirements:
    def test_missing_required_dimension_is_unframed_required(self) -> None:
        stype = _uid()
        interp, prop = _build_interpreter(semantic_type_id=stype)
        dim, _value = _register_channel(interp)
        interp.register_semantic_type_required_dimension(stype, dim.id)

        disposition, all_passed, status = interp.evaluate_proposition(prop)
        assert (disposition, all_passed, status) == (
            None,
            False,
            "unframed_required",
        )

    def test_satisfied_required_dimension_still_needs_context(self) -> None:
        stype = _uid()
        interp, prop = _build_interpreter(semantic_type_id=stype)
        dim, value = _register_channel(interp)
        interp.register_semantic_type_required_dimension(stype, dim.id)
        _add_supplementary_channel(interp, prop, dim, value)

        disposition, all_passed, status = interp.evaluate_proposition(prop)
        assert (disposition, all_passed, status) == (
            None,
            False,
            "context_required",
        )

    def test_satisfied_required_dimension_with_matching_context_is_scoped(self) -> None:
        stype = _uid()
        interp, prop = _build_interpreter(semantic_type_id=stype)
        dim, value = _register_channel(interp)
        interp.register_semantic_type_required_dimension(stype, dim.id)
        _add_supplementary_channel(interp, prop, dim, value)

        disposition, all_passed, status = interp.evaluate_proposition(
            prop, context={"channel": "interactive"}
        )
        assert (disposition, all_passed, status) == (
            Disposition.ASSERTED,
            True,
            "scoped",
        )

    def test_satisfied_required_dimension_with_mismatch_refuses(self) -> None:
        stype = _uid()
        interp, prop = _build_interpreter(semantic_type_id=stype)
        dim, value = _register_channel(interp)
        interp.register_semantic_type_required_dimension(stype, dim.id)
        _add_supplementary_channel(interp, prop, dim, value)

        disposition, all_passed, status = interp.evaluate_proposition(
            prop, context={"channel": "opencode"}
        )
        assert (disposition, all_passed, status) == (
            None,
            False,
            "context_mismatch",
        )
