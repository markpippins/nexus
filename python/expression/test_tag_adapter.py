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


# ── G1: bind_projected_tags_to_governed preserves the projected boundary ──

from unittest import IsolatedAsyncioTestCase

from expression import contract as expression_contract
from expression.tag_adapter import bind_projected_tags_to_governed


class _FakeGovernedTag:
    def __init__(self, id, name, normalized_name, member_kind="concept"):
        self.id = id
        self.name = name
        self.normalized_name = normalized_name
        self.member_kind = member_kind


class _FakeBindingPort:
    """In-memory stand-in for AspectsBindingPort."""

    def __init__(self, governed_tags):
        self._governed_tags = governed_tags
        self.created = []

    async def list_governed_tags(self, active_only=True):
        return list(self._governed_tags)

    async def create_binding(self, **kwargs):
        self.created.append(kwargs)
        return type("CreatedBinding", (), {"id": f"binding-{len(self.created)}"})()


class _ExplodingBindingPort(_FakeBindingPort):
    async def create_binding(self, **kwargs):
        raise RuntimeError("aspects unavailable")


class BindProjectedTagsBoundaryTests(IsolatedAsyncioTestCase):
    def _bundle(self):
        return adapt_tag_records([
            {"id": "record-1", "source_kind": "agent_record", "tags": ["to-architect", "unrouted-tag"]},
        ])

    async def test_binding_never_mutates_projected_observations(self):
        port = _FakeBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        bundle = self._bundle()
        result = await bind_projected_tags_to_governed(bundle, port)

        self.assertEqual(result["authority_status"], "non_authoritative")
        for obs in result["observations"]:
            self.assertEqual(obs["authority_status"], "projected")
            self.assertIsNone(obs["governed_tag_id"])
            self.assertNotIn("binding_status", obs)

    async def test_bound_bundle_still_satisfies_canonicalize_tag_bundle(self):
        port = _FakeBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        result = await bind_projected_tags_to_governed(self._bundle(), port)
        canonical = expression_contract.canonicalize_tag_bundle(result)
        self.assertEqual(canonical["authority_status"], "non_authoritative")
        self.assertTrue(all(obs["governed_tag_id"] is None for obs in canonical["observations"]))

    async def test_binding_outcomes_live_in_bindings_section(self):
        port = _FakeBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        result = await bind_projected_tags_to_governed(self._bundle(), port, status="approved", bound_by="engineer")

        self.assertEqual(len(result["bindings"]), 1)
        binding = result["bindings"][0]
        self.assertEqual(binding["governed_tag_id"], "gt-1")
        self.assertEqual(binding["status"], "approved")
        self.assertEqual(binding["bound_by"], "engineer")
        self.assertEqual(binding["binding_status"], "created")
        self.assertEqual(result["binding_summary"], {"proposed": 0, "approved": 1, "rejected": 0, "unmatched": 1, "error": 0})

    async def test_status_parameter_is_honored_not_hardcoded(self):
        port = _FakeBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        await bind_projected_tags_to_governed(self._bundle(), port, status="approved")
        self.assertEqual(port.created[0]["status"], "approved")

    async def test_binding_error_is_captured_and_projection_untouched(self):
        port = _ExplodingBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        result = await bind_projected_tags_to_governed(self._bundle(), port)

        self.assertEqual(result["bindings"][0]["binding_status"], "error")
        self.assertIn("aspects unavailable", result["bindings"][0]["binding_error"])
        self.assertEqual(result["binding_summary"]["error"], 1)
        self.assertTrue(all(obs["authority_status"] == "projected" for obs in result["observations"]))

    async def test_invalid_status_fails_closed(self):
        port = _FakeBindingPort([])
        with self.assertRaises(ValueError):
            await bind_projected_tags_to_governed(self._bundle(), port, status="governed")
        self.assertEqual(port.created, [])

    async def test_accepts_canonicalized_bundle_observations(self):
        port = _FakeBindingPort([_FakeGovernedTag("gt-1", "To Architect", "to-architect")])
        canonical = expression_contract.canonicalize_tag_bundle(self._bundle())
        result = await bind_projected_tags_to_governed(canonical, port)
        self.assertEqual(len(result["bindings"]), 1)


if __name__ == "__main__":
    unittest.main()
