#!/usr/bin/env python3
"""Guard: no NEW `ON CONFLICT` clause that Postgres cannot execute.

Background
----------
`ON CONFLICT (cols)` requires Postgres to infer a unique index on exactly
those columns. When it cannot, the statement raises at execution:

    ERROR: there is no unique or exclusion constraint matching the
           ON CONFLICT specification

A repo-wide audit on 2026-09-27 found 339 clauses across 1945 files and 23
that could never run. Two of those were live endpoints:

  * `POST /api/artifact-provenance` (adonisjs nebula_meta_controller) does
    `ON CONFLICT ON CONSTRAINT uq_artifact_provenance_pair` against
    `nebula.artifact_provenance`, which is a VIEW over
    `nebula.artifact_provenance_history`. Confirmed by invoking it: HTTP 500,
    `constraint "uq_artifact_provenance_pair" for table
    "artifact_provenance" does not exist`, and the history table had 0 rows
    before and after — the endpoint had never written anything.
  * `provisionRole` step 6 in tackle-srv did the same to `nebula.roles`.
    Fixed on branch persona-bridge-parity (6ef79aa4).

The failure only shows up when the code path runs, which for a rarely-called
endpoint can be never.

What counts as broken
---------------------
Verified experimentally against PG 17.11, not assumed:

  ON CONFLICT (k)                  vs PARTIAL index  -> ERROR
  ON CONFLICT (k) WHERE <matches>  vs PARTIAL index  -> OK
  ON CONFLICT (k) WHERE <differs>  vs PARTIAL index  -> ERROR
  ON CONFLICT (k) WHERE <anything> vs FULL index     -> OK
  ON CONFLICT DO NOTHING (no target)                -> always OK, needs no index
  target is a view / matview / foreign table         -> always ERROR

The last two matter: a view is fine for a bare `DO NOTHING`, and a view can
NEVER satisfy a conflict target. Checking `pg_indexes` for a view returns zero
rows by definition — that is how `nebula.roles` was once mistaken for a table
with a missing index.

Deliberately NOT treated as broken
----------------------------------
A clause whose target relation is absent from the database under audit. That
is UNVERIFIABLE, not broken: the cluster carries 16 databases, and
`shrapnel.bridge_cursor` — reported "not found" from `nexus` — exists and is a
real table in the `postgres` database. Those are listed separately so they
cannot quietly become either a false alarm or a hidden regression.

Known-broken entries are keyed on (file, relation, columns, action) and NOT on
line numbers, so ordinary edits do not churn the allowlist. The list is
checked for staleness in both directions: a new broken clause fails, and so
does a known-broken clause that has been fixed but not unpinned.

Skips without a DSN (CONDUIT_PG_DSN / NEXUS_PG_DSN); the extraction tests
below still run, so the suite is never silently empty.
"""
from __future__ import annotations

import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCAN_DIRS = ("moleculer", "adonisjs", "typescript", "python", "bin", "sql", "scripts")
EXCLUDE_DIR_PARTS = (
    "node_modules", "dist", "__pycache__", ".git", "vendor", "seeds",
    "migrations-proposal", "base-repo",
)
SCAN_EXT = (".ts", ".js", ".py", ".sql")

