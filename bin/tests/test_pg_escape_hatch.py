"""Hermetic tests for bin/pg-escape-hatch.sh — local nexus PG escape-hatch
backup (audit f74eb976 zero-coverage finding).

Two execution strategies keep this suite hermetic AND honest:

1. MechanicsMockTest (deterministic, no server): pg_dump and pg_restore are
   mocked on PATH. A marker file records whether the mock dump actually
   ran, so "nothing was attempted" is pinned behaviorally, not just
   textually. Covers: happy path (manifest + stamp + retention + ok log),
   verify-gate rejection (artifact removed, no manifest/stamp, honest
   FAIL), pg_dump failure, dangling-destination fail-fast (guard fires
   BEFORE the dump), dry-run.

2. DockerE2ETest (real binaries end-to-end): a throwaway postgres:17-class
   container on a random loopback port receives the dump; pg_restore
   --list genuinely verifies the archive. Skipped when Docker or the
   pgvector image is unavailable (e.g. GitHub runners). The live
   pgvector_db is NEVER touched — tests bind their own container.

Run:
  python3 -m pytest bin/tests/test_pg_escape_hatch.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "bin", "pg-escape-hatch.sh")

PGUSER = "pguser"
PGPASS = "pgpass"


class MechanicsMockTest(unittest.TestCase):
    """Script mechanics with mocked pg_dump/pg_restore (no server)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pgeh-mock-")
        self.bk = os.path.join(self.tmp, "bk")
        self.bindir = os.path.join(self.tmp, "bin")
        self.marker = os.path.join(self.tmp, "pg_dump-ran.marker")
        os.makedirs(self.bindir)
        self.env = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": "9",  # closed port — a real pg_dump attempt would fail
            "PGUSER": PGUSER,
            "PGPASSWORD": PGPASS,
            "PGDATABASE": "nexus",
            "BACKUP_DIR": self.bk,
            "LOCK_FILE": os.path.join(self.tmp, "lock"),
            "NEBULA_URL": os.path.join(self.tmp, "unwritable-nebula"),  # best-effort incident fails silently
            "PATH": self.bindir + os.pathsep + os.environ["PATH"],
            "PG_DUMP_MARKER": self.marker,
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- mock helpers -----------------------------------------------------
    def _mock_pg_dump(self, payload: bytes, exit_code: int = 0):
        """Fake pg_dump: honors --file <path> (real pg_dump creates the
        artifact itself), touches the marker, exits with the given code.
        Payload delivered via a file + cp — no shell quoting games."""
        payload_path = os.path.join(self.tmp, "pg_dump-payload.bin")
        with open(payload_path, "wb") as pf:
            pf.write(payload)
        self.env["PG_DUMP_PAYLOAD"] = payload_path
        mock = os.path.join(self.bindir, "pg_dump")
        with open(mock, "w") as fh:
            fh.write(
                "#!/bin/bash\n"
                "out=\"\"\n"
                "prev=\"\"\n"
                'for a in "$@"; do\n'
                '  if [ "$prev" = "--file" ]; then out="$a"; fi\n'
                '  prev="$a"\n'
                "done\n"
                'if [ -n "$out" ]; then cp "$PG_DUMP_PAYLOAD" "$out"; fi\n'
                'touch "$PG_DUMP_MARKER"\n'
                f"exit {exit_code}\n"
            )
        os.chmod(mock, 0o755)

    def _mock_pg_restore(self, exit_code: int = 0):
        mock = os.path.join(self.bindir, "pg_restore")
        with open(mock, "w") as fh:
            fh.write(f"#!/bin/bash\nexit {exit_code}\n")
        os.chmod(mock, 0o755)

    def _run(self, *args):
        return subprocess.run(
            ["bash", SCRIPT, *args], capture_output=True, text=True, env=self.env
        )

    def _log(self) -> str:
        path = os.path.join(self.bk, "pg-escape-hatch.log")
        if not os.path.exists(path):
            return ""
        with open(path) as fh:
            return fh.read()

    # -- tests -------------------------------------------------------------
    def test_happy_path_manifest_stamp_ok_log(self):
        self._mock_pg_dump(b"FAKE-ARCHIVE-BYTES")
        self._mock_pg_restore(0)
        proc = self._run()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        dumps = [f for f in os.listdir(self.bk) if f.startswith("nexus__") and f.endswith(".dump")]
        self.assertEqual(1, len(dumps), "exactly one dump artifact expected")
        self.assertTrue(os.path.exists(os.path.join(self.bk, "last-backup.json")))
        manifests = [f for f in os.listdir(self.bk) if f.startswith("manifest__")]
        self.assertEqual(1, len(manifests))
        with open(os.path.join(self.bk, "last-backup.json")) as fh:
            stamp = fh.read()
        self.assertIn('"database": "nexus"', stamp)
        self.assertIn("local-escape-hatch", stamp)
        self.assertIn("dumped + verified", self._log())
        self.assertIn("complete (ok)", self._log())

    def test_verify_gate_rejection_removes_artifact(self):
        # pg_restore (mocked) rejects what pg_dump produced -> honest FAIL,
        # artifact removed, NO manifest, NO stamp update.
        self._mock_pg_dump(b"GARBAGE-NOT-AN-ARCHIVE")
        self._mock_pg_restore(1)
        proc = self._run()
        self.assertEqual(1, proc.returncode)
        dumps = [f for f in os.listdir(self.bk) if f.startswith("nexus__")]
        self.assertEqual([], dumps, "rejected artifact must be removed")
        self.assertFalse(os.path.exists(os.path.join(self.bk, "last-backup.json")))
        self.assertIn("pg_restore --list rejected", self._log())
        self.assertNotIn("complete (ok)", self._log())

    def test_pg_dump_failure_honest(self):
        self._mock_pg_dump(b"", exit_code=3)
        self._mock_pg_restore(0)  # would pass if reached — must not be reached
        proc = self._run()
        self.assertEqual(1, proc.returncode)
        dumps = [f for f in os.listdir(self.bk) if f.startswith("nexus__")]
        self.assertEqual([], dumps, "failed dump must leave no artifact")
        self.assertIn("FAIL: pg_dump pipeline failed", self._log())
        self.assertNotIn("complete (ok)", self._log())

    def test_dangling_destination_fail_fast_before_dump(self):
        link = os.path.join(self.tmp, "bk")
        os.symlink("/nonexistent/target", link)
        self._mock_pg_dump(b"SHOULD-NEVER-BE-WRITTEN")
        self._mock_pg_restore(0)
        proc = self._run()
        self.assertEqual(1, proc.returncode)
        self.assertFalse(
            os.path.exists(self.marker),
            "guard must fire BEFORE any dump attempt (nothing was attempted)",
        )
        # the honest reason reaches stderr (journal channel) even though
        # the log dir itself is unusable
        self.assertIn("dangling symlink", proc.stderr)
        self.assertIn("Nothing was attempted", proc.stderr)

    def test_dry_run_no_dump_no_artifacts(self):
        self._mock_pg_dump(b"SHOULD-NEVER-BE-WRITTEN")
        proc = self._run("--dry-run")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(self.marker), "dry-run must not dump")
        bk_files = os.listdir(self.bk) if os.path.isdir(self.bk) else []
        self.assertEqual(["pg-escape-hatch.log"], bk_files)
        self.assertIn("[dry] complete", self._log())

    def test_retention_prunes_old_keeps_recent(self):
        os.makedirs(self.bk, exist_ok=True)
        old = os.path.join(self.bk, "nexus__20260901_000000.dump")
        new = os.path.join(self.bk, "nexus__20990101_000000.dump")
        for p in (old, new):
            with open(p, "wb") as fh:
                fh.write(b"x")
        aged = time.time() - 20 * 86400
        os.utime(old, (aged, aged))
        self._mock_pg_dump(b"FAKE")
        self._mock_pg_restore(0)
        proc = self._run()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(old), "old artifact must be pruned")
        self.assertTrue(os.path.exists(new), "recent artifact must be kept")
        self.assertIn("retention: pruned 1 dump(s)", self._log())


