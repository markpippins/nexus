#!/usr/bin/env python3
"""Guard: schema-enumerating SQL must exclude the `pg_` schemas.

Background
----------
`pg_namespace` on the live `nexus` database holds 234 schemas, of which only
**35 are real**:

    pg_toast            1     permanent — ~1.21 GB of real out-of-line storage
    pg_toast_temp_N   197     per-session
    pg_temp_N         197     per-session
    real schemas      35

`pg_toast` cannot be removed — it is PostgreSQL-internal and `pg_`-prefixed
schemas are undroppable and unrenameable. So the only way to get a clean
listing is to FILTER at the query. Any query that enumerates schemas without a
`pg_` predicate returns ~200 rows of noise for every 1 row of signal.

The pre-existing idiom in this repo was a hand-maintained denylist:

    nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
      AND nspname NOT LIKE 'pg_temp%'

which is both easy to forget (in each driver copy it was applied to 6 of 9
queries) and incomplete — it misses `pg_toast_temp_N` and `pg_temp_N`, which
are `pg_`-prefixed but not `pg_temp%`. This test pins the invariant.

What counts as safe
------------------
A schema-enumerating query is acceptable if it EITHER

  * excludes the `pg_` schemas (`!~ '^pg_'`, `NOT LIKE 'pg_%'`,
    `NOT IN (... 'pg_toast' ...)`, `NOT IN (... 'pg_catalog' ...)`), OR
  * is scoped to one named schema — `nspname = 'nebula'`, `table_schema = %s`,
    `schema_name = current_schema()` — which cannot return a `pg_` row.

Scanning method
---------------
Candidates are found by extracting **string literals** and testing each one,
not by scanning raw lines. Line scanning produces two false-positive classes
that make a guard worse than useless:

  * Comments and prose. `tackle-seeds/index.ts` documents a migration that
    checks `information_schema.columns`; a line scanner flags it, and a
    blank-line-delimited "block" scanner then reports 4029 hits in that one
    file because the whole seed blob is a single block.
  * Python implicit string concatenation. The idiomatic psycopg2 call is

        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s", (schema, table))

    Two adjacent literals that concatenate into ONE statement. Testing them
    separately sees an enumerator in the first half and the scope clause in
    the second half, and reports a leak that does not exist.

Adjacent literals separated only by whitespace are therefore merged before
testing. See `test_extraction_merges_python_implicit_concatenation` for the
regression that pins this.

Out of scope
------------
* `sql/` and `*/migrations/` — historical records of migrations that ran.
  Rewriting them would falsify the record; a migration is a timestamped
  account of the past, not a live query.
* Test files — assertions against throwaway test databases, often scoped by
  table name. Several count `information_schema` rows unscoped; that is a
  separate, lower-severity concern and is tracked separately rather than
  bundled into this change.
* `vendor/`, `*seeds*` — generated blobs.
"""
from __future__ import annotations

import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

SCAN_DIRS = ("moleculer", "adonisjs", "typescript", "python", "bin")
SCAN_EXT = (".ts", ".js", ".py", ".sql")

EXCLUDE_DIR_PARTS = (
    "node_modules",
    "dist",
    "__pycache__",
    ".git",
    "migrations-proposal",
    "migrations",  # historical migration records — see module docstring
    "sql",  # ditto
    "vendor",
    "seeds",
)


def _excluded(path: str) -> bool:
    parts = path.split("/")
    if any(p in EXCLUDE_DIR_PARTS for p in parts):
        return True
    base = parts[-1]
    # Test files, by either convention.
    return base.startswith("test_") or ".test." in base or base.endswith("_test.py")


# Enumerates objects across every schema. The FROM/JOIN requirement is what
# separates real SQL from prose that merely names a view.
ENUMERATORS = re.compile(
    r"\b(?:from|join)\s+"
    r"(?:information_schema\s*\.\s*\w+"
    r"|pg_namespace|pg_class|pg_proc|pg_trigger|pg_indexes|pg_tables|pg_views)\b",
    re.IGNORECASE,
)

