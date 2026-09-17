#!/usr/bin/env python3
"""Hermetic tests: attestation-chain gate contract (wr-conf-031, Wave 3).

No database, no network — the gate contract is pure logic and the
capability resolver is injected. Pins the contract the Wave-3 thread
cites for the lead-engineer greenlight ratification:

  G1 self-attestation refused (authoring role cannot attest)
  G2 evidence-free attestation refused
  G3 attester must hold can_verify_work_requests (via injected resolver)
  G4 greenlight must cite a valid attestation that passed G1-G3
     (greenlight_without_verification_attestation)

Plus the full positive chain via run_chain, and resolver-misbehavior
(unreachable DB must raise, not attest absence — auditor epistemic rule).
"""
import importlib.util
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.abspath(
    os.path.join(_SELF, "..", "..", "..", "..", "bin", "attestation-chain-demo.py"))
_spec = importlib.util.spec_from_file_location("attestation_chain_demo", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["attestation_chain_demo"] = _mod
_spec.loader.exec_module(_mod)

from attestation_chain_demo import (  # noqa: E402
    Attestation, Greenlight, Refusal, VerificationRequest,
    gate_capability, gate_evidence, gate_greenlight_citation,
    gate_self_attestation, make_live_resolver, run_chain,
)

REQUEST = VerificationRequest("wr:123", requested_by="dba", scope="test-verification")
EVIDENCE = ["CI run green", "live probe output", "R2 record"]


def resolver(caps_by_role):
    def resolve(role):
        return caps_by_role.get(role, {})
    return resolve


GOOD = resolver({"tester": {"can_verify_work_requests": True},
                 "auditor": {"can_verify_work_requests": True},
                 "lead-engineer": {"can_verify_work_requests": False,
                                   "can_greenlight": True}})


class TestG1SelfAttestation(unittest.TestCase):
    def test_authoring_role_refused(self):
        att = Attestation("wr:123", "dba", EVIDENCE)
        with self.assertRaises(Refusal) as ctx:
            gate_self_attestation(REQUEST, att)
        self.assertEqual("G1-self-attestation", ctx.exception.gate)

    def test_second_role_allowed(self):
        gate_self_attestation(REQUEST, Attestation("wr:123", "tester", EVIDENCE))


class TestG2Evidence(unittest.TestCase):
    def test_empty_evidence_refused(self):
        with self.assertRaises(Refusal) as ctx:
            gate_evidence(Attestation("wr:123", "tester", []))
        self.assertEqual("G2-evidence-free", ctx.exception.gate)

    def test_whitespace_only_refused(self):
        with self.assertRaises(Refusal):
            gate_evidence(Attestation("wr:123", "tester", ["  "]))

    def test_citable_evidence_passes(self):
        gate_evidence(Attestation("wr:123", "tester", EVIDENCE))


class TestG3Capability(unittest.TestCase):
    def test_holder_passes(self):
        gate_capability(Attestation("wr:123", "tester", EVIDENCE), GOOD)

    def test_non_holder_refused(self):
        with self.assertRaises(Refusal) as ctx:
            gate_capability(Attestation("wr:123", "builder", EVIDENCE), GOOD)
        self.assertEqual("G3-capability", ctx.exception.gate)

    def test_unknown_role_refused(self):
        with self.assertRaises(Refusal):
            gate_capability(Attestation("wr:123", "nobody", EVIDENCE), GOOD)


class TestG4GreenlightCitation(unittest.TestCase):
    def test_no_attestation_refused(self):
        g = Greenlight("wr:123", "lead-engineer", None)
        with self.assertRaises(Refusal) as ctx:
            gate_greenlight_citation(g, REQUEST, [])
        self.assertEqual("greenlight_without_verification_attestation",
                         ctx.exception.gate)

    def test_work_ref_mismatch_refused(self):
        att = Attestation("wr:999", "tester", EVIDENCE)
        g = Greenlight("wr:123", "lead-engineer", att)
        with self.assertRaises(Refusal):
            gate_greenlight_citation(g, REQUEST, [att])

    def test_uncertified_attestation_refused(self):
        """An attestation object that never passed the gates cannot back a
        greenlight — attestation is not self-declared."""
        att = Attestation("wr:123", "tester", EVIDENCE)  # never gate-checked
        g = Greenlight("wr:123", "lead-engineer", att)
        with self.assertRaises(Refusal):
            gate_greenlight_citation(g, REQUEST, [])

    def test_valid_citation_passes(self):
        att = Attestation("wr:123", "tester", EVIDENCE)
        gate_greenlight_citation(
            Greenlight("wr:123", "lead-engineer", att), REQUEST, [att])


class TestRunChain(unittest.TestCase):
    def test_full_chain_reports_four_steps(self):
        att = Attestation("wr:123", "tester", EVIDENCE)
        result = run_chain(REQUEST, att,
                           Greenlight("wr:123", "lead-engineer", att), GOOD)
        self.assertEqual(
            ["G1-self-attestation", "G2-evidence-free", "G3-capability",
             "greenlight_without_verification_attestation"],
            [s["gate"] for s in result["steps"]])
        self.assertTrue(all(s["result"] == "PASS" for s in result["steps"]))

    def test_chain_refuses_at_first_failing_gate(self):
        att = Attestation("wr:123", "dba", EVIDENCE)  # self-attestation
        with self.assertRaises(Refusal) as ctx:
            run_chain(REQUEST, att,
                      Greenlight("wr:123", "lead-engineer", att), GOOD)
        self.assertEqual("G1-self-attestation", ctx.exception.gate)


class TestLiveResolverDiscipline(unittest.TestCase):
    def test_unreachable_db_raises_not_absent(self):
        """A failed DB connection must raise — never attest absence."""
        import os
        old = os.environ.get("CONDUIT_PG_DSN")
        os.environ["CONDUIT_PG_DSN"] = (
            "postgresql://pguser:pgpass@127.0.0.1:1/nexus")  # nothing listens
        try:
            resolve = make_live_resolver()
            with self.assertRaises(Exception) as ctx:
                resolve("tester")
            self.assertNotIsInstance(ctx.exception, KeyError)
        finally:
            if old is None:
                os.environ.pop("CONDUIT_PG_DSN", None)
            else:
                os.environ["CONDUIT_PG_DSN"] = old

    def test_missing_role_is_absent_not_error(self):
        """After a successful connect, an unknown role is a capability
        absence — resolver returns falsy caps, does not raise."""
        resolve = make_live_resolver()
        caps = resolve("role-that-does-not-exist-zzz")
        self.assertFalse(caps.get("can_verify_work_requests"))


if __name__ == "__main__":
    unittest.main()
