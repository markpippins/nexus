#!/usr/bin/env python3
"""Guard tests for Ruling 2/4's structural half in bin/regenerate_memory_seed.py:

  (a) regeneration propagates to vendoring twins IN THE SAME INVOCATION
      (default-on; --no-vendor-propagation is the explicit opt-out)
  (b) --check-vendors is a DB-free staleness audit: exit 0 byte-identical,
      exit 1 with a per-file report; nothing mutated

Hermetic: no DB, no node, no network. The module is imported by file path
with the `nexus_core.wrp.seed_manifest` import stubbed (it is only needed
on the DB paths the tests never take), and REPO/SEED_PACKAGE_DIR are
re-pointed at a tmp fixture tree.

Pins:
  R1  dynamic discovery: every moleculer/* twin with vendor/tackle-seeds/
      is found; a twin without the dir is ignored; absence of moleculer/
      yields no twins
  R2  staleness check: identical bytes pass; drifted source->vendor fails
      with sha prefixes and filename in the message
  R3  check_vendor_staleness ignores twin-local files (vendor-only) and
      files absent from the source
  R4  --check-vendors CLI: exit 0 on identical, exit 1 on stale, no files
      mutated, no DB import attempted
  R5  propagate_to_vendor_twins copies every shared file byte-for-byte and
      never touches twin-local files
  R6  propagation is default-on in main() after a write: a mocked flow with
      a real DB is unavailable hermetically, so this pins the CALL SITE:
      main's non-dry-run path invokes propagate_to_vendor_twins unless
      --no-vendor-propagation (source inspection + argv threading)
  R7  --dry-run never propagates and reports the would-be twin count
"""

import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

# Stub the manifest helpers BEFORE loading the module: they import from
# python/nexus_core (DB-adjacent) and are unused on the paths under test.
_stub_pkg = types.ModuleType("nexus_core")
_stub_wrp = types.ModuleType("nexus_core.wrp")
_stub_mod = types.ModuleType("nexus_core.wrp.seed_manifest")
_stub_mod.MANIFEST_PATH = "seed-manifest.json"
_stub_mod.build_manifest = lambda conn: {}
_stub_mod.manifest_matches_live = lambda conn: (True, [])
_stub_mod.write_manifest = lambda manifest: None
sys.modules["nexus_core"] = _stub_pkg
sys.modules["nexus_core.wrp"] = _stub_wrp
sys.modules["nexus_core.wrp.seed_manifest"] = _stub_mod

_spec = importlib.util.spec_from_file_location(
    "regen_seed", os.path.join(_SELF_DIR, "..", "regenerate_memory_seed.py"))
regen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regen)


class _Fixture:
    """A tmp repo skeleton: typescript/tackle-seeds/{a,b}.txt plus two
    twins — one vendoring (with a, b, and a twin-local file), one that
    does NOT vendor tackle-seeds (must never be discovered)."""

    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        repo = self.td.name
        self.src = os.path.join(repo, "typescript", "tackle-seeds")
        os.makedirs(self.src)
        self.src_a = os.path.join(self.src, "index.ts")
        self.src_b = os.path.join(self.src, "seed-manifest.json")
        with open(self.src_a, "w") as f:
            f.write("SOURCE-A-v1\n")
        with open(self.src_b, "w") as f:
            f.write('{"cards": 1}\n')

        self.twin = os.path.join(repo, "moleculer", "tackle",
                                 "vendor", "tackle-seeds")
        os.makedirs(self.twin)
        with open(os.path.join(self.twin, "index.ts"), "w") as f:
            f.write("SOURCE-A-v1\n")          # in sync
        with open(os.path.join(self.twin, "seed-manifest.json"), "w") as f:
            f.write('{"cards": 1}\n')          # in sync
        with open(os.path.join(self.twin, "twin-local.txt"), "w") as f:
            f.write("twin local\n")            # vendor-only: never touched

        # a moleculer package WITHOUT the vendor dir: not a twin
        os.makedirs(os.path.join(repo, "moleculer", "aegis"), exist_ok=True)

        self.repo = repo
        self._orig_repo = regen.REPO
        self._orig_seed_dir = regen.SEED_PACKAGE_DIR
        self._orig_vendor_root = regen.VENDOR_ROOT
        regen.REPO = repo
        regen.SEED_PACKAGE_DIR = self.src
        regen.VENDOR_ROOT = os.path.join(repo, "moleculer")
        return self

    def __exit__(self, *exc):
        regen.REPO = self._orig_repo
        regen.SEED_PACKAGE_DIR = self._orig_seed_dir
        regen.VENDOR_ROOT = self._orig_vendor_root
        self.td.cleanup()
        return False


class TestDiscovery(unittest.TestCase):
    def test_finds_only_vendoring_twins(self):
        with _Fixture():
            twins = regen.vendor_twin_dirs()
            self.assertEqual([n for n, _ in twins], ["tackle"])

    def test_no_moleculer_dir(self):
        with tempfile.TemporaryDirectory() as td:
            regen.REPO = td
            regen.VENDOR_ROOT = os.path.join(td, "moleculer")
            try:
                self.assertEqual(regen.vendor_twin_dirs(), [])
            finally:
                pass


