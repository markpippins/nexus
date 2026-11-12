#!/usr/bin/env python3
"""Tests for the forum-scope read path (bin/check-forum-scope.py).

Contract under test (2026-11-12): forum-scoped Assembly triage must read real
rows, never a silent zero. `assembly.posts.forum_id` is NULL on every row, so
the live key is `posts.forum_uuid` and the supported read path is
`assembly.forum_scope_v`. These tests pin:

  T1 the DDL defines the view joining on forum_uuid, not the legacy column
  T2 the legacy column is marked deprecated so the next reader is warned
  T3 the checker passes on healthy counts (and reports the legacy join as 0)
  T4 a zero-row view is a FAILURE, not an empty queue
  T5 resolved-vs-posts mismatch (dropped/invented rows) is a FAILURE
  T6 a missing psql is a connection error (exit 2), not a pass

The checker is exercised against a fake `psql` on PATH, so T3-T6 need no
database; only the view definition itself is pinned statically in T1/T2.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "bin" / "check-forum-scope.py"
DDL = ROOT / "typescript" / "assembly-srv" / "assembly-views-procs.sql"

FAKE_PSQL = """#!/usr/bin/env python3
import sys
sql = sys.argv[-1]
if "forum_id::text" in sql:
    print("{legacy}")
elif "EXISTS (SELECT 1 FROM assembly.forums" in sql:
    print("{resolvable}")
elif "GROUP BY forum_slug" in sql:
    print("to-do|{todo}|{todo}")
    print("transcripts|10|10")
elif "FROM assembly.forum_scope_v" in sql:
    print("{total}|{resolved}|{orphans}|{live}")
else:
    print("0")
"""


class FakePsql:
    """A temp dir holding an executable `psql` that prints canned counts."""

    def __init__(self, **counts):
        defaults = dict(
            legacy=0, resolvable=15434, total=15461, resolved=15434,
            orphans=27, live=11528, todo=535,
        )
        defaults.update(counts)
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "psql"
        path.write_text(FAKE_PSQL.format(**defaults))
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.path = str(path)

    def run_checker(self):
        env = dict(os.environ)
        env["PATH"] = self.dir.name + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [sys.executable, str(CHECKER)], capture_output=True, text=True, env=env,
        )

    def close(self):
        self.dir.cleanup()


class DdlContract(unittest.TestCase):
    def setUp(self):
        self.sql = DDL.read_text()

    def test_T1_view_joins_on_forum_uuid(self):
        self.assertIn("CREATE OR REPLACE VIEW assembly.forum_scope_v", self.sql)
        self.assertIn("LEFT JOIN assembly.forums f ON f.id = p.forum_uuid", self.sql)
        # and it must NOT reintroduce the dead column as the join key
        self.assertNotIn("ON f.id::text = p.forum_id::text", self.sql)

    def test_T2_legacy_column_is_marked_deprecated(self):
        self.assertIn("COMMENT ON COLUMN assembly.posts.forum_id", self.sql)
        block = self.sql.split("COMMENT ON COLUMN assembly.posts.forum_id", 1)[1]
        self.assertIn("DEPRECATED", block[:400])
        self.assertIn("forum_uuid", block[:400])


class CheckerBehaviour(unittest.TestCase):
    def check(self, **counts):
        fake = FakePsql(**counts)
        try:
            return fake.run_checker()
        finally:
            fake.close()

    def test_T3_healthy_counts_pass_and_report_legacy_zero(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("verdict: PASS", r.stdout)
        self.assertIn("legacy_forum_id_join: 0", r.stdout)
        self.assertIn("resolved: 15434", r.stdout)
        self.assertIn("orphans: 27", r.stdout)

    def test_T4_zero_row_view_fails(self):
        r = self.check(total=0, resolved=0, orphans=0, live=0)
        self.assertEqual(r.returncode, 1)
        self.assertIn("C1", r.stderr)
        self.assertNotIn("verdict: PASS", r.stdout)

    def test_T5_all_orphan_view_fails_as_empty_forum_scope(self):
        r = self.check(total=27, resolved=0, orphans=27, live=27, resolvable=0)
        self.assertEqual(r.returncode, 1)
        self.assertIn("C1", r.stderr)
        self.assertNotIn("verdict: PASS", r.stdout)

    def test_T6_resolved_mismatch_fails(self):
        r = self.check(total=10, resolved=8, orphans=2, live=10, resolvable=9)
        self.assertEqual(r.returncode, 1)
        self.assertIn("C2", r.stderr)

    def test_T7_missing_psql_is_a_connection_error(self):
        env = dict(os.environ)
        env["PATH"] = "/nonexistent-for-forum-scope-test"
        r = subprocess.run(
            [sys.executable, str(CHECKER)], capture_output=True, text=True, env=env,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
