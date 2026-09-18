"""Hermetic tests for bin/flow-recorder-v0.py — the induction contract.

No database, no network: induce() is pure over injected receipt dicts, and
resolve_role/ROLE_ALIAS are plain data. Pins clauses C1-C8 of the contract
documented in the tool docstring (design thread 1adce409 r1; RoleAlias law
from thread e9f81ae7, PROVISIONAL).

    C1  empirical alphabet; ABANDONED speculative
    C2  same-type consecutive receipts -> attempt_count, self-loops carry
        only receipt-observed
    C3  API_LIMIT is environment, not a node; breaks adjacency
    C4  actor-pair guards ONLY on true role-boundary crossings (both
        endpoints agent roles, resolved targets differ)
    C5  unmapped role -> unresolved:<name> + suspect flow-record
    C6  raw role strings preserved in evidence
    C7  executor-crash summaries -> observation, never resolved
    C8  observer-only purity: no write statements in the module source
"""

import importlib.util
import json
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "flow-recorder-v0.py")

_spec = importlib.util.spec_from_file_location("flow_recorder_v0", _TOOL)
fr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fr)


def rid(n):
    return f"r{n:03d}"


def receipt(n, type_, role, summary="", at=None):
    return {"id": rid(n), "type": type_, "role": role, "summary": summary,
            "issued_at": at or f"2026-09-17T21:{n:02d}:00Z"}


class TestResolveRole(unittest.TestCase):
    """C5 law: identity/alias/class/system-actor, never silent."""

    def test_identity(self):
        self.assertEqual(fr.resolve_role("planner"), ("planner", "identity"))
        self.assertEqual(fr.resolve_role("inspector"), ("inspector", "identity"))

    def test_alias_builder_engineer(self):
        self.assertEqual(fr.resolve_role("builder"), ("engineer", "alias"))

    def test_class_reviewer_verify_holder(self):
        self.assertEqual(fr.resolve_role("reviewer"), ("verify-holder", "class"))

    def test_system_actor(self):
        for name in ("cli", "cli-executor", "conduit-worker", "watchdog"):
            self.assertEqual(fr.resolve_role(name), ("system-actor", "system-actor"))

    def test_unmapped_is_unresolved_not_silent(self):
        self.assertEqual(fr.resolve_role("night-shift-agent"),
                         ("unresolved:night-shift-agent", "unresolved"))


class TestAttemptCounters(unittest.TestCase):
    """C2: consecutive same-type receipts are attempts, not fan-out."""

    def test_self_loops_collapse_to_attempts(self):
        receipts = [
            receipt(1, "PLAN_CREATE", "planner"),
            receipt(2, "BLOCK", "builder"), receipt(3, "BLOCK", "builder"),
            receipt(4, "BLOCK", "builder"),
            receipt(5, "REVIEW_PASS", "reviewer"),   # terminal
        ]
        flow = fr.induce("u1", receipts)
        by = {s["state"]: s for s in flow["states"]}
        self.assertEqual(by["BLOCK"]["attempt_count"], 3)
        edges = [(e["from"], e["to"]) for e in flow["edges"]]
        # C2: same-type adjacency produces NO edge — attempts, not fan-out.
        self.assertNotIn(("BLOCK", "BLOCK"), edges)
        self.assertEqual(edges, [("PLAN_CREATE", "BLOCK"),
                                 ("BLOCK", "REVIEW_PASS")])

    def test_cross_type_edges_count(self):
        receipts = [
            receipt(1, "PLAN_CREATE", "planner"),
            receipt(2, "BLOCK", "builder"),
            receipt(3, "IMPLEMENTATION", "builder"),
            receipt(4, "REVIEW_REJECT", "reviewer"),
            receipt(5, "REVIEW_PASS", "reviewer"),
        ]
        flow = fr.induce("u2", receipts)
        self.assertEqual(
            [(e["from"], e["to"], e["count"]) for e in flow["edges"]],
            [("PLAN_CREATE", "BLOCK", 1),
             ("BLOCK", "IMPLEMENTATION", 1),
             ("IMPLEMENTATION", "REVIEW_REJECT", 1),
             ("REVIEW_REJECT", "REVIEW_PASS", 1)])

    def test_interleaved_same_type_not_consecutive(self):
        # B R B R P: no same-type adjacency — each receipt is one attempt.
        receipts = [
            receipt(1, "BLOCK", "builder"),
            receipt(2, "REVIEW_REJECT", "reviewer"),
            receipt(3, "BLOCK", "builder"),
            receipt(4, "REVIEW_REJECT", "reviewer"),
            receipt(5, "REVIEW_PASS", "reviewer"),   # terminal
        ]
        flow = fr.induce("u3", receipts)
        by = {s["state"]: s for s in flow["states"]}
        self.assertEqual(by["BLOCK"]["attempt_count"], 2)
        self.assertEqual(by["REVIEW_REJECT"]["attempt_count"], 2)
        self.assertEqual(
            [(e["from"], e["to"], e["count"]) for e in flow["edges"]],
            [("BLOCK", "REVIEW_REJECT", 2), ("REVIEW_REJECT", "BLOCK", 1),
             ("REVIEW_REJECT", "REVIEW_PASS", 1)])


