#!/usr/bin/env python3
"""E2E: current role-vocabulary PIN — scratch widening (real migration, throwaway DB).

This historical V190 workflow tracks the current single ROLE-VOCAB PIN rather
than freezing V190's old 24/25-role target. Three paths, one suite:

- RepairPath: a skeleton carrying the current PIN on nebula and a stale scratch
  mirror, then the REAL pin-owning migration applies and the contract is
  exercised against live constraint behavior, no mocks on the DB path:
    * pre-state defect: lead-engineer write through the scratch path rejected
    * repair: every currently widened role is accepted
    * bogus role still rejected; empty-string role still allowed
    * constraint parity: scratch def == nebula def (normalized literals)
    * existing rows survive the swap
- DriftGate: nebula's CHECK grown beyond the pin (simulated future
  vocabulary growth) -> the current pin migration refuses, nothing changes.
- BootstrapPath: the REAL patched ci-bootstrap deploys born-clean — the
  current pinned CHECK is present at birth, every pinned role (plus '') is
  insertable, and a bogus role is rejected. No repair is needed.

Each class creates and drops its own throwaway database; no production
database is touched.
"""
import os
import re
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
BOOTSTRAP_PATH = os.path.join(_REPO_ROOT, "sql", "ci-bootstrap", "nexus-ci-bootstrap.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")


def _find_pin_migration():
    """Locate the migration carrying the ROLE-VOCAB PIN marker (dynamic since
    V197: the marker lives in exactly one sql/ file — wr-conf-042's parity
    suite P3 enforces uniqueness, so discovery here is safe and stays
    correct as the pin moves in future widenings)."""
    sql_dir = os.path.join(_REPO_ROOT, "sql")
    hits = []
    for name in sorted(os.listdir(sql_dir)):
        if not name.endswith(".sql"):
            continue
        with open(os.path.join(sql_dir, name), encoding="utf-8") as fh:
            if "ROLE-VOCAB PIN" in fh.read():
                hits.append(os.path.join(sql_dir, name))
    if len(hits) != 1:
        raise RuntimeError(
            "ROLE-VOCAB PIN marker must exist in exactly one sql/ file, found %d: %r"
            % (len(hits), hits))
    return hits[0]


PIN_PATH = _find_pin_migration()


def _load_pin_roles():
    """Derive the vocabulary from the ROLE-VOCAB PIN in its owning migration
    (single in-repo copy — wr-conf-042's parity suite enforces bootstrap
    parity; deriving here keeps the E2E from carrying a second list that
    could silently drift)."""
    with open(PIN_PATH, encoding="utf-8") as fh:
        text = fh.read()
    mk = text.find("ROLE-VOCAB PIN")
    if mk < 0:
        raise RuntimeError("ROLE-VOCAB PIN marker missing")
    arr = text.find("ARRAY[", mk)
    close = text.find("]", arr)
    roles = sorted({r for r in re.findall(r"'([^']*)'", text[arr:close]) if r})
    if len(roles) < 20:
        raise RuntimeError("pin parse yielded implausible role count: %d" % len(roles))
    for role in ("ontologist", "lead-engineer", "sound-technician"):
        if role not in roles:
            raise RuntimeError("pin lost ratified-12 role: %s" % role)
    return roles


PINNED_ROLES = _load_pin_roles()
WIDENING_ROLES = (
    "ontologist", "lead-engineer", "sound-technician", "supervisor",
)
STALE_ROLES = [r for r in PINNED_ROLES if r not in WIDENING_ROLES]

RECORD_TYPE_CHECK = (
    "CHECK ((record_type = ANY (ARRAY['report'::text, 'analysis'::text, "
    "'assessment'::text, 'inspection'::text, 'prompt'::text, 'response'::text, "
    "'engineering_log'::text, 'architecture_note'::text, 'decision'::text])))"
)


def _role_check_sql(roles):
    arr = ", ".join("'%s'::text" % r for r in roles)
    return ("CONSTRAINT agent_records_role_check "
            "CHECK (((role = ''::text) OR (role = ANY (ARRAY[%s]))))" % arr)


# Faithful column shape of the live agent_records_history tables (census
# 2026-09-20): NOT NULL columns with their DECLARE-time defaults — the
# skeleton-vs-live drift lesson from the sql-pitfalls card.
TABLE_SQL = """
CREATE TABLE {qual} (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    record_type text NOT NULL,
    role text NOT NULL DEFAULT ''::text,
    title text NOT NULL DEFAULT ''::text,
    content text NOT NULL DEFAULT ''::text,
    source_path text,
    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    tags text[] NOT NULL DEFAULT '{{}}'::text[],
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    plan_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00'::timestamptz,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00'::timestamptz,
    level integer NOT NULL DEFAULT 1,
    visibility_scope text NOT NULL DEFAULT 'all'::text,
    model text,
    CONSTRAINT agent_records_record_type_check {rt_check},
    {role_check}
);
"""


