"""Decision 18 + Decision 20: the storage-shaped field exemption registry for
the casing ratchet.

Decision 18 (record c53289ef): option (a) adopted as a FIELD-LEVEL registry — a
snake_case field in typespec/v1 is exempt ONLY if declared in
bin/contract-casing-exemptions.json with the storage location whose name the
wire field is required to mirror; anything undeclared — inside or outside the
CDLC family — is a ratchet violation. The floor is the non-exempt leak count.

Decision 20 (record 25f5fa36): unified entry kinds — column|jsonb (canonical
storage spelling), external (non-nexus store, e.g. MongoDB keychain), table
(table-scoped envelope key), computed (derived read-model aggregate with a
dated camelCase remediation target; unremediated past target = violation).
Malformed registries hard-fail (ValueError), never silently widen.

Both directions are tested, per the ruling's consequence for the tester:
exempt-declared fields pass; undeclared snake_case fails, including a
same-family-but-unlisted field.
"""
import importlib.util
import json
import pathlib
import sys
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1]
CHECKER = BIN / "check_contract_casing.py"
REGISTRY = BIN / "contract-casing-exemptions.json"
BASELINE = BIN / "contract-casing-baseline.json"

spec = importlib.util.spec_from_file_location("ccc", CHECKER)
ccc = importlib.util.module_from_spec(spec)
sys.modules["ccc"] = ccc
spec.loader.exec_module(ccc)


class TestRegistryShape(unittest.TestCase):
    def test_registry_exists_and_parses(self):
        self.assertTrue(REGISTRY.exists(), "exemption registry missing")
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("fields"), dict,
                              "registry must have a top-level 'fields' object")

    def test_every_entry_cites_pr_and_kind_appropriate_evidence(self):
        """Decision 13 condition 2 via Decision 18/20: no silent growth — each
        exemption cites the introducing PR plus kind-appropriate evidence:
        storage kinds cite the canonical location; computed cites the
        producing computation AND a dated remediation target."""
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for field, entries in data["fields"].items():
            self.assertTrue(ccc.SNAKE.match(field),
                            f"registry field not snake_case: {field}")
            self.assertGreater(len(entries), 0, f"{field}: no entries")
            for entry in entries:
                self.assertTrue(entry.get("pr"),
                                f"{field}: entry lacks 'pr' (Decision 13 condition 2)")
                kind = entry.get("kind")
                self.assertIn(kind, ccc.VALID_KINDS,
                              f"{field}: invalid kind {kind!r} (Decision 20 schema)")
                if kind in ccc.STORAGE_KINDS:
                    self.assertTrue(entry.get("storage"),
                                    f"{field}: {kind} entry lacks 'storage'")
                    self.assertRegex(str(entry["storage"]),
                                     r"[a-z_:][a-zA-Z0-9_.\[\]]*",
                                     f"{field}: storage must name a location")
                if kind == "computed":
                    self.assertTrue(entry.get("computation"),
                                    f"{field}: computed entry lacks 'computation'")
                    self.assertRegex(str(entry.get("target") or ""),
                                     r"^\d{4}-\d{2}-\d{2}$",
                                     f"{field}: computed target must be a YYYY-MM-DD date")

    def test_exempt_fields_actually_exist_in_the_tree(self):
        """An exemption for a field that occurs nowhere is dead weight and
        silently widens the gate — catch drift in either direction."""
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        live = set()
        for f in ccc.TYPESPEC.rglob("models.tsp"):
            live.update(ccc.snake_fields(f))
        for field in data["fields"]:
            self.assertIn(field, live,
                          f"exemption declares a field absent from typespec/v1: {field}")


class TestExemptionSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = ccc.scan()

    def test_exemptions_remove_declared_fields_from_leaks(self):
        """Direction 1: exempt-declared fields pass — they are not leaks."""
        for f, names in self.data.get("exempt", {}).items():
            for n in names:
                self.assertNotIn(n, self.data["leaks"].get(f, []),
                                 f"{f}: exempted field counted as a leak: {n}")

    def test_floor_is_the_non_exempt_count(self):
        """Decision 18: 'leaks' is the NON-EXEMPT count. The baseline floor
        must equal the current non-exempt total (575 after the Decision 20
        computed entries; 590 at the Decision 18 registry landing)."""
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(base["leaks"], self.data["leak_total"],
                         "baseline floor must be recalculated to the "
                         "non-exempt leak count after registry changes")
        self.assertEqual(base["leaks"] + self.data["exempt_total"], 616,
                         "exempt + non-exempt must reconcile to the "
                         "pre-registry total (616)")

    def test_ratchet_passes_with_registry(self):
        rc = ccc.main.__wrapped__() if hasattr(ccc.main, "__wrapped__") else None
        # run the real gate end-to-end in ratchet mode via argv override
        import contextlib
        argv = sys.argv
        sys.argv = ["check_contract_casing.py"]
        try:
            with contextlib.redirect_stdout(pathlib.Path(os.devnull).open("w")) if False else contextlib.nullcontext():
                rc = ccc.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0, "ratchet must pass with the registry applied")

    def test_undeclared_snake_case_still_violates(self):
        """Direction 2: an undeclared snake_case field — even in the same
        CDLC family as declared ones — is a leak."""
        scan = ccc.scan(exemptions={})
        # without the registry every snake field leaks (pre-Decision-18 view)
        total_no_registry = scan["leak_total"]
        self.assertEqual(total_no_registry, 616,
                         "registry-off scan must reproduce the 616 baseline")
        # a field deliberately NOT registered but present in the same family
        undeclared = "generated_at"
        self.assertNotIn(undeclared, ccc.load_exemptions(),
                         f"{undeclared} must stay undeclared (derived aggregate)")
        leaks_nb = self.data["leaks"].get(
            "typespec/v1/nexus-broker/typescript/models.tsp", [])
        self.assertIn(undeclared, leaks_nb,
                      "derived aggregate in the CDLC family must remain a leak")

    def test_missing_registry_fails_closed_to_no_exemptions(self):
        """A missing registry file means no exemptions — the ratchet cannot
        be widened by deleting the file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            gone = pathlib.Path(tmp) / "contract-casing-exemptions.json"
            self.assertFalse(gone.exists())
            # loader on a non-existent path returns {} (no exemptions)
            orig = ccc.EXEMPTIONS
            try:
                ccc.EXEMPTIONS = gone
                self.assertEqual(ccc.load_exemptions(), {})
                scan = ccc.scan()
                self.assertEqual(scan["leak_total"], 616,
                                 "no registry -> full 616 leak view (fail closed)")
            finally:
                ccc.EXEMPTIONS = orig

    def test_malformed_registry_fails_loudly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "contract-casing-exemptions.json"
            bad.write_text(json.dumps({"no_fields_key": True}), encoding="utf-8")
            orig = ccc.EXEMPTIONS
            try:
                ccc.EXEMPTIONS = bad
                with self.assertRaises(ValueError):
                    ccc.load_exemptions()
            finally:
                ccc.EXEMPTIONS = orig


class TestDecision20Kinds(unittest.TestCase):
    """Decision 20 (record 25f5fa36): computed/external/table kinds, remediation
    pairing, hard-fail validation — the anti-phantom discipline extended."""

    @classmethod
    def setUpClass(cls):
        cls.reg = json.loads(REGISTRY.read_text(encoding="utf-8"))["fields"]

    def test_unified_registry_carries_all_kinds(self):
        kinds = {e.get("kind") for entries in self.reg.values() for e in entries}
        self.assertIn("column", kinds)
        self.assertIn("jsonb", kinds)
        self.assertIn("computed", kinds)
        self.assertIn("external", kinds)
        self.assertIn("table", kinds)

    def test_eleven_computed_census_aggregates_declared(self):
        """Decision 20 option (1): the 11 buildCensusReportIndex aggregates
        enter as computed with the same evidence discipline."""
        expected = {
            "report_count", "category_counts", "finding_count", "daily_counts",
            "observed_day_count", "trigger_counts", "rejected_report_count",
            "rejected_reports", "report_ids_with_no_findings",
            "report_count_no_findings", "anticipated_finding_count",
        }
        self.assertTrue(expected.issubset(self.reg),
                        f"missing computed entries: {sorted(expected - set(self.reg))}")
        for field in expected:
            for entry in self.reg[field]:
                self.assertEqual(entry.get("kind"), "computed")
                self.assertIn("buildCensusReportIndex", entry.get("computation", ""))
                self.assertTrue(entry.get("target"), f"{field}: no remediation target")

    def test_computed_entries_without_target_hard_fail(self):
        """A computed entry with no dated target cannot exist: remediation
        pairing is what stops computed from becoming a permanent waiver."""
        import tempfile
        broken = {
            "fields": {
                "some_count": [{"kind": "computed", "computation": "x", "pr": "#1"}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "contract-casing-exemptions.json"
            bad.write_text(json.dumps(broken), encoding="utf-8")
            orig = ccc.EXEMPTIONS
            try:
                ccc.EXEMPTIONS = bad
                with self.assertRaises(ValueError):
                    ccc.load_exemptions()
            finally:
                ccc.EXEMPTIONS = orig

    def test_computed_without_computation_hard_fails(self):
        import tempfile
        broken = {
            "fields": {
                "some_count": [{"kind": "computed", "target": "2026-12-31", "pr": "#1"}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "contract-casing-exemptions.json"
            bad.write_text(json.dumps(broken), encoding="utf-8")
            orig = ccc.EXEMPTIONS
            try:
                ccc.EXEMPTIONS = bad
                with self.assertRaises(ValueError):
                    ccc.load_exemptions()
            finally:
                ccc.EXEMPTIONS = orig

    def test_storage_kind_without_storage_hard_fails(self):
        import tempfile
        broken = {
            "fields": {
                "some_id": [{"kind": "column", "pr": "#1"}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "contract-casing-exemptions.json"
            bad.write_text(json.dumps(broken), encoding="utf-8")
            orig = ccc.EXEMPTIONS
            try:
                ccc.EXEMPTIONS = bad
                with self.assertRaises(ValueError):
                    ccc.load_exemptions()
            finally:
                ccc.EXEMPTIONS = orig

    def test_expired_computed_target_is_a_violation(self):
        """Decision 20: unremediated past milestone = violation — the expiry
        predicate must flag computed entries whose target has lapsed, and
        only those."""
        fields = {
            "past_field": [{"kind": "computed", "target": "2026-01-01"}],
            "future_field": [{"kind": "computed", "target": "2099-01-01"}],
            "storage_field": [{"kind": "column", "target": "2020-01-01"}],
        }
        expired = ccc.expired_computed(fields, "2026-09-29")
        self.assertEqual(set(expired), {"past_field"})
        self.assertEqual(expired["past_field"], ["2026-01-01"])

    def test_live_computed_targets_not_expired(self):
        """The shipped registry must not be expired today (guards against
        landing an already-lapsed remediation target)."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(ccc.expired_computed(self.reg, today), {},
                         "shipped registry has computed entries past their target")


import os
import contextlib

if __name__ == "__main__":
    unittest.main()