# (file, relation, columns, action) — verified broken on 2026-09-27.
# `nebula.roles` is already fixed on persona-bridge-parity; it stays pinned
# here because origin/main still carries it, and unpins when that lands.
KNOWN_BROKEN = {
    # ---- live endpoints, all the same SCD4 defect: writing through a view ----
    # POST /api/artifact-provenance. Confirmed by invoking it: HTTP 500,
    # `constraint "uq_artifact_provenance_pair" for table
    # "artifact_provenance" does not exist`; the history table held 0 rows
    # before and after, so this endpoint had never written anything.
    ("adonisjs/nexus-control-edge/app/controllers/nebula_meta_controller.ts",
     "nebula.artifact_provenance", (), "on-constraint"),
    # a SECOND handler with the identical defect, in nebula-srv
    ("typescript/nebula-srv/src/routes.ts",
     "nebula.artifact_provenance", (), "on-constraint"),
    # ---- provisionRole step 6, THREE verbatim copies ----
    # tackle-srv fixed on persona-bridge-parity (6ef79aa4). The moleculer
    # twin is still broken and was missed by that fix: moleculer services are
    # documented as "COPIED VERBATIM from the incumbent", so a fix applied to
    # one copy is reverted by the next port unless the other is fixed too.
    ("typescript/tackle-srv/src/db.ts", "nebula.roles", ("name",), "update"),
    ("moleculer/tackle/services/db.ts", "nebula.roles", ("name",), "update"),
    # ---- other live code ----
    # partial-index inference cannot succeed: conflict target has no
    # predicate, the only unique index is partial
    ("python/voyager-adapter/db.py", "semantics.canonical_asset",
     ("canonical_asset_id",), "update"),
    # ---- latent: inside a CREATE FUNCTION body, and neither kernel
    # function is attached to a trigger, so these are currently dead paths.
    # ci-bootstrap carries the function definitions; 025 carries the same
    # INSERTs as migration SQL.
    ("sql/ci-bootstrap/nexus-ci-bootstrap.sql", "nebula.assessments",
     ("id",), "nothing"),
    ("sql/ci-bootstrap/nexus-ci-bootstrap.sql", "nebula.observations",
     ("id",), "nothing"),
    ("typescript/nebula-srv/migrations/025-kernel-lifecycle-events.sql",
     "nebula.assessments", ("id",), "nothing"),
    ("typescript/nebula-srv/migrations/025-kernel-lifecycle-events.sql",
     "nebula.observations", ("id",), "nothing"),
    # ---- migration seeds targeting views ----
    ("typescript/nebula-srv/migrations/seed-conduit-wrp-bridge.sql",
     "nebula.projections", ("name",), "nothing"),
    ("typescript/nebula-srv/migrations/seed-projections-crossrefs.sql",
     "nebula.projections", ("name",), "nothing"),
    # ---- test / fixture code ----
    ("moleculer/nexus-broker/tests/fixtures/keychain-delta-ci-pg.sql",
     "nebula.agent_records", ("id",), "nothing"),
    ("python/conduit/tests/test_lifecycle.py", "plans", ("id",), "update"),
}

# Relations that do not exist in the `nexus` database. These are NOT broken:
# they are unverifiable from this connection, and an unresolvable target is a
# different question from a missing arbiter index. `shrapnel.bridge_cursor` is
# the cautionary case — absent from `nexus`, yet a real table in the `postgres`
# database that its service actually uses.
#
# Pinned so the set is explicit rather than silently discarded. Add an entry
# here with a note saying which database/DSN the relation really lives in.
KNOWN_UNVERIFIABLE = {
    # exists as a real table in the `postgres` database, not in `nexus`
    ("bin/agent-records-to-shrapnel-bridge.py", "shrapnel.bridge_cursor"),
    # ---- stale namespace, NOT a different database ----
    # `semantics` exists in `nexus` but holds the *asset* tables
    # (canonical_asset, evidence_item, ...). The concept tables were created
    # as `resolution.*` — so `semantics.concept` never existed under that
    # name in this database. Same for `registry.service_identity_map`.
    ("sql/V053__registry_service_identity_map.sql", "registry.service_identity_map"),
    ("sql/V059__semantics_seed_lookup.sql", "semantics.concept"),
    ("sql/V059__semantics_seed_lookup.sql", "semantics.identity_strategy"),
    ("sql/V059__semantics_seed_lookup.sql", "semantics.owning_subsystem"),
    # `resolution.relation_vocabulary` is absent from every database on this
    # server, so V182 has not been applied here (or never landed)
    ("sql/V182__relation_vocabulary.sql", "resolution.relation_vocabulary"),
    # unqualified: no table named `history` in ANY schema of `nexus`
    ("typescript/tackle-seeds/index.ts", "history"),
    # `shrapnel.stereotype_instance_storage` is absent from every database on
    # this server — the EAV storage tsp-eav-emitter targets was never created
    ("typescript/tsp-eav-emitter/shrapnel-catalog.sql",
     "shrapnel.stereotype_instance_storage"),
    ("typescript/tsp-eav-emitter/tester-attestation-request.sql",
     "shrapnel.stereotype_instance_storage"),
}

