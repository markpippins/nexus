#!/usr/bin/env python3
"""Hermetic tests for lockfile-vs-installed drift detection.

Context: Ruling 38L/38M made lockfiles tracked, which fixes what gets resolved
at install time. It does not notice a tree whose `node_modules` and lockfile
have parted ways -- which is how `adonisjs/nexus-control-edge` came to run
7.5.0 under a lockfile that was itself ignored by git, so a fast-forward
replaced it with no error and the declared version silently stopped describing
the running service.

What is pinned here:

  * MISMATCH is detected: a package present at the wrong version is drift.
  * Optional-but-absent is NOT drift. npm lists optionalDependencies for every
    platform but only materialises the os/cpu-matching subset, so a linux-x64
    tree legitimately has no `@esbuild/darwin-arm64`. An earlier version of
    this check ignored `optional` and reported 12 of 20 live services as
    drifted on that basis alone; a guard that cries wolf gets switched off.
  * Optional-but-PRESENT at the wrong version IS still drift.
  * Non-optional-but-absent IS drift: the tree is not what the lock says.
  * Nested (`node_modules/a/node_modules/b`) and scoped (`@scope/pkg`) paths
    resolve, because that is how npm actually lays out conflicting deps.
  * "Could not check" is a distinct outcome from "checked and matched". A tree
    with no node_modules must never report success, or a CI run that verified
    nothing would look identical to one that verified everything.
  * An extra package on disk is not drift -- hoisting legitimately produces
    packages the lockfile does not pin at that path.

Run:
    python3 -m pytest bin/tests/test_lockfile_drift.py -v
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin" / "lib"))

from lockfile_drift import (  # noqa: E402
    DRIFTED,
    MATCHED,
    SKIPPED,
    Pin,
    check_service,
    compare,
    read_lock_pins,
)

CLI = REPO_ROOT / "bin" / "check_lockfile_drift.py"


def write_lock(service: pathlib.Path, packages: dict, root_deps: dict | None = None) -> pathlib.Path:
    """Write a minimal lockfileVersion-3 lockfile for `service`."""
    service.mkdir(parents=True, exist_ok=True)
    lock = service / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "name": service.name,
                "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "": {"name": service.name, "dependencies": root_deps or {}},
                    **packages,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return lock


def install(service: pathlib.Path, key: str, version: str) -> None:
    """Materialise node_modules/<key>/package.json at `version`."""
    target = service / key / "package.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"name": key.split("node_modules/")[-1], "version": version}), encoding="utf-8")


class TestReadLockPins(unittest.TestCase):
    def test_reads_versions_and_optional_flags(self):
        with tempfile.TemporaryDirectory() as td:
            lock = write_lock(
                pathlib.Path(td) / "svc",
                {
                    "node_modules/pg": {"version": "8.13.1"},
                    "node_modules/@esbuild/darwin-arm64": {"version": "0.28.2", "optional": True, "os": ["darwin"]},
                },
            )
            pins = read_lock_pins(lock)
        self.assertEqual(pins["node_modules/pg"], Pin("8.13.1", False))
        self.assertEqual(pins["node_modules/@esbuild/darwin-arm64"], Pin("0.28.2", True))

    def test_skips_root_and_non_nodemodules_entries(self):
        # Workspace members appear as "packages/foo" WITH a version -- 26 such
        # entries exist across this repo's tracked lockfiles. They are source
        # directories, not installs, so including them would invent a MISSING
        # drift for a package that is never meant to live in node_modules.
        # A first draft of this test used a link entry with no `version`, which
        # the "if version:" filter already dropped -- so the path filter looked
        # redundant and a mutant that deleted it survived. This fixture is
        # shaped like the real entries on purpose.
        with tempfile.TemporaryDirectory() as td:
            lock = write_lock(
                pathlib.Path(td) / "svc",
                {
                    "node_modules/pg": {"version": "8.13.1"},
                    "packages/shared": {"name": "shared", "version": "1.4.0"},
                    "node_modules/linked": {"link": True, "resolved": "x"},
                },
            )
            pins = read_lock_pins(lock)
        self.assertEqual(set(pins), {"node_modules/pg"})
        self.assertNotIn("packages/shared", pins, "workspace source dirs must not be treated as installs")

    def test_rejects_v1_lockfile(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            svc.mkdir()
            (svc / "package-lock.json").write_text(json.dumps({"dependencies": {"pg": {"version": "1"}}}), "utf-8")
            with self.assertRaises(ValueError):
                read_lock_pins(svc / "package-lock.json")


class TestCompareSemantics(unittest.TestCase):
    """The absence rules. This is where a naive check goes wrong."""

    def test_matching_package_is_not_drift(self):
        pins = {"node_modules/pg": Pin("8.13.1")}
        self.assertEqual(compare(pins, lambda k: "8.13.1"), [])

    def test_present_at_wrong_version_is_drift(self):
        pins = {"node_modules/pg": Pin("8.13.1")}
        drifts = compare(pins, lambda k: "8.13.0")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "MISMATCH")
        self.assertEqual(drifts[0].pinned, "8.13.1")
        self.assertEqual(drifts[0].installed, "8.13.0")

    def test_optional_absent_is_NOT_drift(self):
        # npm pins optional deps for every platform; only the matching subset installs.
        pins = {"node_modules/@esbuild/darwin-arm64": Pin("0.28.2", optional=True)}
        self.assertEqual(compare(pins, lambda k: None), [])

    def test_optional_present_at_wrong_version_IS_drift(self):
        pins = {"node_modules/@esbuild/linux-x64": Pin("0.28.2", optional=True)}
        drifts = compare(pins, lambda k: "0.28.1")
        self.assertEqual(len(drifts), 1, "an installed optional package must still match its pin")

    def test_non_optional_absent_IS_drift(self):
        pins = {"node_modules/pg": Pin("8.13.1", optional=False)}
        drifts = compare(pins, lambda k: None)
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "MISSING")

    def test_extra_installed_package_is_not_drift(self):
        pins = {"node_modules/pg": Pin("8.13.1")}
        def lookup(k):
            return "8.13.1" if k == "node_modules/pg" else "9.9.9"  # hoisted extra
        self.assertEqual(compare(pins, lookup), [])

    def test_nested_and_scoped_paths_resolve(self):
        pins = {
            "node_modules/a/node_modules/b": Pin("2.0.0"),
            "node_modules/@scope/pkg": Pin("1.2.3"),
        }
        table = {
            "node_modules/a/node_modules/b": "2.0.0",
            "node_modules/@scope/pkg": "1.2.3",
        }
        self.assertEqual(compare(pins, lambda k: table.get(k)), [])


class TestCheckService(unittest.TestCase):
    def test_matched_when_installed_tree_agrees(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            lock = write_lock(svc, {"node_modules/pg": {"version": "8.13.1"}})
            install(svc, "node_modules/pg", "8.13.1")
            res = check_service(lock)
        self.assertEqual(res.status, MATCHED)
        self.assertEqual(res.checked, 1)
        self.assertEqual(res.drifts, [])

    def test_drifted_when_versions_disagree(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            lock = write_lock(svc, {"node_modules/pg": {"version": "8.13.1"}})
            install(svc, "node_modules/pg", "8.13.0")
            res = check_service(lock)
        self.assertEqual(res.status, DRIFTED)
        self.assertEqual([d.kind for d in res.drifts], ["MISMATCH"])

    def test_skipped_when_no_node_modules(self):
        with tempfile.TemporaryDirectory() as td:
            lock = write_lock(pathlib.Path(td) / "svc", {"node_modules/pg": {"version": "8.13.1"}})
            res = check_service(lock)
        self.assertEqual(res.status, SKIPPED)
        self.assertIn("no node_modules", res.reason)
        self.assertNotEqual(res.status, MATCHED, "SKIPPED must never be reported as a pass")

    def test_skipped_not_matched_when_lockfile_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            bad = pathlib.Path(td) / "package-lock.json"
            bad.write_text("{not json", encoding="utf-8")
            res = check_service(bad)
        self.assertEqual(res.status, SKIPPED)


class TestCLIReporting(unittest.TestCase):
    """The honesty rule, asserted end-to-end through the real binary."""

    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd or str(REPO_ROOT),
        )

    def test_repo_run_never_reports_skip_as_match(self):
        # On a clean checkout there is no node_modules, so the run must say so.
        proc = self._run()
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertIn("skipped", proc.stdout)
        # The forbidden claim: a pass count that hides the skips.
        if "COULD NOT BE CHECKED" not in proc.stdout:
            self.assertNotIn(" 0 skipped", proc.stdout, "skips must be surfaced when they occur")

    def test_strict_fails_when_nothing_could_be_checked(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            write_lock(svc, {"node_modules/pg": {"version": "8.13.1"}})
            subprocess.run(["git", "init", "-q", str(td)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(td), "add", "-A"], check=True, capture_output=True)
            proc = self._run("--root", td, "--strict")
        self.assertEqual(proc.returncode, 1, "a run that verified nothing must not pass --strict")
        self.assertIn("1 skipped", proc.stdout)

    def test_non_strict_tolerates_skips_but_reports_them(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            write_lock(svc, {"node_modules/pg": {"version": "8.13.1"}})
            subprocess.run(["git", "init", "-q", str(td)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(td), "add", "-A"], check=True, capture_output=True)
            proc = self._run("--root", td)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("1 skipped", proc.stdout)

    def test_exit_one_on_real_drift(self):
        with tempfile.TemporaryDirectory() as td:
            svc = pathlib.Path(td) / "svc"
            write_lock(svc, {"node_modules/pg": {"version": "8.13.1"}})
            install(svc, "node_modules/pg", "8.0.0")
            subprocess.run(["git", "init", "-q", str(td)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(td), "add", "-A"], check=True, capture_output=True)
            proc = self._run("--root", td)
        self.assertEqual(proc.returncode, 1, "real drift must fail the run")
        self.assertIn("MISMATCH", proc.stdout)

    def test_json_output_shape(self):
        proc = self._run("--json")
        data = json.loads(proc.stdout)
        for key in ("total", "matched", "drifted", "skipped", "services"):
            self.assertIn(key, data)
        self.assertEqual(data["matched"] + data["drifted"] + data["skipped"], data["total"])


class TestRepoPopulation(unittest.TestCase):
    def test_every_tracked_lockfile_is_reachable(self):
        """Guards against iter_lockfiles silently returning an empty population."""
        from lockfile_drift import iter_lockfiles

        found = list(iter_lockfiles(REPO_ROOT))
        tracked = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "*package-lock.json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertEqual(len(found), len(tracked), "iter_lockfiles must see every tracked lockfile")
        self.assertGreater(len(found), 0)


if __name__ == "__main__":
    unittest.main()
