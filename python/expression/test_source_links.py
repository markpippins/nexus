import json
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from expression.source_links import LiveGovernedArtifactResolver, resolve_source_links
from expression.pipeline import build_expression_bundle


class SourceLinkResolverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.http_responses = {}
        self.commands = []

        def http_get(url):
            if url.endswith("/api/meta"):
                return self.http_responses.get(url, {"tables": [{"table": "relation_vocabulary"}]})
            if url not in self.http_responses:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            result = self.http_responses[url]
            if isinstance(result, Exception):
                raise result
            return result

        def command_runner(args, **kwargs):
            self.commands.append(args)
            return subprocess.CompletedProcess(args, 0, json.dumps({"number": 251, "url": "https://github.com/acme/nexus/pull/251", "state": "OPEN"}), "")

        self.resolver = LiveGovernedArtifactResolver(
            repo_root=self.root,
            nebula_api="http://nebula/api",
            resolution_api="http://resolution",
            github_repo="acme/nexus",
            http_get=http_get,
            command_runner=command_runner,
        )

    def test_legacy_relation_registry_is_explicitly_unavailable(self):
        self.http_responses["http://resolution/api/meta"] = {"tables": []}
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = urllib.error.HTTPError("http://resolution/api/relation_vocabulary?limit=1000&offset=0", 404, "not found", {}, None)
        result = self.resolver.resolve_relation("mentions")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "relation_registry_not_exposed_by_live_service")

    def test_registered_artifact_url_resolves_via_api_route(self):
        uuid = "91f9e827-13b2-4dd7-8c67-3c1f240452f8"
        self.http_responses[f"http://nebula/api/agent-records/{uuid}"] = {"id": uuid}
        result = self.resolver.resolve_reference(f"http://localhost/api/agent-records/{uuid}", "url")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolved_to"]["artifact_kind"], "agent_record")

    def test_record_uuid_resolves_in_registry_order(self):
        uuid = "91f9e827-13b2-4dd7-8c67-3c1f240452f8"
        agent_url = f"http://nebula/api/agent-records/{uuid}"
        self.http_responses[agent_url] = {"id": uuid}
        result = self.resolver.resolve_reference(uuid, "record")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolved_to"], {"artifact_kind": "agent_record", "id": uuid})
        self.assertEqual(len(self.http_responses), 1)

    def test_missing_registry_record_is_unresolved_but_outage_is_unavailable(self):
        uuid = "91f9e827-13b2-4dd7-8c67-3c1f240452f8"
        result = self.resolver.resolve_reference(uuid, "record")
        self.assertEqual(result["status"], "unresolved")
        self.http_responses[f"http://nebula/api/agent-records/{uuid}"] = RuntimeError("offline")
        outage = self.resolver.resolve_reference(uuid, "record")
        self.assertEqual(outage["status"], "unavailable")

    def test_plan_uuid_harvest_and_candidate_resolve_against_nebula(self):
        plan = "8261664"
        harvest = "91f9e827-13b2-4dd7-8c67-3c1f240452f8"
        for path, payload in ((f"/plans/{plan}", {"id": plan}), (f"/harvests/{harvest}", {"id": harvest})):
            self.http_responses[f"http://nebula/api{path}"] = payload
        self.assertEqual(self.resolver.resolve_reference(plan)["resolved_to"]["artifact_kind"], "implementation_plan")
        self.assertEqual(self.resolver.resolve_reference(harvest)["resolved_to"]["artifact_kind"], "harvest")

    def test_pr_migration_and_path_resolve_to_registered_artifact(self):
        pr = self.resolver.resolve_reference("PR #251", "pr")
        self.assertEqual(pr["status"], "resolved")
        self.assertEqual(pr["resolved_to"]["id"], "acme/nexus#251")
        self.assertEqual(self.commands[0][0:3], ["gh", "pr", "view"])

        migration = self.resolver.resolve_reference("V182", "migration")
        self.assertEqual(migration["status"], "resolved")
        self.assertEqual(migration["resolved_to"]["id"], "sql/V182__relation_vocabulary.sql")

        path = self.resolver.resolve_reference("nexus/python/expression/pipeline.py", "path")
        self.assertEqual(path["status"], "resolved")
        self.assertEqual(path["resolved_to"]["id"], "python/expression/pipeline.py")

    def test_uuid_probe_continues_after_not_found(self):
        uuid = "91f9e827-13b2-4dd7-8c67-3c1f240452f8"
        harvest_url = f"http://nebula/api/harvests/{uuid}"
        self.http_responses[harvest_url] = {"id": uuid}
        result = self.resolver.resolve_reference(uuid)
        self.assertEqual(result["resolved_to"]["artifact_kind"], "harvest")

    def test_ambiguous_migration_and_traversal_path_fail_closed(self):
        version = self.resolver.resolve_reference("V194", "migration")
        self.assertEqual(version["status"], "ambiguous")
        traversal = self.resolver.resolve_reference("../../etc/passwd", "path")
        self.assertEqual(traversal["status"], "unresolved")

    def test_relation_must_be_active_in_live_resolution_registry(self):
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = {
            "count": 1,
            "items": [{"id": "relation-uuid", "name": "mentions", "expired_at": None}]
        }
        result = self.resolver.resolve_relation("mentions")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolved_to"]["id"], "relation-uuid")
        self.assertEqual(self.resolver.resolve_relation("mentions_decision_language")["status"], "unresolved")

    def test_each_proposition_is_blocked_by_any_unresolved_bundle_reference(self):
        transcript = {
            "transcript_id": "source-link-cross-turn",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "Review V999."},  # must not exist on main
                {"role": "assistant", "content": "The decision was approved."},
            ],
        }
        bundle = build_expression_bundle(transcript)
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = {
            "count": 1,
            "items": [{"id": "relation-uuid", "name": "mentions", "expired_at": None}]
        }
        report = resolve_source_links(bundle, self.resolver)
        self.assertTrue(all(not gate["eligible"] for gate in report["proposition_gates"]))
        self.assertTrue(any("source_link_unresolved" in gate["reasons"] for gate in report["proposition_gates"]))

    def test_source_resolution_gate_blocks_any_unresolved_link_or_relation(self):
        transcript = {
            "transcript_id": "source-link-gate",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "The decision was approved."},
            ],
        }
        bundle = build_expression_bundle(transcript)
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = {
            "count": 1,
            "items": [{"id": "relation-uuid", "name": "mentions", "expired_at": None}]
        }
        report = resolve_source_links(bundle, self.resolver)
        self.assertTrue(report["eligible_proposition_ids"])
        self.assertTrue(all(gate["eligible"] for gate in report["proposition_gates"]))
        self.assertTrue(all(gate["source_link_resolutions"] for gate in report["proposition_gates"]))

        # Make the extracted PR reference fail to resolve.
        def missing_pr(*_args, **_kwargs):
            return subprocess.CompletedProcess(["gh"], 1, "", "not found")
        self.resolver._command_runner = missing_pr
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = {"count": 0, "items": []}
        blocked = resolve_source_links(bundle, self.resolver)
        self.assertTrue(all(not gate["eligible"] for gate in blocked["proposition_gates"]))
        self.assertTrue(any("source_link_unresolved" in gate["reasons"] for gate in blocked["proposition_gates"]))
        self.assertTrue(all("governed_relation_unresolved" in gate["reasons"] for gate in blocked["proposition_gates"]))

    def test_unmapped_predicate_cannot_pass_relation_gate(self):
        self.http_responses["http://resolution/api/relation_vocabulary?limit=1000&offset=0"] = {
            "count": 1,
            "items": [{"id": "relation-uuid", "name": "mentions", "expired_at": None}]
        }
        bundle = {
            "observations": [],
            "proposition_candidates": [{"proposition_id": "p1", "predicate": "free_form", "source_observation_ids": []}],
        }
        report = resolve_source_links(bundle, self.resolver)
        self.assertEqual(report["blocked_proposition_ids"], ["p1"])
        self.assertEqual(report["relation_bindings"][0]["reason"], "predicate_token_unmapped")


if __name__ == "__main__":
    unittest.main()