class TestActorPairGuards(unittest.TestCase):
    """C4: actor-pair only on true role-boundary crossings."""

    def test_cross_role_edge_carries_actor_pair(self):
        receipts = [receipt(1, "PLAN_CREATE", "planner"),
                    receipt(2, "BLOCK", "builder")]
        flow = fr.induce("u4", receipts)
        e = flow["edges"][0]
        self.assertEqual(e["guards"]["actor-pair"],
                         {"from_role": "planner", "to_role": "engineer"})

    def test_within_role_edge_has_no_actor_pair(self):
        # builder->builder resolves engineer->engineer: no crossing.
        receipts = [receipt(1, "BLOCK", "builder"),
                    receipt(2, "IMPLEMENTATION", "builder")]
        flow = fr.induce("u5", receipts)
        self.assertNotIn("actor-pair", flow["edges"][0]["guards"])
        self.assertEqual(list(flow["edges"][0]["guards"]), ["receipt-observed"])

    def test_reviewer_alias_maps_to_class_on_guards(self):
        receipts = [receipt(1, "IMPLEMENTATION", "builder"),
                    receipt(2, "REVIEW_PASS", "reviewer")]
        flow = fr.induce("u6", receipts)
        self.assertEqual(flow["edges"][0]["guards"]["actor-pair"],
                         {"from_role": "engineer", "to_role": "verify-holder"})

    def test_system_actor_edges_exempt(self):
        receipts = [receipt(1, "CANCELLED", "watchdog"),
                    receipt(2, "BLOCK", "builder")]
        flow = fr.induce("u7", receipts)
        e = flow["edges"][0]
        self.assertNotIn("actor-pair", e["guards"])
        self.assertIn("system-actor", e["actors_resolved"])

    def test_flow1_e6193a2e_shape(self):
        """The shape of the real first induction (4 cross-role edges -> 2)."""
        receipts = [
            receipt(1, "PLAN_CREATE", "planner"),
            receipt(2, "BLOCK", "builder"), receipt(3, "BLOCK", "builder"),
            receipt(4, "BLOCK", "builder"), receipt(5, "BLOCK", "builder"),
            receipt(6, "IMPLEMENTATION", "builder"),
            receipt(7, "REVIEW_REJECT", "reviewer"),
            receipt(8, "REVIEW_REJECT", "reviewer"),
            receipt(9, "REVIEW_REJECT", "reviewer"),
            receipt(10, "REVIEW_REJECT", "reviewer"),
            receipt(11, "REVIEW_PASS", "reviewer"),
        ]
        flow = fr.induce("u8", receipts)
        ap = [(e["from"], e["guards"].get("actor-pair")) for e in flow["edges"]]
        self.assertEqual(
            [p for p in ap if p[1] is not None],
            [("PLAN_CREATE", {"from_role": "planner", "to_role": "engineer"}),
             ("IMPLEMENTATION",
              {"from_role": "engineer", "to_role": "verify-holder"})])
        self.assertEqual(flow["terminal_state"], "REVIEW_PASS")
        self.assertEqual(len(flow["edges"]), 4)


