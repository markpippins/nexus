"""E2E: V198 — ST.01 promotion batch + deterministic candidate-set keys.

wr-conf house pattern (V190/V191/V192 companions): the REAL migration applies
to a throwaway database built from a faithful minimal skeleton (resolution
schema + resolution.candidate at the ratified pin window: 0 rows), then the
ratified behavior is probed end-to-end:

- BornClean: table/index/function shape, the R2.4 negative fixtures (both
  CHECK pins reject violations), the R3 partial-unique active-batch
  invariant, and the double-apply preflight refusal.
- KeySemantics: order-independence, duplicate-collapsing (the disclosed fix
  vs pre-stage 1e942769, whose string_agg did NOT dedupe), jsonb-canonical
  eligibility hashing (R1.2), determinism (Q2 discipline).
- ClaimPath: atomic claim, race convergence on the winner's batch, crash
  rollback frees the slot, seal/supersede lineage (R1.4), normalized
  membership links (PC5 D3).
"""

import os
import threading
import unittest
import uuid as uuid_mod
from pathlib import Path

import psycopg2

_REPO_ROOT = Path(__file__).resolve().parents[4]
V198_PATH = _REPO_ROOT / "sql" / "V198__st01_promotion_batch_candidate_set_keys.sql"
DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/nexus")

