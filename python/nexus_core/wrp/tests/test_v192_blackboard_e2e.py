#!/usr/bin/env python3
"""E2E: V192 — coordination_checkpoints + v_coordination_blackboard (real
migration, throwaway DB).

wr-conf house pattern (V175/V190/V191 companions). The skeleton recreates the
LIVE-SHAPE prerequisites V192 composes (census 2026-09-20, titanium):

  - nebula.roles (name PK) — FK target + routing token source
  - assembly.forums with the 'to-do' forum (slug unique)
  - assembly.posts (rating bigint, forum_uuid, created timestamp WITHOUT tz —
    the live shape; the view pins UTC explicitly)
  - assembly.comments (role text — the addressee-attribution surface)
  - resolution.migration_ledger (P1 out-of-order guard reads it)

Then the REAL sql/V192 applies and the full contract is exercised against live
constraint behavior — no mocks on the DB path:

  - preflight refusals: non-unique to-do forum (P2), missing agent-records
    surface (P3), empty roles (P4), double-apply (P5)
  - checkpoint table: FK refusal of unknown role, item_kind CHECK, seed rows
    born per role x kind, updated_at trigger fires
  - todo fold routing: bracket token -> nebula.roles mapping; unmapped tokens
    land in 'unrouted' (visible, never guessed); arrow form [inspector→engineer]
    routes to inspector (first token)
  - bucket logic: action-needed (inside SLA / reopened), in-flight (rating
    2/3/8 AND the acked-but-unadvanced case, policy §6), awaiting-pickup
    (past 72h, unacked), stale (past 14d, unacked), done (4/5/7)
  - inbox fold: to:<role> tags split via unnest; checkpoint boundary decides
    action-needed vs seen; unknown to: targets emit nothing
  - checkpoint branch: epoch => 'never reviewed'; advance => 'reviewed'
  - advance: updating a checkpoint moves the boundary (new record -> seen)
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V192_PATH = os.path.join(_REPO_ROOT, "sql", "V192__coordination_checkpoints_blackboard.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = r"""
CREATE SCHEMA nebula;
CREATE SCHEMA assembly;
CREATE SCHEMA resolution;

-- nebula.roles: live shape (name text PK)
CREATE TABLE nebula.roles (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text PRIMARY KEY
);
INSERT INTO nebula.roles (name) VALUES
    ('dba'), ('engineer'), ('engineer-ii'), ('analyst'), ('architect'),
    ('inspector'), ('planner'), ('reviewer'), ('operator'), ('devops');

-- resolution.migration_ledger (P1 reads it; empty = nothing newer than V192)
CREATE TABLE resolution.migration_ledger (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_name text,
    migration_label text,
    description text,
    applied_by text,
    applied_at timestamptz DEFAULT now()
);

-- nebula.agent_records_history: minimal live-shape subset for the inbox fold
CREATE TABLE nebula.agent_records_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    record_type text,
    role text,
    title text,
    content text,
    metadata jsonb,
    tags text[],
    created_at timestamptz DEFAULT now(),
    model text
);

-- assembly.forums: live shape (id uuid PK, slug)
CREATE TABLE assembly.forums (
    id uuid PRIMARY KEY,
    name varchar(255),
    description text,
    slug varchar(255)
);
INSERT INTO assembly.forums (id, slug) VALUES
    ('836a1dec-39a7-4c97-8932-472f07fc16f5', 'to-do');

-- assembly.posts: live shape (created is timestamp WITHOUT tz; rating bigint)
CREATE TABLE assembly.posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created timestamp NOT NULL DEFAULT now(),
    updated timestamp,
    text text,
    rating bigint,
    posted_by_id uuid,
    forum_uuid uuid REFERENCES assembly.forums(id),
    title varchar(500),
    model text,
    role text
);

