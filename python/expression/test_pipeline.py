import unittest

from expression.pipeline import (
    build_candidate_links,
    build_expression_bundle,
    build_proposition_candidates,
    extract_explicit_observations,
    segment_transcript,
    source_fingerprint,
)


class ExpressionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.transcript = {
            "transcript_id": "t-001",
            "turns": [
                {"role": "user", "content": "Review PR #251 and nexus/sql/V163__wr.sql."},
                {"role": "assistant", "content": "The Architect approved the pre-stage decision."},
                {"role": "assistant", "content": "See https://example.test/evidence and keep it disputed."},
            ],
        }

    def test_segments_are_deterministic_and_source_addressable(self):
        first = segment_transcript(self.transcript)
        second = segment_transcript(self.transcript)
        self.assertEqual(first, second)
        self.assertEqual([segment["ordinal"] for segment in first], [0, 1])
        self.assertEqual(first[-1]["turn_count"], 2)
        self.assertTrue(all(segment["text_hash"] for segment in first))
        self.assertTrue(all(segment["transcript_id"] == "t-001" for segment in first))

    def test_changed_source_changes_fingerprint_and_segment_identity(self):
        changed = {**self.transcript, "turns": [*self.transcript["turns"], {"role": "user", "content": "Correction."}]}
        self.assertNotEqual(source_fingerprint(self.transcript), source_fingerprint(changed))
        self.assertNotEqual(segment_transcript(self.transcript)[-1]["segment_id"], segment_transcript(changed)[-1]["segment_id"])

    def test_explicit_observations_are_non_authoritative_and_linked(self):
        observations = extract_explicit_observations(self.transcript)
        values = {observation["value"] for observation in observations}
        self.assertIn("PR #251", values)
        self.assertIn("V163", values)
        self.assertIn("approved", values)
        self.assertTrue(all(observation["disposition"] == "unreviewed" for observation in observations))
        self.assertTrue(all(observation["source"]["segment_id"] for observation in observations))
        self.assertTrue(all(observation["input_fingerprint"] for observation in observations))

    def test_candidate_links_are_proposed_or_ambiguous_never_silently_confirmed(self):
        observations = extract_explicit_observations(self.transcript)
        links = build_candidate_links(
            observations,
            {
                "asset:pr-251": ["PR #251"],
                "asset:duplicate": ["PR #251"],
                "asset:v163": ["V163"],
            },
        )
        self.assertTrue(any(link["status"] == "ambiguous" for link in links))
        self.assertTrue(any(link["status"] == "ambiguous" or link["status"] == "proposed" for link in links))
        self.assertTrue(all(link["method"] != "name_only_merge" for link in links))

    def test_proposition_candidates_are_about_source_language_not_authority(self):
        observations = extract_explicit_observations(self.transcript)
        propositions = build_proposition_candidates(observations)
        self.assertTrue(propositions)
        self.assertTrue(all(item["status"] == "candidate" for item in propositions))
        self.assertTrue(all(item["predicate"] == "mentions_decision_language" for item in propositions))
        self.assertTrue(all(item["modality"] == "reported" for item in propositions))

    def test_bundle_is_reproducible_and_explicitly_non_authoritative(self):
        first = build_expression_bundle(self.transcript)
        second = build_expression_bundle(self.transcript)
        self.assertEqual(first, second)
        self.assertEqual(first["authority_status"], "non_authoritative")
        self.assertEqual(first["contract_revision"], "expression-v0.1")

    def test_reprocessing_is_idempotent(self):
        self.assertEqual(
            extract_explicit_observations(self.transcript),
            extract_explicit_observations(self.transcript),
        )


if __name__ == "__main__":
    unittest.main()