# Excludes the pg_ family.
FILTERS = (
    re.compile(r"!\s*~\s*'\^pg_'", re.IGNORECASE),
    re.compile(r"not\s+like\s+'\s*pg\\_?%'", re.IGNORECASE),
    re.compile(r"not\s+like\s+'\s*pg_temp%", re.IGNORECASE),
    re.compile(r"not\s+in\s*\([^)]*'pg_toast'", re.IGNORECASE),
    re.compile(r"not\s+in\s*\([^)]*'pg_catalog'", re.IGNORECASE),
)

# Scoped to a single, known schema: a literal, a bind placeholder, or
# current_schema(). `schema_name` is included because
# information_schema.schemata exposes that column name, not `nspname`.
_SCHEMA_COLS = r"nspname|schemaname|table_schema|schema_name|constraint_schema"
_SCOPED_VALUE = (
    r"'[^']*'"  # literal
    r"|\$\{[^}]*\}"  # JS template interpolation
    r"|\$\d+"  # $1 (pg)
    r"|%\(\w+\)s|%s"  # psycopg2 named / positional
    r"|:\w+"  # :name
    r"|\?"  # sqlite/mysql
    r"|@\w+"  # mssql
    r"|current_schema\s*\(\s*\)"
)
SCOPED = re.compile(
    rf"\b(?:{_SCHEMA_COLS})\s*=\s*(?:{_SCOPED_VALUE})", re.IGNORECASE
)

# A catalog column compared against a regclass — `conrelid = 'nebula.users'`,
# `tgrelid = %s::regclass` — resolves to exactly one relation OID, so it
# cannot span schemas either. Sited by the column, not by the cast alone,
# so that a bare `::regclass` in unrelated text is not treated as a scope.
_SCOPED_REGCLASS = re.compile(
    rf"\b(?:conrelid|contypid|conindid|tgrelid|tgtype|relid)\s*=\s*"
    rf"(?:{_SCOPED_VALUE})\s*::\s*regclass",
    re.IGNORECASE,
)

# NB: literal bodies are removed by slicing, never by str.strip() —
# str.strip takes a CHARACTER SET, so strip("`\"'") silently eats the
# closing quote of a SQL literal like "… n.nspname = 'nebula'", which then
# fails to match SCOPED and reports a leak that is not there.
_OPENERS = {'"': '"', "'": "'", "`": "`"}


def scan_literals(text: str, ext: str) -> list[tuple[int, int, str]]:
    """Return (start, end, body) for every string literal, comments excluded.

    A hand-written scanner rather than a regex. A regex over raw source
    unbalances on the first apostrophe inside a comment — `// don't dedupe`
    opens a `'` literal that runs until the next apostrophe in the file, and
    the "literal" that comes out is a hundred lines of TypeScript. Feeding
    that to the enumerator check is how a first cut of this test reported
    4029 hits in a single generated seed file.
    """
    hash_comments = ext == ".py"
    out: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        # Line comments. `#` is a comment in Python only — in TS it is a
        # private-field sigil (`this.#cache`) and must not swallow the line.
        if (ch == "/" and nxt == "/") or (ch == "#" and hash_comments):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        # Block comments.
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue

        delim = None
        if text[i : i + 3] in ('"""', "'''"):
            delim = text[i : i + 3]
        elif ch in _OPENERS:
            delim = ch
        if delim is not None:
            # A literal may carry a prefix (`f"..."`, `b"..."`) or be a tagged
            # template (`sql`...``). Walk back over the identifier so that
            # prefix does not land in the inter-literal gap: with
            #     "SELECT count(*) FROM pg_trigger "
            #     f"WHERE tgrelid='{schema}.{table}'::regclass "
            # the `f` would otherwise read as code between the two halves and
            # the statement would not merge.
            lit_start = i
            while lit_start > 0 and (
                text[lit_start - 1].isalnum() or text[lit_start - 1] in "_$"
            ):
                lit_start -= 1
            start = i + len(delim)
            j = start
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j : j + len(delim)] == delim:
                    break
                j += 1
            out.append((lit_start, j + len(delim), text[start:j]))
            i = j + len(delim)
            continue

        i += 1
    return out


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "ls-files", "--", *SCAN_DIRS],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = []
        for d in SCAN_DIRS:
            for root, dirs, files in os.walk(os.path.join(ROOT, d)):
                dirs[:] = [x for x in dirs if x not in EXCLUDE_DIR_PARTS]
                for f in files:
                    if f.endswith(SCAN_EXT):
                        out.append(os.path.relpath(os.path.join(root, f), ROOT))
    return [
        f
        for f in out
        if f
        and f.endswith(SCAN_EXT)
        and not _excluded(f)
        and os.path.isfile(os.path.join(ROOT, f))
    ]


