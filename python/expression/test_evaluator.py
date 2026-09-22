import copy
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
        self.read_set = {"read_set_id": "rs-expression-001", "generation": "g-001", "fingerprint": "read-set-sha256"}
        self.identities = {"evaluator_revision": "solscript-live-v0.1", "ontology_revision": "ontology-v0.1", "authority_owner": "resolution"}

    def test_missing_read_set_is_unevaluable(self):
        result = evaluate_bundle(self.bundle, read_set=None, **self.identities)
        self.assertTrue(all(item["disposition"] == "unevaluable" for item in result["results"]))
        self.assertTrue(all(item["reason_code"] == "missing_read_set" for item in result["results"]))
        self.assertEqual(result["authority_status"], "evaluation_only")
        self.assertEqual(result["mutation_policy"], "forbidden")

    def test_missing_evaluator_is_pending(self):
        result = evaluate_bundle(self.bundle, read_set=self.read_set, **self.identities)
        self.assertTrue(all(item["disposition"] == "pending" for item in result["results"]))

    def test_archived_resolution_dispositions_map_to_wire_outcomes(self):
        expected = {"Asserted": "asserted", "Disputed": "disputed", "Rejected": "rejected", "Pending": "pending", "Proposed": "advisory", "Stale": "stale", "Retracted": "refused"}
        for raw, normalized in expected.items():
            result = evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=lambda _candidate, _request, raw=raw: {"disposition": raw}, **self.identities)
            self.assertTrue(all(item["disposition"] == normalized for item in result["results"]))

    def test_uncertain_and_advisory_are_non_authoritative(self):
        for disposition in ("uncertain", "advisory"):
            result = evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=lambda _candidate, _request, disposition=disposition: {"disposition": disposition, "reason_code": "fixture"}, **self.identities)
            self.assertTrue(all(item["disposition"] == disposition for item in result["results"]))
            self.assertTrue(all(item["authority_status"] in {"evaluation_only", "advisory"} for item in result["results"]))

    def test_invalid_evaluator_disposition_fails_closed(self):
        result = evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=lambda _candidate, _request: {"disposition": "grant_authority"}, **self.identities)
        self.assertTrue(all(item["disposition"] == "refused" for item in result["results"]))
        self.assertTrue(all(item["reason_code"] == "invalid_evaluator_disposition" for item in result["results"]))

    def test_callback_cannot_mutate_bundle_or_read_set(self):
        original_bundle = copy.deepcopy(self.bundle)
        original_read_set = copy.deepcopy(self.read_set)

        def malicious(candidate, request):
            candidate["status"] = "evaluated"
            request["read_set"]["fingerprint"] = "tampered"
            request["authority_status"] = "governed"
            return {"disposition": "advisory"}

        evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=malicious, **self.identities)
        self.assertEqual(self.bundle, original_bundle)
        self.assertEqual(self.read_set, original_read_set)

    def test_request_fingerprint_pins_all_identities(self):
        result = evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=lambda _candidate, request: {"disposition": "uncertain", "request_seen": request["read_set_fingerprint"]}, **self.identities)
        self.assertTrue(all(item["request_fingerprint"] for item in result["results"]))
        self.assertEqual(result["read_set_fingerprint"], "read-set-sha256")
        self.assertEqual(result["evaluator_revision"], "solscript-live-v0.1")

    def test_injected_evaluator_is_pure_and_replayable(self):
        calls = []

        def evaluator(candidate, request):
            calls.append((request["request_fingerprint"], request["mutation_policy"]))
            return {"disposition": "asserted", "reason_code": "fixture_pass"}

        first = evaluate_bundle(self.bundle, read_set=self.read_set, evaluator=evaluator, **self.identities)
        replay = replay_evaluation(self.bundle, first, read_set=self.read_set, evaluator=evaluator, **self.identities)
        self.assertTrue(replay["match"])
        self.assertEqual(len(calls), 2 * len(self.bundle["proposition_candidates"]))
        self.assertTrue(all(item["disposition"] == "asserted" for item in first["results"]))

    def test_missing_observation_refuses_without_evaluation(self):
        bundle = {**self.bundle, "proposition_candidates": [{"proposition_id": "bad-proposition", "subject_ref": "segment:missing", "predicate": "mentions_decision_language", "source_observation_ids": ["missing-observation"], "modality": "reported", "status": "candidate"}]}
        called = []
        result = evaluate_bundle(bundle, read_set=self.read_set, evaluator=lambda *_args: called.append(True) or {"disposition": "asserted"}, **self.identities)
        self.assertEqual(result["results"][0]["disposition"], "refused")
        self.assertEqual(result["results"][0]["reason_code"], "invalid_expression_bundle")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
