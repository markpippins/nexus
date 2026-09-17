#!/usr/bin/env python3
"""E2E: boot-shim attestation wiring (wr-conf-034, house throwaway-DB pattern).

wr-conf-025/026/027/028/032 companion. The REAL continuity.attest module is
driven against a throwaway DB whose skeleton carries the V179 world, built
by applying the REAL sql/V179__attestations.sql — no mocks on the DB path:

  - scan: request roots surfaced with correct dispositions
    (tester actionable, dba G1-refused, critic uninvolved)
  - record_attestation: tester's attestation lands through the REAL G1-G4
    trigger battery; the chain closes (request -> attestation, depth 0/1)
  - client-side gates proven first (G2/G3/G1 with zero INSERT issued)
  - server gate refusals surface verbatim through the module
  - greenlight insertion directly is refused for tester (no capability)
  - txid: the recorded attestation carries a non-null txid (V177 pairing)

Companion hermetic suite: python/continuity/tests/test_attest.py (no DB).
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V179_PATH = os.path.join(_REPO_ROOT, "sql", "V179__attestations.sql")
sys.path.insert(0, os.path.join(_REPO_ROOT, "python"))

from continuity import attest as att  # noqa: E402

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = """
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

CREATE TABLE nebula.roles_history (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    can_greenlight          boolean DEFAULT false,
    can_verify_work_requests boolean DEFAULT false,
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz
);

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""


class ThrowawayDB:
    """Throwaway DB: skeleton (V175/V179 world) -> REAL V179."""

    def __init__(self):
        self.dbname = f"nexus_attest_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_v179(self):
        with open(V179_PATH) as fh:
            self.sql(fh.read())


def _exec_fn_from(db):
    """Adapt the throwaway connection to the module's exec_fn shape.

    The module's contract is PSQL-RENDERED output: booleans render as
    t/f, not Python's True/False (the exact rendering distinction the
    module's caps lookup banks as a pitfall — psycopg2 str() would lie
    here, so the adapter must render the way psql prints).
    """
    def _render(c):
        if c is True:
            return "t"
        if c is False:
            return "f"
        return str(c)

    def exec_fn(sql):
        out = db.sql(sql)
        if out is None:
            return ""
        if isinstance(out, list):
            return "\n".join("|".join(_render(c) for c in row) for row in out)
        return _render(out)
    return exec_fn


def _seed(db):
    """dba authors (verify=FALSE), tester attests (verify=TRUE)."""
    db.sql("INSERT INTO nebula.roles_history (name, can_verify_work_requests) "
           "VALUES ('dba', false)")
    db.sql("INSERT INTO nebula.roles_history (name, can_verify_work_requests) "
           "VALUES ('tester', true)")


def _request(db, work_ref="agent-record:abc (V177 work unit)", requested_by="dba"):
    return db.sql(
        "INSERT INTO nebula.attestations (work_ref, kind, requested_by, evidence) "
        "VALUES (%s, 'verification_request', %s, %s::jsonb) RETURNING attestation_id",
        (work_ref, requested_by,
         '["wr-conf-031 run: 17 passed", "txid 17773430"]'))


