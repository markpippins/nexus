"""E7 tests — live ResolutionInterpreter adapter (hermetic, in-memory).

Builds a minimal in-memory interpreter (no database) with one concept,
entity, and an unframed proposition carrying a passing assertion, then
verifies the adapter maps the live dispositions and context gates into the
E4 wire vocabulary without mutating interpreter state.
"""
from __future__ import annotations

import unittest
from typing import Any

from expression.e5 import build_e5_slice
from expression.pipeline import build_expression_bundle
from expression.solscript_adapter import (
    evaluate_bundle_with_interpreter,
    replay_bundle_with_interpreter,
    solscript_evaluator,
)


def _build_interpreter():
    """Construct a ResolutionInterpreter with a passing unframed proposition."""
    from solscript.interpreter import ResolutionInterpreter
    from solscript.models import (
        Concept,
        ConceptAttribute,
        Disposition,
        Entity,
        Expression,
        ExpressionKind,
        Operator,
        Proposition,
        Rule,
        RuleType,
        Severity,
    )

    def _uid() -> str:
        import uuid

        return str(uuid.uuid4())

    interp = ResolutionInterpreter()
    concept = Concept(
        id="concept-expression",
        name="ExpressionFixture",
        description=None,
    )
    interp.concepts[concept.id] = concept

    status = ConceptAttribute(
        id="attr-state",
        concept_id=concept.id,
        name="status",
        description="fixture state",
        value_type="text",
        is_state_attribute=True,
        allowed_values=["open", "closed"],
        default_value="open",
    )
    concept.attributes[status.id] = status

    entity = Entity(
        id="entity-expression",
        concept_id=concept.id,
        attributes={"status": "open"},
        external_id="expression-fixture-entity",
    )
    interp.entities[entity.id] = entity

    status_eq = Expression(
        id=_uid(),
        kind=ExpressionKind.OPERATOR,
        operator=Operator.EQ,
        return_type="boolean",
        operands=[
            Expression(
                id=_uid(),
                kind=ExpressionKind.ATTRIBUTE_REF,
                return_type=status.value_type,
                attribute_id=status.id,
            ),
            Expression(
                id=_uid(),
                kind=ExpressionKind.LITERAL,
                return_type="text",
                literal_value="open",
            ),
        ],
    )
    rule = Rule(
        id="rule-expression-open",
        name="status_is_open",
        rule_type=RuleType.INVARIANT,
        expression=status_eq,
        severity=Severity.HARD,
        concept_id=concept.id,
    )
    concept.invariants.append(rule)
    interp.rules[rule.id] = rule

    prop = Proposition(
        id="prop-expression-001",
        title="expression fixture proposition",
        description=None,
        asset_concept_id=concept.id,
        subject_entity_id=entity.id,
        disposition=Disposition.PROPOSED,
        assertions=[rule],
    )
    interp.propositions[prop.id] = prop
    return interp, prop