def _skeleton(nebula_roles, scratch_roles):
    return (
        "CREATE SCHEMA nebula; CREATE SCHEMA scratch;\n"
        + TABLE_SQL.format(qual="nebula.agent_records_history",
                           rt_check=RECORD_TYPE_CHECK,
                           role_check=_role_check_sql(nebula_roles))
        + TABLE_SQL.format(qual="scratch.agent_records_history",
                           rt_check=RECORD_TYPE_CHECK,
                           role_check=_role_check_sql(scratch_roles))
    )


def _load_bootstrap_sql() -> str:
    """Real bootstrap, minus the pg_dump 16+ psql meta-command lines."""
    with open(BOOTSTRAP_PATH) as fh:
        lines = fh.readlines()
    return "".join(ln for ln in lines
                   if not ln.startswith("\\restrict")
                   and not ln.startswith("\\unrestrict"))


def _probe(role):
    """INSERT probe for one role (defaults fill everything else)."""
    return ("INSERT INTO scratch.agent_records_history "
            "(record_type, role, title, content) "
            "VALUES ('report', %s, 'V190 probe', 'probe row')")


def _extract_literals(constraint_def):
    return sorted(re.findall(r"'([^']*)'", constraint_def))


class _Base(unittest.TestCase):
    def _make_db(self, dbname_prefix):
        class DB:
            def __enter__(self_inner):
                admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
                admin.autocommit = True
                with admin.cursor() as cur:
                    cur.execute(f'DROP DATABASE IF EXISTS "{self_inner.dbname}"')
                    cur.execute(f'CREATE DATABASE "{self_inner.dbname}"')
                admin.close()
                self_inner.conn = psycopg2.connect(
                    DSN.rsplit("/", 1)[0] + "/" + self_inner.dbname)
                self_inner.conn.autocommit = True
                return self_inner

            def __exit__(self_inner, *exc):
                try:
                    if self_inner.conn:
                        self_inner.conn.close()
                finally:
                    admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
                    admin.autocommit = True
                    with admin.cursor() as cur:
                        cur.execute(f'DROP DATABASE IF EXISTS "{self_inner.dbname}"')
                    admin.close()

            def sql(self_inner, stmt, params=None):
                with self_inner.conn.cursor() as cur:
                    cur.execute(stmt, params)
                    rows = cur.fetchall() if cur.description else None
                if rows and len(rows) == 1 and len(rows[0]) == 1:
                    return rows[0][0]
                return rows

            def expect_error(self_inner, stmt, params=None):
                try:
                    self_inner.sql(stmt, params)
                except psycopg2.Error as exc:
                    # autocommit mode: conn.rollback() is a NO-OP — clear the
                    # server-side aborted implicit transaction with a raw
                    # ROLLBACK (V186-harness lesson) or every later statement
                    # dies with InFailedSqlTransaction.
                    self_inner.conn.cursor().execute("ROLLBACK")
                    return str(exc).split("\n")[0]
                raise AssertionError("expected the statement to fail")

            def in_tx(self_inner, fn):
                """Run fn(cur) inside an explicit transaction, always ROLLBACK
                (raw server-side — see expect_error note)."""
                cur = self_inner.conn.cursor()
                try:
                    cur.execute("BEGIN")
                    fn(cur)
                finally:
                    try:
                        cur.execute("ROLLBACK")
                    except psycopg2.Error:
                        self_inner.conn.cursor().execute("ROLLBACK")
                    cur.close()

        db = DB()
        db.dbname = f"nexus_v190_{dbname_prefix}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        return db

    def role_check_def(self, db, qual):
        return db.sql(
            "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
            "WHERE con.conrelid = %s::regclass AND con.conname = "
            "'agent_records_role_check'", (qual,))


