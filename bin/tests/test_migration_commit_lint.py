#!/usr/bin/env python3
"""Migration transactional-integrity lint (pitfall #19 companion).

Hermetic: no database, no network. Walks sql/V1*.sql in this repository and
pins the invariant learned from the V181 incident (PR #319, R2 f00be42c):

  V181 (node_requirements DDL) was authored with `BEGIN;` but no trailing
  `COMMIT;`. Every live apply printed full success (CREATE TABLE / INSERT 0 5)
  yet persisted NOTHING — psql rolls back the open transaction on session
  exit. Two E2E suites could not catch it because the house throwaway-DB
  pattern holds ONE connection for the whole test: an uncommitted transaction
  is visible inside the session that opened it. Only a fresh-session post-
  apply probe — or this lint — can catch the class.

The invariant, stated precisely against the real corpus (80 migrations):

  A migration file MUST NOT open a top-level transaction (`BEGIN;`) without
  closing it (`COMMIT;`).

  - Files with NO BEGIN are legal: psql runs each statement in autocommit.
    (V112, V113, V119, V122, V123, V130, V136, V138–V142, V146, V147, V162,
    V163 — autocommit DDL, intentionally untransactional.)
  - Files with BEGIN and COMMIT are the majority house shape (V155, V166,
    V175, V179, V180, ...).
  - BEGIN without COMMIT is the bug class. On 2026-09-18 the corpus contained
    exactly one member: V181. This lint makes the count zero, permanently.

Positive-control fixtures prove the parser itself detects the class (a lint
that silently passes everything is worse than no lint).

Run:
  python3 -m pytest bin/tests/test_migration_commit_lint.py -v
"""

import os
import re
import tempfile
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_SELF, "..", ".."))
SQL_DIR = os.path.join(REPO_ROOT, "sql")

# Statement-level matchers. Anchored to statement starts so nested
# dollar-quoted bodies (trigger functions quoting BEGIN/COMMIT inside
# $$ ... $$) and comments do not count.
_BEGIN_RE = re.compile(r"^\s*BEGIN\s*;\s*$", re.MULTILINE | re.IGNORECASE)
_COMMIT_RE = re.compile(r"^\s*COMMIT\s*;\s*$", re.MULTILINE | re.IGNORECASE)


def strip_dollar_quotes(text):
    """Replace $$...$$ / $tag$...$tag$ bodies with a placeholder so a
    trigger function's internal BEGIN/COMMIT keywords cannot satisfy the
    lint. Also strips line comments (a commented-out COMMIT must not count)."""
    out = re.sub(
        r"\$(\w*)\$.*?\$\1\$",
        " /* dollar-quoted block */ ",
        text,
        flags=re.DOTALL,
    )
    return out


def transaction_shape(sql_text):
    """Return (begin_count, commit_count) for a migration's text."""
    stripped = strip_dollar_quotes(sql_text)
    return (
        len(_BEGIN_RE.findall(stripped)),
        len(_COMMIT_RE.findall(stripped)),
    )


def lint_migration_file(path):
    """Return a list of violation strings for one migration (empty = ok)."""
    with open(path, "r", encoding="utf-8") as f:
        begins, commits = transaction_shape(f.read())
    if begins > 0 and commits < begins:
        return [
            f"opens {begins} transaction(s) but closes {commits} — "
            f"psql rolls the open transaction back on session exit, so the "
            f"whole migration silently persists NOTHING (pitfall #19 / "
            f"V181 incident). Add a trailing COMMIT; per the house shape "
            f"(V180 is the reference)."
        ]
    return []


def lint_corpus(sql_dir=SQL_DIR):
    """Lint every V1*.sql migration; return {filename: [violations]}."""
    violations = {}
    if not os.path.isdir(sql_dir):
        return violations
    for name in sorted(os.listdir(sql_dir)):
        if re.fullmatch(r"V1\d+__.*\.sql", name):
            vs = lint_migration_file(os.path.join(sql_dir, name))
            if vs:
                violations[name] = vs
    return violations


