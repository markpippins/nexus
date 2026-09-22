import copy
import unittest

from expression.e5 import build_e5_slice, replay_slice, rollback_slice
from expression.evaluator import evaluate_bundle
from expression.pipeline import build_expression_bundle


class ExpressionE5Tests(unittest.TestCase):
    def setUp(self):
        transcript = {
            "transcript_id": "e5-fixture-001",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "The Architect approved the pre-stage."},
            ],
        }
        self.bundle = build_expression_bundle(transcript)
        self.read_set = {"read_set_id": "e5-read-set-001", "fingerprint": "e5-read-set-sha256"}
        self.evaluation = evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            evaluator=lambda _candidate, _request: {"disposition": "advisory", "reason_code": "fixture"},
            evaluator_revision="solscript-e5-fixture",
            ontology_revision="ontology-e5-fixture",
            authority_owner="resolution",
        )

    def test_builds_canonical_receipts_and_regenerable_projections(self):
        artifact = build_e5_slice(self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001")
        self.assertTrue(artifact["canonical_receipts"])
        self.assertEqual(artifact["write_policy"]["resolution"], "canonical_receipt_only")
        self.assertEqual(artifact["write_policy"]["mutation"], "forbidden")
        self.assertEqual(artifact["graph_projection"]["authority_status"], "projection")
        self.assertEqual(artifact["keychain_context"]["source_content_stored"], False)
        self.assertEqual(artifact["keychain_context"]["source_fingerprint"], self.bundle["source_fingerprint"])

    def test_traceability_has_source_run_and_read_set(self):
        artifact = build_e5_slice(self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001")
        self.assertEqual(artifact["source_run_id"], "run-e5-001")
        self.assertEqual(artifact["keychain_context"]["read_set_fingerprint"], "e5-read-set-sha256")
        for receipt in artifact["canonical_receipts"]:
            self.assertEqual(receipt["source_run_id"], "run-e5-001")
            self.assertEqual(receipt["canonical_owner"], "resolution")

    def test_replay_is_byte_stable_for_same_inputs(self):
        artifact = build_e5_slice(self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001")
        replay = replay_slice(artifact, self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001")
        self.assertTrue(replay["match"])

    def test_rollback_is_append_only_and_retains_lineage(self):
        artifact = build_e5_slice(self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001")
        original = copy.deepcopy(artifact)
        rolled_back = rollback_slice(artifact, rollback_id="rb-e5-001", reason="fixture review failed")
        self.assertEqual(artifact, original)
        self.assertEqual(rolled_back["rollback"]["status"], "rolled_back")
        self.assertEqual(rolled_back["rollback"]["rollback_of"], artifact["artifact_fingerprint"])
        self.assertNotEqual(rolled_back["artifact_fingerprint"], artifact["artifact_fingerprint"])

    def test_retention_class_is_explicit(self):
        with self.assertRaises(ValueError):
            build_e5_slice(self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e5-001", retention_class="forever")

    def test_evaluation_mutation_policy_is_required(self):
        evaluation = dict(self.evaluation)
        evaluation["mutation_policy"] = "allowed"
        with self.assertRaises(ValueError):
            build_e5_slice(self.bundle, evaluation, read_set=self.read_set, source_run_id="run-e5-001")


if __name__ == "__main__":
    unittest.main()
