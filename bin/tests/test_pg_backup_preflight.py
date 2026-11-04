#!/usr/bin/env python3
"""Hermetic tests for the off-machine PG target preflight.

Incident (2026-11-02): vanadium was powered off overnight. The nightly backup
dumped all 55 databases (90s, 637M for `nexus` alone) and only then died at the
transfer step, logging

    ssh: Could not resolve hostname vanadium: Name or service not known
    FAIL: cannot create remote dir pg-backups/titanium on vanadium

which points at DNS and at a permissions problem on the remote path. Neither
was true — the host was not on the LAN and the router had dropped its name with
the lease. The run wasted a dump cycle, and the log sent triage the wrong way.

What is pinned here:

  * An unresolvable target reports "target unreachable" and says so in words
    that name the likely cause (a dead host takes its name with it), rather
    than appearing to be a DNS defect.
  * The three failure layers stay distinguishable: no name (2), name but no
    port (3), port but no ssh (4) — and ssh's own message is carried through.
  * A reachable target is silent and returns 0 (no noise on the happy path).
  * The backup script runs the preflight BEFORE the dump loop, so an absent
    target can never cost a dump cycle again.
  * --dry-run still does no network work at all.

Run:
  python3 -m pytest bin/tests/test_pg_backup_preflight.py -v
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB = os.path.join(REPO_ROOT, "bin", "lib", "pg-target-preflight.sh")
SCRIPT = os.path.join(REPO_ROOT, "bin", "pg-backup-to-vanadium.sh")

# Source the library, call it, print the detail, exit with its status.
CALLER = r"""
. "{}"
detail="$(pg_target_preflight)"
rc=$?
printf '%s' "$detail"
exit $rc
"""


def free_port():
    """A port nothing is listening on (bound then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PreflightLayers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pgpre-")
        self.stubdir = os.path.join(self.tmp, "bin")
        os.makedirs(self.stubdir)
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
        """An open loopback port, so the TCP layer succeeds."""
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        return self.listener.getsockname()[1]

    def run_lib(self, port):
        env = dict(os.environ)
        env["PATH"] = self.stubdir + ":" + env["PATH"]
        env["REMOTE_HOST"] = "vanadium"
        env["REMOTE_USER"] = ""
        env["TARGET_PORT"] = str(port)
        env["TARGET_PROBE_TIMEOUT"] = "2"
        return subprocess.run(
            ["bash", "-c", CALLER.format(LIB)],
            capture_output=True, text=True, env=env, timeout=30,
        )

    # -- layer 1 -----------------------------------------------------------
    def test_unresolvable_target_says_unreachable_not_dns(self):
        self.stub("getent", "exit 2")
        p = self.run_lib(free_port())
        self.assertEqual(p.returncode, 2)
        self.assertIn("target unreachable", p.stdout)
        self.assertIn("does not resolve", p.stdout)
        # The point of the fix: the log must not let a dead host pass as a
        # DNS defect, and must not claim a remote-path problem.
        self.assertIn("not proof that DNS is broken", p.stdout)
        self.assertNotIn("remote dir", p.stdout)

    # -- layer 2 -----------------------------------------------------------
    def test_resolves_but_port_silent(self):
        self.stub("getent", 'echo "127.0.0.1 vanadium"')
        p = self.run_lib(free_port())
        self.assertEqual(p.returncode, 3)
        self.assertIn("target unreachable", p.stdout)
        self.assertIn("resolves to 127.0.0.1", p.stdout)
        self.assertIn("does not answer", p.stdout)

    # -- layer 3 -----------------------------------------------------------
    def test_port_open_but_ssh_fails_carries_ssh_message(self):
        self.stub("getent", 'echo "127.0.0.1 vanadium"')
        self.stub("ssh", 'echo "Permission denied (publickey)." >&2\nexit 255')
        p = self.run_lib(self.listen())
        self.assertEqual(p.returncode, 4)
        self.assertIn("answers TCP", p.stdout)
        self.assertIn("ssh failed", p.stdout)
        self.assertIn("Permission denied", p.stdout)

    # -- happy path --------------------------------------------------------
    def test_reachable_target_is_silent(self):
        self.stub("getent", 'echo "127.0.0.1 vanadium"')
        self.stub("ssh", "exit 0")
        p = self.run_lib(self.listen())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "")

    # -- an IP literal needs no resolution ---------------------------------
    def test_ip_literal_skips_resolution(self):
        self.stub("getent", "exit 2")  # would fail if consulted
        self.stub("ssh", "exit 0")
        p = subprocess.run(
            ["bash", "-c", CALLER.format(LIB)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ,
                 "PATH": self.stubdir + ":" + os.environ["PATH"],
                 "REMOTE_HOST": "127.0.0.1",
                 "REMOTE_USER": "",
                 "TARGET_PORT": str(self.listen()),
                 "TARGET_PROBE_TIMEOUT": "2"},
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class Wiring(unittest.TestCase):
    def setUp(self):
        with open(SCRIPT) as fh:
            self.body = fh.read()

    def test_script_sources_and_calls_the_preflight(self):
        self.assertIn("pg-target-preflight.sh", self.body)
        self.assertIn("pg_target_preflight", self.body)

    def test_preflight_runs_before_the_dump_loop(self):
        probe = self.body.index("pg_target_preflight")
        dump = self.body.index("for db in $DBS")
        self.assertLess(probe, dump,
                        "preflight must come before dumping, or the fix buys "
                        "nothing: an absent target still costs a dump cycle")

    def test_preflight_is_skipped_for_dry_run(self):
        guard = self.body.index('if [ "$DRY_RUN" = 0 ]')
        probe = self.body.index("pg_target_preflight")
        self.assertLess(guard, probe,
                        "--dry-run must stay hermetic: the preflight does ssh")

    def test_dry_run_stays_network_free(self):
        """Executed, not just asserted in text: --dry-run must not reach ssh."""
        tmp = tempfile.mkdtemp(prefix="pgdry-")
        try:
            stubdir = os.path.join(tmp, "bin")
            os.makedirs(stubdir)
            marker = os.path.join(tmp, "ssh-was-called")
            with open(os.path.join(stubdir, "ssh"), "w") as fh:
                fh.write("#!/bin/sh\ntouch %s\nexit 1\n" % marker)
            os.chmod(os.path.join(stubdir, "ssh"), 0o755)

            env = dict(os.environ)
            env["PATH"] = stubdir + ":" + env["PATH"]
            env["LOG_FILE"] = os.path.join(tmp, "run.log")
            env["SPOOL_DIR"] = os.path.join(tmp, "spool")
            env["CONTAINER"] = "no-such-container-dry-run"
            # Never let a test write an incident record into the real DB, and
            # never queue behind a live tier's cluster lock.
            env["NEBULA_URL"] = "http://127.0.0.1:9/api/agent-records"
            env["PG_DUMP_LOCK_WAIT"] = "1"
            subprocess.run(["bash", SCRIPT, "--dry-run"],
                           capture_output=True, text=True, env=env, timeout=90)
            self.assertFalse(os.path.exists(marker),
                             "--dry-run called ssh: it is supposed to be inert")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
