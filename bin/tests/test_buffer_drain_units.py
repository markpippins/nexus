"""Tests for the buffer-drainer systemd units (PR #587 scheduled deployment).

Checks the invariants that keep the schedule safe and honest:

  - timer:  OnCalendar is the documented *:0/15, Persistent=true (post-outage
            catch-up), the scheduled Unit is the drainer service, a small
            random skew, and [Install] under timers.target
  - service: Type=oneshot, User codex, ExecStart points at the in-tree
            drainer, NATS_URL set (thallium), and NO DATABASE_URL (the
            drainer never touches PG — dead config would lie)
  - both:   parse clean via `systemd-analyze verify` when systemd is
            present (skipped otherwise); ExecStart target exists in-tree

Run:
  python3 -m pytest bin/tests/test_buffer_drain_units.py -v
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BIN = REPO_ROOT / "bin"
SERVICE = BIN / "write-queue-buffer-drain.service"
TIMER = BIN / "write-queue-buffer-drain.timer"


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

    def test_persistent_for_post_outage_catchup(self):
        self.assertEqual(_get(self.s, "Timer", "Persistent"), ["true"])

    def test_targets_the_drainer_service(self):
        self.assertEqual(_get(self.s, "Timer", "Unit"),
                         ["write-queue-buffer-drain.service"])

    def test_small_random_skew(self):
        self.assertEqual(_get(self.s, "Timer", "RandomizedDelaySec"), ["30"])

    def test_install_wantedby_timers(self):
        self.assertEqual(_get(self.s, "Install", "WantedBy"), ["timers.target"])

    def test_documentation_points_at_pr_587(self):
        docs = _get(self.s, "Unit", "Documentation")
        self.assertTrue(docs and "pull/587" in docs[0], docs)


class ServiceUnitTest(unittest.TestCase):
    def setUp(self):
        self.s = _parse_unit(SERVICE)

    def test_oneshot_type(self):
        self.assertEqual(_get(self.s, "Service", "Type"), ["oneshot"])

    def test_runs_as_codex(self):
        self.assertEqual(_get(self.s, "Service", "User"), ["codex"])
        self.assertEqual(_get(self.s, "Service", "Group"), ["codex"])

    def test_execstart_targets_the_in_tree_drainer(self):
        execs = _get(self.s, "Service", "ExecStart")
        self.assertEqual(len(execs), 1)
        self.assertTrue(execs[0].endswith("/bin/drain_write_queue_buffer.py"), execs)

    def test_nats_url_is_thallium(self):
        self.assertEqual(_get(self.s, "Service", "Environment"),
                         ["NATS_URL=nats://192.168.1.82:4222"])

    def test_no_database_url_dead_config(self):
        # The drainer never touches PG; a DATABASE_URL here would imply it does.
        envs = " ".join(_get(self.s, "Service", "Environment"))
        self.assertNotIn("DATABASE_URL", envs)

    def test_timeout_bounds_a_hung_run(self):
        self.assertEqual(_get(self.s, "Service", "TimeoutStartSec"), ["300"])

    def test_install_wantedby_default_target(self):
        self.assertEqual(_get(self.s, "Install", "WantedBy"),
                         ["default.target"])

    def test_execstart_script_exists_in_tree(self):
        # The unit points at the deployed checkout path; until merge, the
        # in-tree check is that the script lives at bin/ in THIS tree and
        # the ExecStart basename matches it.
        execs = _get(self.s, "Service", "ExecStart")
        self.assertTrue(execs)
        script = pathlib.Path(execs[0].split()[-1])
        self.assertEqual(script.name, "drain_write_queue_buffer.py")
        self.assertTrue((BIN / "drain_write_queue_buffer.py").exists(),
                        "in-tree drainer missing from bin/")


@unittest.skipIf(shutil.which("systemd-analyze") is None,
                 "systemd-analyze not available")
class SystemdVerifyTest(unittest.TestCase):
    def test_units_pass_systemd_analyze_verify(self):
        for unit in (SERVICE, TIMER):
            with self.subTest(unit=unit.name):
                r = subprocess.run(
                    ["systemd-analyze", "verify", str(unit)],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(
                    r.returncode, 0,
                    f"{unit.name} failed verify: rc={r.returncode} "
                    f"stdout={r.stdout[:400]} stderr={r.stderr[:400]}",
                )


if __name__ == "__main__":
    unittest.main()