class DockerE2ETest(unittest.TestCase):
    """Real pg_dump -> real pg_restore verification against a throwaway
    postgres container on a random loopback port. Never touches the live
    pgvector_db. Skips cleanly when Docker/image is unavailable."""

    IMAGE = "pgvector/pgvector:pg17"
    CONTAINER = "pgeh-e2e-pg"

    @classmethod
    def setUpClass(cls):
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker not available")
        img = subprocess.run(
            ["docker", "image", "inspect", cls.IMAGE, "--format", "ok"],
            capture_output=True, text=True,
        )
        if img.returncode != 0:
            raise unittest.SkipTest(f"image {cls.IMAGE} not present")
        subprocess.run(["docker", "rm", "-f", cls.CONTAINER],
                       capture_output=True)
        r = subprocess.run(
            ["docker", "run", "--rm", "-d", "--name", cls.CONTAINER,
             "-e", f"POSTGRES_USER={PGUSER}",
             "-e", f"POSTGRES_PASSWORD={PGPASS}",
             "-e", "POSTGRES_DB=nexus",
             "-p", "127.0.0.1::5432",
             cls.IMAGE],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise unittest.SkipTest(f"docker run failed: {r.stderr[-200:]}")
        cls._started = True
        port_out = subprocess.run(
            ["docker", "port", cls.CONTAINER, "5432"],
            capture_output=True, text=True,
        ).stdout.strip()
        cls.port = int(port_out.split(":")[-1])
        env = dict(os.environ, PGPASSWORD=PGPASS)
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            chk = subprocess.run(
                ["psql", "-h", "127.0.0.1", "-p", str(cls.port), "-U", PGUSER,
                 "-d", "nexus", "-X", "-qAt", "-c", "SELECT 1"],
                capture_output=True, text=True, env=env,
            )
            if chk.returncode == 0 and chk.stdout.strip() == "1":
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise unittest.SkipTest("throwaway container did not become ready")
        subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", str(cls.port), "-U", PGUSER,
             "-d", "nexus", "-X", "-q", "-v", "ON_ERROR_STOP=1",
             "-c", "CREATE TABLE t1 (id serial PRIMARY KEY, v text);",
             "-c", "INSERT INTO t1 (v) VALUES ('escape-hatch-fixture');"],
            capture_output=True, text=True, env=env,
        )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_started", False):
            subprocess.run(["docker", "rm", "-f", cls.CONTAINER],
                           capture_output=True)

    def test_real_dump_verified_end_to_end(self):
        tmp = tempfile.mkdtemp(prefix="pgeh-e2e-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": str(self.port),
            "PGUSER": PGUSER,
            "PGPASSWORD": PGPASS,
            "PGDATABASE": "nexus",
            "BACKUP_DIR": os.path.join(tmp, "bk"),
            "LOCK_FILE": os.path.join(tmp, "lock"),
            "NEBULA_URL": os.path.join(tmp, "unwritable-nebula"),
        }
        proc = subprocess.run(["bash", SCRIPT], capture_output=True,
                              text=True, env=env)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        bk = os.path.join(tmp, "bk")
        dumps = [f for f in os.listdir(bk) if f.startswith("nexus__") and f.endswith(".dump")]
        self.assertEqual(1, len(dumps))
        dump = os.path.join(bk, dumps[0])
        # REAL verification gate: the archive pg_dump produced must parse
        chk = subprocess.run(
            ["pg_restore", "--list", dump], capture_output=True, text=True
        )
        self.assertEqual(0, chk.returncode, chk.stderr)
        self.assertIn("TABLE", chk.stdout)  # fixture table present in TOC
        with open(os.path.join(bk, "last-backup.json")) as fh:
            self.assertIn('"database": "nexus"', fh.read())


if __name__ == "__main__":
    unittest.main()
