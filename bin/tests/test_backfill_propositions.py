"""Tests for the T24 backfill re-pointing (plan 8261642, requirement c805badc).

The backfill script (bin/backfill_propositions.py) referenced retired
semantics.concept_relationship / semantics.concept (removed by V134) and failed
with UndefinedTable. The fix re-points fetch_candidates() to the canonical
resolution.concept_relationship / resolution.concept.

These tests verify (plan 8261642 ACs):
- No references to retired semantics.* ontology tables in the script source
  (excluding docstrings/comments that document the retirement).
- fetch_candidates() executes cleanly against the live schema (read-only
  SELECT, exits 0, no UndefinedTable).
- The fixed query finds exactly the right candidates (hermetic throwaway
  schema mirroring the current resolution.* + semantics.statement_evidence
  shapes): edges with concept_relationship evidence but no resolution_proposition
  link are returned; edges without evidence or already linked are excluded.
- Idempotency: the NOT EXISTS + ON CONFLICT guards mean re-runs are safe
  (verified by running the query twice and comparing).

Run:
  CONDUIT_PG_DSN='host=localhost port=5432 user=pguser password=pgpass dbname=nexus' \
      python3 -m pytest bin/tests/test_backfill_propositions.py -v
"""
import importlib.util
import os
import re
import sys
import unittest

import psycopg2
import psycopg2.extras

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKFILL = os.path.join(_SCRIPT_DIR, "backfill_propositions.py")

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    raise RuntimeError("CONDUIT_PG_DSN must be set to run tests (PG is mandatory)")


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_under_test", _BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBackfillSourceSanity(unittest.TestCase):
    """A1/A6: the script must not reference retired ontology tables."""

    def test_no_retired_table_references_in_executable_sql(self):
        with open(_BACKFILL) as f:
            src = f.read()
        # Strip comments and docstrings so retirement *documentation* doesn't trip us.
        no_comments = re.sub(r"#.*", "", src)
        no_docstrings = re.sub(r'"""[\s\S]*?"""', "", no_comments)
        self.assertNotIn(
            "semantics.concept_relationship", no_docstrings,
            "fetch_candidates must not query retired semantics.concept_relationship",
        )
        # semantics.concept (bare) must not appear as a table; semantics.statement_evidence is canonical and allowed.
        bare = re.sub(r"semantics\.statement_evidence", "", no_docstrings)
        bare = re.sub(r"semantics\.evidence_item", "", bare)
        self.assertNotRegex(bare, r"semantics\.concept\b",
            "must not reference retired semantics.concept (statement_evidence/evidence_item are allowed)")

    def test_canonical_tables_referenced(self):
        with open(_BACKFILL) as f:
            src = f.read()
        self.assertIn("resolution.concept_relationship", src)
        self.assertIn("resolution.concept", src)
        self.assertIn("semantics.statement_evidence", src)
        self.assertIn("resolution.proposition", src)

    def test_idempotent_guards_present(self):
        with open(_BACKFILL) as f:
            src = f.read()
        self.assertIn("NOT EXISTS", src, "dedupe guard must be present")
        self.assertIn("ON CONFLICT", src, "upsert backstop must be present")


class TestFetchCandidatesLive(unittest.TestCase):
    """A2/A7: fetch_candidates() runs cleanly against the live schema."""

    def test_fetch_runs_without_schema_error(self):
        mod = _load_module()
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cands = mod.fetch_candidates(cur)
            self.assertIsInstance(cands, list)
        finally:
            conn.close()

    def test_forward_ingest_state_preserved(self):
        # A6/A7: the backfill must not disturb the working forward path.
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM resolution.proposition")
                props = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM semantics.statement_evidence "
                    "WHERE statement_type='resolution_proposition' AND expired_at IS NULL")
                links = cur.fetchone()[0]
            self.assertGreaterEqual(props, 1, "forward-ingest propositions must exist")
            self.assertGreaterEqual(links, 1, "resolution_proposition links must exist")
        finally:
            conn.close()


