#!/usr/bin/env python3
"""Hermetic tests for bin/flow-emit-aegis.py (Stage-4 Aegis emitter).

No database, no network, no aegis import side effects: the flow-record
fixture is a frozen dict shaped like the recorder's live output (unit
e6193a2e, 11 receipts, re-induced under #327/#328 law). Pins:

  - G1..G4 gate refusals, each with its reason string
  - G4 default-deny: CLI path refuses even a perfect ratified record
  - G3 determinism: byte-identical artifact across two emit() calls;
    no clock/uuid anywhere in the artifact
  - artifact -> aegis.StateMachineRegistryManager shape compatibility:
    states/transitions land via the manager API and validate_registry
    reports no ERRORS (warnings allowed: concept_id binds at apply time)
  - guard-expression grammar: no capitalized tokens outside declared
    states (aegis's _extract_state_references constraint), balanced parens
  - Q5 flow_ref slot present and empty; Q4 evaluation note present
  - role_facts: RA2 historical fact carries as_of; capacity never appears
    (environment events are variables, not guards)
"""

import importlib.util
import json
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
_BIN = os.path.join(_REPO, "bin")
sys.path.insert(0, _BIN)

import importlib.util as _iu
_spec_em = _iu.spec_from_file_location(
    "flow_emit_aegis", os.path.join(_BIN, "flow-emit-aegis.py"))
em = _iu.module_from_spec(_spec_em)
_spec_em.loader.exec_module(em)

# Import aegis's USABLE REGION (dataclasses + StateMachineRegistryManager,
# lines 1–436). aegis.py has a PRE-EXISTING import-time bug: section 3's
# StateMachineToResolutionBridge annotates with an undefined `Concept`
# (line 533), so the whole module has never been importable — nothing in
# the repo imports it; this emitter is its first consumer. Reported
# upstream; NOT fixed here (out of scope, someone's incomplete work).
# The registry core (sections 1–2) is clean and is the emit target.
_src = open(os.path.join(_REPO, "python", "aegis", "aegis.py"),
            encoding="utf-8").read().splitlines()
_aegis_core = "\n".join(_src[:436])
aegis_mod = type(importlib.util)("aegis_core")  # fresh module object
exec(compile(_aegis_core, "aegis_core.py", "exec"), aegis_mod.__dict__)


def make_flow(**over):
    """Frozen flow-record shaped like the live recorder output (e6193a2e)."""
    flow = {
        "flow_record_v0": True,
        "design_ref": "discussions 1adce409 revision r1 (9c04ab1f)",
        "parameters": {
            "WorkRef": "e6193a2e-0000-0000-0000-000000000000",
            "RoleAlias_applied": {
                "planner": {"resolved": "planner", "rule": "identity"},
                "engineer": {"resolved": "engineer", "rule": "identity"},
                "reviewer": {"resolved": "verify-holder", "rule": "class"},
            },
            "EvidenceRefs": ["r1", "r2", "r3"],
        },
        "source_evidence": {
            "store": "execution.receipts",
            "receipt_count": 3,
            "window": ["2026-07-01 10:00:00+00",
                       "2026-07-02 11:00:00+00"],
            "title_hint": "unit fixture",
        },
        "states": [
            {"state": "PLAN_CREATE", "attempt_count": 1,
             "roles_raw": ["planner"], "first_at": "t0", "last_at": "t0"},
            {"state": "IMPLEMENTATION", "attempt_count": 1,
             "roles_raw": ["engineer"], "first_at": "t1", "last_at": "t1"},
            {"state": "REVIEW_PASS", "attempt_count": 1,
             "roles_raw": ["reviewer"], "first_at": "t2", "last_at": "t2"},
        ],
        "edges": [
            {"from": "PLAN_CREATE", "to": "IMPLEMENTATION", "count": 1,
             "guards": {"receipt-observed": {"type": "IMPLEMENTATION"},
                        "actor-pair": {"from_role": "planner",
                                       "to_role": "engineer",
                                       "evaluation": "live", "as_of": None}},
             "actors_raw": ["planner", "engineer"],
             "actors_resolved": ["planner", "engineer"],
             "evidence": ["r1->r2"]},
            {"from": "IMPLEMENTATION", "to": "REVIEW_PASS", "count": 1,
             "guards": {"receipt-observed": {"type": "REVIEW_PASS"},
                        "actor-pair": {"from_role": "engineer",
                                       "to_role": "verify-holder",
                                       "evaluation": "historical",
                                       "as_of": "2026-07-02T11:00:00Z"}},
             "actors_raw": ["engineer", "reviewer"],
             "actors_resolved": ["engineer", "verify-holder"],
             "evidence": ["r2->r3"]},
        ],
        "environment_events": [{"type": "API_LIMIT", "role_raw": "engineer",
                                "role_resolved": "engineer",
                                "receipt_id": "rX", "at": "tX"}],
        "terminal_state": "REVIEW_PASS",
        "suspect": False,
        "observations": [],
        "aegis_emission": "NOT PERFORMED (observer-only)",
    }
    flow.update(over)
    return flow