_OPENERS = {'"': '"', "'": "'", "`": "`"}
_DOLLAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
_INSERT = re.compile(
    r"\binsert\s+into\s+([A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*)?)", re.I)
_ONC = re.compile(r"\bon\s+conflict\b", re.I)
_TARGET = re.compile(
    r"\bon\s+conflict\s*\(([^)]*)\)\s*(?:where\s+(.+?)\s*)?do\s+(nothing|update)\b",
    re.I | re.S)
_NOTARGET = re.compile(r"\bon\s+conflict\s+do\s+(nothing|update)\b", re.I)
_ONCONSTRAINT = re.compile(
    r"\bon\s+conflict\s+on\s+constraint\s+([A-Za-z_][\w$]*)", re.I)


def _norm_cols(s: str) -> tuple[str, ...]:
    out = []
    for c in s.split(","):
        c = re.sub(r"\s+", " ", c.strip().strip('"').lower())
        if c:
            out.append(c)
    return tuple(sorted(out))


def _norm_pred(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().rstrip(";").lower()
    s = re.sub(r"::\s*(timestamp with time zone|timestamptz|timestamp|text|int|integer|bigint)\b", "", s)
    s = re.sub(r"::\s*'[^']*'", "", s)
    s = s.replace("'infinity'::timestamp with time zone", "'infinity'")
    s = s.replace("'9999-12-31 00:00:00+00'", "'9999-12-31'")
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s
        if s.startswith("(") and s.endswith(")"):
            depth = 0
            ok = True
            for i, ch in enumerate(s):
                depth += 1 if ch == "(" else (-1 if ch == ")" else 0)
                if depth == 0 and i != len(s) - 1:
                    ok = False
                    break
            s = s[1:-1].strip() if ok else s
    parts, depth, cur, i = [], 0, "", 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and s[i:i + 5] == " and ":
            parts.append(cur)
            cur = ""
            i += 5
            continue
        cur += ch
        i += 1
    parts.append(cur)
    if len(parts) > 1:
        s = " and ".join(sorted(p.strip() for p in parts))
    return s


def _literals(text: str, ext: str):
    """(offset, body) per SQL statement / string literal, comments removed.

    Comments MUST be stripped: scd-type4-temporal.sql carries a doc comment
    reading "-- POST /api/audit/sync (INSERT ... ON CONFLICT (file_path) DO
    UPDATE)" which an earlier version of this audit reported as a real clause
    against a table that has no unique indexes at all.
    """
    if ext == ".sql":
        i, n, buf, start = 0, len(text), [], 0
        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if ch == "-" and nxt == "-":
                j = text.find("\n", i)
                i = n if j < 0 else j + 1
                continue
            if ch == "/" and nxt == "*":
                j = text.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if ch in ("'", '"'):
                q = ch
                buf.append(ch)
                i += 1
                while i < n:
                    if text[i] == "\\":
                        buf.append(text[i:i + 2])
                        i += 2
                        continue
                    buf.append(text[i])
                    if text[i] == q:
                        if i + 1 < n and text[i + 1] == q:
                            buf.append(text[i + 1])
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                continue
            m = _DOLLAR.match(text, i)
            if m:
                tag = m.group(0)
                j = text.find(tag, m.end())
                buf.append(text[i:(n if j < 0 else j + len(tag))])
                i = n if j < 0 else j + len(tag)
                continue
            if ch == ";":
                stmt = "".join(buf)
                if stmt.strip():
                    yield start, stmt
                buf, start = [], i + 1
                i += 1
                continue
            buf.append(ch)
            i += 1
        stmt = "".join(buf)
        if stmt.strip():
            yield start, stmt
        return

    hash_comments = ext == ".py"
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if (ch == "/" and nxt == "/") or (ch == "#" and hash_comments):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        delim = None
        if text[i:i + 3] in ('"""', "'''"):
            delim = text[i:i + 3]
        elif ch in _OPENERS:
            delim = ch
        if delim is not None:
            lit = i
            while lit > 0 and (text[lit - 1].isalnum() or text[lit - 1] in "_$"):
                lit -= 1
            start, j = i + len(delim), i + len(delim)
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j:j + len(delim)] == delim:
                    break
                j += 1
            yield lit, text[start:j]
            i = j + len(delim)
            continue
        i += 1


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "ls-files", "--", *SCAN_DIRS],
            capture_output=True, text=True, check=True).stdout.split("\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [
        f for f in out
        if f and f.endswith(SCAN_EXT)
        and not any(p in EXCLUDE_DIR_PARTS for p in f.split("/"))
        and os.path.isfile(os.path.join(ROOT, f))
    ]