class SolscriptAdapterTests(unittest.TestCase):
    def setUp(self):
        self.interpreter, self.prop = _build_interpreter()
        transcript = {
            "transcript_id": "e7-fixture-001",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "The Architect approved the pre-stage."},
            ],
        }
        self.bundle = build_expression_bundle(transcript)
        # Pin the pipeline's propositions to the interpreter's fixture id so
        # the adapter can look them up.
        self.bundle["proposition_candidates"] = [
            {
                "proposition_id": self.prop.id,
                "subject_ref": f"entity:{self.prop.subject_entity_id}",
                "predicate": "mentions_decision_language",
                "object_ref": None,
                "source_observation_ids": [
                    self.bundle["observations"][0]["observation_id"]
                ],
                "modality": "reported",
                "status": "candidate",
                "required_read_set": None,
            }
        ]
        self.read_set = {"read_set_id": "e7-read-set", "fingerprint": "e7-read-set-sha256"}

    def test_unframed_passing_proposition_asserts(self):
        envelope = evaluate_bundle_with_interpreter(
            self.bundle,
            self.interpreter,
            read_set=self.read_set,
            ontology_revision="ontology-e7",
        )
        self.assertEqual(len(envelope["results"]), 1)
        result = envelope["results"][0]
        self.assertEqual(result["disposition"], "asserted")
        self.assertEqual(result["authority_status"], "advisory")
        self.assertEqual(envelope["evaluator_revision"], "solscript-resolution-interpreter-v33-e84")
        self.assertEqual(envelope["authority_status"], "evaluation_only")
        self.assertEqual(envelope["mutation_policy"], "forbidden")

    def test_unknown_proposition_is_pending_not_refused(self):
        bundle = dict(self.bundle)
        bundle["proposition_candidates"] = [
            {**self.bundle["proposition_candidates"][0], "proposition_id": "prop-does-not-exist"}
        ]
        envelope = evaluate_bundle_with_interpreter(
            bundle, self.interpreter, read_set=self.read_set, ontology_revision="ontology-e7"
        )
        self.assertEqual(envelope["results"][0]["disposition"], "pending")
        self.assertEqual(envelope["results"][0]["reason_code"], "proposition_not_in_interpreter")

    def test_unknown_context_key_is_refused_even_for_unframed_propositions(self):
        # E8.4 removes the silent discard of caller context. An unknown key
        # cannot be used to establish a valid evaluation context.
        evaluator = solscript_evaluator(self.interpreter, context={"no-such-dimension": "x"})
        result = evaluator(
            self.bundle["proposition_candidates"][0], {"request_fingerprint": "r"}
        )
        self.assertEqual(result["disposition"], "refused")
        self.assertEqual(result["reason_code"], "invalid_context")

    def test_required_type_without_instance_frame_is_unevaluable(self):
        from solscript.models import FrameDimension

        self.interpreter.frame_dimensions["dim-required"] = FrameDimension(
            id="dim-required", name="migration_phase", description=None,
            value_kind="typed_scalar", scalar_type="text",
        )
        self.prop.semantic_type_id = "type-target"
        self.interpreter.register_semantic_type_required_dimension(
            "type-target", "dim-required"
        )
        result = solscript_evaluator(self.interpreter)(
            self.bundle["proposition_candidates"][0], {"request_fingerprint": "r"}
        )
        self.assertEqual(result["disposition"], "unevaluable")
        self.assertEqual(result["reason_code"], "context_unframed_required")

    def test_required_type_without_context_is_unevaluable(self):
        from solscript.models import FrameDimension, PropositionFrameValue

        self.interpreter.frame_dimensions["dim-required"] = FrameDimension(
            id="dim-required", name="migration_phase", description=None,
            value_kind="typed_scalar", scalar_type="text",
        )
        self.prop.semantic_type_id = "type-target"
        self.prop.frame_values.append(PropositionFrameValue(
            id="pfv-required", proposition_id=self.prop.id,
            dimension_id="dim-required", scalar_value="pre_migration",
        ))
        self.interpreter.register_semantic_type_required_dimension(
            "type-target", "dim-required"
        )
        result = solscript_evaluator(self.interpreter)(
            self.bundle["proposition_candidates"][0], {"request_fingerprint": "r"}
        )
        self.assertEqual(result["disposition"], "unevaluable")
        self.assertEqual(result["reason_code"], "context_context_required")

    def test_supplementary_contradiction_is_refused(self):
        from solscript.models import FrameDimension, PropositionFrameValue

        self.interpreter.frame_dimensions["dim-supplementary"] = FrameDimension(
            id="dim-supplementary", name="as_of_version", description=None,
            value_kind="typed_scalar", scalar_type="text",
        )
        self.prop.frame_values.append(PropositionFrameValue(
            id="pfv-supplementary", proposition_id=self.prop.id,
            dimension_id="dim-supplementary", scalar_value="v1",
        ))
        result = solscript_evaluator(
            self.interpreter, context={"as_of_version": "v9"}
        )(
            self.bundle["proposition_candidates"][0], {"request_fingerprint": "r"}
        )
        self.assertEqual(result["disposition"], "refused")
        self.assertEqual(result["reason_code"], "context_context_mismatch")

    def test_supplementary_absence_is_allowed(self):
        from solscript.models import FrameDimension, PropositionFrameValue

        self.interpreter.frame_dimensions["dim-supplementary"] = FrameDimension(
            id="dim-supplementary", name="as_of_version", description=None,
            value_kind="typed_scalar", scalar_type="text",
        )
        self.prop.frame_values.append(PropositionFrameValue(
            id="pfv-supplementary", proposition_id=self.prop.id,
            dimension_id="dim-supplementary", scalar_value="v1",
        ))
        result = solscript_evaluator(self.interpreter)(
            self.bundle["proposition_candidates"][0], {"request_fingerprint": "r"}
        )
        self.assertEqual(result["disposition"], "asserted")

    def test_interpreter_state_is_not_mutated_by_evaluation(self):
        before = self.prop.disposition
        evaluate_bundle_with_interpreter(
            self.bundle, self.interpreter, read_set=self.read_set, ontology_revision="ontology-e7"
        )
        self.assertEqual(self.prop.disposition, before)
        self.assertIsNone(self.prop.last_evaluated_at)

    def test_replay_is_deterministic_through_live_interpreter(self):
        first = evaluate_bundle_with_interpreter(
            self.bundle, self.interpreter, read_set=self.read_set, ontology_revision="ontology-e7"
        )
        replay = replay_bundle_with_interpreter(
            self.bundle,
            first,
            self.interpreter,
            read_set=self.read_set,
            ontology_revision="ontology-e7",
        )
        self.assertTrue(replay["match"])

    def test_e5_slice_builds_from_live_evaluation(self):
        envelope = evaluate_bundle_with_interpreter(
            self.bundle, self.interpreter, read_set=self.read_set, ontology_revision="ontology-e7"
        )
        artifact = build_e5_slice(
            self.bundle, envelope, read_set=self.read_set, source_run_id="run-e7-001"
        )
        self.assertTrue(artifact["canonical_receipts"])
        receipt = artifact["canonical_receipts"][0]
        self.assertEqual(receipt["disposition"], "asserted")
        self.assertEqual(receipt["canonical_owner"], "resolution")


if __name__ == "__main__":
    unittest.main()