SKELETON = """
-- Extensions are per-database: the throwaway must install pgcrypto itself
-- (the live nexus DB and the ci-bootstrap both carry it; V198's preflight
-- refuses loudly when a host lacks it).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA resolution;
-- Minimal faithful stand-in for live resolution.candidate (census 2026-09-23:
-- 0 rows — the ratified pin window; only existence + PK matter to V198).
CREATE TABLE resolution.candidate (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


class _Base(unittest.TestCase):
    def make_db(self, prefix):
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
                    # autocommit: clear the aborted tx with a raw ROLLBACK
                    # (V186-harness lesson) so later statements survive.
                    self_inner.conn.cursor().execute("ROLLBACK")
                    return str(exc).split("\n")[0]
                raise AssertionError("expected the statement to fail")

        db = DB()
        db.dbname = f"nexus_v198_{prefix}_{os.getpid()}_{uuid_mod.uuid4().hex[:6]}"
        return db

    def apply_v198(self, db):
        with open(V198_PATH) as fh:
            db.sql(fh.read())

    def two_candidates(self, db):
        a = db.sql("INSERT INTO resolution.candidate DEFAULT VALUES RETURNING id")
        b = db.sql("INSERT INTO resolution.candidate DEFAULT VALUES RETURNING id")
        return [a, b]


class BornClean(_Base):
    def test_01_apply_shape(self):
        with self.make_db("born") as db:
            db.sql(SKELETON)
            self.apply_v198(db)
            self.assertIsNotNone(
                db.sql("SELECT to_regclass('resolution.promotion_batch')"))
            self.assertIsNotNone(
                db.sql("SELECT to_regclass('resolution.promotion_batch_candidate')"))
            self.assertEqual(
                db.sql("SELECT count(*) FROM pg_indexes "
                       "WHERE indexname = 'uq_promotion_batch_active'"), 1)
            for fn in ("resolution.candidate_set_key(uuid[],text)",
                       "resolution.eligibility_hash(jsonb)",
                       "resolution.claim_open_batch(uuid[],jsonb,integer)",
                       "resolution.close_open_batch(uuid,text)"):
                self.assertIsNotNone(db.sql(
                    "SELECT to_regprocedure(%s)", (fn,)), fn)
            # R1.4: envelope_version present, default 1.
            self.assertIn("1", db.sql(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema='resolution' AND table_name='promotion_batch' "
                "AND column_name='envelope_version'"))

    def test_02_negative_fixture_shape_pins(self):
        """R2.4: each pin must REJECT its violation class."""
        with self.make_db("pins") as db:
            db.sql(SKELETON)
            self.apply_v198(db)
            base = ("INSERT INTO resolution.promotion_batch "
                    "(batch_key, eligibility_snapshot, eligibility_canonical_text, "
                    "eligibility_hash, created_by) VALUES ")
            err = db.expect_error(base + "('not-a-hash', '{}', '{}', '%s', 't')"
                                  % ("a" * 64,))
            self.assertIn("promotion_batch_key_hash_check", err)
            err = db.expect_error(base + "('%s', '{}', '{}', 'short', 't')"
                                  % ("b" * 64,))
            self.assertIn("promotion_batch_hash_check", err)
            err = db.expect_error(
                "INSERT INTO resolution.promotion_batch (batch_key, status, "
                "eligibility_snapshot, eligibility_canonical_text, eligibility_hash, "
                "created_by) VALUES ('%s', 'bogus', '{}', '{}', '%s', 't')"
                % ("c" * 64, "d" * 64))
            self.assertIn("promotion_batch_status_check", err)

    def test_03_negative_fixture_active_invariant(self):
        """R3: two OPEN batches with one batch_key is impossible; sealing frees
        the slot."""
        with self.make_db("inv") as db:
            db.sql(SKELETON)
            self.apply_v198(db)
            row = ("INSERT INTO resolution.promotion_batch (batch_key, status, "
                   "eligibility_snapshot, eligibility_canonical_text, "
                   "eligibility_hash, created_by) VALUES ('%s', '%%s', '{}', '{}', "
                   "'%s', 't')" % ("e" * 64, "f" * 64))
            db.sql(row % "open")
            err = db.expect_error(row % "open")
            self.assertIn("uq_promotion_batch_active", err)
            db.sql(row % "sealed")   # same key, not open — allowed
            self.assertEqual(db.sql(
                "SELECT count(*) FROM resolution.promotion_batch"), 2)

    def test_04_double_apply_refused(self):
        with self.make_db("reapply") as db:
            db.sql(SKELETON)
            self.apply_v198(db)
            with open(V198_PATH) as fh:
                err = db.expect_error(fh.read())
            self.assertIn("V198 PREFLIGHT FAIL", err)
            self.assertIn("already exists", err)


class KeySemantics(_Base):
    def setUp(self):
        self.db = self.make_db("keys")
        self.db.__enter__()
        self.db.sql(SKELETON)
        self.apply_v198(self.db)
        self.ids = self.two_candidates(self.db)
        self.h = "a" * 64

    def tearDown(self):
        self.db.__exit__(None, None, None)

    def _key(self, ids, h=None):
        return self.db.sql("SELECT resolution.candidate_set_key(%s::uuid[], %s)",
                           (ids, h or self.h))

    def test_05_order_duplicate_independence(self):
        a, b = self.ids
        k1 = self._key([a, b])
        self.assertEqual(k1, self._key([b, a]))          # order-free
        self.assertEqual(k1, self._key([a, b, a, b]))    # dupes collapse (the fix)
        self.assertNotEqual(k1, self._key([a]))          # still set-sensitive
        self.assertRegex(k1, r"^[0-9a-f]{64}$")

    def test_06_eligibility_canonical_hash(self):
        s1 = '{"a": 1, "b": [2, 3]}'
        s2 = '{ "b": [2,3], "a":1 }'      # different text, same jsonb
        h1 = self.db.sql("SELECT resolution.eligibility_hash(%s::jsonb)", (s1,))
        h2 = self.db.sql("SELECT resolution.eligibility_hash(%s::jsonb)", (s2,))
        self.assertEqual(h1, h2)          # R1.2: canonical, not raw text
        h3 = self.db.sql("SELECT resolution.eligibility_hash(%s::jsonb)",
                         ('{"a": 2, "b": [2, 3]}',))
        self.assertNotEqual(h1, h3)

    def test_07_hash_case_insensitive_key_discipline(self):
        a, b = self.ids
        self.assertEqual(self._key([a, b], "A" * 64), self._key([a, b], "a" * 64))
        # determinism: repeated evaluation, same inputs
        self.assertEqual(self._key([a, b]), self._key([a, b]))


class ClaimPath(_Base):
    def setUp(self):
        self.db = self.make_db("claim")
        self.db.__enter__()
        self.db.sql(SKELETON)
        self.apply_v198(self.db)
        self.ids = self.two_candidates(self.db)
        self.snap = '{"min_score": 0.7, "window": "2026-Q3"}'

    def tearDown(self):
        self.db.__exit__(None, None, None)

    def _claim(self, ids=None, conn=None, ver=1):
        cur = (conn or self.db.conn).cursor()
        cur.execute("SELECT resolution.claim_open_batch(%s::uuid[], %s::jsonb, %s)",
                    (ids or self.ids, self.snap, ver))
        return cur.fetchone()[0]

    def test_08_claim_membership_and_fk_restict(self):
        a, b = self.ids
        batch = self._claim([a, b, a])            # dupes collapse end-to-end
        rows = self.db.sql(
            "SELECT candidate_id FROM resolution.promotion_batch_candidate "
            "WHERE batch_id = %s ORDER BY candidate_id", (batch,))
        got = sorted(str(r[0] if isinstance(r, tuple) else r) for r in rows)
        self.assertEqual(got, sorted([str(a), str(b)]))
        # D3: membership is normalized links — FK RESTRICT, not JSONB.
        err = None
        try:
            self.db.sql("DELETE FROM resolution.candidate WHERE id = %s", (a,))
        except psycopg2.Error as exc:
            err = str(exc).split("\n")[0]
            self.db.conn.cursor().execute("ROLLBACK")
        self.assertIsNotNone(err, "candidate delete must be RESTRICTed")

    def test_09_race_converges_on_winner(self):
        """Two concurrent claimers of the same set+snapshot: one INSERT wins,
        the loser converges on the winner's batch — one open batch total."""
        results = []

        def claimer(store):
            conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.db.dbname)
            conn.autocommit = True
            try:
                store.append(self._claim(conn=conn))
            finally:
                conn.close()

        t1, t2 = threading.Thread(target=claimer, args=(results,)), \
            threading.Thread(target=claimer, args=(results,))
        t1.start(); t2.start(); t1.join(30); t2.join(30)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1],
                         "losers must converge on the winner's batch")
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM resolution.promotion_batch WHERE status='open'"), 1)

    def test_10_crash_rollback_frees_slot(self):
        conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.db.dbname)
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("SELECT resolution.claim_open_batch(%s::uuid[], %s::jsonb)",
                        (self.ids, self.snap))
            conn.rollback()   # claimer died before commit
        finally:
            conn.close()
        # Slot is free: a fresh claim succeeds and owns the only open batch.
        batch = self._claim()
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM resolution.promotion_batch WHERE status='open'"), 1)

    def test_11_seal_supersede_lineage(self):
        b1 = self._claim()
        self.db.sql("SELECT resolution.close_open_batch(%s, 'sealed')", (b1,))
        # Same set+snapshot can claim again now — new generation, v2 envelope.
        b2 = self._claim(ver=2)
        self.assertNotEqual(b1, b2)
        self.assertEqual(self.db.sql(
            "SELECT envelope_version FROM resolution.promotion_batch WHERE id=%s",
            (b2,)), 2)
        # close_open_batch is fail-closed: no reopen, no bogus status.
        self.assertIn("accepts only",
                      self.db.expect_error(
                          "SELECT resolution.close_open_batch(%s, 'bogus')", (b2,)))
        self.assertIn("not open",
                      self.db.expect_error(
                          "SELECT resolution.close_open_batch(%s, 'sealed')", (b1,)))
        # History retained append-only (W-A reads sealed/superseded rows).
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM resolution.promotion_batch"), 2)


if __name__ == "__main__":
    unittest.main()