def clauses():
    """Yield (file, lineno, relation, cols, pred, action) for every clause."""
    for f in tracked_files():
        ext = "." + f.rsplit(".", 1)[-1]
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for off, body in _literals(text, ext):
            if not _ONC.search(body):
                continue
            for m in _ONC.finditer(body):
                ins = None
                for im in _INSERT.finditer(body, 0, m.start()):
                    ins = im
                if ins is None:
                    continue
                rel = re.sub(r"\s+", "", ins.group(1))
                ln = text.count("\n", 0, off) + 1
                tail = body[m.start():]
                oc = _ONCONSTRAINT.match(tail)
                if oc:
                    yield (f, ln, rel, (), "", "on-constraint")
                    continue
                t = _TARGET.match(tail)
                if t:
                    yield (f, ln, rel, _norm_cols(t.group(1)),
                           _norm_pred(t.group(2)), t.group(3).lower())
                    continue
                nt = _NOTARGET.match(tail)
                if nt:
                    yield (f, ln, rel, (), "", nt.group(1).lower())


def dsn() -> str | None:
    return os.environ.get("CONDUIT_PG_DSN") or os.environ.get("NEXUS_PG_DSN")


def _catalog(dsn: str):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT n.nspname||'.'||c.relname, c.relkind::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname !~ '^pg_' AND c.relkind IN ('r','v','m','f','p')")
    rels = {}
    for nm, rk in cur.fetchall():
        rels.setdefault(nm.lower(), []).append((nm, rk))
    cur.execute(
        "SELECT n.nspname||'.'||c.relname, ix.indisunique::text, "
        "array_to_string(ARRAY(SELECT pg_get_indexdef(ix.indexrelid, k+1, true) "
        "                 FROM generate_series(0, ix.indnatts-1) k), ','), "
        "coalesce(pg_get_expr(ix.indpred, ix.indrelid),'') "
        "FROM pg_index ix JOIN pg_class c ON c.oid=ix.indrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname !~ '^pg_'")
    idx = {}
    for nm, uniq, cols, pred in cur.fetchall():
        idx.setdefault(nm.lower(), []).append(
            (uniq, _norm_cols(cols), _norm_pred(pred)))
    cur.close()
    conn.close()
    return rels, idx