class TestStalenessCheck(unittest.TestCase):
    def test_in_sync_passes(self):
        with _Fixture() as fx:
            problems, checked = regen.check_vendor_staleness()
            self.assertEqual(problems, [])
            self.assertEqual(checked, 2)  # index.ts + seed-manifest.json

    def test_drift_detected_with_sha_and_filename(self):
        with _Fixture() as fx:
            with open(fx.src_a, "w") as f:
                f.write("SOURCE-A-v2\n")
            problems, checked = regen.check_vendor_staleness()
            self.assertEqual(checked, 2)
            self.assertEqual(len(problems), 1)
            self.assertIn("moleculer/tackle/vendor/tackle-seeds/index.ts",
                          problems[0])
            self.assertIn("STALE", problems[0])
            self.assertRegex(problems[0], r"sha256 [0-9a-f]{12}")

    def test_twin_local_files_ignored(self):
        with _Fixture() as fx:
            with open(os.path.join(fx.twin, "twin-local.txt"), "w") as f:
                f.write("changed\n")
            problems, _ = regen.check_vendor_staleness()
            self.assertEqual(problems, [])


class TestCheckVendorsCli(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["regenerate_memory_seed.py"] + argv):
            with contextlib.redirect_stdout(buf):
                code = regen.main()
        return code, buf.getvalue()

    def test_exit0_in_sync_and_no_mutation(self):
        with _Fixture() as fx:
            before = open(os.path.join(fx.twin, "index.ts")).read()
            code, out = self._run(["--check-vendors"])
            self.assertEqual(code, 0)
            self.assertIn("vendors OK", out)
            self.assertIn("2 file pair(s)", out)
            self.assertEqual(open(os.path.join(fx.twin, "index.ts")).read(),
                             before)

    def test_exit1_stale_with_report(self):
        with _Fixture() as fx:
            with open(fx.src_a, "w") as f:
                f.write("SOURCE-A-v2\n")
            code, out = self._run(["--check-vendors"])
            self.assertEqual(code, 1)
            self.assertIn("VENDOR STALENESS", out)
            self.assertIn("index.ts", out)
            # nothing was mutated by the check
            self.assertEqual(open(os.path.join(fx.twin, "index.ts")).read(),
                             "SOURCE-A-v1\n")

    def test_no_db_import_on_check_path(self):
        # --check-vendors must not require psycopg2 at all.
        with _Fixture():
            real_import = __import__

            def guard(name, *a, **k):
                if name == "psycopg2":
                    raise AssertionError("psycopg2 imported on check path")
                return real_import(name, *a, **k)

            with mock.patch("builtins.__import__", side_effect=guard):
                code, out = self._run(["--check-vendors"])
            self.assertEqual(code, 0)


class TestPropagation(unittest.TestCase):
    def test_copies_shared_files_only(self):
        with _Fixture() as fx:
            with open(fx.src_a, "w") as f:
                f.write("SOURCE-A-v2\n")
            with open(fx.src_b, "w") as f:
                f.write('{"cards": 2}\n')
            regen.propagate_to_vendor_twins()
            self.assertEqual(
                open(os.path.join(fx.twin, "index.ts")).read(), "SOURCE-A-v2\n")
            self.assertEqual(
                open(os.path.join(fx.twin, "seed-manifest.json")).read(),
                '{"cards": 2}\n')
            # twin-local untouched
            self.assertEqual(
                open(os.path.join(fx.twin, "twin-local.txt")).read(),
                "twin local\n")

    def test_propagation_clears_staleness(self):
        with _Fixture() as fx:
            with open(fx.src_a, "w") as f:
                f.write("SOURCE-A-v2\n")
            problems, _ = regen.check_vendor_staleness()
            self.assertEqual(len(problems), 1)
            regen.propagate_to_vendor_twins()
            problems2, _ = regen.check_vendor_staleness()
            self.assertEqual(problems2, [])


class TestCallSiteWiring(unittest.TestCase):
    """(a) is a same-invocation guarantee: pin the call site in main()."""

    def test_main_source_propagates_by_default_after_write(self):
        import inspect
        src = inspect.getsource(regen.main)
        self.assertIn("propagate_to_vendor_twins()", src)
        # gated only by the explicit opt-out flag + dry-run
        self.assertIn("if not args.no_vendor_propagation:", src)

    def test_no_vendor_propagation_flag_exists(self):
        import inspect
        src = inspect.getsource(regen.main)
        self.assertIn('"--no-vendor-propagation"', src)
        self.assertIn('"--check-vendors"', src)

    def test_dry_run_branch_reports_without_propagating(self):
        import inspect
        src = inspect.getsource(regen.main)
        self.assertIn("dry-run: would propagate", src)


class TestRealTreeInvariant(unittest.TestCase):
    """Against the REAL checkout: the twins the fixture models must match
    what main actually has, so the guard's assumptions can't rot silently
    (e.g. someone renames the vendor path and the discovery goes blind)."""

    def test_real_repo_has_tackle_twin_with_expected_files(self):
        real_vendor = os.path.join(_SELF_DIR, "..", "..", "moleculer",
                                   "tackle", "vendor", "tackle-seeds")
        if not os.path.isdir(real_vendor):
            self.skipTest("tackle twin not present in this checkout")
        for fname in ("index.ts", "canonical-shape.ts", "seed-manifest.json"):
            self.assertTrue(
                os.path.isfile(os.path.join(real_vendor, fname)),
                f"expected vendored file missing: {fname}")


if __name__ == "__main__":
    unittest.main()