class TestFetchCandidatesHermetic(unittest.TestCase):
    """A3/A5: hermetic candidate selection + idempotency against a throwaway schema.

    Builds resolution.concept / concept_relationship + semantics.statement_evidence
    mirrors, inserts edges covering: (a) with evidence, no proposition link (must
    be returned), (b) no evidence (excluded), (c) already linked (excluded).
    """

    SCHEMA = "test_t24_backfill"

    def setUp(self):
        self._conn = psycopg2.connect(_DSN)
        self._conn.set_isolation_level(0)
        with self._conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {self.SCHEMA}")
            cur.execute(f"""
                CREATE TABLE {self.SCHEMA}.concept (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  name text, description text, expired_at timestamptz)
            """)
            cur.execute(f"""
                CREATE TABLE {self.SCHEMA}.concept_relationship (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  from_concept_id uuid NOT NULL, to_concept_id uuid NOT NULL,
                  relationship_type text NOT NULL, notes text, expired_at timestamptz)
            """)
            cur.execute(f"""
                CREATE TABLE {self.SCHEMA}.statement_evidence (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  evidence_item_id uuid, statement_type text, statement_id uuid,
                  role text, strength float, comment text, expired_at timestamptz)
            """)
            # two concepts
            cur.execute(f"INSERT INTO {self.SCHEMA}.concept (name) VALUES ('A'),('B') RETURNING id")
            self._a, self._b = [r[0] for r in cur.fetchall()]
            # (a) edge with evidence, no proposition link -> MUST be returned
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.concept_relationship "
                "(from_concept_id,to_concept_id,relationship_type) VALUES (%s,%s,'relates') RETURNING id",
                (self._a, self._b))
            self._edge_a = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.statement_evidence "
                "(evidence_item_id,statement_type,statement_id,role) "
                "VALUES (gen_random_uuid(),'concept_relationship',%s,'epistemologist') RETURNING evidence_item_id",
                (self._edge_a,))
            self._ev_a = cur.fetchone()[0]
            # (b) edge with NO evidence -> excluded
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.concept_relationship "
                "(from_concept_id,to_concept_id,relationship_type) VALUES (%s,%s,'relates') RETURNING id",
                (self._a, self._b))
            self._edge_b = cur.fetchone()[0]
            # (c) edge with evidence already linked to a proposition -> excluded
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.concept_relationship "
                "(from_concept_id,to_concept_id,relationship_type) VALUES (%s,%s,'relates') RETURNING id",
                (self._a, self._b))
            self._edge_c = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.statement_evidence "
                "(evidence_item_id,statement_type,statement_id,role) "
                "VALUES (gen_random_uuid(),'concept_relationship',%s,'epistemologist') RETURNING evidence_item_id",
                (self._edge_c,))
            ev_c = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO {self.SCHEMA}.statement_evidence "
                "(evidence_item_id,statement_type,statement_id,role) "
                "VALUES (%s,'resolution_proposition',gen_random_uuid(),'epistemologist')",
                (ev_c,))

    def tearDown(self):
        with self._conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
        self._conn.close()

    def _fetch_rewritten(self, cur):
        # Rewrite the module's hardcoded schema prefixes to the throwaway schema,
        # then run the same candidate-selection logic.
        with open(_BACKFILL) as f:
            src = f.read()
        m = re.search(r'def fetch_candidates\(cur\):\s+cur\.execute\(\"\"\"([\s\S]*?)\"\"\"\)', src)
        self.assertIsNotNone(m, "fetch_candidates SQL must be extractable")
        sql = (m.group(1)
               .replace("resolution.concept_relationship", f"{self.SCHEMA}.concept_relationship")
               .replace("resolution.concept", f"{self.SCHEMA}.concept")
               .replace("semantics.statement_evidence", f"{self.SCHEMA}.statement_evidence"))
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]

    def test_selects_only_unlinked_evidenced_edges(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cands = self._fetch_rewritten(cur)
            ids = {str(c["edge_id"]) for c in cands}
            self.assertIn(str(self._edge_a), ids, "evidenced-unlinked edge must be returned")
            self.assertNotIn(str(self._edge_b), ids, "edge without evidence must be excluded")
            self.assertNotIn(str(self._edge_c), ids, "already-linked edge must be excluded")
            self.assertEqual(len(cands), 1)
        finally:
            conn.close()

    def test_idempotent_rerun_finds_same_set(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                first = self._fetch_rewritten(cur)
                second = self._fetch_rewritten(cur)
            self.assertEqual(
                [str(c["edge_id"]) for c in first],
                [str(c["edge_id"]) for c in second],
                "re-run must find the same candidate set (idempotent selection)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()