ALLOW = lambda: True  # noqa: E731 — injected G4 allow resolver


class GateTests(unittest.TestCase):
    def test_g1_unratified_refused(self):
        ok, failures = em.check_gates(make_flow(), capability_resolver=ALLOW)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("gate1") for f in failures))

    def test_g1_operator_go_satisfies(self):
        ok, failures = em.check_gates(make_flow(), capability_resolver=ALLOW,
                                      operator_go="go-record-uuid")
        self.assertTrue(ok, failures)

    def test_g1_ratified_flag_satisfies(self):
        ok, _ = em.check_gates(make_flow(ratified=True),
                               capability_resolver=ALLOW)
        self.assertTrue(ok)

    def test_g2_unresolved_refused(self):
        flow = make_flow(ratified=True)
        flow["parameters"]["RoleAlias_applied"]["wombat"] = {
            "resolved": "unresolved:wombat", "rule": "unresolved"}
        flow["suspect"] = True
        ok, failures = em.check_gates(flow, capability_resolver=ALLOW)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("gate2") for f in failures))

    def test_g3_no_workref_refused(self):
        flow = make_flow(ratified=True)
        del flow["parameters"]["WorkRef"]
        ok, failures = em.check_gates(flow, capability_resolver=ALLOW)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("gate3") for f in failures))

    def test_g4_default_deny(self):
        ok, failures = em.check_gates(make_flow(ratified=True),
                                      capability_resolver=None)
        self.assertFalse(ok)
        self.assertTrue(any("default deny" in f for f in failures))

    def test_g4_denied_when_resolver_false(self):
        ok, failures = em.check_gates(make_flow(ratified=True),
                                      capability_resolver=lambda: False)
        self.assertFalse(ok)
        self.assertTrue(any("can_emit_flows" in f for f in failures))

    def test_all_gates_pass_with_resolver_and_go(self):
        ok, failures = em.check_gates(make_flow(ratified=True),
                                      capability_resolver=ALLOW)
        self.assertTrue(ok, failures)


