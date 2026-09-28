"""Tests for the Gestalt pressure #5 contract markers and counter.

Each test that asserts current counts also documents a negative check: the
guard is proven to fail when the condition it guards against is introduced,
so a green run is evidence rather than a tautology.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

import yaml

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent
ROOT = DOCS.parent.parent
sys.path.insert(0, str(DOCS))

_spec = importlib.util.spec_from_file_location("gen_openapi", DOCS / "gen_openapi.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

PRESSURE = DOCS / "check_contract_pressure.py"


def run_pressure(*args):
    return subprocess.run([sys.executable, str(PRESSURE), *args],
                          capture_output=True, text=True, cwd=ROOT)


def operations(path):
    doc = yaml.safe_load(path.read_text(errors="ignore")) or {}
    return {(m.upper(), p) for p, ops_ in (doc.get("paths") or {}).items()
            for m in ops_ if m.lower() in ("get", "post", "put", "patch", "delete")}


class TestContractResolution(unittest.TestCase):
    def test_resolves_source_and_compiled(self):
        got = gen.contract_of_record("typescript/aegis-srv")
        self.assertEqual(got["source"], "typespec/v1/aegis-srv")
        self.assertTrue(got["compiled"].endswith("openapi.yaml"))
        self.assertTrue((ROOT / got["compiled"]).is_file())

    def test_unmodelled_service_returns_none_not_a_guess(self):
        # A wrong pointer is worse than admitting there is none.
        self.assertIsNone(gen.contract_of_record("typescript/voyager-srv"))
        self.assertIsNone(gen.contract_of_record("typescript/peb-srv"))

    def test_pointer_never_dangles(self):
        for key in gen.CONTRACT_OF_RECORD:
            got = gen.contract_of_record(key)
            if got is None:
                continue
            self.assertTrue((ROOT / got["source"]).is_dir(),
                            f"{key} points at a missing contract dir")
            if got["compiled"]:
                self.assertTrue((ROOT / got["compiled"]).is_file(),
                                f"{key} points at a missing compiled schema")


class TestEmittedSpec(unittest.TestCase):
    """The committed specs must carry the markers, or consumers still see silence."""

    @classmethod
    def setUpClass(cls):
        cls.paths = [(k, pathlib.Path(gen.spec_path(k))) for k in gen.SERVICES
                     if k not in gen.SKIPPED_KEYS
                     and pathlib.Path(gen.spec_path(k)).is_file()]

    def test_population_is_the_19_expected(self):
        # Cross-check: must equal the count of specs declaring untyped bodies,
        # and the pressure classes must sum to it. 19 since #603 added
        # substance-srv on main after this branch originally cut; its untyped
        # bodies are real pressure and the ratchet floor moved with it.
        self.assertEqual(len(self.paths), 19)

    def test_every_generated_spec_declares_untyped_bodies(self):
        for key, p in self.paths:
            self.assertEqual(yaml.safe_load(p.read_text(errors="ignore"))
                             .get("x-response-bodies"), "untyped",
                             f"{key} does not declare its bodies untyped")

    def test_contract_pointer_present_or_explicitly_null(self):
        for key, p in self.paths:
            doc = yaml.safe_load(p.read_text(errors="ignore"))
            self.assertIn("x-contract-of-record", doc,
                          f"{key} omits the contract pointer entirely")
            self.assertEqual(doc.get("x-contract-of-record"),
                             gen.contract_of_record(key))

    def test_jsonbody_cannot_silently_return_to_being_generous(self):
        # The regression guarded: 'Generic JSON body (fields are
        # service-specific)' reads as a schema. It must stay UNSHAPED.
        for key, p in self.paths:
            desc = (yaml.safe_load(p.read_text(errors="ignore"))
                    .get("components", {}).get("schemas", {})
                    .get("JsonBody", {}).get("description", ""))
            self.assertIn("UNSHAPED", desc, f"{key}: JsonBody reads as a schema")
            self.assertIn("Do not generate a client from this property", desc)

    def test_aegis_contract_already_covers_the_whole_service_surface(self):
        # The load-bearing fact behind the whole pressure class: a typed
        # contract exists and matches every operation, so the untyped
        # inventory is a strictly worse copy of a complete contract.
        c = gen.contract_of_record("typescript/aegis-srv")["compiled"]
        self.assertTrue(operations(ROOT / c), "compiled contract has no operations")
        self.assertEqual(operations(ROOT / c),
                         operations(ROOT / "typescript/aegis-srv/openapi.yaml"),
                         "aegis-srv contract no longer covers the full surface")


class TestPressureCounter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(run_pressure("--json").stdout)

    def test_counts_match_the_committed_baseline(self):
        baseline = json.loads((DOCS / "contract-pressure-baseline.json").read_text())
        for k, v in self.data["classes"].items():
            self.assertEqual(baseline[k], len(v), f"{k} drifted from its ratchet floor")

    def test_typed_ignored_plus_unmodelled_equals_untyped_population(self):
        # Guards the counter against silently skipping a service.
        self.assertEqual(
            len(self.data["classes"]["typed-contract-ignored"])
            + len(self.data["classes"]["unmodeled-boundary"]), 19)

    def test_aegis_is_the_provable_case(self):
        self.assertIn("typescript/aegis-srv",
                      self.data["classes"]["typed-contract-ignored"])
        self.assertEqual(self.data["detail"]["typescript/aegis-srv"]["contract"]["compiled"],
                         "typespec/v1/aegis-srv/generated/schema/openapi.yaml")

    def test_ratchet_passes_at_baseline(self):
        self.assertEqual(run_pressure().returncode, 0)

    def test_strict_fails_while_pressure_exists(self):
        # Negative check: --strict must fail on the real, non-zero state.
        self.assertEqual(run_pressure("--strict").returncode, 1)

    def test_ratchet_fails_when_pressure_grows(self):
        # Negative check: drop one class below its floor, expect a hard fail.
        p = DOCS / "contract-pressure-baseline.json"
        orig = p.read_text()
        b = json.loads(orig)
        b["unmodeled-boundary"] -= 1
        try:
            p.write_text(json.dumps(b, indent=2))
            r = run_pressure()
            self.assertEqual(r.returncode, 1, "ratchet did not fail on grown pressure")
            self.assertIn("must not grow", r.stderr)
        finally:
            p.write_text(orig)


if __name__ == "__main__":
    unittest.main(verbosity=2)