class TestLiveCorpus(unittest.TestCase):
    def test_no_migration_opens_a_transaction_it_never_closes(self):
        """THE invariant: BEGIN without COMMIT does not exist on main.

        As of 2026-09-18 the corpus was 80 files; the single member of the
        bug class (V181) was fixed in the same PR as this lint.
        """
        violations = lint_corpus()
        self.assertEqual(
            violations,
            {},
            "Migrations that open BEGIN; without COMMIT; — these apply "
            "successfully and persist nothing on a psql session exit:\n"
            + "\n".join(f"  {k}: {v[0]}" for k, v in violations.items()),
        )

    def test_corpus_is_nonempty(self):
        """The lint must be walking a real corpus — a silent zero-file walk
        (wrong checkout, renamed dir) would make the invariant test vacuous."""
        names = [
            n
            for n in os.listdir(SQL_DIR)
            if re.fullmatch(r"V1\d+__.*\.sql", n)
        ]
        self.assertGreaterEqual(
            len(names), 80, f"expected the full migration corpus in {SQL_DIR}"
        )

    def test_v181_reference_shape(self):
        """V181 — the incident file — carries the trailing COMMIT; and is
        balanced. Pinned by name so its repair stays visible in history."""
        path = None
        for n in os.listdir(SQL_DIR):
            if n.startswith("V181__"):
                path = os.path.join(SQL_DIR, n)
        self.assertIsNotNone(path, "V181 not found in sql/")
        with open(path, "r", encoding="utf-8") as f:
            begins, commits = transaction_shape(f.read())
        self.assertGreater(begins, 0, "V181 is transactional (BEGIN;)")
        self.assertEqual(commits, begins, "V181 closes every transaction")


class TestParserPositiveControls(unittest.TestCase):
    """Prove the parser DETECTS the class — the lint must not be a no-op."""

    def _lint_text(self, tmp, name, text):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return lint_migration_file(p)

    def test_begin_without_commit_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V199__bug_class.sql",
                "CREATE TABLE t(id int);\nBEGIN;\nINSERT INTO t VALUES (1);\n",
            )
            self.assertEqual(len(vs), 1)
            self.assertIn("persists NOTHING", vs[0])

    def test_begin_with_commit_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V198__ok.sql",
                "BEGIN;\nCREATE TABLE t(id int);\nCOMMIT;\n",
            )
            self.assertEqual(vs, [])

    def test_autocommit_without_begin_is_legal(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V197__autocommit.sql",
                "CREATE TABLE t(id int);\nALTER TABLE t ADD COLUMN c text;\n",
            )
            self.assertEqual(vs, [])

    def test_dollar_quoted_body_cannot_satisfy_the_lint(self):
        """A trigger function's internal BEGIN...END inside $$...$$ must not
        count as the migration's transaction close."""
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V196__tricky.sql",
                "BEGIN;\n"
                "CREATE FUNCTION f() RETURNS trigger AS $$\n"
                "BEGIN\n"
                "  NEW.x := 1;\n"
                "  RETURN NEW;\n"
                "END;\n"
                "$$ LANGUAGE plpgsql;\n",
            )
            self.assertEqual(len(vs), 1, "nested BEGIN must not close BEGIN;")

    def test_commented_out_commit_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V195__commented.sql",
                "BEGIN;\n-- COMMIT;\n",
            )
            self.assertEqual(len(vs), 1)

    def test_multiple_transactions_all_must_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = self._lint_text(
                tmp,
                "V194__two_tx.sql",
                "BEGIN;\nCREATE TABLE a(i int);\nCOMMIT;\n"
                "BEGIN;\nCREATE TABLE b(i int);\n",
            )
            self.assertEqual(len(vs), 1)
            self.assertIn("closes 1", vs[0])


if __name__ == "__main__":
    unittest.main()