-- assembly.comments: live shape subset (role carries the attribution)
CREATE TABLE assembly.comments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created timestamp NOT NULL DEFAULT now(),
    text text,
    post_id uuid,
    role text
);
"""


class ThrowawayDB:
    def __init__(self, dbname_prefix):
        self.dbname = f"nexus_v192_{dbname_prefix}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
        return self

    def __exit__(self, *exc):
        try:
            if self.conn:
                self.conn.close()
        finally:
            admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            admin.close()

    def sql(self, stmt, params=None):
        with self.conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall() if cur.description else None
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def expect_error(self, stmt, params=None, fragment=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            self.conn.rollback()
            msg = str(exc).split("\n")[0]
            if fragment and fragment not in msg:
                raise AssertionError(
                    f"expected error containing {fragment!r}, got: {msg}")
            return msg
        raise AssertionError("expected the statement to fail")

    def apply_v192(self):
        with open(V192_PATH) as fh:
            self.sql(fh.read())

    # -- to-do seed helper: title, rating, age_hours, addressee comment role --
    def todo(self, title, rating, age_hours=1.0, comment_role=None):
        tid = self.sql(
            "INSERT INTO assembly.posts (forum_uuid, title, rating, created) "
            "VALUES ('836a1dec-39a7-4c97-8932-472f07fc16f5', %s, %s, "
            "        (now() AT TIME ZONE 'UTC') - (%s * interval '1 hour')) "
            "RETURNING id", (title, rating, age_hours))
        if comment_role:
            self.sql("INSERT INTO assembly.comments (post_id, role, text) "
                     "VALUES (%s, %s, 'ack')", (tid, comment_role))
        return tid

    def bucket_of(self, title):
        return self.sql(
            "SELECT bucket FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='todo' AND title=%s", (title,))


class SchemaPath(unittest.TestCase):
    def test_01_preflight_refusals(self):
        # P2: to-do forum not unique
        db = ThrowawayDB("p2")
        with db as d:
            d.sql("INSERT INTO assembly.forums (id, slug) "
                  "VALUES (gen_random_uuid(), 'to-do')")
            msg = d.expect_error("SELECT 1", fragment=None) if False else None
            try:
                d.apply_v192()
                self.fail("P2 should have refused")
            except psycopg2.Error as exc:
                d.conn.rollback()
                self.assertIn("PREFLIGHT P2 FAIL", str(exc))
        # P3: no agent_records_history
        db = ThrowawayDB("p3")
        with db as d:
            d.sql("DROP TABLE nebula.agent_records_history")
            try:
                d.apply_v192()
                self.fail("P3 should have refused")
            except psycopg2.Error as exc:
                d.conn.rollback()
                self.assertIn("PREFLIGHT P3 FAIL", str(exc))
        # P4: empty roles
        db = ThrowawayDB("p4")
        with db as d:
            d.sql("DELETE FROM nebula.roles")
            try:
                d.apply_v192()
                self.fail("P4 should have refused")
            except psycopg2.Error as exc:
                d.conn.rollback()
                self.assertIn("PREFLIGHT P4 FAIL", str(exc))

    def test_02_double_apply_refused(self):
        db = ThrowawayDB("p5")
        with db as d:
            d.apply_v192()
            try:
                d.apply_v192()
                self.fail("P5 should have refused the re-apply")
            except psycopg2.Error as exc:
                d.conn.rollback()
                self.assertIn("PREFLIGHT P5 FAIL", str(exc))

    def test_03_checkpoint_table_contract(self):
        db = ThrowawayDB("table")
        with db as d:
            d.apply_v192()
            # Born seed: 10 roles x 5 kinds = 50 never-reviewed rows
            n = d.sql("SELECT count(*) FROM nebula.coordination_checkpoints")
            self.assertEqual(n, 50)
            self.assertEqual(d.sql(
                "SELECT count(*) FROM nebula.coordination_checkpoints "
                "WHERE last_reviewed_at = to_timestamp(0)"), 50)
            # FK refusal
            d.expect_error(
                "INSERT INTO nebula.coordination_checkpoints (role, item_kind) "
                "VALUES ('nonexistent-role', 'todo')",
                fragment="coordination_checkpoints_role_fkey")
            # item_kind CHECK
            d.expect_error(
                "INSERT INTO nebula.coordination_checkpoints (role, item_kind) "
                "VALUES ('dba', 'fantasy-kind')",
                fragment="coordination_checkpoints_item_kind_check")
            # updated_at trigger
            row0 = d.sql(
                "SELECT updated_at FROM nebula.coordination_checkpoints "
                "WHERE role='dba' AND item_kind='todo'")
            d.sql("UPDATE nebula.coordination_checkpoints "
                  "SET last_reviewed_at = now() WHERE role='dba' AND item_kind='todo'")
            row1 = d.sql(
                "SELECT updated_at FROM nebula.coordination_checkpoints "
                "WHERE role='dba' AND item_kind='todo'")
            self.assertGreater(row1, row0, "touch trigger did not fire")


class TodoFold(unittest.TestCase):
    def setUp(self):
        self._db = ThrowawayDB("todo")
        self.db = self._db.__enter__()
        self.db.apply_v192()

    def tearDown(self):
        self._db.__exit__()

    def test_01_routing_and_buckets(self):
        d = self.db
        # action-needed: inside SLA, no ack
        d.todo("[engineer] fix the widget", 0, age_hours=2)
        # in-flight: rating 2
        d.todo("[engineer] widget in progress", 2)
        # in-flight via 8
        d.todo("[engineer] operator-approved item", 8)
        # acked-but-unadvanced (policy §6): rating 0, addressee commented
        d.todo("[engineer] acked but never advanced", 0, age_hours=200,
               comment_role="engineer")
        # awaiting-pickup: past 72h, no ack
        d.todo("[analyst] three days stale pickup", 0, age_hours=96)
        # stale: past 14d, no ack
        d.todo("[planner] ancient unacked item", 0, age_hours=24 * 20)
        # done: ratings 4/5/7
        d.todo("[inspector] completed item", 4)
        d.todo("[inspector] rejected item", 5)
        d.todo("[inspector] archived item", 7)
        # reopened: rating 6 -> action-needed
        d.todo("[inspector] regression", 6, age_hours=24 * 30)
        # unrouted: bracket token not a role
        d.todo("[barbie] parity sweep", 0)
        # unrouted: no bracket at all
        d.todo("loose thread with no routing", 0)

        self.assertEqual(d.bucket_of("[engineer] fix the widget"), "action-needed")
        self.assertEqual(d.bucket_of("[engineer] widget in progress"), "in-flight")
        self.assertEqual(d.bucket_of("[engineer] operator-approved item"), "in-flight")
        self.assertEqual(d.bucket_of("[engineer] acked but never advanced"), "in-flight")
        self.assertEqual(d.bucket_of("[analyst] three days stale pickup"), "awaiting-pickup")
        self.assertEqual(d.bucket_of("[planner] ancient unacked item"), "stale")
        self.assertEqual(d.bucket_of("[inspector] completed item"), "done")
        self.assertEqual(d.bucket_of("[inspector] rejected item"), "done")
        self.assertEqual(d.bucket_of("[inspector] archived item"), "done")
        self.assertEqual(d.bucket_of("[inspector] regression"), "action-needed")
        self.assertEqual(d.bucket_of("[barbie] parity sweep"), "unrouted")
        self.assertEqual(d.bucket_of("loose thread with no routing"), "unrouted")

    def test_02_arrow_form_routes_to_first_role(self):
        d = self.db
        d.todo("[inspector→engineer] D2 finding", 0)
        row = d.sql(
            "SELECT routed_role, bucket FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='todo' AND title='[inspector→engineer] D2 finding'")
        rows = row if isinstance(row, list) else [row]
        self.assertEqual(sorted(rows), [("inspector", "action-needed")])

    def test_03_last_activity_reflects_comments(self):
        d = self.db
        tid = d.todo("[engineer] activity probe", 0)
        self.assertIsNone(d.sql(
            "SELECT last_activity_at FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='todo' AND title='[engineer] activity probe'"))
        d.sql("INSERT INTO assembly.comments (post_id, role, text) VALUES (%s, 'analyst', 'x')", (tid,))
        self.assertIsNotNone(d.sql(
            "SELECT last_activity_at FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='todo' AND title='[engineer] activity probe'"))


class InboxAndCheckpoints(unittest.TestCase):
    def setUp(self):
        self._db = ThrowawayDB("inbox")
        self.db = self._db.__enter__()
        self.db.apply_v192()

    def tearDown(self):
        self._db.__exit__()

    def test_01_inbox_fold_respects_checkpoint_boundary(self):
        d = self.db
        # Record tagged to engineer AND to a nonexistent role: only the real
        # role's row may emit (unknown to: targets emit nothing).
        d.sql(
            "INSERT INTO nebula.agent_records_history (title, tags, created_at) "
            "VALUES ('W3 disposition', ARRAY['to:engineer','to:ghost-role'], now())")
        rows = d.sql(
            "SELECT routed_role, bucket FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='inbox' AND title='W3 disposition'")
        rows = rows if isinstance(rows, list) else [rows]
        rows = [r if isinstance(r, tuple) else (r,) for r in rows]
        self.assertEqual(sorted(r[0] for r in rows), ["engineer"],
                         "unknown to: target must not emit")
        self.assertTrue(all(r[1] == "action-needed" for r in rows),
                        "never-reviewed checkpoint => everything is new")
        # Advance the engineer inbox checkpoint; the same record is now 'seen'.
        d.sql("UPDATE nebula.coordination_checkpoints SET last_reviewed_at = now() "
              "WHERE role='engineer' AND item_kind='inbox'")
        b = d.sql(
            "SELECT bucket FROM nebula.v_coordination_blackboard "
            "WHERE item_kind='inbox' AND title='W3 disposition' AND routed_role='engineer'")
        self.assertEqual(b, "seen")

    def test_02_checkpoint_branch_freshness(self):
        d = self.db
        rows = d.sql(
            "SELECT reason FROM nebula.v_coordination_blackboard "
            "WHERE bucket='checkpoint' AND routed_role='dba' ORDER BY reason")
        reasons = [r[0] if isinstance(r, tuple) else r for r in rows] if isinstance(rows, list) else [rows]
        self.assertTrue(any("never reviewed" in str(r) for r in reasons),
                        f"epoch checkpoints must read 'never reviewed': {reasons}")
        d.sql("UPDATE nebula.coordination_checkpoints SET last_reviewed_at = now() "
              "WHERE role='dba' AND item_kind='discussions'")
        row = d.sql(
            "SELECT reason FROM nebula.v_coordination_blackboard "
            "WHERE bucket='checkpoint' AND routed_role='dba' AND item_kind='discussions'")
        self.assertIn("reviewed", str(row))

    def test_03_view_is_read_only(self):
        d = self.db
        d.expect_error(
            "INSERT INTO nebula.v_coordination_blackboard DEFAULT VALUES",
            fragment="cannot insert")


if __name__ == "__main__":
    unittest.main(verbosity=2)
