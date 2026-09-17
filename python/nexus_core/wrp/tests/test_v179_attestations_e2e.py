#!/usr/bin/env python3
"""E2E: V179 — nebula.attestations lineage table (staged inert).

wr-conf-025/026/027/028 companion, house throwaway-DB pattern.

World: a minimal skeleton carrying the surfaces V179 composes —
nebula.roles_history + nebula.roles view (V175-skeleton shape) — then the
REAL V179 applies and the full write-path contract is exercised:

  - schema: kinds, shape constraints, indexes, lineage view, 2 triggers
  - G1 self-attestation refused (ATP0001)
  - G2 evidence-free refused (ATP0002: empty array AND blank-string cases)
  - G3 capability refused for verify=FALSE and for a missing role (ATP0003,
    fail-closed: absent is not attestable)
  - ATP0005: an attestation citing a non-request row is refused
  - happy chain: request -> attestation (cites request) -> greenlight
    (cites attestation), lineage depths 0/1/2
  - G4 refuses: no cite, unknown cite, wrong-work_ref cite (ATP0004)
  - txid pairing: a chain persisted in ONE transaction carries ONE txid
    (the V177 property, expressed on attestations)
  - append-only: DELETE refused (ATP010), superseded rows frozen (ATP011),
    business-column mutation refused (ATP012; supersede close alone works)
  - idempotent re-apply
  - ATP-GATE-001: alien-shape refusal, proven rolled back server-side
"""
import json
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V179_PATH = os.path.join(_REPO_ROOT, "sql", "V179__attestations.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

EVIDENCE = ["wr-conf-031 run: 17 passed",
            "trigger battery txid 17773430",
            "local suite 10/10"]

WORK = "agent-record:06889fc1"

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
    """Throwaway DB: skeleton (pre-V179 world) -> REAL V179."""

    def __init__(self):
        self.dbname = f"nexus_v179_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def expect_error(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the statement to fail")

    def apply_v179(self):
        with open(V179_PATH) as fh:
            self.sql(fh.read())


def _seed_roles(db):
    """dba authors (verify=FALSE), tester attests (verify=TRUE)."""
    db.sql("INSERT INTO nebula.roles_history (name, can_verify_work_requests) "
           "VALUES ('dba', false)")
    db.sql("INSERT INTO nebula.roles_history (name, can_verify_work_requests) "
           "VALUES ('tester', true)")


def _request(db, work_ref=WORK, requested_by="dba"):
    return db.sql(
        "INSERT INTO nebula.attestations (work_ref, kind, requested_by) "
        "VALUES (%s, 'verification_request', %s) RETURNING attestation_id",
        (work_ref, requested_by))


def _attestation(db, work_ref=WORK, requested_by="dba", attester="tester",
                 evidence=None, cites=None):
    return db.sql(
        "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
        "attester_role, evidence, cites_id) "
        "VALUES (%s, 'attestation', %s, %s, %s::jsonb, %s::uuid) "
        "RETURNING attestation_id",
        (work_ref, requested_by, attester,
         json.dumps(evidence or EVIDENCE), str(cites) if cites else None))


def _greenlight(db, work_ref=WORK, requested_by="dba", authority="lead-engineer",
                cites=None):
    return db.sql(
        "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
        "authority_role, cites_id) VALUES (%s, 'greenlight', %s, %s, %s::uuid) "
        "RETURNING attestation_id",
        (work_ref, requested_by, authority, str(cites) if cites else None))


class V179SchemaE2E(unittest.TestCase):
    def test_schema_shape_and_triggers(self):
        with ThrowawayDB() as db:
            db.apply_v179()
            self.assertEqual(2, db.sql(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgrelid='nebula.attestations'::regclass "
                "AND NOT tgisinternal"))
            self.assertEqual(3, db.sql(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid='nebula.attestations'::regclass "
                "AND contype='c'"))
            self.assertIsNotNone(
                db.sql("SELECT to_regclass('nebula.attestation_lineage')"))
            self.assertEqual(0, db.sql(
                "SELECT count(*) FROM nebula.attestations"))

    def test_idempotent_reapply(self):
        with ThrowawayDB() as db:
            db.apply_v179()
            db.apply_v179()  # must not raise
            self.assertEqual(2, db.sql(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgrelid='nebula.attestations'::regclass "
                "AND NOT tgisinternal"))