def _probe_accepts(dsn: str, rel: str, cols, pred: str) -> bool:
    """Ask Postgres directly whether it can infer an arbiter for this target.

    String comparison of predicates is not reliable: the catalog renders
    `((recorded_until_dt = 'infinity'::timestamp with time zone) AND
    (valid_until = 'infinity'::timestamp with time zone))` while code writes
    `recorded_until_dt = 'infinity' AND valid_until = 'infinity'`. Those are
    the SAME predicate, but a naive comparison reports them as different and
    calls a working clause broken.

    So when the columns match but the predicates do not compare equal, probe:

        INSERT INTO rel (cols) SELECT cols FROM rel WHERE false
          ON CONFLICT (cols) [WHERE pred] DO NOTHING

    Inference happens at PLAN time, so a clause Postgres cannot infer raises
    immediately; `WHERE false` means zero rows, so nothing is ever written and
    NOT NULL / CHECK constraints cannot fire. The transaction is rolled back
    regardless. The only rows this can touch are none.
    """
    import psycopg2
    collist = ", ".join(cols)
    where = f" WHERE {pred}" if pred else ""
    stmt = (f"INSERT INTO {rel} ({collist}) SELECT {collist} FROM {rel} "
            f"WHERE false ON CONFLICT ({collist}){where} DO NOTHING")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        conn.cursor().execute(stmt)
        return True
    except psycopg2.Error:
        return False
    finally:
        conn.rollback()
        conn.close()


def classify(cl, rels, idx, dsn: str | None = None):
    """-> 'safe' | 'ok' | 'review' | 'broken' | 'unverifiable'"""
    _f, _ln, rel, cols, pred, action = cl
    cands = list(rels.get(rel.lower(), []))
    if not cands and "." not in rel:
        base = rel.lower()
        for nm, lst in rels.items():
            if nm.split(".")[-1] == base:
                cands.extend(lst)
    if not cands:
        return "unverifiable"
    if len(cands) > 1:
        return "review"          # only search_path can resolve it
    full, relkind = cands[0]
    if action == "on-constraint":
        return "broken" if relkind not in ("r", "p") else "review"
    if not cols:
        # bare DO NOTHING: no arbiter inferred, so a view is fine here
        return "safe"
    if relkind not in ("r", "p"):
        return "broken"
    uniq = [x for x in idx.get(full.lower(), []) if x[0]]
    same = [x for x in uniq if x[1] == cols]
    if any(x[2] == pred for x in same):
        return "ok"
    if pred and re.search(r"\{|\$\{|%s", pred):
        return "review"          # f-string predicate, not comparable statically
    if not same:
        return "broken"           # no unique index on those columns at all
    # Columns match but the predicate does not compare equal. Ask Postgres.
    if dsn:
        return "ok" if _probe_accepts(dsn, full, cols, pred) else "broken"
    return "review"


class Extraction(unittest.TestCase):
    """Hermetic: the scanner itself, so the suite is meaningful with no DB."""

    def test_scan_covers_the_repo(self) -> None:
        files = tracked_files()
        self.assertGreater(len(files), 500, f"only {len(files)} files scanned")
        n = sum(1 for _ in clauses())
        self.assertGreater(n, 100, f"only {n} ON CONFLICT clauses found")

    def test_bare_do_nothing_is_safe_not_broken(self) -> None:
        # Wrapped in a string literal because for .py/.ts the scanner works on
        # literals, which is how real code reaches it.
        src = 'q("INSERT INTO some_table (a) VALUES (1) ON CONFLICT DO NOTHING")'
        found = list(clauses_from_text(src, ".py"))
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][5], "nothing")
        self.assertEqual(found[0][3], ())

    def test_conflict_target_and_predicate_are_parsed(self) -> None:
        src = ('q("INSERT INTO t (a, b) VALUES (1, 2) '
               'ON CONFLICT (a, b) WHERE expired_at IS NULL DO UPDATE SET a = 3")')
        f, _ln, _rel, cols, pred, action = list(clauses_from_text(src, ".py"))[0]
        self.assertEqual(cols, ("a", "b"))
        self.assertEqual(pred, "expired_at is null")
        self.assertEqual(action, "update")

    def test_comments_are_not_matched(self) -> None:
        src = "-- POST /api/x (INSERT ... ON CONFLICT (file_path) DO UPDATE)\nSELECT 1"
        self.assertEqual(list(clauses_from_text(src, ".sql")), [])

    def test_semicolons_inside_a_function_body_do_not_split(self) -> None:
        src = ("CREATE FUNCTION f() RETURNS void AS $$ BEGIN "
               "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO NOTHING; "
               "END $$; SELECT 2;")
        found = list(clauses_from_text(src, ".sql"))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][3], ("a",))


