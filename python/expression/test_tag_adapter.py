import unittest

from expression.compatibility import adapt_records
from expression.tag_adapter import adapt_tag_record, adapt_tag_records, attach_projected_tags


class ExpressionTagAdapterTests(unittest.TestCase):
    def test_agent_record_tags_keep_namespace_and_source_identity(self):
        observations = adapt_tag_record({
            "id": "record-1",
            "source_kind": "agent_record",
            "tag_namespace": "agent-record",
            "source_revision": "rev-1",
            "tags": ["to:architect", "type:decision"],
            "metadata": {"priority": "high"},
        })
        self.assertEqual(len(observations), 3)
        self.assertTrue(all(item["source_identity"] == "agent_record:record-1" for item in observations))
        self.assertTrue(all(item["namespace"] == "agent-record" for item in observations))
        self.assertTrue(all(item["authority_status"] == "projected" for item in observations))
        self.assertTrue(all(item["governed_tag_id"] is None for item in observations))

    def test_projection_is_idempotent(self):
        record = {"id": "record-1", "tags": ["to:architect"]}
        first = adapt_tag_records([record, record])
        self.assertEqual(len(first["observations"]), 1)
        self.assertEqual(first["conflicts"], [])

    def test_same_projected_identity_with_different_raw_values_is_conflict(self):
        bundle = adapt_tag_records([
            {"id": "record-1", "tags": ["To Architect"]},
            {"id": "record-1", "tags": ["to-architect"]},
        ])
        self.assertEqual(len(bundle["observations"]), 1)
        self.assertEqual(len(bundle["conflicts"]), 1)
        self.assertEqual(bundle["conflicts"][0]["status"], "conflict")

    def test_attachment_uses_source_identity_not_display_name(self):
        source = adapt_records([{"id": "candidate-1", "source_kind": "harvest_candidate", "content": "A"}])
        tags = adapt_tag_records([{"id": "candidate-1", "source_kind": "harvest_candidate", "tags": ["candidate"]}])
        combined = attach_projected_tags(source, tags)
        self.assertEqual(len(combined["projected_tags"]), 1)
        self.assertEqual(combined["unresolved_tag_observations"], [])

    def test_unmatched_tag_is_explicitly_unresolved(self):
        source = adapt_records([{"id": "candidate-1", "source_kind": "harvest_candidate", "content": "A"}])
        tags = adapt_tag_records([{"id": "candidate-2", "source_kind": "harvest_candidate", "tags": ["candidate"]}])
        combined = attach_projected_tags(source, tags)
        self.assertEqual(combined["projected_tags"], [])
        self.assertEqual(combined["unresolved_tag_observations"][0]["status"], "unresolved")

    def test_missing_source_identity_fails_closed(self):
        with self.assertRaises(ValueError):
            adapt_tag_record({"tags": ["orphan"]})


if __name__ == "__main__":
    unittest.main()
