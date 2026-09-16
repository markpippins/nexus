#!/usr/bin/env python3
"""E2E: V172 capability/adapter registry against the REAL migration (throwaway DB).

wr-conf-022 companion, house throwaway-DB pattern (wr-conf-018/019/020/021):
each run creates a throwaway database (nexus_v172_test_<pid>_<hex>) on the
DEV Postgres, builds the minimal skeleton (nebula schema + tackle.system_logs
+ tackle.role_leases for provenance), applies the REAL
sql/V172__capability_adapter_registry.sql, then asserts the registry
invariants live — and drops the database on exit.

  - capability inventory + unique names
  - adapter truth table on the satisfaction view: no adapters → unsatisfied,
    declared → unsatisfied, active → satisfied, degraded → unsatisfied,
    retired → unsatisfied, active+retired mixed → satisfied
  - provider-agnosticism: a MySQL adapter satisfies the same capability that
    a PostgreSQL adapter also satisfies (provider is data, not doctrine)
  - evidence gate: active status with empty evidence is refused post-apply
    (the verification gate fires on re-apply with a live violation)
  - provenance guard: registered_lease must reference an open lease row
  - audit trail: NEBULA_AUDIT rows land in tackle.system_logs
  - idempotent re-apply
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V172_PATH = os.path.join(_REPO_ROOT, "sql", "V172__capability_adapter_registry.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = """
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

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

CREATE TABLE tackle.system_logs (
    id        text PRIMARY KEY,
    timestamp timestamptz NOT NULL DEFAULT now(),
    level     text NOT NULL,
    category  text NOT NULL,
    message   text NOT NULL,
    source    text,
    details   jsonb
);
"""


class ThrowawayDB:
    """Throwaway per-run database with the V172 skeleton applied."""

    def __init__(self):
        self.dbname = f"nexus_v172_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
        return rows

    def apply_v172(self):
        with open(V172_PATH) as fh:
            self.sql(fh.read())

    def add_capability(self, name, lease_id=None, spec=None):
        rows = self.sql("""
INSERT INTO nebula.capabilities (name, protocol_spec, registered_by, registered_lease)
VALUES (%s, %s, 'dba', %s) RETURNING id;
""", (name, spec or '{}', lease_id))
        return rows[0][0]

    def add_adapter(self, capability_id, provider, status='declared',
                    evidence=None, lease_id=None):
        rows = self.sql("""
INSERT INTO nebula.adapters
    (capability_id, provider, adapter_status, evidence, registered_by, registered_lease,
     last_checked_at)
VALUES (%s, %s, %s, %s, 'dba', %s,
        CASE WHEN %s = 'active' THEN now() ELSE NULL END)
RETURNING id;
""", (capability_id, provider, status,
      psycopg2.extras.Json(evidence if evidence is not None else
                           ({'probe': 'e2e', 'result': 'ok'} if status == 'active' else {})),
      lease_id, status))
        return rows[0][0]


import psycopg2.extras  # noqa: E402  (needs DSN-time import order above)


class V172E2E(unittest.TestCase):
    db = None

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(lambda: self.db.__exit__())
        self.db.apply_v172()

    # ── invariants ───────────────────────────────────────────────────────
    def test_01_capability_inventory_and_unique_names(self):
        cid = self.db.add_capability("has-active-shrapnel-protocol")
        self.assertIsNotNone(cid)
        # duplicate names refused
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            self.db.add_capability("has-active-shrapnel-protocol")
        # view exposes the inventory with zero adapters = unsatisfied
        row = self.db.sql("""
SELECT capability, active_adapters, satisfied
FROM nebula.v_capability_satisfaction
WHERE capability = 'has-active-shrapnel-protocol';
""")[0]
        self.assertEqual((row[0], row[1], row[2]),
                         ("has-active-shrapnel-protocol", 0, False))

    def test_02_satisfaction_truth_table(self):
        # no adapters -> unsatisfied (from test_01 pattern)
        none_cap = self.db.add_capability("cap-none")
        # declared -> unsatisfied
        decl_cap = self.db.add_capability("cap-declared")
        self.db.add_adapter(decl_cap, "postgresql", "declared")
        # active -> satisfied
        act_cap = self.db.add_capability("cap-active")
        self.db.add_adapter(act_cap, "postgresql", "active")
        # degraded -> unsatisfied
        degr_cap = self.db.add_capability("cap-degraded")
        self.db.add_adapter(degr_cap, "mysql", "degraded")
        # retired -> unsatisfied
        ret_cap = self.db.add_capability("cap-retired")
        self.db.add_adapter(ret_cap, "elasticsearch", "retired")
        # active + retired mixed -> satisfied (any active over any provider)
        mix_cap = self.db.add_capability("cap-mixed")
        self.db.add_adapter(mix_cap, "mysql", "retired")
        self.db.add_adapter(mix_cap, "convex", "active")

        rows = {r[0]: (r[1], r[2]) for r in self.db.sql("""