class DeterminismTests(unittest.TestCase):
    def test_byte_identical_reemission(self):
        a = em.serialize(em.emit_blueprint(make_flow(ratified=True),
                                           operator_go="go-1"))
        b = em.serialize(em.emit_blueprint(make_flow(ratified=True),
                                           operator_go="go-1"))
        self.assertEqual(a, b)

    def test_no_clock_no_uuid_in_artifact(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        text = json.dumps(art)
        for bad in ("created_at", "updated_at", "uuid", str(id(art))):
            self.assertNotIn(bad, text)

    def test_sha256_stable(self):
        f1 = make_flow(ratified=True)
        f2 = make_flow(ratified=True)
        self.assertEqual(em._sha256_flow(f1), em._sha256_flow(f2))


class ArtifactShapeTests(unittest.TestCase):
    """The artifact must load into the REAL aegis registry machinery."""

    def _registry(self, flow):
        art = em.emit_blueprint(flow)
        mgr = aegis_mod.StateMachineRegistryManager()
        reg = mgr.create_registry(art["machine_name"], art["description"])
        for sname, s in art["states"].items():
            self.assertTrue(mgr.add_state(reg.id, aegis_mod.StateDefinition(
                name=sname, description=s["description"],
                variable_assignments=s["variable_assignments"],
                concept_id=s["concept_id"])))
        for tname, t in art["transitions"].items():
            self.assertTrue(mgr.add_transition(
                reg.id, aegis_mod.TransitionDefinition(
                    name=tname, description=t["description"],
                    guard_expression=t["guard_expression"],
                    action=t["action"])))
        for inv in art["invariants"]:
            self.assertTrue(mgr.add_invariant(
                reg.id, aegis_mod.InvariantDefinition(
                    name=inv["name"], expression=inv["expression"],
                    description=inv.get("description"))))
        for cname, cm in art["concept_mappings"].items():
            reg.concept_mappings[cname] = aegis_mod.ConceptMapping(
                tla_name=cm["tla_name"], concept_id=cm["concept_id"],
                mapping_type=cm["mapping_type"], cardinality=cm["cardinality"])
        validation = mgr.validate_registry(reg.id)
        return art, validation

    def test_registry_validates_without_errors(self):
        art, validation = self._registry(make_flow(ratified=True))
        self.assertEqual(validation["errors"], [],
                         f"registry errors: {validation['errors']}")

    def test_transition_state_refs_are_declared_states(self):
        """aegis's _extract_state_references must find no undeclared states:
        guard facts are lower_snake, so every capitalized token in any
        guard_expression is a state name we declared."""
        flow = make_flow(ratified=True)
        art = em.emit_blueprint(flow)
        declared = set(art["states"])
        for tname, t in art["transitions"].items():
            refs = aegis_mod.StateMachineRegistryManager._extract_state_references(
                None, t["guard_expression"])
            undeclared = [r for r in refs if r not in declared]
            self.assertEqual(undeclared, [],
                             f"{tname}: undeclared state refs {undeclared}")

    def test_guard_grammar_balanced_and_lowercase_facts(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        for t in art["transitions"].values():
            ge = t["guard_expression"]
            self.assertEqual(ge.count("("), ge.count(")"))
            for fact in ge.split(" /\\ "):
                # lower_snake check must tolerate the digit-bearing as_of
                # suffix (RA2); the invariant is: no uppercase, no parens.
                self.assertEqual(fact, fact.lower(),
                                 f"fact not lowercase: {fact}")
                self.assertNotIn("(", fact)

    def test_ra2_historical_fact_carries_as_of(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        ge = art["transitions"]["IMPLEMENTATION_to_REVIEW_PASS"]["guard_expression"]
        self.assertIn("actor_pair_engineer_verify_holder_as_of_2026_07_02t11_00_00z", ge)
    def test_environment_events_are_variables_not_states(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        self.assertNotIn("API_LIMIT", art["states"])
        self.assertEqual(art["variables"]["env_events_observed"]["domain"],
                         ["API_LIMIT"])

    def test_q5_flow_ref_slot_present_and_empty(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        self.assertIn("flow_ref", art["metadata"])
        self.assertIsNone(art["metadata"]["flow_ref"])

    def test_q4_evaluation_note_declares_not_evaluates(self):
        art = em.emit_blueprint(make_flow(ratified=True))
        self.assertIn("wind-srv", art["metadata"]["evaluation_note"])

    def test_concept_mappings_bridge_every_guard(self):
        flow = make_flow(ratified=True)
        art = em.emit_blueprint(flow)
        guards = {c for e in flow["edges"] for c in e["guards"]}
        self.assertEqual(set(art["concept_mappings"]), guards)


class RoleFactsTests(unittest.TestCase):
    def test_unknown_guard_vocabulary_raises(self):
        with self.assertRaises(ValueError):
            em.role_facts({"lease-evil": {}})

    def test_live_actor_pair_shape(self):
        facts = em.role_facts({"actor-pair": {"from_role": "planner",
                                              "to_role": "engineer",
                                              "evaluation": "live"}})
        self.assertEqual(facts, ["actor_pair_planner_engineer"])

    def test_none_as_of_not_emitted_for_live(self):
        facts = em.role_facts({"actor-pair": {"from_role": "a",
                                              "to_role": "b",
                                              "evaluation": "live",
                                              "as_of": "should-not-appear"}})
        self.assertEqual(facts, ["actor_pair_a_b"])


class CliInertnessTests(unittest.TestCase):
    """The staged-inert contract at the CLI boundary: even a perfectly
    ratified record is REFUSED because G4 has no resolver wired."""

    def _run_cli(self, argv):
        import io
        from unittest import mock
        flow = make_flow(ratified=True)
        with mock.patch("sys.argv", ["flow-emit-aegis"] + argv), \
             mock.patch("builtins.open",
                        mock.mock_open(read_data=json.dumps(flow))), \
             mock.patch("sys.stderr", new=io.StringIO()), \
             mock.patch("sys.stdout", new=io.StringIO()):
            rc = em.main(argv)
        return rc

    def test_cli_refuses_perfect_record(self):
        rc = self._run_cli(["--flow-file", "/tmp/flow.json"])
        self.assertEqual(rc, 1)

    def test_cli_refuses_go_without_confirmation(self):
        rc = self._run_cli(["--flow-file", "/tmp/flow.json",
                            "--operator-go", "some-uuid"])
        self.assertEqual(rc, 2)

    def test_no_write_path_to_registry_stores(self):
        src = open(os.path.join(_REPO, "bin", "flow-emit-aegis.py"),
                   encoding="utf-8").read()
        for banned in ("StateMachineRegistryManager", "urllib.request",
                       "requests.", "psql"):
            self.assertNotIn(banned, src,
                             f"emitter must not write: found {banned}")


if __name__ == "__main__":
    unittest.main()
