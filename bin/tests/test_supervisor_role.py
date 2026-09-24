#!/usr/bin/env python3
"""Hermetic source/projection coverage for the bounded Supervisor role.

No database or network: these tests pin the role's initial contract and the
committed projections that will be activated only after the DB rollout gate.
"""
from __future__ import annotations

import json
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class SupervisorRoleContract(unittest.TestCase):
    def test_registry_requests_full_surfaces_without_receipt_governance(self):
        spec = json.loads(read("config/roles/roles.json"))
        role = spec["roles"]["supervisor"]
        self.assertIs(role["governance"], False)
        for surface in ("persona", "procedures", "assemblyAlias", "harnessFile", "nebulaCheck"):
            self.assertIs(spec["roleDefaults"][surface], True)
        self.assertIn("WorkRequest execution", role["_note"])

    def test_disposition_matrix_records_bounded_authority(self):
        matrix = json.loads(read("schemas/decision-b-freeze/roles-disposition-matrix.json"))
        rows = [row for row in matrix["rows"] if row["stable_identity"] == "supervisor"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["class"], "role")
        self.assertTrue(row["in_nebula_roles"])
        self.assertIn("no WorkRequest execution", row["authority_scope"])
        self.assertEqual(
            set(row["source_registries"]),
            {"nebula.roles_history", "tackle.roles", "assembly.users"},
        )

    def test_opencode_harness_has_pointer_and_initial_boundary(self):
        text = read("config/harnesses/opencode/agents/supervisor.md")
        self.assertIn("assumes_role: supervisor", text)
        self.assertIn("role-system administration", text)
        self.assertIn("regenerates doctrine and OpenCode", text)
        self.assertIn("projections", text)
        self.assertIn("not implemented", text)
        self.assertIn("no authority to:", text)
        self.assertIn("issue pipeline receipts", text)
        self.assertIn("'gh pr merge*': deny", text)

    def test_persona_migration_registers_role_prompt_and_minimum_cards(self):
        text = read("schemas/migrations/tackle/supervisor_persona_v1.sql")
        self.assertIn("INSERT INTO tackle.roles", text)
        self.assertIn("'supervisor',\n    'opencode-persona',", text)
        self.assertIn("INSERT INTO tackle.role_memory", text)
        for slug in (
            "role-creation",
            "agent-config-template",
            "inbox-query-procedure",
            "pipeline-health-check",
            "worktree-development-workflow",
            "pr-protocol",
        ):
            self.assertIn(f"'{slug}'", text)
        self.assertIn("initial role-system procedure assignments", text)
        self.assertIn("intentionally NOT auto-applied", text)

    def test_initial_grant_has_no_execution_or_verification_capability(self):
        text = read("sql/grants/supervisor-grant-v0.1.sql")
        self.assertIn("ARRAY['role_administration']", text)
        self.assertIn(
            "false, false, false, false, false,\n        0,",
            text,
            "all five capability booleans and max_open_questions must stay fail-closed",
        )
        self.assertIn("DRAFT — NOT APPLIED TO LIVE", text)
        self.assertIn("WorkRequest execution authority is deferred", text)

    def test_all_default_role_seed_paths_include_supervisor(self):
        for rel in (
            "typescript/tackle-srv/src/db.ts",
            "typescript/tackle-mcp/src/db.ts",
            "typescript/conduit-mcp/src/db.ts",
        ):
            self.assertIn('{ name: "supervisor"', read(rel), rel)

    def test_assembly_and_nebula_surfaces_include_supervisor(self):
        assembly = read("typescript/assembly-srv/assembly-migration.sql")
        nebula = read("typescript/nebula-srv/migrations/055-allow-supervisor-role.sql")
        self.assertIn("'supervisor', 'supervisor@nexus.local'", assembly)
        self.assertIn("'supervisor'", nebula)
        self.assertIn("grants no WorkRequest", nebula)
        self.assertIn("execution, receipt, review, or verification", nebula)

    def test_supervisor_is_discoverable_but_not_receipt_executor(self):
        self.assertIn('"supervisor"', read("typescript/conduit-mcp/src/role-vocabulary.ts"))
        self.assertIn('"operator", "supervisor", "DBA"', read("bin/fleet-blackboard-digest.py"))
        governance = read("typescript/harness-srv/src/governance.ts")
        match = re.search(r"KNOWN_EXECUTORS\s*=\s*new Set\(\[(.*?)\]\)", governance, re.S)
        self.assertIsNotNone(match)
        self.assertNotIn('"supervisor"', match.group(1))

    def test_proposed_execution_protocol_remains_inactive(self):
        protocol = json.loads(read("schemas/protocol/supervisor-execution-protocol.json"))
        self.assertEqual(protocol["status"], "proposed")
        self.assertIn("supervisor_is_not", protocol["topology"])
        self.assertIn("execution_authority", protocol["topology"]["supervisor_is_not"])

    def test_generated_seed_assigns_supervisor_to_relevant_cards(self):
        manifest = json.loads(read("typescript/tackle-seeds/seed-manifest.json"))
        by_slug = {card["slug"]: card for card in manifest["cards"]}
        expected = {
            "role-creation",
            "agent-config-template",
            "bootstrap-self-update",
            "pipeline-health-check",
            "inbox-query-procedure",
            "tag-routing-reference",
            "thread-tracking",
            "post-turn-self-update",
            "role-governance",
            "knowledge-stratification",
            "pr-protocol",
            "worktree-development-workflow",
        }
        actual = {slug for slug, card in by_slug.items() if "supervisor" in card["roles"]}
        self.assertEqual(actual, expected)
        self.assertEqual(manifest["role_count"], 580)

        role_creation = by_slug["role-creation"]["body_md"]
        self.assertIn("**vanadium**", role_creation)
        self.assertNotIn("replicate to barium", role_creation.lower())


if __name__ == "__main__":
    unittest.main()
