import unittest

from expression.pipeline import build_expression_bundle, extract_explicit_observations, validate_observations
from expression.taxonomy import EXPLICIT_KINDS, OBSERVATION_KIND_EXPECTATIONS, expected_observation_contract


class ExpressionTaxonomyTests(unittest.TestCase):
    def test_explicit_kinds_match_current_expectations(self):
        self.assertIn("reference", OBSERVATION_KIND_EXPECTATIONS)
        self.assertIn("version", OBSERVATION_KIND_EXPECTATIONS)
        self.assertIn("speech_act", OBSERVATION_KIND_EXPECTATIONS)
        for kind, expectation in OBSERVATION_KIND_EXPECTATIONS.items():
            self.assertEqual(expectation["disposition_expectation"], "unreviewed")
            self.assertEqual(expectation["authority_expectation"], "non_authoritative")

    def test_supported_kinds_are_explicit_in_current_pipeline(self):
        transcript = {
            "transcript_id": "taxonomy-001",
            "turns": [
                {"role": "user", "content": "Review PR #99 and nexus/sql/V101__net.sql."},
                {"role": "assistant", "content": "The Architect approved the staging decision."},
            ],
        }
        observations = extract_explicit_observations(transcript)
        kinds = {observation["kind"] for observation in observations}
        self.assertTrue(kinds.issubset(EXPLICIT_KINDS))

    def test_unsupported_kind_is_rejected_by_contract(self):
        errors = expected_observation_contract({"kind": "entity_mention", "value": "Foo"})
        self.assertEqual(errors, ["unsupported observation kind: 'entity_mention'"])

    def test_misdispositioned_observation_reports_contract_error(self):
        errors = expected_observation_contract(
            {"kind": "reference", "value": "PR #99", "disposition": "accepted", "authority_status": "non_authoritative"}
        )
        self.assertTrue(any("accepted" in error for error in errors))

    def test_observations_are_validated_without_mutation(self):
        transcript = {
            "transcript_id": "taxonomy-002",
            "turns": [
                {"role": "user", "content": "Review PR #99."},
                {"role": "assistant", "content": "Approved."},
            ],
        }
        observations = extract_explicit_observations(transcript)
        errors = validate_observations(observations)
        self.assertEqual(errors, [])

    def test_bundle_taxonomy_contract_is_current(self):
        bundle = build_expression_bundle(
            {"transcript_id": "taxonomy-003", "turns": [{"role": "user", "content": "Review PR #99."}]}
        )
        errors = validate_observations(bundle["observations"])
        self.assertEqual(errors, [])