class RepairPath(_Base):
    def _make(self):
        db = self._make_db("repair")
        return db

    def test_01_prestate_defect_and_repair(self):
        db = self._make()
        with db as d:
            # nebula already carries the current PIN; scratch is the stale
            # mirror being repaired. This is the reapply/convergence path.
            d.sql(_skeleton(PINNED_ROLES, STALE_ROLES))
            # Pre-state: every newly widened role is rejected by scratch.
            for role in WIDENING_ROLES:
                err = d.expect_error(_probe(role), (role,))
                self.assertIn("agent_records_role_check", err)
            # A stale-era role works fine pre-repair.
            d.in_tx(lambda cur: cur.execute(_probe("engineer"), ("engineer",)))
            # Apply the REAL migration.
            with open(PIN_PATH) as fh:
                d.sql(fh.read())
            # The newly widened roles now pass through scratch.
            for role in WIDENING_ROLES:
                d.in_tx(lambda cur, r=role: cur.execute(_probe(r), (r,)))
            # Empty-string role still allowed (the '' escape hatch).
            d.in_tx(lambda cur: cur.execute(_probe(""), ("",)))
            # Bogus role still rejected.
            err = d.expect_error(_probe("bogus-role"), ("bogus-role",))
            self.assertIn("agent_records_role_check", err)
            # Constraint parity: scratch literals == nebula literals.
            scratch_def = self.role_check_def(d, "scratch.agent_records_history")
            nebula_def = self.role_check_def(d, "nebula.agent_records_history")
            self.assertEqual(_extract_literals(scratch_def),
                             sorted(PINNED_ROLES + [""]))
            self.assertEqual(_extract_literals(scratch_def),
                             _extract_literals(nebula_def))

    def test_02_existing_rows_survive_swap(self):
        db = self._make()
        with db as d:
            d.sql(_skeleton(PINNED_ROLES, STALE_ROLES))
            # Seed rows the way the scratch writer did, pre-migration —
            # autocommit INSERTs so they PERSIST (the point of the test).
            for r in ("architect", "engineer", "dba", "DBA", ""):
                d.sql(_probe(r), (r,))
            with open(PIN_PATH) as fh:
                d.sql(fh.read())
            # The pre-existing row set is intact and its roles unchanged.
            rows = d.sql("SELECT role, count(*) FROM "
                         "scratch.agent_records_history GROUP BY role "
                         "ORDER BY role")
            self.assertEqual(sorted(rows), sorted([
                ("DBA", 1), ("", 1), ("architect", 1), ("dba", 1),
                ("engineer", 1)]))

    def test_03_reapply_is_idempotent(self):
        """Re-apply after the swap: preflight still passes (pin==nebula),
        drop+add of the same-named constraint converges — re-apply is a
        no-op that succeeds, ending in the identical state."""
        db = self._make()
        with db as d:
            d.sql(_skeleton(PINNED_ROLES, STALE_ROLES))
            with open(PIN_PATH) as fh:
                d.sql(fh.read())
            with open(PIN_PATH) as fh:
                d.sql(fh.read())  # must succeed, not error
            scratch_def = self.role_check_def(d, "scratch.agent_records_history")
            self.assertEqual(_extract_literals(scratch_def),
                             sorted(PINNED_ROLES + [""]))


class DriftGate(_Base):
    def test_nebula_grown_beyond_pin_refuses(self):
        """Future vocabulary growth on nebula without updating the current pin:
        the pin migration must refuse and change nothing."""
        db = self._make_db("drift")
        with db as d:
            grown = STALE_ROLES + ["future-role"]
            d.sql(_skeleton(grown, STALE_ROLES))
            with open(PIN_PATH) as fh:
                err = d.expect_error(fh.read())
            self.assertIn("PREFLIGHT FAIL", err)
            self.assertIn("matches neither", err)
            # Nothing changed: scratch still carries the stale constraint.
            scratch_def = self.role_check_def(d, "scratch.agent_records_history")
            self.assertEqual(_extract_literals(scratch_def),
                             sorted(STALE_ROLES + [""]))

    def test_missing_nebula_source_refuses(self):
        """No nebula.agent_records_history (foreign topology): refuse."""
        db = self._make_db("nosrc")
        with db as d:
            d.sql("CREATE SCHEMA scratch;\n"
                  + TABLE_SQL.format(qual="scratch.agent_records_history",
                                     rt_check=RECORD_TYPE_CHECK,
                                     role_check=_role_check_sql(STALE_ROLES)))
            with open(PIN_PATH) as fh:
                err = d.expect_error(fh.read())
            self.assertIn("PREFLIGHT FAIL", err)
            self.assertIn("not found", err)


class BootstrapPath(_Base):
    """The REAL patched bootstrap deploys born-clean (no repair needed)."""

    def test_born_clean_current_pin(self):
        db = self._make_db("boot")
        with db as d:
            d.conn.autocommit = False
            try:
                with d.conn.cursor() as cur:
                    cur.execute(_load_bootstrap_sql())
                d.conn.commit()
            except Exception:
                d.conn.rollback()
                raise
            d.conn.autocommit = True
            # The CHECK at birth carries exactly the pinned set.
            defn = self.role_check_def(d, "nebula.agent_records_history")
            self.assertIsNotNone(defn)
            self.assertEqual(_extract_literals(defn),
                             sorted(PINNED_ROLES + [""]))
            # Every pinned role (plus '') inserts through the canonical path.
            def insert_all(cur):
                for role in PINNED_ROLES + [""]:
                    cur.execute(
                        "INSERT INTO nebula.agent_records_history "
                        "(record_type, role, title, content) "
                        "VALUES ('report', %s, 'born-clean probe', 'x')",
                        (role,))
            d.in_tx(insert_all)
            # A bogus role is rejected at birth.
            err = d.expect_error(
                "INSERT INTO nebula.agent_records_history "
                "(record_type, role, title, content) "
                "VALUES ('report', 'bogus-role', 'x', 'x')",
                ("bogus-role",))
            self.assertIn("agent_records_role_check", err)


if __name__ == "__main__":
    unittest.main()
