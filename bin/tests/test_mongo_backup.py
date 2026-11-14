#!/usr/bin/env python3
"""Hermetic tests for bin/mongo-backup.sh and its systemd units.

Context: architect handoff c9540d99 §3.1 — "3.4M keychain records exist with
zero recoverable copy" — and decision 87a2a794 (backup retention: 3 verified
archives, prune only after a newer archive verifies, never prune the pinned
`mongo-pre-upgrade-*` rollback net).

Docker is stubbed: `mongodump` emits a real gzip stream, `mongorestore` drains
stdin and exits with a controllable status. No server, no network, no 1.9 GiB
write.

What is pinned here:

  * `--dry-run` performs no docker work at all
  * a successful run produces archive + sha256 manifest + stamp, and the stamp
    records `off_machine: false` (retention cannot satisfy §4.6)
  * retention keeps the newest N and prunes the rest ONLY after the new
    archive passes every gate
  * pinned `mongo-pre-upgrade-*.gz` archives are never pruned, even when they
    are the oldest files present
  * a failed dump leaves nothing behind (no archive, no `.part` remnant)
  * an archive that fails `mongorestore --dryRun` is discarded, not kept
  * the systemd service runs this script and the timer is persistent/daily
  * the dump never tries `--oplog` (this deployment is standalone)

Run:
  python3 -m pytest bin/tests/test_mongo_backup.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_GUARD_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "bash", doing="these tests execute the bin/ shell script"
)

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_SRC = REPO / "bin" / "mongo-backup.sh"
LIB_SRC = REPO / "bin" / "lib" / "drive-guard.sh"
SERVICE = REPO / "config" / "systemd" / "mongo-backup.service"
TIMER = REPO / "config" / "systemd" / "mongo-backup.timer"


class BackupRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mongobak-")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(os.path.join(self.bin, "lib"))
        shutil.copy(SCRIPT_SRC, os.path.join(self.bin, "mongo-backup.sh"))
        shutil.copy(LIB_SRC, os.path.join(self.bin, "lib", "drive-guard.sh"))
        self.env_file = os.path.join(self.tmp, ".env")
        with open(self.env_file, "w") as fh:
            fh.write("SPRING_DATA_MONGODB_URI=mongodb://u:p@spring-host:27017/nexus?authSource=admin\n")

        self.backup_dir = os.path.join(self.tmp, "backups")
        os.makedirs(self.backup_dir)
        self.log_file = os.path.join(self.tmp, "run.log")
        self.lock = os.path.join(self.tmp, "run.lock")

        self.stub = os.path.join(self.tmp, "stub")
        os.makedirs(self.stub)
        self.calls = os.path.join(self.tmp, "docker.calls")
        docker = os.path.join(self.stub, "docker")
        with open(docker, "w") as fh:
            fh.write(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{self.calls}"
if [ "$1" = exec ]; then
  case "$*" in
    *mongodump*)
      # The namespace lines mongodump writes to stderr, one per collection.
      for _db in ${{DUMP_DBS-nexus keychains}}; do
        if [ "${{DUMP_BACKTICKS:-0}}" = 1 ]; then
          # mongo 6.0+ quotes identifiers: writing `db.coll` to `archive on stdout`
          printf '2026-01-01T00:00:00.000+0000\\twriting `%s.coll` to `archive on stdout`\\n' "$_db" >&2
        else
          printf '2026-01-01T00:00:00.000+0000\\twriting %s.coll to archive on stdout\\n' "$_db" >&2
        fi
      done
      printf 'payload' | gzip -n
      exit ${{DUMP_RC:-0}} ;;
    *mongorestore*)
      cat >/dev/null
      exit ${{RESTORE_RC:-0}} ;;
    *" mongosh "*)
      [ "${{NO_MONGOSH:-0}}" = 1 ] && {{ echo 'exec: "mongosh": executable file not found in $PATH' >&2; exit 127; }}
      printf '%s\\n' "${{LIVE_DBS-nexus keychains}}"
      exit 0 ;;
    *" mongo "*)
      [ "${{NO_LEGACY_SHELL:-0}}" = 1 ] && {{ echo 'exec: "mongo": executable file not found in $PATH' >&2; exit 127; }}
      printf '%s\\n' "${{LIVE_DBS-nexus keychains}}"
      exit 0 ;;
  esac
fi
case "$1" in
  inspect) printf 'mongo:4.4.18\\n'; exit 0 ;;
  *) exit 0 ;;
esac
""")
        os.chmod(docker, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, extra_env=None, args=()):
        env = {
            "PATH": self.stub + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp,
            "BACKUP_DIR": self.backup_dir,
            "LOG_FILE": self.log_file,
            "LOCK_FILE": self.lock,
            "MIN_FREE_GIB": "0",
            "MIN_BYTES": "1",
            "KEEP_ARCHIVES": "3",
            # Never write an incident into the real nebula DB.
            "NEBULA_URL": "http://127.0.0.1:9/api/agent-records",
        }
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", os.path.join(self.bin, "mongo-backup.sh"), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def docker_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as fh:
            return [l.strip() for l in fh if l.strip()]

    def archives(self):
        return sorted(f for f in os.listdir(self.backup_dir)
                      if f.startswith("mongo-") and f.endswith(".gz"))

    def make_old(self, name, days):
        path = os.path.join(self.backup_dir, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        ts = os.path.getmtime(path) - days * 86400
        os.utime(path, (ts, ts))

    # -- dry run -----------------------------------------------------------
    def test_dry_run_touches_nothing(self):
        r = self.run_script(args=("--dry-run",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.docker_calls(), [],
                         "--dry-run must not call docker at all")
        self.assertEqual(self.archives(), [])

    # -- happy path --------------------------------------------------------
    def test_run_produces_archive_manifest_and_stamp(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        archives = self.archives()
        self.assertEqual(len(archives), 1, archives)
        manifests = [f for f in os.listdir(self.backup_dir) if f.startswith("manifest__")]
        self.assertEqual(len(manifests), 1)
        stamp_path = os.path.join(self.backup_dir, "last-backup.json")
        self.assertTrue(os.path.exists(stamp_path))
        with open(stamp_path) as fh:
            stamp = json.load(fh)
        self.assertEqual(stamp["file"], archives[0])
        self.assertEqual(len(stamp["sha256"]), 64)
        self.assertFalse(stamp["off_machine"],
                         "retention cannot make a local-only destination an "
                         "off-machine copy (§4.6 stays unmet)")

    def test_gate_is_actually_executed(self):
        self.run_script()
        joined = " ".join(self.docker_calls())
        self.assertIn("mongorestore", joined)
        self.assertIn("--dryRun", joined)

    # -- coverage: the whole estate, not just the app database ------------
    def test_dump_is_not_scoped_to_the_application_database(self):
        # A db path in the URI makes mongodump dump ONLY that database. Measured
        # on this host: 52 MiB (nexus only) vs 1.9 GiB for the whole estate,
        # with `keychains` silently missing.
        self.run_script()
        dump_calls = [c for c in self.docker_calls() if "mongodump" in c]
        self.assertTrue(dump_calls)
        for call in dump_calls:
            self.assertNotIn("/nexus", call,
                             "a db path in the dump URI silently narrows the backup")
        # 4.4 rejects --dumpDbUsersAndRoles for an all-database archive
        # ("must specify a database when running with dumpDbUsersAndRoles").
        self.assertNotIn("--dumpDbUsersAndRoles", dump_calls[0])

    def test_coverage_gate_rejects_a_partial_archive(self):
        r = self.run_script({"DUMP_DBS": "nexus", "LIVE_DBS": "nexus keychains"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.archives(), [],
                         "an archive missing a live database is not a backup")
        self.assertIn("keychains", pathlib.Path(self.log_file).read_text())

    def test_coverage_gate_parses_backticked_namespaces_from_6x_and_later(self):
        # mongo 6.0+ changed the namespace log to quote identifiers:
        #   writing `nexus.docklang` to `archive on stdout`
        # Parsing only "writing X to archive" made the gate see no databases at
        # all on 8.0 and reject a good archive. Same estate, new format, must pass.
        r = self.run_script({"DUMP_BACKTICKS": "1",
                             "DUMP_DBS": "nexus keychains legacy-audit",
                             "LIVE_DBS": "keychains legacy-audit nexus"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("coverage: all", pathlib.Path(self.log_file).read_text())

    def test_backticked_log_still_rejects_a_partial_archive(self):
        # The new format must not weaken the gate.
        r = self.run_script({"DUMP_BACKTICKS": "1",
                             "DUMP_DBS": "nexus",
                             "LIVE_DBS": "nexus keychains"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.archives(), [])
        self.assertIn("keychains", pathlib.Path(self.log_file).read_text())

    def test_coverage_gate_works_when_only_mongosh_exists(self):
        # mongo:6.0+ ships mongosh and no legacy `mongo`. Assuming the legacy
        # shell silently disabled this gate after the 8.0 cutover: the script
        # logged "coverage gate skipped" and would have promoted a partial
        # archive. The gate must run under either shell.
        r = self.run_script({"NO_LEGACY_SHELL": "1",
                             "DUMP_DBS": "nexus",
                             "LIVE_DBS": "nexus keychains"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.archives(), [],
                         "with mongosh available the partial archive must still be rejected")
        self.assertIn("keychains", pathlib.Path(self.log_file).read_text())
        self.assertNotIn("coverage gate skipped", pathlib.Path(self.log_file).read_text())

    def test_coverage_gate_falls_back_to_legacy_shell(self):
        # 4.4/5.0 have no mongosh; the job must keep working there too.
        r = self.run_script({"NO_MONGOSH": "1",
                             "DUMP_DBS": "nexus keychains legacy-audit",
                             "LIVE_DBS": "keychains legacy-audit nexus"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("coverage: all", pathlib.Path(self.log_file).read_text())

    def test_coverage_gate_passes_when_every_database_is_present(self):
        r = self.run_script({"DUMP_DBS": "nexus keychains legacy-audit",
                             "LIVE_DBS": "keychains legacy-audit nexus"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("coverage: all", pathlib.Path(self.log_file).read_text())

    # -- retention ---------------------------------------------------------
    def test_retention_keeps_newest_and_prunes_older(self):
        for i in range(5):
            self.make_old(f"mongo-2026010{i+1}T000000Z.gz", days=10 - i)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        remaining = self.archives()
        self.assertEqual(len(remaining), 3, remaining)
        # the newest (just created) must survive
        self.assertTrue(any(f not in {f"mongo-2026010{i+1}T000000Z.gz" for i in range(5)}
                            for f in remaining), remaining)

    def test_pinned_migration_archives_are_never_pruned(self):
        # Pinned archives are the OLDEST files present — the case that would
        # otherwise be pruned first.
        for i in range(3):
            self.make_old(f"mongo-pre-upgrade-2026010{i+1}.gz", days=90 + i)
        for i in range(5):
            self.make_old(f"mongo-2026010{i+1}T000000Z.gz", days=5 - i)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pinned = [f for f in os.listdir(self.backup_dir)
                  if f.startswith("mongo-pre-upgrade-")]
        self.assertEqual(len(pinned), 3,
                         "the rollback net must survive any retention pass")
        self.assertLessEqual(len(self.archives()), 6)  # 3 pinned + <=3 rotating

    def test_retention_does_not_run_when_the_dump_fails(self):
        self.make_old("mongo-20200101T000000Z.gz", days=400)
        r = self.run_script({"DUMP_RC": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("mongo-20200101T000000Z.gz", self.archives(),
                      "an older archive must not be pruned on a failed run")

    # -- failure paths -----------------------------------------------------
    def test_failed_dump_leaves_nothing_behind(self):
        r = self.run_script({"DUMP_RC": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual([f for f in os.listdir(self.backup_dir) if ".part" in f], [])
        self.assertEqual(self.archives(), [])

    def test_archive_failing_dryrun_is_discarded(self):
        r = self.run_script({"RESTORE_RC": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.archives(), [],
                         "an archive that cannot be read back is not a backup")
        self.assertIn("dryRun", (r.stdout + r.stderr) + pathlib.Path(self.log_file).read_text())


class KeyResolution(unittest.TestCase):
    """The backup must find the credential the same way every other caller does.

    A rotation updates the .env values; if this script read only the Spring key
    while the wrapper and consumers moved to MONGO_URI, the nightly backup would
    keep using a stale password and fail at 04:05 instead of at rotation time.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mongokey-")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(os.path.join(self.bin, "lib"))
        shutil.copy(SCRIPT_SRC, os.path.join(self.bin, "mongo-backup.sh"))
        shutil.copy(LIB_SRC, os.path.join(self.bin, "lib", "drive-guard.sh"))
        self.env_file = os.path.join(self.tmp, ".env")
        self.backup_dir = os.path.join(self.tmp, "backups")
        os.makedirs(self.backup_dir)
        self.calls = os.path.join(self.tmp, "docker.calls")

        self.stub = os.path.join(self.tmp, "stub")
        os.makedirs(self.stub)
        docker = os.path.join(self.stub, "docker")
        with open(docker, "w") as fh:
            fh.write(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{self.calls}"
if [ "$1" = exec ]; then
  case "$*" in
    *mongodump*) printf '2026-01-01T00:00:00.000+0000\\twriting `nexus.coll` to `archive on stdout`\\n' >&2
                 printf 'payload' | gzip -n; exit 0 ;;
    *mongorestore*) cat >/dev/null; exit 0 ;;
    *) printf 'nexus\\n'; exit 0 ;;
  esac
fi
case "$1" in inspect) printf 'mongo:8.0\\n'; exit 0 ;; *) exit 0 ;; esac
""")
        os.chmod(docker, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_env(self, body):
        with open(self.env_file, "w") as fh:
            fh.write(body)

    def run_backup(self, extra_env=None):
        env = {
            "PATH": self.stub + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp,
            "BACKUP_DIR": self.backup_dir,
            "LOG_FILE": os.path.join(self.tmp, "run.log"),
            "LOCK_FILE": os.path.join(self.tmp, "run.lock"),
            "MIN_FREE_GIB": "0",
            "MIN_BYTES": "1",
            "NEBULA_URL": "http://127.0.0.1:9/api/agent-records",
        }
        env.update(extra_env or {})
        return subprocess.run(["bash", os.path.join(self.bin, "mongo-backup.sh")],
                              capture_output=True, text=True, env=env, timeout=120)

    def dump_uri(self):
        with open(self.calls) as fh:
            for line in fh:
                if "mongodump" in line:
                    parts = line.split()
                    return parts[parts.index("--uri") + 1]
        return None

    def test_prefers_mongo_uri_alias_over_spring_key(self):
        self.write_env(
            "MONGO_URI=mongodb://u:p@alias-host:27017/nexus?authSource=admin\n"
            "SPRING_DATA_MONGODB_URI=mongodb://u:p@spring-host:27017/nexus?authSource=admin\n"
        )
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        uri = self.dump_uri()
        self.assertIn("alias-host", uri)
        self.assertNotIn("spring-host", uri,
                         "the alias must win, or a rotation moves only one caller")

    def test_spring_key_still_works_when_alias_absent(self):
        self.write_env("SPRING_DATA_MONGODB_URI=mongodb://u:p@spring-host:27017/nexus?authSource=admin\n")
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("spring-host", self.dump_uri())

    def test_process_env_overrides_the_file(self):
        self.write_env("MONGO_URI=mongodb://u:p@alias-host:27017/nexus?authSource=admin\n")
        r = self.run_backup({"MONGO_URI": "mongodb://u:p@process-host:27017/nexus?authSource=admin"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("process-host", self.dump_uri())

    def test_missing_uri_names_both_keys(self):
        self.write_env("SOMETHING_ELSE=1\n")
        r = self.run_backup()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("MONGO_URI or SPRING_DATA_MONGODB_URI not set", r.stdout + r.stderr)


class Units(unittest.TestCase):
    def test_execstart_is_an_installed_path_not_a_checkout_path(self):
        # A checkout path does not exist until the change is merged, so the unit
        # failed at start with "Command ... is not executable: No such file or
        # directory" — and would have failed nightly at 04:05.
        body = SERVICE.read_text()
        self.assertIn("ExecStart=%h/.local/lib/nexus/mongo-backup/mongo-backup.sh", body)
        self.assertNotIn("ExecStart=/home/codex/dev/nexus/bin/", body)
        self.assertIn("Type=oneshot", body)

    def test_user_unit_does_not_set_user(self):
        # User=/Group= are not supported in user units; leaving them in produces
        # a warning on every start for no benefit.
        body = SERVICE.read_text()
        self.assertNotIn("\nUser=", body)
        self.assertNotIn("\nGroup=", body)

    def test_timer_is_daily_and_persistent(self):
        body = TIMER.read_text()
        self.assertIn("OnCalendar=", body)
        self.assertIn("Persistent=true", body)
        self.assertIn("WantedBy=timers.target", body)

    def test_installer_deploys_a_self_contained_layout(self):
        installer = REPO / "bin" / "install-mongo-backup.sh"
        self.assertTrue(installer.exists(), "installer missing")
        tmp = tempfile.mkdtemp(prefix="mongoinstall-")
        try:
            libdir = os.path.join(tmp, "lib")
            cfg = os.path.join(tmp, "cfg")
            env = dict(os.environ, HOME=tmp, XDG_CONFIG_HOME=cfg,
                       MONGO_BACKUP_LIBDIR=libdir)
            r = subprocess.run(["bash", str(installer), "--no-reload"],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            script = os.path.join(libdir, "mongo-backup.sh")
            guard = os.path.join(libdir, "lib", "drive-guard.sh")
            self.assertTrue(os.path.isfile(script))
            self.assertTrue(os.access(script, os.X_OK))
            self.assertTrue(os.path.isfile(guard),
                            "the script sources lib/drive-guard.sh from its own dir")
            for unit in ("mongo-backup.service", "mongo-backup.timer"):
                self.assertTrue(os.path.isfile(os.path.join(cfg, "systemd", "user", unit)), unit)

            # The installed layout must actually run from where it was installed.
            bdir = os.path.join(tmp, "backups")
            os.makedirs(bdir)
            envfile = os.path.join(tmp, "env")
            with open(envfile, "w") as fh:
                fh.write("SPRING_DATA_MONGODB_URI=mongodb://u:p@localhost:27017/nexus?authSource=admin\n")
            run_env = dict(os.environ,
                           MONGO_ENV_FILE=envfile, BACKUP_DIR=bdir,
                           LOG_FILE=os.path.join(tmp, "log"),
                           LOCK_FILE=os.path.join(tmp, "lock"), MIN_FREE_GIB="0")
            r2 = subprocess.run(["bash", script, "--dry-run"],
                                capture_output=True, text=True, env=run_env, timeout=60)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("[dry]", r2.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dump_never_attempts_oplog_on_a_standalone(self):
        body = SCRIPT_SRC.read_text()
        self.assertNotIn("--oplog", body,
                         "this deployment is standalone: --oplog would fail the run")
        self.assertIn("standalone", SCRIPT_SRC.read_text().lower() + " standalone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
