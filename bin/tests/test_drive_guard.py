"""Hermetic absent-drive guard tests for the nexus backup script family.

Background (DBA audit record f74eb976, 2026-09-15): backup scripts depended
on destinations behind /mnt removable drives — directly (rsync to
/mnt/WD14A) or indirectly (backup dirs / spools as symlinks into
/mnt/SiP1TB). When a drive is absent, `mkdir -p` on a symlink-to-absent-mount
fails with the misleading "File exists" and every redirect fails with raw
ENOENT — the root cause is invisible (mysql-backup incident, records
0abb6df5 -> cd776865). Worse, vanadium-ci-backup could reach "complete (ok)"
having fetched nothing — a silent no-op marked verified.

Pinned behaviors (all hermetic — tmpdirs + DRIVE_GUARD_REMOVABLE_BASES /
DRIVE_GUARD_FAKE_MOUNTS seams; production default bases are /mnt and /media):

- drive-guard lib: verdicts dangling/unmounted/ok/local for both symlinked
  and PLAIN paths; require_dir fail-fast rc 90/91/92 with honest
  DRIVE_GUARD_REASON; skip_if_absent rc 99; mountable destinations proceed
  with rc 0 and really are created
- mysql-backup.sh: unusable primary destination -> honest exit 1, reason in
  log + journal, NO dump attempted, guard error surfaces BEFORE any docker
  call; usable destination -> normal complete path; --dry-run unaffected
- vanadium-ci-backup.sh: absent spool drive -> SKIP exit 0, journal says
  "skipped (drive absent)", NOT "complete (ok)"; reason names the drive;
  present spool -> dry-run completes as before (existing suite keeps
  covering the retarget/V4 behaviors)
- mysql-health-monitor.sh: stale alert text names the drive state; lib
  source line present (monitor stays read-only, no require/skip semantics)

Run:
  python3 -m pytest bin/tests/test_drive_guard.py -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB = os.path.join(REPO_ROOT, "bin", "lib", "drive-guard.sh")
MYSQL_BACKUP = os.path.join(REPO_ROOT, "bin", "mysql-backup.sh")
VDCI_BACKUP = os.path.join(REPO_ROOT, "bin", "vanadium-ci-backup.sh")
HEALTH_MONITOR = os.path.join(REPO_ROOT, "bin", "mysql-health-monitor.sh")


def bash(script: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


class LibVerdictTest(unittest.TestCase):
    """drive_guard_describe verdicts for symlinked and plain paths."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-verdict-")

    def test_dangling_symlink(self):
        link = os.path.join(self.tmp, "backups")
        os.symlink("/nonexistent/target", link)
        out = bash(f'source "{LIB}"; drive_guard_describe "{link}"')
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertTrue(out.stdout.startswith("dangling /nonexistent/target"))

    def test_plain_path_under_absent_drive_root(self):
        # The backup-nexus shape: BAK_DIR=/mnt/WD14A/bak/pgdata with no drive.
        out = bash(
            f'source "{LIB}"; '
            f'DRIVE_GUARD_REMOVABLE_BASES="{self.tmp}" '
            f'drive_guard_describe "{self.tmp}/MyDrive/bak/pgdata"'
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(f"unmounted {self.tmp}/MyDrive", out.stdout.strip())

    def test_ok_under_fake_mounted_drive(self):
        out = bash(
            f'source "{LIB}"; '
            f'DRIVE_GUARD_REMOVABLE_BASES="{self.tmp}" '
            f'DRIVE_GUARD_FAKE_MOUNTS="{self.tmp}/MyDrive" '
            f'drive_guard_describe "{self.tmp}/MyDrive/bak/pgdata"'
        )
        self.assertEqual("ok", out.stdout.strip())

    def test_local_path(self):
        out = bash(f'source "{LIB}"; drive_guard_describe "{self.tmp}/sub"')
        self.assertEqual("local", out.stdout.strip())


class LibRequireTest(unittest.TestCase):
    """drive_guard_require_dir fail-fast semantics (primary destinations)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-require-")

    def test_dangling_fails_90_with_reason(self):
        link = os.path.join(self.tmp, "backups")
        os.symlink("/nonexistent/target", link)
        out = bash(
            f'source "{LIB}"; drive_guard_require_dir "{link}" "backup dir"; '
            f'rc=$?; echo "rc=$rc"; echo "reason=$DRIVE_GUARD_REASON"',
            env_extra={"DRIVE_GUARD_REMOVABLE_BASES": "/mnt /media"},
        )
        self.assertIn("rc=90", out.stdout)
        self.assertIn("FAIL:", out.stdout)
        self.assertIn("dangling symlink", out.stdout)
        self.assertIn("Nothing was attempted", out.stdout)

    def test_plain_path_absent_drive_fails_90(self):
        # Would previously SUCCEED via mkdir -p writing to the root fs.
        # The path is NOT under the default removable bases, so nothing in
        # the env fakes anything — mkdir would genuinely create it; the
        # guard must classify it as unmounted and refuse (rc 90).
        out = bash(
            f'source "{LIB}"; '
            f'DRIVE_GUARD_REMOVABLE_BASES="{self.tmp} /mnt /media" '
            f'drive_guard_require_dir "{self.tmp}/MyDrive/bak/pgdata"; '
            f'echo "rc=$?"'
        )
        self.assertIn("rc=90", out.stdout)
        # guard diagnostics go to stderr by design (journal capture must not
        # depend on a writable log dir)
        self.assertIn("NOT a mounted drive", out.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "MyDrive")))

    def test_unwritable_target_fails_92(self):
        # Pre-existing dir that the user cannot write: mkdir -p succeeds
        # (dir exists), the writability probe must then reject it (rc 92).
        ro = os.path.join(self.tmp, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        self.addCleanup(os.chmod, ro, 0o755)
        out = bash(
            f'source "{LIB}"; drive_guard_require_dir "{ro}"; echo "rc=$?"'
        )
        self.assertIn("rc=92", out.stdout)
        self.assertIn("not writable", out.stderr)

    def test_mountable_destination_proceeds(self):
        out = bash(
            f'source "{LIB}"; '
            f'DRIVE_GUARD_REMOVABLE_BASES="{self.tmp}" '
            f'DRIVE_GUARD_FAKE_MOUNTS="{self.tmp}/MyDrive" '
            f'drive_guard_require_dir "{self.tmp}/MyDrive/bak/pgdata"; '
            f'echo "rc=$?"'
        )
        self.assertIn("rc=0", out.stdout)
        self.assertTrue(
            os.path.isdir(os.path.join(self.tmp, "MyDrive", "bak", "pgdata"))
        )


class LibSkipTest(unittest.TestCase):
    """drive_guard_skip_if_absent semantics (scratch/spool paths)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-skip-")

    def test_dangling_spool_skips_99(self):
        link = os.path.join(self.tmp, "spool")
        os.symlink("/nonexistent/target", link)
        out = bash(
            f'source "{LIB}"; drive_guard_skip_if_absent "{link}" "spool"; '
            f'echo "rc=$?"; echo "reason=$DRIVE_GUARD_REASON"'
        )
        self.assertIn("rc=99", out.stdout)
        self.assertIn("SKIP:", out.stdout)
        self.assertIn("environment condition, not a backup failure", out.stdout)

    def test_present_spool_proceeds(self):
        out = bash(
            f'source "{LIB}"; '
            f'drive_guard_skip_if_absent "{self.tmp}/spool"; echo "rc=$?"'
        )
        self.assertIn("rc=0", out.stdout)


class MysqlBackupGuardTest(unittest.TestCase):
    """mysql-backup.sh: fail-fast + honest incident on unusable destination."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-mysql-")
        self.log = os.path.join(self.tmp, "mysql-backup.log")
        self.env = {
            "LOG_FILE": self.log,
            # unwritable path -> best-effort curl fails silently (by design)
            "NEBULA_URL": os.path.join(self.tmp, "nebula-post.txt"),
            # NOTE: production default bases (/mnt /media) apply — no seam,
            # so plain local paths classify as 'local' and the guard stays
            # out of the way; only genuinely dangling symlinks fire.
        }

    def run_script(self, *args):
        return subprocess.run(
            ["bash", MYSQL_BACKUP, *args],
            capture_output=True, text=True,
            env={**os.environ, **self.env},
        )

    def test_absent_drive_honest_fail_fast(self):
        link = os.path.join(self.tmp, "backups")
        os.symlink(f"{self.tmp}/MyDrive/home/backups", link)
        proc = self.run_script_with_env(
            {"BACKUP_DIR": link}
        )
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        with open(self.log) as fh:
            log = fh.read()
        self.assertIn("dangling symlink", log)
        self.assertIn("Nothing was attempted", log)
        self.assertIn("FAIL", log)
        # guard must fire BEFORE the dump pipeline (no docker errors appended)
        self.assertNotIn("mysqldump", log)

    def run_script_with_env(self, extra):
        env = {**os.environ, **self.env, **extra}
        return subprocess.run(
            ["bash", MYSQL_BACKUP],
            capture_output=True, text=True, env=env,
        )

    def test_usable_local_destination_completes(self):
        # Local path, production bases — the guard must NOT fire (plain
        # local dirs never classify as unmounted). The dump pipeline may
        # still fail (no docker/my-mysql in CI) but the log must never
        # contain a drive-guard error, and the destination must exist.
        bk = os.path.join(self.tmp, "bk")
        proc = self.run_script_with_env({"BACKUP_DIR": bk})
        with open(self.log) as fh:
            log = fh.read()
        self.assertNotIn("dangling symlink", log)
        self.assertNotIn("NOT a mounted drive", log)
        self.assertTrue(os.path.isdir(bk), "guard must pre-create destination")

    def test_dry_run_unaffected_by_guard_on_usable_dir(self):
        env = {**os.environ, **self.env,
               "BACKUP_DIR": os.path.join(self.tmp, "bk2")}
        proc = subprocess.run(
            ["bash", MYSQL_BACKUP, "--dry-run"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        with open(self.log) as fh:
            self.assertIn("[dry] complete", fh.read())


class VdciSkipTest(unittest.TestCase):
    """vanadium-ci-backup.sh: SKIP (exit 0, honest label) on absent spool."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-vdci-")
        self.log = os.path.join(self.tmp, "test-backup.log")
        self.env = {
            "LOG_FILE": self.log,
            "LOCK_FILE": os.path.join(self.tmp, "lock"),
            # production default bases — local spools must NOT skip
        }

    def run_script(self, *args, extra=None):
        env = {**os.environ, **self.env, **(extra or {})}
        return subprocess.run(
            ["bash", VDCI_BACKUP, *args],
            capture_output=True, text=True, env=env,
        )

    def test_absent_spool_drive_skip_exit0(self):
        spool = os.path.join(self.tmp, "spool")
        os.symlink(f"{self.tmp}/MyDrive/home/dev/pgsql/vdci-spool", spool)
        proc = self.run_script(extra={"SPOOL_DIR": spool})
        # Environment condition: exit 0 so timers stay honest, but the log
        # must say SKIPPED and must NOT say "complete (ok)".
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        with open(self.log) as fh:
            log = fh.read()
        self.assertIn("skipped (drive absent)", log)
        self.assertIn("dangling symlink", log)
        self.assertNotIn("complete (ok)", log)
        self.assertNotIn("backup start", log)

    def test_unmounted_plain_spool_path_skips(self):
        # Simulate a spool DIRECTLY on a removable drive (plain path, no
        # symlink) by pointing the removable base at the tmpdir: the spool
        # must classify 'unmounted' and the script must skip WITHOUT
        # creating the directory.
        spool = os.path.join(self.tmp, "MyDrive", "spool")
        proc = self.run_script(extra={
            "SPOOL_DIR": spool,
            "DRIVE_GUARD_REMOVABLE_BASES": f"{self.tmp} /mnt /media",
        })
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        with open(self.log) as fh:
            log = fh.read()
        self.assertIn("skipped (drive absent)", log)
        self.assertIn("NOT a mounted drive", log)
        self.assertFalse(os.path.exists(spool))

    def test_present_spool_dry_run_still_completes(self):
        # Local spool (production bases): guard must not fire; dry-run
        # reaches its own completion before any ssh.
        spool = os.path.join(self.tmp, "spool")
        proc = self.run_script("--dry-run", extra={"SPOOL_DIR": spool})
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        with open(self.log) as fh:
            log = fh.read()
        self.assertIn("dry run complete", log)
        self.assertNotIn("skipped", log)