def literals(text: str, ext: str) -> list[tuple[int, str]]:
    """Return (lineno, body) for each SQL-carrying string literal.

    Adjacent literals separated only by whitespace are merged, because Python
    implicit concatenation makes them one statement.
    """
    if ext == ".sql":
        return [(1, text)]

    merged: list[tuple[int, str]] = []
    pending: list[tuple[int, str]] = []
    prev_end: int | None = None

    def close() -> None:
        if pending:
            merged.append((pending[0][0], "".join(b for _, b in pending)))
            pending.clear()

    for start, end, body in scan_literals(text, ext):
        # Implicit concatenation: two literals separated by nothing but
        # whitespace are one statement (the psycopg2 idiom). `prev_end` is
        # the offset just past the previous literal's closing delimiter, so
        # the gap excludes the quotes themselves.
        gap = text[prev_end:start] if prev_end is not None else ""
        if pending and gap.strip() == "":
            pending.append((start, body))
        else:
            close()
            pending.append((start, body))
        prev_end = end
    close()
    return [(text.count("\n", 0, off) + 1, body) for off, body in merged]


def offenders(path: str) -> list[tuple[int, str]]:
    """(lineno, snippet) for each unscoped schema-enumerating literal."""
    ext = os.path.splitext(path)[1]
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    hits: list[tuple[int, str]] = []
    for lineno, body in literals(text, ext):
        if not ENUMERATORS.search(body):
            continue
        if any(f.search(body) for f in FILTERS):
            continue
        if SCOPED.search(body) or _SCOPED_REGCLASS.search(body):
            continue
        hits.append((lineno, " ".join(body.split())[:120]))
    return hits