class TestEnvironment(unittest.TestCase):
    """C3: API_LIMIT is environment and breaks adjacency."""

    def test_env_not_a_node_and_breaks_chain(self):
        receipts = [
            receipt(1, "BLOCK", "builder"),
            receipt(2, "API_LIMIT", "reviewer"),
            receipt(3, "IMPLEMENTATION", "builder"),
        ]
        flow = fr.induce("u9", receipts)
        states = {s["state"] for s in flow["states"]}
        self.assertNotIn("API_LIMIT", states)
        self.assertEqual(len(flow["environment_events"]), 1)
        # No BLOCK->IMPLEMENTATION edge: the env receipt broke adjacency.
        edges = [(e["from"], e["to"]) for e in flow["edges"]]
        self.assertNotIn(("BLOCK", "IMPLEMENTATION"), edges)


class TestSuspectAndEvidence(unittest.TestCase):
    """C5+C6: unresolved marking, raw strings preserved."""

    def test_unresolved_marks_suspect(self):
        receipts = [receipt(1, "BLOCK", "night-shift-agent"),
                    receipt(2, "IMPLEMENTATION", "night-shift-agent")]
        flow = fr.induce("u10", receipts)
        self.assertTrue(flow["suspect"])
        self.assertTrue(any("night-shift-agent" in r
                            for r in flow["suspect_reasons"]))

    def test_mapped_roles_not_suspect(self):
        receipts = [receipt(1, "BLOCK", "builder")]
        flow = fr.induce("u11", receipts)
        self.assertFalse(flow["suspect"])
        self.assertNotIn("suspect_reasons", flow)

    def test_raw_roles_preserved_in_states_and_edges(self):
        receipts = [receipt(1, "PLAN_CREATE", "planner"),
                    receipt(2, "BLOCK", "builder"),
                    receipt(3, "IMPLEMENTATION", "builder")]
        flow = fr.induce("u12", receipts)
        by = {s["state"]: s for s in flow["states"]}
        self.assertEqual(by["BLOCK"]["roles_raw"], ["builder"])
        self.assertIn("actors_raw", flow["edges"][0])
        # RoleAlias_applied carries BOTH the resolution and the rule.
        self.assertEqual(flow["parameters"]["RoleAlias_applied"]["builder"],
                         {"resolved": "engineer", "rule": "alias"})


class TestConflationObservation(unittest.TestCase):
    """C7: crash summaries surface as observations, not resolutions."""

    def test_crash_summaries_recorded(self):
        receipts = [receipt(1, "BLOCK", "builder", summary="agent failed exit=3"),
                    receipt(2, "IMPLEMENTATION", "builder"),
                    receipt(3, "REVIEW_PASS", "reviewer")]   # terminal
        flow = fr.induce("u13", receipts)
        confl = [o for o in flow["observations"] if "conflates" in o]
        self.assertEqual(len(confl), 1)
        self.assertIn("conflates work-rejection with agent-crash", confl[0])

    def test_clean_receipts_no_observation(self):
        receipts = [receipt(1, "BLOCK", "builder"),
                    receipt(2, "IMPLEMENTATION", "builder"),
                    receipt(3, "REVIEW_PASS", "reviewer")]   # terminal
        flow = fr.induce("u14", receipts)
        self.assertEqual(flow["observations"], [])


class TestObserverOnlyPurity(unittest.TestCase):
    """C8: the module contains no SQL write path and posts only via the
    explicit --emit-record agent-record API."""

    def test_no_sql_writes_in_source(self):
        src = open(_TOOL).read()
        for banned in ("INSERT INTO", "UPDATE ", "DELETE FROM",
                       "CREATE TABLE", "DROP "):
            self.assertNotIn(banned, src)

    def test_json_serializable_roundtrip(self):
        receipts = [receipt(1, "PLAN_CREATE", "planner"),
                    receipt(2, "BLOCK", "builder")]
        flow = fr.induce("u15", receipts)
        json.loads(json.dumps(flow))  # must not raise


if __name__ == "__main__":
    unittest.main()
