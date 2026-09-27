#!/usr/bin/env python3
"""Hermetic coverage for bin/rehearse-migration.py.

No database and no network: every test here is a pure function test or drives
the CLI in --plan-only mode, which by construction never connects. The point
is that a test asserting the guard works must not itself require the thing the
guard protects.

The regression that matters is `test_repo_migrations_that_self_transact_are_refused`:
the failure this script exists to prevent happened against a real checked-in
migration, so the suite pins the behaviour against the real repository rather
than only against a synthetic fixture.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "bin", "rehearse-migration.py")


def load():
    """Import the script as a module.

    It must be registered in sys.modules BEFORE exec_module: @dataclass
    resolves its own module through sys.modules at class-creation time and
    raises AttributeError otherwise.
    """
    spec = importlib.util.spec_from_file_location("rehearse_migration", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rehearse_migration"] = mod
    spec.loader.exec_module(mod)
    return mod


rm = load()

MIGRATION_DIRS = (
    "typescript/nebula-srv/migrations",
    "typescript/shrapnel/migrations",
)
TEST_DSN = "postgresql://pguser@localhost:5432/nexus_test"
LIVE_DSN = "postgresql://pguser@localhost:5432/nexus"


def repo_migrations() -> list[str]:
    out = []
    for d in MIGRATION_DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if name.endswith(".sql"):
                out.append(os.path.join(full, name))
    return out


class Masking(unittest.TestCase):
    """A BEGIN that is not statement-level must not be treated as one."""

    def test_masks_line_comments(self):
        masked = rm.mask_sql("SELECT 1;\n-- BEGIN;\nSELECT 2;\n")
        self.assertNotIn("BEGIN", masked.upper().replace("SELECT", ""))

    def test_masks_block_comments_including_nested(self):
        sql = "/* outer /* inner */ still */\nBEGIN;\n"
        masked = rm.mask_sql(sql)
        # the real BEGIN after the comment survives
        self.assertIn("BEGIN", masked)
        self.assertEqual(sql.count("BEGIN"), 1)

    def test_masks_string_literals(self):
        masked = rm.mask_sql("INSERT INTO t VALUES ('BEGIN;');\nBEGIN;\n")
        self.assertEqual(masked.count("BEGIN"), 1, "literal BEGIN must be masked")

    def test_masks_quoted_identifiers(self):
        masked = rm.mask_sql('CREATE TABLE "BEGIN;" (x int);\n')
        self.assertEqual(masked.count("BEGIN"), 0, "identifier BEGIN must be masked entirely")

    def test_masks_dollar_quoted_function_body(self):
        sql = (
            "CREATE FUNCTION f() RETURNS void AS $$\n"
            "BEGIN\n"
            "  INSERT INTO t VALUES (1);\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;\n"
        )
        self.assertEqual(rm.find_transaction_control(sql), [], "function body must not count")

    def test_masks_tagged_dollar_quote(self):
        sql = "DO $mig$\nBEGIN\n  PERFORM 1;\nEND;\n$mig$;\n"
        self.assertEqual(rm.find_transaction_control(sql), [])

    def test_mask_preserves_length(self):
        sql = "SELECT 'BEGIN;' /* x */ -- y\nBEGIN;\n"
        self.assertEqual(len(rm.mask_sql(sql)), len(sql))

    def test_mask_preserves_newlines(self):
        sql = "SELECT 1;\n-- c\nSELECT 2;\n"
        self.assertEqual(rm.mask_sql(sql).count("\n"), sql.count("\n"))


class Detection(unittest.TestCase):
    def test_finds_top_level_control_with_line_numbers(self):
        sql = "SELECT 1;\n\nBEGIN;\nSELECT 2;\nCOMMIT;\n"
        found = rm.find_transaction_control(sql)
        self.assertEqual([c.line for c in found], [3, 5])
        self.assertEqual(found[0].statement.upper().strip(";").strip(), "BEGIN")
        self.assertEqual(found[1].statement.upper().strip(";").strip(), "COMMIT")

    def test_detects_start_transaction_and_end(self):
        sql = "START TRANSACTION;\nSELECT 1;\nEND;\n"
        self.assertEqual(len(rm.find_transaction_control(sql)), 2)

    def test_is_case_insensitive(self):
        self.assertEqual(len(rm.find_transaction_control("begin;\ncommit;\n")), 2)

    def test_ignores_indented_lookalike_word(self):
        # `begins;` is not transaction control
        self.assertEqual(rm.find_transaction_control("SELECT begins;\n"), [])

    def test_ignores_begin_without_semicolon_in_string(self):
        self.assertEqual(rm.find_transaction_control("SELECT 'BEGIN';\n"), [])

    def test_empty_input(self):
        self.assertEqual(rm.find_transaction_control(""), [])


class Stripping(unittest.TestCase):
    def test_removes_control_and_preserves_line_numbers(self):
        sql = "SELECT 1;\n\nBEGIN;\nSELECT 2;\nCOMMIT;\n"
        found = rm.find_transaction_control(sql)
        out = rm.strip_transaction_control(sql, found)
        self.assertEqual(rm.find_transaction_control(out), [])
        self.assertEqual(out.count("\n"), sql.count("\n"), "line numbers must not shift")
        self.assertIn("SELECT 2;", out, "surrounding SQL must survive")

    def test_does_not_touch_string_contents(self):
        sql = "INSERT INTO t VALUES ('BEGIN;');\nBEGIN;\n"
        found = rm.find_transaction_control(sql)
        out = rm.strip_transaction_control(sql, found)
        self.assertIn("'BEGIN;'", out, "literal must be untouched")


class Dsn(unittest.TestCase):
    def test_rejects_keyword_value_dsn(self):
        with self.assertRaises(ValueError):
            rm.parse_dsn("host=localhost dbname=nexus")

    def test_never_returns_password(self):
        t = rm.parse_dsn("postgresql://user:s3cr3t@db.internal:5433/nexus")
        self.assertNotIn("s3cr3t", t.redacted())
        self.assertNotIn("s3cr3t", repr(t))

    def test_parses_components(self):
        t = rm.parse_dsn("postgresql://pguser@localhost:5432/nexus")
        self.assertEqual((t.host, t.port, t.user, t.dbname), ("localhost", "5432", "pguser", "nexus"))

    def test_disposable_detection(self):
        for db in ("nexus_test", "scrap", "tmp_db", "rehearsal1", "ephemeral"):
            self.assertTrue(rm.Target(dbname=db).is_disposable(), db)
        for db in ("nexus", "production", "staging", ""):
            self.assertFalse(rm.Target(dbname=db).is_disposable(), db)

    def test_scratch_is_not_treated_as_disposable(self):
        # Documented decision: in this repo `scratch` is a SCHEMA inside the
        # live `nexus` database, so inferring safety from that name would be
        # precisely the false confidence this script exists to prevent.
        self.assertFalse(rm.Target(dbname="scratch").is_disposable())


class PlanGuards(unittest.TestCase):
    def setUp(self):
        self.clean = os.path.join(ROOT, "bin", "tests", "_tmp_clean.sql")
        self.txing = os.path.join(ROOT, "bin", "tests", "_tmp_txing.sql")
        with open(self.clean, "w", encoding="utf-8") as fh:
            fh.write("CREATE TABLE zz_probe (id int);\n")
        with open(self.txing, "w", encoding="utf-8") as fh:
            fh.write("BEGIN;\nCREATE TABLE zz_probe (id int);\nCOMMIT;\n")

    def tearDown(self):
        for p in (self.clean, self.txing):
            if os.path.exists(p):
                os.unlink(p)

    def test_refuses_live_dsn(self):
        with self.assertRaises(rm.Refusal) as ctx:
            rm.build_plan(self.clean, LIVE_DSN)
        self.assertIn("REFUS", str(ctx.exception).lower().replace("refusing", "REFUS"))

    def test_allows_live_when_opted_in(self):
        plan = rm.build_plan(self.clean, LIVE_DSN, allow_live=True)
        self.assertTrue(any("NOT DISPOSABLE" in w for w in plan.warnings))

    def test_refuses_self_transaction(self):
        with self.assertRaises(rm.Refusal) as ctx:
            rm.build_plan(self.txing, TEST_DSN)
        self.assertIn("own transaction", str(ctx.exception))

    def test_refusal_names_the_offending_lines(self):
        with self.assertRaises(rm.Refusal) as ctx:
            rm.build_plan(self.txing, TEST_DSN)
        self.assertIn("line 1", str(ctx.exception))

    def test_strips_self_transaction_when_opted_in(self):
        plan = rm.build_plan(self.txing, TEST_DSN, allow_self_transaction=True)
        self.assertTrue(plan.stripped)
        self.assertEqual(rm.find_transaction_control(plan.body), [])

    def test_refuses_when_dbname_absent(self):
        with self.assertRaises(rm.Refusal):
            rm.build_plan(self.clean, "postgresql://u@localhost:5432/")

    def test_render_always_wraps_in_begin_and_rollback(self):
        # even a file with no transaction control of its own is wrapped, so the
        # rollback is the helper's guarantee rather than the operator's assumption
        plan = rm.build_plan(self.clean, TEST_DSN)
        out = rm.render(plan)
        self.assertTrue(out.lstrip().upper().startswith("BEGIN;"))
        self.assertIn("ROLLBACK;", out)
        # the BODY carries no transaction control of its own; the only BEGIN and
        # ROLLBACK in the rendered file are the wrapper this helper adds.
        self.assertEqual(rm.find_transaction_control(plan.body), [])
        wrapper = rm.find_transaction_control(out)
        self.assertEqual(
            [c.statement.upper() for c in wrapper], ["BEGIN;", "ROLLBACK;"]
        )


class RepoRegression(unittest.TestCase):
    """Pin the guard against the real repository, not only synthetic fixtures."""

    def _first_self_transacting(self):
        for path in repo_migrations():
            with open(path, encoding="utf-8") as fh:
                if rm.find_transaction_control(fh.read()):
                    return path
        return None

    def test_repo_contains_migrations_that_self_transact(self):
        # If this ever fails, either the hazard class is gone (good, update the
        # script's rationale) or detection regressed (bad). Either way it must
        # be a deliberate change.
        self.assertIsNotNone(
            self._first_self_transacting(),
            "no migration in the repo self-transacts — detection may have regressed",
        )

    def test_repo_migrations_that_self_transact_are_refused(self):
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        with self.assertRaises(rm.Refusal):
            rm.build_plan(path, TEST_DSN)
        # ...and proceed cleanly once the operator takes responsibility
        plan = rm.build_plan(path, TEST_DSN, allow_self_transaction=True)
        self.assertTrue(plan.stripped)

    def test_count_is_substantial(self):
        """The hazard is the default shape of the repo, not a one-file curiosity."""
        n = 0
        for path in repo_migrations():
            with open(path, encoding="utf-8") as fh:
                if rm.find_transaction_control(fh.read()):
                    n += 1
        total = len(repo_migrations())
        self.assertGreater(total, 0)
        self.assertGreater(n / total, 0.2, f"expected a large minority, got {n}/{total}")


class Cli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=ROOT
        )

    def test_plan_only_refuses_and_exits_3(self):
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        r = self._run(path, "--dsn", TEST_DSN, "--plan-only")
        self.assertEqual(r.returncode, 3)
        self.assertIn("REFUSED", r.stderr)

    def test_plan_only_succeeds_with_overrides_and_connects_to_nothing(self):
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        r = self._run(
            path, "--dsn", LIVE_DSN, "--allow-live", "--allow-self-transaction", "--plan-only"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # plan/progress output is on stderr so --emit yields clean SQL on stdout
        self.assertIn("nothing executed, nothing connected", r.stderr)
        self.assertIn("STRIPPED", r.stderr)
        self.assertEqual(r.stdout.strip(), "", "stdout must be empty without --emit")

    def test_emit_writes_only_sql_to_stdout(self):
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        r = self._run(
            path, "--dsn", TEST_DSN, "--allow-self-transaction",
            "--emit", "--plan-only",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertTrue(out.lstrip().upper().startswith("BEGIN;"))
        self.assertIn("ROLLBACK;", out)
        self.assertNotIn("WARNING:", out, "plan text must not contaminate stdout")
        self.assertNotIn("migration :", out)

    def test_psql_is_invoked_so_it_cannot_prompt_or_hang(self):
        """Regression: psql blocked on an interactive password prompt, the run
        was killed at the timeout, and the rendered migration leaked to disk."""
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        captured = {}

        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return real_run(
                [sys.executable, "-c", "import sys; sys.exit(3)"], capture_output=True
            )

        rm.subprocess.run = fake_run
        try:
            rm.main(
                [
                    os.path.relpath(path, ROOT),
                    "--dsn", TEST_DSN,
                    "--allow-self-transaction",
                ]
            )
        finally:
            rm.subprocess.run = real_run

        self.assertIn("-w", captured["cmd"], "psql must be told never to prompt")
        self.assertIs(
            captured["kwargs"].get("stdin"),
            rm.subprocess.DEVNULL,
            "stdin must be closed so no prompt can block",
        )
        self.assertIn("ON_ERROR_STOP=1", captured["cmd"])
        self.assertEqual(captured["cmd"][-2], "-f")

    def test_no_temp_file_is_left_behind_on_psql_failure(self):
        path = self._first_self_transacting()
        if not path:
            self.skipTest("no self-transacting migration present")
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "rehearsal-*.sql")))
        r = self._run(
            path,
            "--dsn", "postgresql://pguser@127.0.0.1:1/nexus_test",
            "--allow-self-transaction",
        )
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), "rehearsal-*.sql")))
        self.assertEqual(after - before, set(), "temp file leaked on psql failure")
        self.assertNotEqual(r.returncode, 0)

    def test_missing_dsn_is_refused(self):
        r = self._run("bin/rehearse-migration.py", "--plan-only")
        self.assertIn(r.returncode, (2, 3))

    def test_missing_file_is_refused(self):
        r = self._run("bin/does-not-exist.sql", "--dsn", TEST_DSN, "--plan-only")
        self.assertEqual(r.returncode, 2)

    @staticmethod
    def _first_self_transacting():
        for path in repo_migrations():
            with open(path, encoding="utf-8") as fh:
                if rm.find_transaction_control(fh.read()):
                    return path
        return None


if __name__ == "__main__":
    unittest.main()
