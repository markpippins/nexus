import unittest

from expression.compatibility import adapt_observation, adapt_records


class ExpressionCompatibilityTests(unittest.TestCase):
    def test_harvest_candidate_retains_source_identity_and_provenance(self):
        result = adapt_observation({
            "id": "candidate-1",
            "harvest_id": "harvest-1",
            "title": "A candidate",
            "intent_description": "A source-linked candidate",
            "source_schema": "nebula.harvest_candidates",
        })
        self.assertEqual(result["source_identity"], "harvest_candidate:candidate-1")
        self.assertEqual(result["source"]["harvest_id"], "harvest-1")
        self.assertEqual(result["authority_status"], "non_authoritative")
        self.assertEqual(result["disposition"], "unreviewed")

    def test_semantics_observation_uses_its_own_identity(self):
        result = adapt_observation({
            "source_observation_id": "obs-1",
            "asset_kind": "transcript",
            "raw_location": "transcripts/a.json",
            "revision_id": "rev-1",
            "content": "observed text",
        })
        self.assertEqual(result["source_identity"], "source_observation:obs-1")
        self.assertEqual(result["source"]["source_schema"], "semantics.source_observation")
        self.assertEqual(result["source"]["revision_id"], "rev-1")

    def test_same_identity_and_hash_is_idempotent(self):
        records = [
            {"id": "h-1", "source_kind": "harvest", "content": "same"},
            {"id": "h-1", "source_kind": "harvest", "content": "same"},
        ]
        bundle = adapt_records(records)
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(bundle["conflicts"], [])

    def test_same_identity_with_changed_content_is_explicit_conflict(self):
        bundle = adapt_records([
            {"id": "h-1", "source_kind": "harvest", "content": "first"},
            {"id": "h-1", "source_kind": "harvest", "content": "second"},
        ])
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(len(bundle["conflicts"]), 1)
        self.assertEqual(bundle["conflicts"][0]["status"], "conflict")

    def test_missing_source_identity_fails_closed(self):
        with self.assertRaises(ValueError):
            adapt_observation({"title": "no id"})

    def test_bundle_is_non_authoritative_and_staging(self):
        bundle = adapt_records([{"id": "h-1", "content": "text"}])
        self.assertEqual(bundle["authority_status"], "non_authoritative")
        self.assertEqual(bundle["boundary"]["observation_storage"], "staging")


if __name__ == "__main__":
    unittest.main()
