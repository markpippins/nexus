"""Decision 18: the storage-shaped field exemption registry for the casing ratchet.

Ruling (record c53289ef): option (a) adopted as a FIELD-LEVEL registry — a
snake_case field in typespec/v1 is exempt ONLY if declared in
bin/contract-casing-exemptions.json with the storage location whose name the
wire field is required to mirror; anything undeclared — inside or outside the
CDLC family — is a ratchet violation. The floor is the non-exempt leak count.

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

    def test_every_entry_cites_storage_and_pr(self):
        """Decision 13 condition 2 via Decision 18: no silent growth — each
        exemption must cite the storage location and the introducing PR."""
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for field, entries in data["fields"].items():
            self.assertTrue(ccc.SNAKE.match(field),
                            f"registry field not snake_case: {field}")
            self.assertGreater(len(entries), 0, f"{field}: no entries")
            for entry in entries:
                self.assertTrue(entry.get("storage"),
                                f"{field}: entry lacks 'storage' (table.column/jsonb key)")
                self.assertTrue(entry.get("pr"),
                                f"{field}: entry lacks 'pr' (Decision 13 condition 2)")
                self.assertRegex(str(entry["storage"]),
                                 r"[a-z_]+\.[a-z_]+(\.[a-z_]+)*",
                                 f"{field}: storage must name a column or jsonb path")

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
        must equal the current non-exempt total (590 at registry landing)."""
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


import os
import contextlib

if __name__ == "__main__":
    unittest.main()
