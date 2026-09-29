"""Hermetic tests for the registry guard in tools/api-docs/check_drift.py.

The drift gate's coverage is data-driven: find_services() silently skips a
registry key whose target directory is missing (the ``os.path.isdir`` gate),
so a deleted/renamed twin shrinks the gate while every remaining check stays
green. That exact failure mode shipped once: operator merge fa15c433 dropped
the whole moleculer/peb/ twin — 35 files — and check_drift.py stayed green.

The guard (registry_problems + exit 2) makes that fail closed. These tests
pin BOTH directions:

  positive — the live tree's registries all resolve (green is evidence)
  negative — a synthetic dangling key is detected and the CLI exits 2

No terrain, no nebula, no route extraction: only directory existence and the
argparse wiring are exercised. The --check-registry-only path avoids the
extraction pipeline entirely, so the CLI-level test is hermetic too.
"""

import contextlib
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_DOCS = os.path.join(_REPO, "tools", "api-docs")

sys.path.insert(0, _DOCS)
_spec = importlib.util.spec_from_file_location("check_drift", os.path.join(_DOCS, "check_drift.py"))
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


class LiveTreeRegistries(unittest.TestCase):
    """Positive side: on a complete checkout every registry key must resolve."""

    def test_live_registries_resolve_to_existing_dirs(self):
        self.assertEqual(
            cd.registry_problems(),
            [],
            "dangling registry keys — the drift gate would silently lose coverage",
        )

    def test_find_services_covers_every_registry_key(self):
        # Every registry-driven key must actually make it into the service
        # inventory; a key missing from find_services() output is coverage
        # the gate has silently lost (the fa15c433 failure shape).
        services = cd.find_services()
        for key in list(cd.er.MOLECULER_SERVICES) + list(cd.er.JVM_SERVICES):
            self.assertIn(key, services, f"registry key {key} absent from find_services()")

    def test_every_mirror_key_is_a_registered_moleculer_app(self):
        # MOLECULER_MIRRORS pins each twin to its incumbent contract, but only
        # keys in extract_routes.MOLECULER_SERVICES are ever extracted and
        # judged. A mirror key outside that registry is an inert pair: the
        # mirror exists, the gate never runs it.
        for key in cd.MOLECULER_MIRRORS:
            self.assertIn(key, cd.er.MOLECULER_SERVICES,
                          f"mirror {key} has no MOLECULER_SERVICES entry — pair is inert")

    def test_check_registry_only_cli_is_green(self):
        proc_out = io.StringIO()
        with mock.patch.object(sys, "argv", ["check_drift.py", "--check-registry-only"]), \
             contextlib.redirect_stdout(proc_out):
            rc = cd.main()
        self.assertEqual(rc, 0)
        self.assertIn("Registry OK", proc_out.getvalue())


class DanglingKeyDetection(unittest.TestCase):
    """Negative side: the guard must fire, name the registry, and fail closed."""

    def test_missing_dir_is_reported(self):
        problems = cd.registry_problems([
            ("TEST_REGISTRY", {
                "moleculer/ghost": "moleculer/__definitely_missing_twin__",
                "moleculer/real": "tools/api-docs",  # exists — must NOT be reported
            }),
        ])
        self.assertEqual(problems, [("TEST_REGISTRY", "moleculer/ghost",
                                     "moleculer/__definitely_missing_twin__")])

    def test_check_registry_only_cli_exits_2_on_dangling_key(self):
        patched = dict(cd.MOLECULER_MIRRORS)
        patched["moleculer/ghost"] = "moleculer/__definitely_missing_twin__"
        proc_out = io.StringIO()
        with mock.patch.object(cd, "MOLECULER_MIRRORS", patched), \
             mock.patch.object(sys, "argv", ["check_drift.py", "--check-registry-only"]), \
             contextlib.redirect_stdout(proc_out):
            rc = cd.main()
        self.assertEqual(rc, 2)
        out = proc_out.getvalue()
        self.assertIn("[MOLECULER_MIRRORS] moleculer/ghost", out)
        self.assertIn("does not exist", out)
        self.assertNotIn("Registry OK", out)

    def test_verify_mode_refuses_to_gate_on_dangling_key(self):
        # Without --check-registry-only, a dangling key must still exit 2 —
        # it must never look like a clean drift pass.
        patched = dict(cd.er.MOLECULER_SERVICES)
        patched["moleculer/ghost"] = "moleculer/__definitely_missing_twin__"
        with mock.patch.object(cd.er, "MOLECULER_SERVICES", patched), \
             mock.patch.object(sys, "argv", ["check_drift.py", "--quiet"]), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = cd.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