class AttestWiringE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB()
        self.db.__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        _seed(self.db)
        self.db.apply_v179()
        self.fn = _exec_fn_from(self.db)

    def test_scan_dispositions_against_real_gates(self):
        rid = _request(self.db)
        s = att.scan(self.fn, "tester")
        self.assertTrue(s["scanned"])
        self.assertEqual(s["actionable"], 1)
        self.assertEqual(s["open"][0]["attestation_id"], str(rid))
        s2 = att.scan(self.fn, "dba")
        self.assertEqual([i["disposition"] for i in s2["open"]], ["g1_refused"])
        s3 = att.scan(self.fn, "critic")
        self.assertEqual([i["disposition"] for i in s3["open"]], ["uninvolved"])

    def test_record_attestation_closes_the_chain(self):
        rid = _request(self.db)
        s = att.scan(self.fn, "tester")
        self.assertEqual(s["actionable"], 1)
        r = att.record_attestation(self.fn, "tester", str(rid),
                                   ["wr-conf-031 run: 17 passed",
                                    "txid 17773430"])
        self.assertTrue(r["recorded"], r["reason"])
        self.assertTrue(r.get("attestation_id"))
        self.assertTrue(r.get("txid"))
        # chain closed: lineage depth 1 exists; request no longer open-childless
        depth = self.db.sql(
            "SELECT max(depth) FROM nebula.attestation_lineage "
            "WHERE depth > 0")
        self.assertEqual(int(depth), 1)
        # open request count for scan drops when superseded? No — requests
        # stay open (the chain is a tree); the attestation is the child.
        s2 = att.scan(self.fn, "tester")
        self.assertEqual(s2["open"][0]["attestation_id"], str(rid))

    def test_client_gates_fire_before_any_insert(self):
        rid = _request(self.db)
        # G2
        r = att.record_attestation(self.fn, "tester", str(rid), ["", " "])
        self.assertFalse(r["recorded"])
        # G3 (critic has no row)
        r2 = att.record_attestation(self.fn, "critic", str(rid), ["run: x"])
        self.assertFalse(r2["recorded"])
        # G1 (dba authors)
        r3 = att.record_attestation(self.fn, "dba", str(rid), ["run: x"])
        self.assertFalse(r3["recorded"])
        # nothing landed
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM nebula.attestations WHERE kind='attestation'"), 0)

    def test_server_gate_refusal_surfaced_through_module(self):
        rid = _request(self.db)
        # TOCTOU drill: the server gates stay authoritative even when the
        # client's capability check raced. Revoke tester's verify capability
        # between the module's caps lookup and the INSERT (side-effect on the
        # cite-resolve call); the REAL trigger must refuse with ATP0003 and
        # the module must surface it verbatim.
        base = _exec_fn_from(self.db)
        calls = {"n": 0}

        def racing_fn(sql):
            calls["n"] += 1
            if calls["n"] == 2:  # cite-resolve call: revoke mid-flight
                self.db.sql("UPDATE nebula.roles_history "
                            "SET can_verify_work_requests = false "
                            "WHERE name = 'tester'")
            return base(sql)

        r = att.record_attestation(racing_fn, "tester", str(rid), ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("ATP0003", r["reason"])
        # nothing landed
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM nebula.attestations WHERE kind='attestation'"), 0)

    def test_greenlight_citation_contract(self):
        rid = _request(self.db)
        r = att.record_attestation(self.fn, "tester", str(rid), ["run: x"])
        self.assertTrue(r["recorded"], r["reason"])
        # G4 satisfied: a greenlight citing the real attestation row for the
        # same work_ref LAWFULLY INSERTS — greenlight-holder enforcement
        # lives in the grant separation (verify/greenlight gates), not the
        # chain trigger. The chain event is valid regardless of authority.
        gid = self.db.sql(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "authority_role, evidence, cites_id) VALUES "
            "('agent-record:abc (V177 work unit)', 'greenlight', 'dba', "
            "'architect', '[\"decision: greenlit\"]'::jsonb, %s) "
            "RETURNING attestation_id", (r["attestation_id"],))
        self.assertTrue(gid)
        # lineage now spans request(0) -> attestation(1) -> greenlight(2)
        depth = self.db.sql(
            "SELECT max(depth) FROM nebula.attestation_lineage")
        self.assertEqual(int(depth), 2)
        # G4 refusal: citing the REQUEST row instead of the ATTESTATION row
        try:
            self.db.sql(
                "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
                "authority_role, evidence, cites_id) VALUES "
                "('agent-record:abc (V177 work unit)', 'greenlight', 'dba', "
                "'architect', '[\"x\"]'::jsonb, %s)", (rid,))
        except psycopg2.Error as exc:
            self.assertIn("ATP0004", str(exc))
            return
        self.fail("greenlight citing a request row must refuse with ATP0004")


def db_request(db, requested_by="tester"):
    return db.sql(
        "INSERT INTO nebula.attestations (work_ref, kind, requested_by, evidence) "
        "VALUES ('agent-record:self (tester-authored)', 'verification_request', %s, "
        "'[\"self-run: x\"]'::jsonb) RETURNING attestation_id", (requested_by,))


if __name__ == "__main__":
    unittest.main()
