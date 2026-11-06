#!/usr/bin/env python3
"""Run a migration inside a GUARANTEED rollback.

Why this exists
---------------
On 2026-09-25 a migration rehearsal committed 28,428 rows to the live populated
`nexus` database. The operator wrapped the file in ``BEGIN ... ROLLBACK`` and
assumed that was sufficient. The file carried its own ``BEGIN; ... COMMIT;``,
so the outer ROLLBACK was a no-op on the file's work and the migration
committed. Nothing errored. The rows were found by reading the output, deleted,
and verified gone -- but the safety was an accident of noticing, not a
property of the procedure.

``migrate.ts`` documents the hazard in its own header:

    Files may carry their own BEGIN/COMMIT (some do); executing the raw file
    text via the simple query protocol honours that ...

and 34 of the 67 migration files under ``typescript/nebula-srv/migrations`` and
``typescript/shrapnel/migrations`` do exactly that. The hazard is not a curiosity
of one file; it is the default shape of half the repository.

This helper makes the two things that were left to memory mechanical:

1. **A migration that manages its own transaction is REFUSED**, by default, with
   the offending line numbers. You cannot wrap your way past it by accident.
   ``--allow-self-transaction`` proceeds, but strips the inner transaction
   control into a temp copy and says so loudly.

2. **An unrecognised DSN is REFUSED.** A rehearsal whose whole purpose is to
   leave no trace should have to name the database it is about to touch. The
   database name must look disposable (test/scrap/tmp/...) or you must pass
   ``--allow-live`` and accept that you are rehearsing against something real.

The rewritten file is always wrapped in ``BEGIN; ... ROLLBACK;`` regardless of
whether the source had transaction control, so the rollback is the helper's
guarantee and not the operator's assumption.

Note on ``scratch``: a database named ``scratch`` is NOT treated as disposable.
In this repository ``scratch`` is a *schema* inside the live ``nexus`` database,
and a guard that inferred safety from a name that is routinely a schema would
give exactly the false confidence this script exists to prevent.

Usage
-----
    bin/rehearse-migration.py <migration.sql> --dsn <dsn> [options]

    # plan only -- detect, rewrite, print, do not connect
    bin/rehearse-migration.py path/to/057.sql --dsn postgresql://u@h/nexus_test --plan-only

    # execute against a disposable database
    bin/rehearse-migration.py path/to/057.sql --dsn postgresql://u@h/nexus_test

    # execute against a live database, deliberately and on the record
    bin/rehearse-migration.py path/to/057.sql --dsn postgresql://u@h/nexus --allow-live

After a successful rehearsal, verify residue yourself. The helper cannot know
which tables your migration touches.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

# Top-level transaction control. A migration containing any of these will defeat
# an outer BEGIN/ROLLBACK wrapper, which is the entire failure this guards.
TX_CONTROL_RE = re.compile(
    r"^[ \t]*(BEGIN|COMMIT|ROLLBACK|END|START\s+TRANSACTION|ABORT)[ \t]*;",
    re.IGNORECASE | re.MULTILINE,
)

# A database name containing any of these is treated as disposable. Deliberately
# does NOT include "scratch" -- see the module docstring.
DISPOSABLE_DB_MARKERS = (
    "test",
    "scrap",
    "tmp",
    "temp",
    "ephemeral",
    "rehearsal",
    "pgtest",
)

REDACTED = "***"


# --------------------------------------------------------------------------- #
# SQL masking: blank out strings, identifiers, comments and dollar-quoted bodies
# while preserving byte offsets, so matches can be located in the ORIGINAL text.
# --------------------------------------------------------------------------- #
def mask_sql(sql: str) -> str:
    """Return `sql` with all non-code regions replaced by spaces.

    Length is preserved (newlines kept) so a span found in the masked text
    indexes the same text in the original. This is what stops a `BEGIN;`
    inside a function body, a DO block, or a string literal from being
    mistaken for real transaction control.
    """
    out = list(sql)
    i, n = 0, len(sql)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = sql[i]

        # -- line comment
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue

        # /* block comment */ (PostgreSQL nests these)
        if sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
            continue

        # $tag$ ... $tag$   (function bodies, DO blocks)
        m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
        if m:
            tag = m.group(0)
            close = sql.find(tag, i + len(tag))
            j = n if close == -1 else close + len(tag)
            blank(i, j)
            i = j
            continue

        # 'string'  with '' escape
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            continue

        # "identifier"
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            continue

        i += 1

    return "".join(out)


def line_of(sql: str, index: int) -> int:
    return sql.count("\n", 0, index) + 1


@dataclass
class TxControl:
    statement: str
    line: int
    start: int  # character offset into the masked text
    end: int

    def __str__(self) -> str:
        return f"line {self.line}: {self.statement}"


def find_transaction_control(sql: str) -> list[TxControl]:
    """Top-level BEGIN/COMMIT/ROLLBACK/END/START TRANSACTION/ABORT statements."""
    masked = mask_sql(sql)
    found = []
    for m in TX_CONTROL_RE.finditer(masked):
        stmt = sql[m.start() : m.end()].strip()
        found.append(
            TxControl(
                statement=stmt,
                line=line_of(sql, m.start()),
                start=m.start(),
                end=m.end(),
            )
        )
    return found


def strip_transaction_control(sql: str, controls: list[TxControl]) -> str:
    """Blank out the given statements, preserving line numbering.

    Offsets come from the masked text, which is the same length as the
    original, so they index the original directly. Deriving a character
    offset from a LINE NUMBER would be wrong the moment a line is not one
    character long -- which is why the spans are carried on TxControl rather
    than recomputed here.
    """
    out = list(sql)
    for c in controls:
        for k in range(c.start, c.end):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


# --------------------------------------------------------------------------- #
# DSN handling
# --------------------------------------------------------------------------- #
@dataclass
class Target:
    host: str = ""
    port: str = ""
    user: str = ""
    dbname: str = ""

    def redacted(self) -> str:
        return f"postgresql://{self.user or '?'}@{self.host or '?'}:{self.port or '5432'}/{self.dbname or '?'}"

    def is_disposable(self) -> bool:
        low = (self.dbname or "").lower()
        return any(m in low for m in DISPOSABLE_DB_MARKERS)


def parse_dsn(dsn: str) -> Target:
    """Parse a postgres DSN. Never returns the password."""
    if "://" not in dsn:
        raise ValueError(
            "DSN must be a URL (postgresql://user@host:port/dbname). "
            "A bare keyword/value DSN cannot be verified, so it is refused."
        )
    p = urlparse(dsn)
    return Target(
        host=p.hostname or "",
        port=str(p.port) if p.port else "",
        user=unquote(p.username) if p.username else "",
        dbname=(p.path or "").lstrip("/"),
    )


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    path: str
    target: Target
    controls: list[TxControl] = field(default_factory=list)
    stripped: bool = False
    warnings: list[str] = field(default_factory=list)
    body: str = ""


class Refusal(Exception):
    """A guard tripped. Carries the operator-facing explanation."""


def build_plan(
    path: str,
    dsn: str,
    allow_live: bool = False,
    allow_self_transaction: bool = False,
) -> Plan:
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()

    target = parse_dsn(dsn)

    if not target.dbname:
        raise Refusal(
            f"Cannot determine the target database from {target.redacted()}. "
            "A rehearsal must name the database it is about to touch."
        )
    if not target.is_disposable() and not allow_live:
        raise Refusal(
            f"Refusing to rehearse against {target.redacted()}.\n"
            f"  The database name {target.dbname!r} is not recognisably disposable.\n"
            "  If this really is a throwaway, pass --allow-live and say so on the record.\n"
            "  (A rehearsal against a populated database can be the right call -- it is\n"
            "   the only way to prove behaviour against real data -- but it must be a\n"
            "   decision, not a default.)"
        )

    controls = find_transaction_control(sql)
    warnings: list[str] = []
    body = sql
    stripped = False

    if controls:
        if not allow_self_transaction:
            lines = "\n".join(f"    {c}" for c in controls)
            raise Refusal(
                f"Refusing to rehearse {os.path.basename(path)}: it manages its own "
                "transaction.\n"
                f"  Found {len(controls)} top-level transaction statement(s):\n{lines}\n"
                "\n"
                "  An outer BEGIN/ROLLBACK cannot roll back through these -- the inner\n"
                "  COMMIT ends the wrapper. This is how 28,428 rows reached the live\n"
                "  database on 2026-09-25.\n"
                "\n"
                "  Choose one:\n"
                "    --allow-self-transaction   strip the inner transaction control into\n"
                "                               a temp copy and wrap THAT (recommended)\n"
                "    (or rehearse the file body directly, without its transaction lines)"
            )
        body = strip_transaction_control(sql, controls)
        stripped = True
        warnings.append(
            f"STRIPPED {len(controls)} transaction statement(s) from {os.path.basename(path)}. "
            "The inner COMMIT would have defeated the outer ROLLBACK."
        )

    if not target.is_disposable():
        warnings.append(
            f"TARGET IS NOT DISPOSABLE: {target.redacted()}. "
            "This rehearsal runs against a database that may hold real state."
        )

    return Plan(
        path=path,
        target=target,
        controls=controls,
        stripped=stripped,
        warnings=warnings,
        body=body,
    )


def render(plan: Plan) -> str:
    return f"BEGIN;\n\n{plan.body}\n\nROLLBACK;\n"


def describe(plan: Plan) -> str:
    lines = [
        f"migration : {plan.path}",
        f"target    : {plan.target.redacted()}",
        f"disposable: {'yes' if plan.target.is_disposable() else 'NO -- live or unknown'}",
        f"own tx    : {len(plan.controls)} transaction statement(s)"
        + (" (STRIPPED)" if plan.stripped else ""),
    ]
    for c in plan.controls:
        lines.append(f"    {c}")
    for w in plan.warnings:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rehearse-migration.py",
        description="Run a migration inside a guaranteed ROLLBACK.",
    )
    ap.add_argument("migration", help="path to the migration .sql file")
    ap.add_argument(
        "--dsn",
        default=os.environ.get("REHEARSAL_DSN") or os.environ.get("DATABASE_URL") or "",
        help="target DSN (default: $REHEARSAL_DSN or $DATABASE_URL)",
    )
    ap.add_argument(
        "--allow-live",
        action="store_true",
        help="permit a DSN whose database name is not recognisably disposable",
    )
    ap.add_argument(
        "--allow-self-transaction",
        action="store_true",
        help="permit a migration that manages its own transaction (it will be stripped)",
    )
    ap.add_argument(
        "--plan-only",
        action="store_true",
        help="detect, rewrite and describe; do not connect to anything",
    )
    ap.add_argument(
        "--emit",
        action="store_true",
        help="print the rewritten rehearsal file to stdout",
    )
    ap.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep the rewritten temp file instead of deleting it",
    )
    args = ap.parse_args(argv)

    if not os.path.isfile(args.migration):
        print(f"error: no such migration: {args.migration}", file=sys.stderr)
        return 2
    if not args.dsn:
        print(
            "error: no --dsn given (and neither REHEARSAL_DSN nor DATABASE_URL is set).\n"
            "       A rehearsal must name the database it is about to touch.",
            file=sys.stderr,
        )
        return 2

    try:
        plan = build_plan(
            args.migration,
            args.dsn,
            allow_live=args.allow_live,
            allow_self_transaction=args.allow_self_transaction,
        )
    except (Refusal, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    # Progress and plan output go to stderr so that `--emit` yields clean SQL
    # on stdout and composes: rehearse-migration.py M --emit > rehearsal.sql
    print(describe(plan), file=sys.stderr)
    rendered = render(plan)

    # `--emit` and `--plan-only` are orthogonal: emit the rewritten SQL first,
    # then honour plan-only by returning before any connection is attempted.
    if args.emit:
        sys.stdout.write(rendered)

    if args.plan_only:
        print("\nplan-only: nothing executed, nothing connected.", file=sys.stderr)
        return 0

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="rehearsal-", suffix=".sql")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)

        print(f"\nrewritten : {tmp_path}", file=sys.stderr)
        print("running   : BEGIN; <migration> ROLLBACK;", file=sys.stderr)
        # -w/--no-password and stdin=DEVNULL are load-bearing, not decoration.
        # Without them psql BLOCKS on an interactive password prompt; a killed
        # or timed-out run then leaves the rendered migration on disk and skips
        # the finally cleanup. A rehearsal tool must fail fast, never hang on
        # stdin, and never leak its temp file because it was waiting for a human.
        proc = subprocess.run(
            ["psql", "-w", "-v", "ON_ERROR_STOP=1", args.dsn, "-f", tmp_path],
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            print(
                "\npsql failed. ON_ERROR_STOP=1 means the transaction aborted, but "
                "verify residue before assuming it did.",
                file=sys.stderr,
            )
            return proc.returncode
    finally:
        if tmp_path and not args.keep_temp and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    print(
        "\nrehearsal complete. ROLLBACK was issued.\n"
        "VERIFY RESIDUE YOURSELF -- this helper cannot know which tables the\n"
        "migration writes, so it cannot prove nothing persisted.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