class V179GateE2E(unittest.TestCase):
    """The #308 gate contract, DDL-encoded and refusing on every write."""

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v179()
        _seed_roles(self.db)
        _request(self.db)

    def test_g1_self_attestation_refused(self):
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence) VALUES (%s, 'attestation', 'dba', "
            "'dba', %s::jsonb)", (WORK, json.dumps(["run 1", "run 2"])))
        self.assertIn("ATP0001", err)
        self.assertIn("G1-self-attestation", err)

    def test_g2_empty_evidence_refused(self):
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence) VALUES (%s, 'attestation', 'dba', "
            "'tester', '[]'::jsonb)", (WORK,))
        self.assertIn("ATP0002", err)
        self.assertIn("G2-evidence-free", err)

    def test_g2_blank_evidence_refused(self):
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence) VALUES (%s, 'attestation', 'dba', "
            "'tester', '[\"  \"]'::jsonb)", (WORK,))
        self.assertIn("ATP0002", err)

    def test_g3_capability_refused(self):
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence) VALUES (%s, 'attestation', 'dba', "
            "'builder', %s::jsonb)", (WORK, json.dumps(["run 1"])))
        self.assertIn("ATP0003", err)

    def test_g3_missing_role_fail_closed(self):
        """Absent role is capability absence, not attestable — fail-closed."""
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence) VALUES (%s, 'attestation', 'dba', "
            "'role-that-does-not-exist', %s::jsonb)",
            (WORK, json.dumps(["run 1"])))
        self.assertIn("ATP0003", err)

    def test_atp0005_attestation_citing_non_request_refused(self):
        other_att = _attestation(self.db, work_ref="agent-record:other")
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "attester_role, evidence, cites_id) VALUES (%s, 'attestation', "
            "'dba', 'tester', %s::jsonb, %s::uuid)",
            (WORK, json.dumps(["run 1"]), str(other_att)))
        self.assertIn("ATP0005", err)

    def test_g4_greenlight_requires_citation(self):
        for cite, label in ((None, "no cite"), (str(uuid.uuid4()), "unknown cite")):
            with self.subTest(case=label):
                err = self.db.expect_error(
                    "INSERT INTO nebula.attestations (work_ref, kind, "
                    "requested_by, authority_role, cites_id) VALUES "
                    "(%s, 'greenlight', 'dba', 'lead-engineer', %s::uuid)",
                    (WORK, cite))
                self.assertIn("ATP0004", err)

    def test_g4_wrong_work_ref_cite_refused(self):
        """The attestation exists and is real — but for a different work unit."""
        other_att = _attestation(self.db, work_ref="agent-record:other")
        err = self.db.expect_error(
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "authority_role, cites_id) VALUES (%s, 'greenlight', 'dba', "
            "'lead-engineer', %s::uuid)", (WORK, str(other_att)))
        self.assertIn("ATP0004", err)


class V179ChainE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v179()
        _seed_roles(self.db)

    def test_happy_chain_and_lineage(self):
        req_id = _request(self.db)
        att_id = _attestation(self.db, cites=req_id)
        _greenlight(self.db, cites=att_id)

        rows = self.db.sql(
            "SELECT depth, kind FROM nebula.attestation_lineage "
            "WHERE root_id = %s ORDER BY depth", (str(req_id),))
        self.assertEqual([(0, "verification_request"),
                          (1, "attestation"),
                          (2, "greenlight")], rows)

    def test_chain_persisted_in_one_transaction_shares_txid(self):
        """The V177 pairing property, on attestations: a chain written in one
        transaction carries one txid — the chain is one attributable event."""
        self.db.sql(
            "WITH a AS ("
            "  INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "           attester_role, evidence) "
            "  VALUES (%s, 'attestation', 'dba', 'tester', %s::jsonb) "
            "  RETURNING attestation_id) "
            "INSERT INTO nebula.attestations (work_ref, kind, requested_by, "
            "         authority_role, cites_id) "
            "SELECT %s, 'greenlight', 'dba', 'lead-engineer', attestation_id "
            "FROM a", (WORK, json.dumps(EVIDENCE), WORK))
        self.assertEqual(1, self.db.sql(
            "SELECT count(DISTINCT txid) FROM nebula.attestations "
            "WHERE work_ref = %s AND kind IN ('attestation','greenlight')",
            (WORK,)))

    def test_append_only_and_freeze_semantics(self):
        att_id = _attestation(self.db)
        self.assertIn("ATP010", self.db.expect_error(
            "DELETE FROM nebula.attestations WHERE attestation_id = %s",
            (str(att_id),)))
        # the supersede close alone works
        self.db.sql(
            "UPDATE nebula.attestations SET recorded_until_dt = now() "
            "WHERE attestation_id = %s", (str(att_id),))
        # then everything is frozen — even reopening
        self.assertIn("ATP011", self.db.expect_error(
            "UPDATE nebula.attestations SET recorded_until_dt = 'infinity' "
            "WHERE attestation_id = %s", (str(att_id),)))

    def test_atp012_business_mutation_refused_on_open_row(self):
        att_id = _attestation(self.db)
        err = self.db.expect_error(
            "UPDATE nebula.attestations SET evidence = '[\"rewritten\"]'::jsonb "
            "WHERE attestation_id = %s", (str(att_id),))
        self.assertIn("ATP012", err)

    def test_gate001_alien_shape_refusal_rolled_back(self):
        with ThrowawayDB() as db:
            db.apply_v179()
            # simulate a foreign table carrying our name, without the contract
            db.sql("DROP TABLE nebula.attestations CASCADE")
            db.sql(
                "CREATE TABLE nebula.attestations ("
                "  attestation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                "  work_ref text NOT NULL, kind text NOT NULL,"
                "  requested_by text NOT NULL,"
                "  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,"
                "  cites_id uuid,"
                "  recorded_on_dt timestamptz NOT NULL DEFAULT now(),"
                "  recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz)")
            err = db.expect_error(open(V179_PATH).read())
            self.assertIn("ATP-GATE-001", err)
            # rollback proof from a fresh connection: the alien table is
            # unchanged and no contract machinery was grafted onto it
            fresh = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + db.dbname)
            fresh.autocommit = True
            with fresh.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid='nebula.attestations'::regclass "
                    "AND NOT tgisinternal")
                self.assertEqual(0, cur.fetchone()[0])
            fresh.close()


if __name__ == "__main__":
    unittest.main()
