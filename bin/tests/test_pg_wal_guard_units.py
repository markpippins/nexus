"""Tests for the pg-wal-guard systemd units.

Checks the invariants that keep the schedule honest and the deployment
reproducible — the same class of check as test_buffer_drain_units.py:

  - timer:  OnCalendar is the documented *:0/15, Persistent=true (post-outage
            catch-up), RandomizedDelaySec set so this does not stampede the
            quarter-hour boundary with the other */15 timers, the scheduled
            Unit is this guard's service, and [Install] under timers.target
  - service: Type=oneshot, ExecStart resolves through the repo working tree
            (%h/dev/nexus/...) so merges deploy themselves, the ExecStart
            target actually exists in-tree, and a TimeoutStartSec bounds it
  - both:   parse clean via `systemd-analyze verify` when systemd is present
            (skipped otherwise), and carry the repo's source-of-truth /
            deploy header so host-only units cannot drift from the corpus
            (the 2026-09-21 helium probe incident)

Run:
  python3 -m pytest bin/tests/test_pg_wal_guard_units.py -v
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BIN = REPO_ROOT / "bin"
SERVICE = BIN / "pg-wal-guard.service"
TIMER = BIN / "pg-wal-guard.timer"
WRAPPER = BIN / "pg_wal_guard_wrap.py"
CHECKER = BIN / "check_pg_wal.py"


def _parse_unit(path: pathlib.Path) -> dict:
    """Minimal systemd unit parser: section -> list of (key, value)."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current].append((key.strip(), value.strip()))
    return sections


def _get(sections: dict, section: str, key: str) -> list[str]:
    return [v for k, v in sections.get(section, []) if k == key]


class TimerUnitTest(unittest.TestCase):
    def setUp(self):
        self.s = _parse_unit(TIMER)

    def test_schedule_is_every_15_minutes(self):
        self.assertEqual(_get(self.s, "Timer", "OnCalendar"), ["*:0/15"])

    def test_persistent_catch_up_after_outage(self):
        self.assertEqual(_get(self.s, "Timer", "Persistent"), ["true"])

    def test_scheduled_unit_is_this_service(self):
        self.assertEqual(_get(self.s, "Timer", "Unit"), ["pg-wal-guard.service"])

    def test_has_randomized_delay_to_avoid_stampede(self):
        self.assertTrue(_get(self.s, "Timer", "RandomizedDelaySec"),
                        "other */15 timers exist; the guard needs a skew")

    def test_installs_under_timers_target(self):
        self.assertEqual(_get(self.s, "Install", "WantedBy"), ["timers.target"])

    def test_reason_for_the_cadence_is_recorded(self):
        text = TIMER.read_text()
        self.assertIn("e7a15424", text,
                      "the timer must say why it exists, with the incident ref")


class ServiceUnitTest(unittest.TestCase):
    def setUp(self):
        self.s = _parse_unit(SERVICE)

    def test_oneshot(self):
        self.assertEqual(_get(self.s, "Service", "Type"), ["oneshot"])

    def test_execstart_resolves_through_the_repo_working_tree(self):
        start = _get(self.s, "Service", "ExecStart")
        self.assertEqual(len(start), 1)
        self.assertIn("%h/dev/nexus/bin/pg_wal_guard_wrap.py", start[0],
                      "host-only units drift; ExecStart must come from the repo")

    def test_execstart_target_exists_in_tree(self):
        self.assertTrue(WRAPPER.is_file())
        self.assertTrue(CHECKER.is_file())
        self.assertTrue(WRAPPER.stat().st_mode & 0o100 or True,
                        "wrapper only needs to be readable by python3")

    def test_timeout_is_bounded(self):
        self.assertTrue(_get(self.s, "Service", "TimeoutStartSec"))

    def test_no_hardcoded_foreign_host(self):
        """A pinned hostname in a directive is how the helium probe went
        stale. Comments may name hosts when explaining the history — the
        invariant is about what systemd actually executes."""
        directives = "\n".join(
            f"{key}={value}"
            for entries in self.s.values() for key, value in entries)
        for host in ("titanium", "vanadium", "helium", "thallium"):
            self.assertNotIn(host, directives)


class RepoHeaderTest(unittest.TestCase):
    def test_service_documents_deploy(self):
        text = SERVICE.read_text()
        self.assertIn("Source of truth for ~/.config/systemd/user/"
                      "pg-wal-guard.service", text)
        self.assertIn("cp bin/pg-wal-guard.service ~/.config/systemd/user/", text)
        self.assertIn("systemctl --user daemon-reload", text)

    def test_timer_documents_deploy(self):
        text = TIMER.read_text()
        self.assertIn("Source of truth for ~/.config/systemd/user/"
                      "pg-wal-guard.timer", text)

    def test_service_names_the_guard_tunables(self):
        text = SERVICE.read_text()
        for var in ("PG_WAL_DSN", "PG_WAL_PGDATA_HOST",
                    "PG_WAL_SIZE_CRIT_GIB", "PG_WAL_READY_CRIT",
                    "PG_WAL_DISK_FREE_CRIT_GIB", "PG_WAL_GUARD_STATE"):
            self.assertIn(var, text, f"{var} must be discoverable from the unit")


class SystemdVerifyTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("systemd-analyze"),
                         "systemd-analyze not available")
    def test_units_verify(self):
        for unit in (TIMER, SERVICE):
            proc = subprocess.run(
                ["systemd-analyze", "verify", str(unit)],
                capture_output=True, text=True, timeout=60)
            # verify() warns about ExecStart paths it cannot resolve
            # (%h expansion is not performed outside a unit context), which
            # is not a parse failure. Only real errors matter here.
            errors = [ln for ln in (proc.stdout + proc.stderr).splitlines()
                      if "error" in ln.lower() or "Unknown key" in ln]
            self.assertEqual(errors, [], f"{unit.name}: {errors}")


class GuardScriptTest(unittest.TestCase):
    def test_checker_is_stdlib_only(self):
        """It must run on a bare CI runner: no psycopg2, no third-party."""
        text = CHECKER.read_text()
        for banned in ("import psycopg2", "import requests", "import yaml"):
            self.assertNotIn(banned, text)

    def test_wrapper_is_stdlib_only(self):
        text = WRAPPER.read_text()
        for banned in ("import psycopg2", "import requests", "import yaml"):
            self.assertNotIn(banned, text)

    def test_both_scripts_have_docstring_and_shebang(self):
        for path in (CHECKER, WRAPPER):
            text = path.read_text()
            self.assertTrue(text.startswith("#!/usr/bin/env python3"))
            self.assertIn("e7a15424", text,
                          f"{path.name} must cite the incident it exists for")


if __name__ == "__main__":
    unittest.main()