class SchemaFilters(unittest.TestCase):
    def test_no_unfiltered_schema_enumeration(self):
        found: dict[str, list[tuple[int, str]]] = {}
        for path in tracked_files():
            if path.endswith("test_pg_schema_filters.py"):
                continue
            hits = offenders(path)
            if hits:
                found[path] = hits

        if found:
            report = []
            for path, hits in sorted(found.items()):
                report.append(f"  {path}:{hits[0][0]} (+{len(hits) - 1} more)")
                report.append(f"      {hits[0][1]}")
            self.fail(
                "Schema-enumerating SQL with no pg_ filter and no named-schema "
                "scope. Either exclude the pg_ schemas (e.g. !~ '^pg_') or "
                "scope to one schema:\n" + "\n".join(report)
            )

    def test_extraction_merges_python_implicit_concatenation(self) -> None:
        """Regression: a two-literal psycopg2 call is ONE statement.

        Unmerged, the enumerator in the first half and the scope clause in
        the second half look like an unscoped leak.
        """
        src = (
            'cur.execute(\n'
            '    "SELECT COUNT(*) FROM information_schema.tables "\n'
            '    "WHERE table_schema = %s AND table_name = %s",\n'
            "    (schema, table))\n"
        )
        found = literals(src, ".py")
        self.assertEqual(len(found), 1, f"expected 1 merged literal, got {len(found)}")
        self.assertIn("table_schema = %s", found[0][1])
        self.assertEqual(offenders_count(found[0][1]), 0)

    def test_extraction_merges_across_a_string_prefix(self) -> None:
        """Regression: an f-string on the second half does not break the merge.

        The `f` is a prefix, not code between the two halves of one statement.
        """
        src = (
            'cur.execute(\n'
            '    "SELECT count(*) FROM pg_trigger "\n'
            "    f\"WHERE tgrelid='{schema}.{table}'::regclass \"\n"
            '    "AND NOT tgisinternal")\n'
        )
        found = literals(src, ".py")
        self.assertEqual(len(found), 1, f"expected 1 merged literal, got {len(found)}")
        self.assertEqual(offenders_count(found[0][1]), 0)

    def test_delimiter_strip_does_not_eat_sql_quotes(self) -> None:
        """Regression: str.strip() takes a CHARACTER SET.

        strip("`\\"'") removes the closing quote of 'nebula', so the scope
        clause stops matching and a correctly-scoped query is reported.
        """
        body = (
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
            "WHERE p.proname = 'update_updated_at' AND n.nspname = 'nebula'"
        )
        self.assertIsNotNone(SCOPED.search(body))
        (lineno, extracted) = literals(f'const q = "{body}";', ".ts")[0]
        self.assertEqual(extracted, body, "literal body was mangled by extraction")
        self.assertEqual(offenders_count(extracted), 0)

    def test_prose_is_not_an_enumerator(self) -> None:
        """A comment that names a view must not trip the guard."""
        prose = (
            "  (`information_schema.columns` checks for every column the file "
            "touches, so it stays authoritative)"
        )
        self.assertEqual(offenders_count(prose), 0)

    def test_unscoped_pg_namespace_enumeration_is_caught(self) -> None:
        """Non-vacuity: the guard must actually fail on a real leak."""
        leak = "SELECT n.nspname FROM pg_namespace n ORDER BY n.nspname"
        self.assertEqual(offenders_count(leak), 1)
        fixed = leak.replace(
            "ORDER BY", "WHERE n.nspname !~ '^pg_' ORDER BY"
        )
        self.assertEqual(offenders_count(fixed), 0)

    def test_both_driver_copies_are_guarded(self) -> None:
        """The driver exists in two services; a canary on each path.

        Only one of them had been fixed when this test was written — the
        moleculer copy and the draft-srv copy are byte-identical in the query
        region, so a guard pinned to one path would have passed while the
        live service (draft-srv, backing the DB Workbench UI) still leaked.
        """
        for rel in (
            "moleculer/draft/services/drivers/postgres.ts",
            "typescript/draft-srv/src/drivers/postgres.ts",
        ):
            self.assertTrue(
                os.path.isfile(os.path.join(ROOT, rel)),
                f"{rel} moved; update this test and its rationale",
            )

    def test_both_driver_copies_exclude_pg_temp_schemas(self) -> None:
        """Direct assertion on the two driver files.

        The old denylist missed `pg_toast_temp_N` and `pg_temp_N` entirely:
        they are `pg_`-prefixed but do not match `pg_temp%`.
        """
        for rel in (
            "moleculer/draft/services/drivers/postgres.ts",
            "typescript/draft-srv/src/drivers/postgres.ts",
        ):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("!~ '^pg_'", body, f"{rel} lost the pg_ filter")
            self.assertNotIn(
                "NOT LIKE 'pg_temp%'",
                body,
                f"{rel} still uses the incomplete pg_temp% denylist",
            )
            self.assertEqual(
                offenders(rel), [], f"{rel} has an unscoped enumerator"
            )


def offenders_count(body: str) -> int:
    """0 if the literal is safe, 1 if it would be reported."""
    if not ENUMERATORS.search(body):
        return 0
    if any(f.search(body) for f in FILTERS):
        return 0
    if SCOPED.search(body) or _SCOPED_REGCLASS.search(body):
        return 0
    return 1


if __name__ == "__main__":
    unittest.main()
