"""Hermetic vanadium-CI backup retarget test (R-2026-09-10-03, plan-record
05f677f7).

Covers the barium -> vanadium retarget plus the ruling-V4 jenkins_home fetch
fix, per record e5cc1f06 / AGENTS.md R9:

- retarget:  script defaults BAR_HOST=vanadium, REMOTE_DIR=pg-backups/vanadium-ci
- dry-run:   `--dry-run` is fully hermetic (no ssh/rsync), exits 0, logs
             "dry run complete", fetches nothing into the spool
- V4 scope:  the jenkins_home tar excludes rebuildables (workspace, builds,
             cache, caches, .m2, war, copy_reference_file.log) and passes
             --warning=no-file-changed, so a live build cannot abort the run
             via tar exit-1 + pipefail (2026-09-10 04:31 ABORT)
- rename:    old "to-barium" names are gone from script + unit files

Run:
  python3 -m pytest bin/tests/test_vdci_backup.py -v
"""
import os
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "bin", "vanadium-ci-backup.sh")


class VdcBackupTest(unittest.TestCase):
    def run_script(self, *args, env_extra=None):
        env = dict(os.environ)
        env["LOG_FILE"] = os.path.join(self.tmp, "test-backup.log")
        env["SPOOL_DIR"] = os.path.join(self.tmp, "spool")
        env["LOCK_FILE"] = os.path.join(self.tmp, "test-backup.lock")
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", SCRIPT, *args], capture_output=True, text=True, env=env
        )

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vdci-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_script_exists(self):
        self.assertTrue(os.path.exists(SCRIPT), f"{SCRIPT} missing")

    def test_old_barium_names_removed(self):
        """The -to-barium script and unit names must be gone from the repo."""
        for stale in (
            "bin/vanadium-ci-backup-to-barium.sh",
            "config/systemd/backup-vanadium-ci-to-barium.service",
            "config/systemd/backup-vanadium-ci-to-barium.timer",
        ):
            self.assertFalse(
                os.path.exists(os.path.join(REPO_ROOT, stale)),
                f"{stale} still present after rename",
            )

    def test_retarget_defaults(self):
        """Defaults must point at vanadium (PG-tier tree), not barium."""
        with open(SCRIPT) as fh:
            body = fh.read()
        self.assertIn('BAR_HOST="${BAR_HOST:-vanadium}"', body)
        self.assertIn('REMOTE_DIR="${REMOTE_DIR:-pg-backups/vanadium-ci}"', body)
        self.assertNotIn('BAR_HOST="${BAR_HOST:-barium}"', body)

    def test_dry_run_hermetic(self):
        """--dry-run must not ssh/rsync: exits 0, logs complete, spool empty."""
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(self.tmp, "test-backup.log")) as fh:
            log = fh.read()
        self.assertIn("dry run complete", log)
        spool = os.path.join(self.tmp, "spool")
        if os.path.isdir(spool):
            self.assertEqual(os.listdir(spool), [], "dry-run must not fetch")

    def test_v4_exclusions_in_jenkins_fetch(self):
        """jenkins_home tar must exclude rebuildables + tolerate live builds."""
        with open(SCRIPT) as fh:
            body = fh.read()
        for flag in (
            "--warning=no-file-changed",
            "--exclude=workspace",
            "--exclude=builds",
            "--exclude=cache",
            "--exclude=caches",
            "--exclude=.m2",
            "--exclude=war",
            "--exclude=copy_reference_file.log",
        ):
            self.assertIn(flag, body, f"missing {flag} in jenkins_home tar")

    def test_unit_files_retargeted(self):
        """Repo unit files must point at the renamed script + vanadium."""
        unit = os.path.join(
            REPO_ROOT, "config", "systemd", "backup-vanadium-ci.service"
        )
        with open(unit) as fh:
            body = fh.read()
        self.assertIn("ExecStart=/home/codex/dev/nexus/bin/vanadium-ci-backup.sh", body)
        self.assertIn("Environment=BAR_HOST=vanadium", body)
        self.assertIn("Environment=REMOTE_DIR=pg-backups/vanadium-ci", body)
        # Active config must not name barium (historic retarget comment is ok).
        active = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertNotIn("barium", "\n".join(active).lower())


if __name__ == "__main__":
    unittest.main()