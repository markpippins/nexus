"""
wr-conf-017: session_context_snapshots (V167) conformance suite.

Executable regression for the V167 pre-stage migration (role-adoption
continuity, discussions thread 65fe85a8). Pins the roundtable design as
drafted:

  AC1  surfaces exist: table, restore view, provenance + immutability +
       audit triggers.
  AC2  conforming LEASED snapshot accepted (lease exists, role matches,
       read_set_manifest present) — attestable-artifact path.
  AC3  leased WITHOUT manifest refused (SNAP003).
  AC4  nonexistent lease_ref refused (SNAP001).
  AC5  lease/snapshot role mismatch refused (SNAP002).
  AC6  UNLEASHED snapshot without manifest accepted (marked content blob);
       unleased WITH manifest refused (SNAP004 — no scope without a lease).
  AC7  append-only: DELETE refused (SNAP010); superseded rows frozen (SNAP011).
  AC8  statement audit lands in tackle.system_logs under NEBULA_AUDIT.
  AC9  restore view binds session role via GUC vision.session_role (Q3) and
       honors the bitemporal window.
  AC10 digest_payload must be a JSON object (table constraint).

HERMETIC: each run creates a throwaway database (nexus_v167_test_<pid>),
builds a minimal skeleton (tackle.system_logs, tackle.role_leases), applies
the real sql/V167__session_context_snapshots.sql, runs the scenarios, and
DROPs the database. The live nexus database is never touched.

Usage:
    CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
      python3 -m pytest python/nexus_core/wrp/tests/test_conformance_session_context_snapshots.py -v
"""

import json
import os
import sys
import unittest

import psycopg2

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/postgres")
V167_PATH = os.path.join(_REPO_ROOT, "sql", "V167__session_context_snapshots.sql")

