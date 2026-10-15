#!/usr/bin/env python3
"""Hermetic tests for the nightly PG-dump serialization lock.

Incident (2026-10-02): the four nightly PG tiers have overlapping timer
windows and each held only its OWN lock, so two pg_dump runs could overlap and
the host OOM-killed the dump clients. bin/lib/pg-dump-lock.sh supplies ONE
cluster-wide lock so the tiers queue instead of stampede.

What is pinned here:

  * Serialization is real: with two callers sharing one lock file, the second
    acquires only AFTER the first releases, and both complete — not a silent
    no-op, not a stampede.
  * Contention is bounded: a caller that cannot acquire within its timeout
    reports TIMEOUT (the caller's honest SKIP path), so the lock can never hang
    a tier forever.
  * The lock is genuinely shared: every PG tier script sources the same
    library and calls pg_dump_lock_acquire (a regression that drops the wiring
    from any tier fails here).
  * The memory-cap drop-ins exist for every tier (the other half of the fix).

Run:
  python3 -m pytest bin/tests/test_pg_dump_lock.py -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB = os.path.join(REPO_ROOT, "bin", "lib", "pg-dump-lock.sh")

# The scripts that must all participate in the ONE shared lock.
TIER_SCRIPTS = [
    os.path.join(REPO_ROOT, "bin", "pg-backup-to-vanadium.sh"),
    os.path.join(REPO_ROOT, "bin", "pg-escape-hatch.sh"),
]

# The memory-cap drop-ins authored in the repo (tracked config).
CAP_DROPINS = [
    "backup-pg-to-vanadium",
    "pg-escape-hatch",
    "sync-pg-to-vanadium",
    "backup-nexus",
]

# Host scripts outside the repo (present on titanium, absent on CI).
HOST_SCRIPTS = [
    "/home/codex/dev/pgsql/backup-nexus.sh",
    "/home/codex/dev/pgsql/sync-to-vanadium.sh",
]


# A caller: acquire, append ACQUIRED, hold, append RELEASED, release.
CALLER = r"""
source "{lib}"
name="$1"; log="$2"; hold="$3"; wait="$4"
if pg_dump_lock_acquire "$wait"; then
  echo "ACQUIRED $name" >> "$log"
  sleep "$hold"
  echo "RELEASED $name" >> "$log"
  pg_dump_lock_release
else
  echo "TIMEOUT $name" >> "$log"
  exit 3
