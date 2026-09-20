import unittest

from expression.evaluator import evaluate_bundle, replay_evaluation
from expression.pipeline import build_expression_bundle


class ExpressionEvaluatorTests(unittest.TestCase):
    def setUp(self):
        transcript = {
            "transcript_id": "eval-fixture-001",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "The Architect approved the pre-stage."},
            ],
        }
        self.bundle = build_expression_bundle(transcript)
        self.read_set = {
            "read_set_id": "rs-expression-001",
            "generation": "g-001",
            "fingerprint": "read-set-sha256",
        }

    def test_missing_read_set_is_explicitly_unevaluable(self):
        result = evaluate_bundle(
            self.bundle,
            read_set=None,
            evaluator_revision="solscript-poc-v0.1",
            ontology_revision="ontology-v0.1",
            authority_owner="resolution",
        )
        self.assertTrue(result["results"])
        self.assertTrue(all(item["disposition"] == "unevaluable" for item in result["results"]))
        self.assertTrue(all(item["reason_code"] == "missing_read_set" for item in result["results"]))
        self.assertEqual(result["authority_status"], "evaluation_only")

    def test_injected_evaluator_is_pure_and_replayable(self):
        calls = []

        def evaluator(candidate, request):
            calls.append(request["request_fingerprint"])
            return {"disposition": "asserted", "reason_code": "fixture_pass"}

        first = evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            evaluator_revision="solscript-poc-v0.1",
            ontology_revision="ontology-v0.1",
            authority_owner="resolution",
            evaluator=evaluator,
        )
        replay = replay_evaluation(
            self.bundle,
            first,
            read_set=self.read_set,
            evaluator_revision="solscript-poc-v0.1",
            ontology_revision="ontology-v0.1",
            authority_owner="resolution",
            evaluator=evaluator,
        )
        self.assertTrue(replay["match"])
        self.assertEqual(len(calls), 2 * len(self.bundle["proposition_candidates"]))
        self.assertTrue(all(item["disposition"] == "asserted" for item in first["results"]))

    def test_invalid_evaluator_disposition_fails_closed(self):
        result = evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            evaluator_revision="solscript-poc-v0.1",
            ontology_revision="ontology-v0.1",
            authority_owner="resolution",
            evaluator=lambda _candidate, _request: {"disposition": "grant_authority"},
        )
        self.assertTrue(all(item["disposition"] == "refused" for item in result["results"]))
        self.assertTrue(all(item["reason_code"] == "invalid_evaluator_disposition" for item in result["results"]))

    def test_missing_observation_refuses_without_evaluation(self):
        bundle = {**self.bundle, "proposition_candidates": [{
            "proposition_id": "bad-proposition",
            "subject_ref": "segment:missing",
            "predicate": "mentions_decision_language",
            "source_observation_ids": ["missing-observation"],
            "modality": "reported",
            "status": "candidate",
        }]}
        called = []
        result = evaluate_bundle(
            bundle,
            read_set=self.read_set,
            evaluator_revision="solscript-poc-v0.1",
            ontology_revision="ontology-v0.1",
            authority_owner="resolution",
            evaluator=lambda *_args: called.append(True) or {"disposition": "asserted"},
        )
        self.assertEqual(result["results"][0]["disposition"], "refused")
        self.assertEqual(result["results"][0]["reason_code"], "invalid_expression_bundle")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