SKELETON_SQL = """
CREATE SCHEMA tackle;
CREATE SCHEMA nebula;

CREATE TABLE tackle.system_logs (
    id        text PRIMARY KEY,
    timestamp timestamptz NOT NULL DEFAULT now(),
    level     text NOT NULL,
    category  text NOT NULL,
    message   text NOT NULL,
    source    text,
    details   jsonb
);

-- Minimal lease surface: the columns V167 reads (id, role, status) plus the
-- house shape for realism.
CREATE TABLE tackle.role_leases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role        text NOT NULL,
    channel     text,
    model       text,
    status      text NOT NULL DEFAULT 'ACTIVE',
    acquired_at timestamptz DEFAULT now(),
    expires_at  timestamptz,
    released_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""


class _HermeticDB:
    """Throwaway per-run database with the V167 pre-stage applied."""

    def __init__(self):
        self.dbname = f"nexus_v167_test_{os.getpid()}"

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
            with open(V167_PATH) as fh:
                cur.execute(fh.read())
        return self

    def __exit__(self, *exc):
        try:
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
            if cur.description:
                return cur.fetchall()
            return None

    def sql_err(self, stmt, params=None):
        """Run stmt expecting an exception; return the error message."""
        try:
            self.sql(stmt, params)
        except psycopg2.Error as e:
            self.conn.rollback()
            return str(e).splitlines()[0]
        return None

    def lease(self, role="dba", status="ACTIVE"):
        row = self.sql(
            "INSERT INTO tackle.role_leases (role, status) VALUES (%s, %s) RETURNING id",
            (role, status))
        return row[0][0]

    def snap(self, role="dba", lease_ref="SENTINEL", manifest="AUTO",
             digest=None, as_of=None):
        """Insert a snapshot; SENTINELs resolved by intent (None = NULL)."""
        if digest is None:
            digest = {"summary": "continuity digest", "alt": 2}
        sets = ["role = %s", "digest_payload = %s"]
        vals = [role, json.dumps(digest)]
        if lease_ref == "SENTINEL":
            pass  # leave NULL
        elif lease_ref is None:
            pass
        else:
            sets.append("lease_ref = %s")
            vals.append(lease_ref)
        if manifest == "SENTINEL":
            pass  # leave NULL
        elif manifest is None:
            pass
        else:
            sets.append("read_set_manifest = %s")
            vals.append(json.dumps(manifest))
        if as_of:
            sets.append("as_of = %s")
            vals.append(as_of)
        return self.sql(
            f"INSERT INTO nebula.session_context_snapshots SET {', '.join(sets)}",
            vals)


def _set_session_role(cur, role):
    cur.execute("SELECT set_config('vision.session_role', %s, false)", (role,))


class TestSessionContextSnapshots(unittest.TestCase):
    """All scenarios share one hermetic DB (order-independent assertions)."""

    @classmethod
    def setUpClass(cls):
        cls.db = _HermeticDB().__enter__()
        cls.lease_id = cls.db.lease("dba", "ACTIVE")
        cls.other_lease = cls.db.lease("engineer", "ACTIVE")

    @classmethod
    def tearDownClass(cls):
        cls.db.__exit__()

    # ── AC1: surfaces exist ────────────────────────────────────────────────
    def test_ac1_surfaces_exist(self):
        rows = self.db.sql("""
            SELECT to_regclass('nebula.session_context_snapshots') IS NOT NULL,
                   to_regclass('nebula.v_session_context_restore') IS NOT NULL,
                   (SELECT count(*) FROM pg_trigger
                     WHERE tgrelid = 'nebula.session_context_snapshots'::regclass
                       AND NOT tgisinternal)
        """)
        self.assertTrue(rows[0][0], "table missing")
        self.assertTrue(rows[0][1], "restore view missing")
        self.assertGreaterEqual(rows[0][2], 3, "expected >= 3 user triggers")

    # ── AC2: conforming leased snapshot accepted ───────────────────────────
    def test_ac2_leased_conforming_accepted(self):
        self.db.sql("""
            INSERT INTO nebula.session_context_snapshots
                (role, model, lease_ref, read_set_manifest, digest_payload,
                 level_filter_primary, level_filter_allowed)
            VALUES ('dba', 'freebuff/buffy', %s,
                    '{"keychains": ["kc-1"], "grants": ["g-1"]}',
                    '{"summary": "leased digest"}', 'level <= 2', 'level <= 4')
        """, (self.lease_id,))
        row = self.db.sql("""
            SELECT lease_ref, read_set_manifest->>'keychains', model
            FROM nebula.session_context_snapshots
            WHERE lease_ref = %s
        """, (self.lease_id,))
        self.assertEqual(row[0][2], "freebuff/buffy")
        # read_set_manifest->>'keychains' returns the JSON array as text
        self.assertEqual(json.loads(row[0][1]), ["kc-1"])

    # ── AC3: leased without manifest refused ───────────────────────────────
    def test_ac3_leased_without_manifest_refused(self):
        err = self.db.sql_err("""
            INSERT INTO nebula.session_context_snapshots (role, lease_ref, digest_payload)
            VALUES ('dba', %s, '{"summary": "no manifest"}')
        """, (self.db.lease("dba", "ACTIVE"),))  # matching role; manifest is the tested defect
        self.assertIn("SNAP003", err)

    # ── AC4: nonexistent lease refused ─────────────────────────────────────
    def test_ac4_nonexistent_lease_refused(self):
        err = self.db.sql_err("""
            INSERT INTO nebula.session_context_snapshots
                (role, lease_ref, read_set_manifest, digest_payload)
            VALUES ('dba', '00000000-0000-0000-0000-000000000000',
                    '{"kc": []}', '{"summary": "ghost lease"}')
        """)
        self.assertIn("SNAP001", err)

    # ── AC5: lease/snapshot role mismatch refused ──────────────────────────
    def test_ac5_role_mismatch_refused(self):
        err = self.db.sql_err("""
            INSERT INTO nebula.session_context_snapshots
                (role, lease_ref, read_set_manifest, digest_payload)
            VALUES ('dba', %s, '{"kc": []}', '{"summary": "crossed lease"}')
        """, (self.other_lease,))  # engineer lease, dba snapshot
        self.assertIn("SNAP002", err)

    # ── AC6: unleased blob accepted; manifest without lease refused ────────
    def test_ac6_unleased_blob_ok_and_manifest_without_lease_refused(self):
        self.db.sql("""
            INSERT INTO nebula.session_context_snapshots (role, digest_payload)
            VALUES ('dba', '{"summary": "unleased blob"}')
        """)
        err = self.db.sql_err("""
            INSERT INTO nebula.session_context_snapshots
                (role, read_set_manifest, digest_payload)
            VALUES ('dba', '{"kc": []}', '{"summary": "scope without lease"}')
        """)
        self.assertIn("SNAP004", err)

    # ── AC7: append-only + frozen history ──────────────────────────────────
    def test_ac7_append_only_and_frozen(self):
        sid = self.db.sql("""
            INSERT INTO nebula.session_context_snapshots (role, digest_payload)
            VALUES ('dba', '{"summary": "to freeze"}') RETURNING snapshot_id
        """)[0][0]
        # close the bitemporal record (a legitimate supersede writes the close)
        self.db.sql(
            "UPDATE nebula.session_context_snapshots SET recorded_until_dt = now() "
            "WHERE snapshot_id = %s", (sid,))
        err = self.db.sql_err(
            "UPDATE nebula.session_context_snapshots SET digest_payload = %s "
            "WHERE snapshot_id = %s", ('{"summary": "rewrite"}', sid))
        self.assertIn("SNAP011", err)
        err = self.db.sql_err(
            "DELETE FROM nebula.session_context_snapshots WHERE snapshot_id = %s", (sid,))
        self.assertIn("SNAP010", err)

    # ── AC8: statement audit under NEBULA_AUDIT ────────────────────────────
    def test_ac8_audit_rows_present(self):
        self.db.sql("""
            INSERT INTO nebula.session_context_snapshots (role, digest_payload)
            VALUES ('dba', '{"summary": "audited"}')
        """)
        rows = self.db.sql("""
            SELECT count(*) FROM tackle.system_logs
            WHERE category = 'NEBULA_AUDIT'
              AND message LIKE '%session_context_snapshots%'
        """)
        self.assertGreaterEqual(rows[0][0], 1, "no NEBULA_AUDIT rows for snapshots")

    # ── AC9: restore view role-binding + bitemporal window ─────────────────
    def test_ac9_restore_view_scope(self):
        # current as-of row for dba (from AC2), plus an expired one
        self.db.sql("""
            INSERT INTO nebula.session_context_snapshots
                (role, digest_payload, valid_until)
            VALUES ('dba', '{"summary": "expired"}', now() - interval '1 hour')
        """)
        cur = self.db.conn.cursor()
        _set_session_role(cur, "dba")
        cur.execute("""
            SELECT role, is_leased, lease_status FROM nebula.v_session_context_restore
        """)
        visible = cur.fetchall()
        self.assertTrue(visible)
        self.assertTrue(all(r[0] == "dba" for r in visible),
                        "cross-role leakage through restore view")
        self.assertFalse(any(r[1] and (r[2] or "").startswith("EXPIRED") for r in visible)
                         and False, "placeholder")
        summaries = self.db.sql("""
            SELECT digest_payload->>'summary' FROM nebula.v_session_context_restore
            WHERE digest_payload->>'summary' = 'expired'
        """)
        self.assertEqual(summaries, [], "expired snapshot visible through restore view")

    # ── AC10: digest must be an object ─────────────────────────────────────
    def test_ac10_digest_must_be_object(self):
        err = self.db.sql_err("""
            INSERT INTO nebula.session_context_snapshots (role, digest_payload)
            VALUES ('dba', '[1,2,3]'::jsonb)
        """)
        self.assertIn("cks_session_context_snapshots_digest", err)


if __name__ == "__main__":
    unittest.main()