fi
"""


def _launch(lib, name, log, hold, wait, lock_path):
    env = dict(os.environ, PG_DUMP_CLUSTER_LOCK=lock_path)
    return subprocess.Popen(
        ["bash", "-c", CALLER.format(lib=lib), "_", name, log, str(hold),
         str(wait)],
        env=env,
    )


def _read(log_path):
    for _ in range(50):
        if os.path.exists(log_path):
            text = open(log_path).read()
            if text.strip():
                return text.splitlines()
        time.sleep(0.1)
    return []


class Serialization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pgdl-")
        self.log = os.path.join(self.tmp, "order.log")
        self.lock = os.path.join(self.tmp, "cluster.lock")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_caller_waits_then_runs(self):
        """B acquires only after A releases; both complete in order."""
        a = _launch(LIB, "A", self.log, 1.2, 10, self.lock)
        time.sleep(0.3)  # let A take the lock first
        b = _launch(LIB, "B", self.log, 0.1, 10, self.lock)
        self.assertEqual(a.wait(timeout=15), 0)
        self.assertEqual(b.wait(timeout=15), 0)

        lines = _read(self.log)
        self.assertEqual(lines,
                         ["ACQUIRED A", "RELEASED A", "ACQUIRED B", "RELEASED B"],
                         "callers did not serialize (got %r)" % (lines,))

    def test_queued_caller_is_not_skipped(self):
        """Serialization queues B — it must not silently no-op or overlap."""
        a = _launch(LIB, "A", self.log, 0.8, 10, self.lock)
        time.sleep(0.2)
        b = _launch(LIB, "B", self.log, 0.2, 10, self.lock)
        a.wait(timeout=15)
        b.wait(timeout=15)
        lines = _read(self.log)
        self.assertIn("ACQUIRED B", lines)
        self.assertIn("RELEASED B", lines)
        # B must be strictly after A's release.
        self.assertLess(lines.index("RELEASED A"), lines.index("ACQUIRED B"))

    def test_contended_caller_times_out(self):
        """A caller that cannot acquire within its timeout reports TIMEOUT."""
        a = _launch(LIB, "A", self.log, 3.0, 10, self.lock)
        time.sleep(0.3)
        b = _launch(LIB, "B", self.log, 0.1, 1, self.lock)  # wait=1s
        self.assertEqual(b.wait(timeout=15), 3)  # exit 3 == TIMEOUT branch
        a.wait(timeout=15)
        self.assertIn("TIMEOUT B", _read(self.log))
        self.assertNotIn("ACQUIRED B", _read(self.log))

    def test_release_allows_immediate_reacquire(self):
        """Once released, a later caller takes the lock at once."""
        a = _launch(LIB, "A", self.log, 0.1, 10, self.lock)
        a.wait(timeout=15)
        start = time.time()
        b = _launch(LIB, "B", self.log, 0.1, 10, self.lock)
        self.assertEqual(b.wait(timeout=15), 0)
        self.assertLess(time.time() - start, 5.0)


class Wiring(unittest.TestCase):
    def test_every_tier_script_sources_the_lock(self):
        for path in TIER_SCRIPTS:
            with open(path) as fh:
                body = fh.read()
            self.assertIn("pg-dump-lock.sh", body,
                          "%s does not source the cluster lock" % path)
            self.assertIn("pg_dump_lock_acquire", body,
                          "%s never acquires the cluster lock" % path)

    def test_host_scripts_source_the_lock(self):
        """The pgsql/ tiers outside the repo also participate (skip on CI)."""
        present = [p for p in HOST_SCRIPTS if os.path.exists(p)]
        if not present:
            self.skipTest("host PG scripts not present (not titanium)")
        for path in present:
            with open(path) as fh:
                body = fh.read()
            self.assertIn("pg-dump-lock.sh", body,
                          "%s does not source the cluster lock" % path)
            self.assertIn("pg_dump_lock_acquire", body,
                          "%s never acquires the cluster lock" % path)

    def test_default_lock_path_is_one_file(self):
        """The default path is a single shared file, not per-script."""
        with open(LIB) as fh:
            body = fh.read()
        self.assertIn("/tmp/pg-dump-cluster.lock", body)

    def test_memory_cap_dropins_present(self):
        for svc in CAP_DROPINS:
            path = os.path.join(REPO_ROOT, "config", "systemd",
                                "%s.service.d" % svc, "memory-cap.conf")
            self.assertTrue(os.path.exists(path), "missing %s" % path)
            with open(path) as fh:
                body = fh.read()
            self.assertIn("MemoryHigh=", body)
            self.assertIn("MemoryMax=", body)
            self.assertIn("MemorySwapMax=", body)

    def test_vanadium_tier_cap_is_larger_than_single_db(self):
        def hard(svc):
            p = os.path.join(REPO_ROOT, "config", "systemd",
                             "%s.service.d" % svc, "memory-cap.conf")
            for line in open(p):
                if line.startswith("MemoryMax="):
                    val = line.strip().split("=", 1)[1]
                    return int(val[:-1]) * (1024 ** 3 if val.endswith("G")
                                            else 1024 ** 2)
            raise AssertionError("no MemoryMax in %s" % p)

        self.assertGreater(hard("backup-pg-to-vanadium"),
                           hard("pg-escape-hatch"))


if __name__ == "__main__":
    unittest.main()
