"""Unit tests for the W-C consumer inventory (plan 8261665).

The registry is a pure data structure plus a structural validator, so these
tests need no database. The live census is exercised separately by
``--census``; here we prove the checked-in artifact does not drift from the
module and that the package/gate contract is internally complete.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from nebula import wc_consumers as wc  # noqa: E402

_ARTIFACT = _REPO_ROOT / "docs" / "promotion-flow" / "w-c-consumer-inventory.json"


class RegistryShapeTest(unittest.TestCase):
    def test_validation_is_clean(self):
        self.assertEqual(wc.validate(), [])

    def test_expected_counts(self):
        self.assertEqual(len(wc.PACKAGES), 9)
        self.assertEqual(len(wc.CONSUMERS), 68)
        self.assertEqual(len(wc.GATES), 9)
        self.assertEqual(len(wc.FINDINGS), 13)

    def test_every_consumer_has_exactly_one_package(self):
        seen = set()
        for consumer in wc.CONSUMERS:
            self.assertNotIn(consumer["id"], seen)
            seen.add(consumer["id"])
            self.assertIn(consumer["package"], wc.PACKAGES)
            self.assertIn(consumer["mode"], wc._ALLOWED_MODES)
            self.assertIn(consumer["runtime"], wc._ALLOWED_RUNTIMES)

    def test_every_package_has_a_gate_and_evidence(self):
        for pid, pkg in wc.PACKAGES.items():
            self.assertTrue(pkg.get("evidence_outputs"), pid)
            self.assertTrue(pkg.get("stop_condition"), pid)
            self.assertIn(pkg["fence"], wc.FENCES, pid)
            gate = wc.GATES.get(pkg["gate"])
            self.assertIsNotNone(gate, pid)
            self.assertEqual(gate["package"], pid)

    def test_db_layer_gates_require_dba(self):
        for gate_id, gate in wc.GATES.items():
            if gate.get("db_layer"):
                self.assertEqual(
                    set(gate["signatories"]), {"operator", "architect", "dba"}, gate_id
                )
            else:
                self.assertEqual(
                    set(gate["signatories"]), {"operator", "architect"}, gate_id
                )
                self.assertTrue(gate.get("dba_waiver"), gate_id)

    def test_no_dependency_cycles(self):
        self.assertEqual(wc._dependency_cycles(wc.PACKAGES), [])

    def test_wave_declares_wa_and_wb_dependencies(self):
        deps = wc.WAVE["dependencies"]
        self.assertIn("W-A", deps)
        self.assertIn("W-B", deps)

    def test_census_is_read_only_sql(self):
        # The census opens a readonly session; guard against a later edit that
        # smuggles a write into the collector.
        sql = wc._CENSUS_SQL.upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER"):
            self.assertNotIn(verb, sql)


class ArtifactDriftTest(unittest.TestCase):
    def test_checked_in_artifact_matches_registry(self):
        self.assertTrue(_ARTIFACT.is_file(), f"missing artifact: {_ARTIFACT}")
        self.assertEqual(_ARTIFACT.read_text(encoding="utf-8"), wc.dumps())

    def test_artifact_is_valid_json(self):
        doc = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(
            set(doc),
            {
                "wave",
                "snapshot",
                "identity_model",
                "fences",
                "packages",
                "consumers",
                "gates",
                "findings",
            },
        )


if __name__ == "__main__":
    unittest.main()