SELECT capability, active_adapters, satisfied
FROM nebula.v_capability_satisfaction
WHERE capability LIKE 'cap-%' ORDER BY capability;
""")}
        self.assertEqual(rows["cap-none"], (0, False))
        self.assertEqual(rows["cap-declared"], (0, False))
        self.assertEqual(rows["cap-active"], (1, True))
        self.assertEqual(rows["cap-degraded"], (0, False))
        self.assertEqual(rows["cap-retired"], (0, False))
        self.assertEqual(rows["cap-mixed"], (1, True))

    def test_03_provider_agnostic_satisfaction(self):
        # the operator's example: the same capability satisfied by adapters
        # over DIFFERENT providers — provider identity is data, not doctrine
        cap = self.db.add_capability("has-active-shrapnel-protocol")
        self.db.add_adapter(cap, "postgresql", "active")
        self.db.add_adapter(cap, "mysql", "active")
        self.db.add_adapter(cap, "elasticsearch", "active")
        row = self.db.sql("""
SELECT active_adapters, satisfied, satisfying_providers
FROM nebula.v_capability_satisfaction
WHERE capability = 'has-active-shrapnel-protocol';
""")[0]
        self.assertEqual(row[0], 3)
        self.assertTrue(row[1])
        self.assertEqual(set(row[2]), {"postgresql", "mysql", "elasticsearch"})

    def test_04_evidence_gate_refuses_empty_active(self):
        # an adapter inserted active with empty evidence violates the
        # post-apply verification gate on re-apply
        cap = self.db.add_capability("cap-no-evidence")
        self.db.sql("""
INSERT INTO nebula.adapters (capability_id, provider, adapter_status, evidence)
VALUES (%s, 'postgresql', 'active', '{}'::jsonb);
""", (cap,))
        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            self.db.apply_v172()
        self.assertIn("active adapters without evidence", str(ctx.exception))

    def test_05_provenance_guard(self):
        # registered_lease must reference an open lease row
        fake_lease = str(uuid.uuid4())
        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            self.db.add_capability("cap-bad-lease", lease_id=fake_lease)
        self.assertIn("CAP0001", str(ctx.exception))
        # a real lease row passes
        real = str(self.db.sql("""
INSERT INTO tackle.role_leases (role, model) VALUES ('dba', 'test') RETURNING id;
""")[0][0])
        cid = self.db.add_capability("cap-good-lease", lease_id=real)
        self.assertIsNotNone(cid)
        # adapter path honors the same guard
        with self.assertRaises(psycopg2.errors.RaiseException):
            self.db.add_adapter(cid, "postgresql", "declared",
                                lease_id=str(uuid.uuid4()))

    def test_06_audit_trail_lands(self):
        cap = self.db.add_capability("cap-audited")
        self.db.add_adapter(cap, "postgresql", "declared")
        caps_rows = self.db.sql("""
SELECT count(*) FROM tackle.system_logs
WHERE category = 'NEBULA_AUDIT'
  AND details->>'table' = 'capabilities'
  AND details->>'op' = 'INSERT'
  AND details->>'keys' = 'cap-audited';
""")[0][0]
        adaps_rows = self.db.sql("""
SELECT count(*) FROM tackle.system_logs
WHERE category = 'NEBULA_AUDIT'
  AND details->>'table' = 'adapters'
  AND details->>'op' = 'INSERT'
  AND details->>'keys' = 'cap-audited/postgresql';
""")[0][0]
        self.assertGreaterEqual(caps_rows, 1)
        self.assertGreaterEqual(adaps_rows, 1)

    def test_07_idempotent_reapply(self):
        cap = self.db.add_capability("cap-idempotent")
        self.db.add_adapter(cap, "postgresql", "active")
        # re-apply: guarded, no errors, no duplicates
        self.db.apply_v172()
        (count,) = self.db.sql(
            "SELECT count(*) FROM nebula.capabilities;")[0]
        self.assertEqual(count, 1)
        (count,) = self.db.sql(
            "SELECT count(*) FROM nebula.adapters;")[0]
        self.assertEqual(count, 1)

    def test_08_seam_columns_inert(self):
        # concept_id exists, nullable, with no FK enforcement yet — the
        # ontologist ruling wires it without a migration
        cols = self.db.sql("""
SELECT column_name FROM information_schema.columns
WHERE table_schema='nebula' AND table_name='capabilities'
  AND column_name='concept_id';
""")
        self.assertEqual(len(cols), 1)
        fks = self.db.sql("""
SELECT count(*) FROM information_schema.table_constraints
WHERE constraint_schema='nebula' AND table_name='capabilities'
  AND constraint_type='FOREIGN KEY';
""")
        self.assertEqual(fks[0][0], 0)  # only constraints are on adapters


if __name__ == "__main__":
    unittest.main(verbosity=2)