def clauses_from_text(text: str, ext: str):
    for off, body in _literals(text, ext):
        if not _ONC.search(body):
            continue
        for m in _ONC.finditer(body):
            ins = None
            for im in _INSERT.finditer(body, 0, m.start()):
                ins = im
            if ins is None:
                continue
            rel = re.sub(r"\s+", "", ins.group(1))
            tail = body[m.start():]
            if _ONCONSTRAINT.match(tail):
                yield ("<test>", 1, rel, (), "", "on-constraint")
                continue
            t = _TARGET.match(tail)
            if t:
                yield ("<test>", 1, rel, _norm_cols(t.group(1)),
                       _norm_pred(t.group(2)), t.group(3).lower())
                continue
            nt = _NOTARGET.match(tail)
            if nt:
                yield ("<test>", 1, rel, (), "", nt.group(1).lower())


class NoNewBrokenClauses(unittest.TestCase):
    def test_no_new_unexecutable_on_conflict(self) -> None:
        d = dsn()
        if not d:
            self.skipTest("no CONDUIT_PG_DSN / NEXUS_PG_DSN set")
        try:
            import psycopg2  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("psycopg2 not installed")
        rels, idx = _catalog(d)

        broken, unver = set(), set()
        for cl in clauses():
            verdict = classify(cl, rels, idx, d)
            key = (cl[0], cl[2], cl[3], cl[5])
            if verdict == "broken":
                broken.add(key)
            elif verdict == "unverifiable":
                unver.add((cl[0], cl[2]))

        new = broken - KNOWN_BROKEN
        self.assertEqual(
            new, set(),
            "new ON CONFLICT clause(s) Postgres cannot execute. Either add an "
            "arbiter index, or if the target is a VIEW write to the underlying "
            "*_history table with the SCD4 close-then-insert pattern (see "
            "provisionRole step 6, 6ef79aa4):\n"
            + "\n".join(f"  {a} -> {b} cols={list(c)} action={e}" for a, b, c, e in sorted(new)),
        )

        stale = KNOWN_BROKEN - broken
        self.assertEqual(
            stale, set(),
            "these were fixed but are still pinned in KNOWN_BROKEN — remove "
            "them so future regressions are caught:\n"
            + "\n".join(f"  {a} -> {b} cols={list(c)} action={e}" for a, b, c, e in sorted(stale)),
        )

        # An unverifiable target is a distinct condition from a missing
        # arbiter: we cannot tell from this connection. Require it to be
        # declared, so it is never mistaken for a pass.
        new_unver = unver - KNOWN_UNVERIFIABLE
        self.assertEqual(
            new_unver, set(),
            "ON CONFLICT clause(s) target a relation absent from this "
            "database. Either the relation was never created, or the code "
            "runs against a different database (see KNOWN_UNVERIFIABLE for "
            "that case). Declare it in KNOWN_UNVERIFIABLE with a note naming "
            "the database it really lives in:\n"
            + "\n".join(f"  {a} -> {b}" for a, b in sorted(new_unver)),
        )
        gone_unver = KNOWN_UNVERIFIABLE - unver
        self.assertEqual(
            gone_unver, set(),
            "pinned in KNOWN_UNVERIFIABLE but now resolvable — the relation "
            "exists, so re-classify it:\n"
            + "\n".join(f"  {a} -> {b}" for a, b in sorted(gone_unver)),
        )

        # Reported, not asserted: an absent relation may exist in another of
        # the cluster's 16 databases, so it is not evidence of a defect.
        self.assertEqual(
            unver - KNOWN_UNVERIFIABLE, set(),
            "new clause targets a relation absent from the audited database — "
            "confirm it exists wherever this code actually runs",
        )


if __name__ == "__main__":
    unittest.main()
