import unittest

from expression.compatibility import adapt_observation, adapt_records


class ExpressionCompatibilityTests(unittest.TestCase):
    def test_harvest_candidate_retains_candidate_identity_not_parent_harvest(self):
        result = adapt_observation({"id": "candidate-1", "harvest_id": "harvest-1", "title": "A candidate", "intent_description": "A source-linked candidate", "source_schema": "nebula.harvest_candidates"})
        self.assertEqual(result["source_identity"], "harvest_candidate:candidate-1")
        self.assertEqual(result["source"]["harvest_id"], "harvest-1")
        self.assertEqual(result["source"]["source_schema"], "nebula.harvest_candidates")
        self.assertEqual(result["authority_status"], "non_authoritative")

    def test_semantics_observation_uses_its_own_identity_and_revision(self):
        result = adapt_observation({"source_observation_id": "obs-1", "asset_kind": "transcript", "raw_location": "transcripts/a.json", "revision_id": "rev-1", "content": "observed text"})
        self.assertEqual(result["source_identity"], "source_observation:obs-1")
        self.assertEqual(result["source"]["source_schema"], "semantics.source_observation")
        self.assertEqual(result["source"]["source_revision"], "rev-1")

    def test_stable_source_identity_precedes_alias(self):
        bundle = adapt_records([{"id": "candidate-1", "source_kind": "harvest_candidate", "content": "Display"}], identity_material=[{"identity_id": "canonical-1", "source_identity": "harvest_candidate:candidate-1", "aliases": ["Display"]}, {"identity_id": "canonical-2", "aliases": ["Display"]}])
        self.assertEqual(bundle["identity_candidates"][0]["candidate_id"], "canonical-1")
        self.assertEqual(bundle["identity_candidates"][0]["method"], "stable_source_id")

    def test_alias_collision_is_ambiguous_not_merged(self):
        bundle = adapt_records([{"id": "candidate-1", "source_kind": "harvest_candidate", "content": "Shared"}], identity_material=[{"identity_id": "canonical-1", "aliases": ["Shared"]}, {"identity_id": "canonical-2", "aliases": ["Shared"]}])
        self.assertEqual(bundle["identity_candidates"][0]["status"], "ambiguous")
        self.assertIsNone(bundle["identity_candidates"][0]["candidate_id"])

    def test_missing_identity_is_explicit_unresolved_and_does_not_abort_batch(self):
        bundle = adapt_records([{"title": "no id"}, {"id": "candidate-1", "source_kind": "harvest_candidate", "content": "valid"}])
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(bundle["unresolved_identities"][0]["status"], "unresolved")

    def test_same_identity_and_hash_is_idempotent(self):
        bundle = adapt_records([{ "id": "h-1", "source_kind": "harvest", "content": "same"}, {"id": "h-1", "source_kind": "harvest", "content": "same"}])
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(bundle["conflicts"], [])

    def test_same_identity_with_changed_content_is_explicit_conflict(self):
        bundle = adapt_records([{ "id": "h-1", "source_kind": "harvest", "content": "first"}, {"id": "h-1", "source_kind": "harvest", "content": "second"}])
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(len(bundle["conflicts"]), 1)
        self.assertEqual(bundle["conflicts"][0]["status"], "conflict")

    def test_supersession_is_lineage_evidence_and_dangling_is_visible(self):
        bundle = adapt_records([{ "id": "new", "source_kind": "source_observation", "content": "new", "supersedes_id": "old"}, {"id": "old", "source_kind": "source_observation", "content": "old"}])
        self.assertEqual(bundle["supersession_links"][0]["status"], "declared")
        dangling = adapt_records([{ "id": "new", "source_kind": "source_observation", "content": "new", "supersedes_id": "old"}])
        self.assertEqual(dangling["supersession_links"][0]["status"], "dangling")

    def test_bundle_is_non_authoritative_and_staging(self):
        bundle = adapt_records([{"id": "h-1", "content": "text"}])
        self.assertEqual(bundle["authority_status"], "non_authoritative")
        self.assertEqual(bundle["boundary"]["observation_storage"], "staging")


if __name__ == "__main__":
    unittest.main()
