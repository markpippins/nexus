#!/usr/bin/env python3
"""Hermetic tests: vanadium-ci backup must report drive absence and target
absence as the two different things they are.

Observed 2026-11-02 (and wrong before this change): the CI backup tier exited
0 with

    === vanadium-ci backup skipped (drive absent) ===
    SKIP: spool directory '.../vdci-spool' is a dangling symlink ... — removable
    drive absent. Nothing fetched; remote state untouched.

while vanadium was *also* off the LAN. The drive guard runs first and exits 0,
so the only reported condition was the drive — the tier read as "one backup
parked by a missing drive" when in fact the host was gone too. Fixing the drive
would not have restored it, and nothing in the log said so.

What is pinned here:

  * drive absent + target unreachable → still SKIP (exit 0, no remote state
    touched) but the log names BOTH conditions and says restoring the drive
    alone will not bring the tier back;
  * drive absent + target reachable → SKIP, and the log says the skip is the
    drive alone (so the two are never conflated in the other direction);
  * drive present + target unreachable → exit 1 with a preflight failure and
    no fetch attempted (it must not surface later as a generic fetch error);
  * --dry-run remains inert: no probe, no ssh.

Run:
  python3 -m pytest bin/tests/test_vdci_preflight.py -v
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import unittest

from host_tool_guard import skip_unless_host_tools

pytestmark = skip_unless_host_tools(
    "bash", doing="these tests execute the bin/ shell scripts"
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "bin", "vanadium-ci-backup.sh")


class VdciTargetReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vdci-pre-")
        self.stubdir = os.path.join(self.tmp, "bin")
        os.makedirs(self.stubdir)
        self.log_path = os.path.join(self.tmp, "run.log")
        self.listener = None

    def tearDown(self):
        if self.listener is not None:
            self.listener.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------
    def stub(self, name, body):
        path = os.path.join(self.stubdir, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, 0o755)

    def listen(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        return self.listener.getsockname()[1]

    def spool_absent(self):
        """A spool that is a dangling symlink — the real drive-absence shape."""
        link = os.path.join(self.tmp, "vdci-spool")
        os.symlink(os.path.join(self.tmp, "absent-drive", "vdci-spool"), link)
        return link

    def spool_present(self):
        path = os.path.join(self.tmp, "spool")
        os.makedirs(path)
        return path

    def run_script(self, spool, target_up, *args, prepare=True):
        if not prepare:
            port = 22  # no probe expected; nothing listens and nothing should ask
        elif target_up:
            self.stub("getent", 'echo "127.0.0.1 vanadium"')
            self.stub("ssh", "exit 0")
            port = self.listen()
        else:
            self.stub("getent", "exit 2")
            self.stub("ssh", "exit 0")  # never reached
            port = 22
        env = dict(os.environ)
        env.update({
            "PATH": self.stubdir + ":" + env["PATH"],
            "LOG_FILE": self.log_path,
            "SPOOL_DIR": spool,
            "LOCK_FILE": os.path.join(self.tmp, "lock"),
            "VD_HOST": "vanadium",
            "TARGET_PORT": str(port),
            "TARGET_PROBE_TIMEOUT": "2",
            # never write an incident into the real DB from a test
            "NEBULA_URL": "http://127.0.0.1:9/api/agent-records",
        })
        proc = subprocess.run(
            ["bash", SCRIPT, *args], capture_output=True, text=True, env=env,
            timeout=120,
        )
        return proc

    def log(self):
        if not os.path.exists(self.log_path):
            return ""
        with open(self.log_path) as fh:
            return fh.read()

    # -- drive absent, target absent: two conditions, both named --------------
    def test_drive_absent_plus_target_absent_names_both(self):
        proc = self.run_script(self.spool_absent(), target_up=False)
        log = self.log()
        self.assertEqual(proc.returncode, 0,
                         "a drive skip stays exit 0 — it is an environment "
                         "condition, not a failure:\n" + log)
        self.assertIn("skipped (drive absent)", log)
        self.assertIn("ALSO unreachable", log)
        self.assertIn("two independent problems", log)
        self.assertIn("does not resolve", log)

    # -- drive absent, target fine: do not implicate the host ----------------
    def test_drive_absent_target_reachable_does_not_implicate_target(self):
        proc = self.run_script(self.spool_absent(), target_up=True)
        log = self.log()
        self.assertEqual(proc.returncode, 0, log)
        self.assertIn("this skip is the drive alone", log)
        self.assertNotIn("ALSO unreachable", log)

    # -- drive present, target absent: fail fast, not as a fetch error ------
    def test_drive_present_target_absent_fails_before_fetching(self):
        proc = self.run_script(self.spool_present(), target_up=False)
        log = self.log()
        self.assertEqual(proc.returncode, 1, log)
        self.assertIn("FAIL(preflight)", log)
        self.assertIn("aborting before fetching", log)
        self.assertNotIn("FAIL fetching", log,
                         "the preflight must stop the run before artifact "
                         "fetches, not after they fail")

    # -- dry-run stays inert ------------------------------------------------
    def test_dry_run_does_not_probe(self):
        probe_marker = os.path.join(self.tmp, "getent-called")
        self.stub("getent", "touch %s" % probe_marker)
        self.stub("ssh", "exit 0")
        proc = self.run_script(self.spool_present(), False, "--dry-run", prepare=False)
        self.assertFalse(os.path.exists(probe_marker),
                         "--dry-run called getent: it is supposed to be inert")
        self.assertNotIn("FAIL(preflight)", self.log())
        self.assertEqual(proc.returncode, 0, self.log())


if __name__ == "__main__":
    unittest.main()
