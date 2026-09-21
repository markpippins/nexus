import json
import pathlib
import unittest

from expression.contract import (
    ACTIVE_CANDIDATE_LINK_STATUSES,
    CONTRACT_REVISION,
    canonicalize_bundle,
    canonicalize_tag_bundle,
    contract_fingerprint,
    contract_manifest,
)
from expression.pipeline import build_expression_bundle
from expression.tag_adapter import adapt_tag_records


class ExpressionContractTests(unittest.TestCase):
    def setUp(self):
        self.transcript = {
            "transcript_id": "contract-fixture-001",
            "turns": [
                {"role": "user", "content": "Review PR #251 before the decision."},
                {"role": "assistant", "content": "The Architect approved the pre-stage."},
            ],
        }

    def test_manifest_is_explicit_and_aspects_safe(self):
        manifest = contract_manifest()
        self.assertEqual(manifest["contract_revision"], CONTRACT_REVISION)
        self.assertEqual(manifest["authority_status"], "non_authoritative")
        self.assertEqual(manifest["active_observation_kinds"], ["reference", "speech_act", "version"])
        self.assertEqual(manifest["active_candidate_link_statuses"], ["ambiguous", "proposed"])
        self.assertEqual(manifest["aspects_boundary"]["governed_tag_id"], None)
        self.assertEqual(len(contract_fingerprint()), 64)

    def test_canonical_bundle_is_reproducible_and_fingerprinted(self):
        first = canonicalize_bundle(build_expression_bundle(self.transcript))
        second = canonicalize_bundle(build_expression_bundle(self.transcript))
        self.assertEqual(first, second)
        self.assertEqual(first["contract_fingerprint"], contract_fingerprint())
        self.assertEqual(first["authority_status"], "non_authoritative")
        self.assertTrue(all(item["authority_status"] == "non_authoritative" for item in first["observations"]))
        self.assertTrue(all(item["predicate_status"] == "unresolved" for item in first["proposition_candidates"]))

    def test_aspects_governed_link_is_rejected(self):
        bundle = build_expression_bundle(self.transcript)
        bundle["candidate_links"] = [{
            "mention_id": "m-1",
            "candidate_id": "a-1",
            "method": "governed_binding",
            "status": "confirmed",
            "evidence": ["PR #251"],
        }]
        with self.assertRaisesRegex(ValueError, "confirmation belongs"):
            canonicalize_bundle(bundle)

    def test_authority_drift_is_rejected(self):
        bundle = build_expression_bundle(self.transcript)
        bundle["authority_status"] = "governed"
        with self.assertRaisesRegex(ValueError, "non_authoritative"):
            canonicalize_bundle(bundle)

    def test_aspects_namespace_is_explicitly_mapped_to_typespec(self):
        tags = adapt_tag_records([{
            "id": "record-1",
            "source_kind": "agent_record",
            "tag_namespace": "agent-record",
            "tags": ["to:architect"],
        }])
        normalized = canonicalize_tag_bundle(tags)
        self.assertEqual(normalized["observations"][0]["tag_namespace"], "agent-record")
        self.assertNotIn("namespace", normalized["observations"][0])
        self.assertIsNone(normalized["observations"][0]["governed_tag_id"])

    def test_aspects_authority_drift_is_rejected(self):
        tags = adapt_tag_records([{"id": "record-1", "tags": ["to:architect"]}])
        tags["observations"][0]["governed_tag_id"] = "tag-1"
        with self.assertRaisesRegex(ValueError, "governed Aspects"):
            canonicalize_tag_bundle(tags)

    def test_contract_revision_drift_is_rejected(self):
        bundle = build_expression_bundle(self.transcript)
        bundle["contract_revision"] = "expression-v0.2"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            canonicalize_bundle(bundle)

    def test_committed_manifest_matches_implementation(self):
        path = pathlib.Path(__file__).parents[2] / "typespec" / "v1" / "expression" / "contract-manifest.json"
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["contract_fingerprint"], contract_fingerprint())
        self.assertEqual(manifest["active_observation_kinds"], contract_manifest()["active_observation_kinds"])

    def test_active_statuses_are_narrow(self):
        self.assertEqual(ACTIVE_CANDIDATE_LINK_STATUSES, {"proposed", "ambiguous"})


if __name__ == "__main__":
    unittest.main()