class HealthMonitorGuardTest(unittest.TestCase):
    """mysql-health-monitor.sh: drive state surfaces in alert text."""

    def test_sources_guard_lib(self):
        with open(HEALTH_MONITOR) as fh:
            body = fh.read()
        self.assertIn("lib/drive-guard.sh", body)
        self.assertIn("drive_guard_describe", body)

    def test_stale_alert_names_drive_state(self):
        with open(HEALTH_MONITOR) as fh:
            body = fh.read()
        # the stale-incident detail must include the drive-state description
        self.assertIn("Backup dir state: $drive_state", body)


class GuardErrorIsolationTest(unittest.TestCase):
    """A missing lib must fail LOUDLY (fail-closed), not disable the guard."""

    def test_missing_lib_aborts_under_set_u(self):
        # POSIX: a failing `.` aborts a non-interactive shell. bash honors
        # this ONLY in POSIX mode — in default mode source-failure is
        # non-fatal. The scripts therefore append `|| { ...; exit 1; }` to
        # their load lines; pin that fail-closed pattern textually.
        for script in (MYSQL_BACKUP, VDCI_BACKUP, HEALTH_MONITOR):
            with open(script) as fh:
                body = fh.read()
            self.assertIn("lib load failed", body,
                          f"{os.path.basename(script)} lacks fail-closed load")

    def test_mount_probe_prefers_findmnt(self):
        # Hardening pass (2026-09-16): the mount probe must try findmnt
        # first (reads /proc/self/mountinfo directly) and keep util-linux
        # `mountpoint` as fallback. Pins the probe chain textually.
        with open(LIB) as fh:
            body = fh.read()
        self.assertLess(body.index("command -v findmnt"),
                        body.index("command -v mountpoint"),
                        "findmnt must be tried before mountpoint")
        self.assertIn("grep -qxF", body,
                      "findmnt match must be EXACT target (not --target)")
        self.assertIn("st_dev", body,
                      "coreutils-only fallback (st_dev comparison) present")


if __name__ == "__main__":
    unittest.main()
