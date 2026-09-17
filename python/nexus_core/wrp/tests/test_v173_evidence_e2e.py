#!/usr/bin/env python3
"""E2E: V173 evidence history + staleness against the REAL migration (throwaway DB).

wr-conf-023 companion, house throwaway-DB pattern (wr-conf-018..022):
creates a throwaway database, builds the skeleton (nebula + tackle surfaces),
applies the REAL V172 then the REAL V173, and asserts the invariants live:

  - evidence normalization: flat object -> one-element array; '{}' -> '[]'
  - CHECK guard: flat-object evidence INSERTs are now structurally rejected
  - probe-append path: newest-last append keeps the CHECK satisfied
  - view: satisfying_providers is '{}' (never NULL) with zero active adapters
    (drill finding 1)
  - staleness slice: satisfaction_state = satisfied / satisfied-stale /
    unsatisfied; last_observed_at drives the verdict; no checks ever ->
    satisfied-stale (an unchecked claim is stale by definition)
  - V172 regression: active adapter + fresh evidence still satisfies;
    degraded-only still unsatisfied; provider-agnosticism intact
  - idempotent re-apply
"""
import os
import sys
import unittest
import uuid

import psycopg2
import psycopg2.extras

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V172_PATH = os.path.join(_REPO_ROOT, "sql", "V172__capability_adapter_registry.sql")
V173_PATH = os.path.join(_REPO_ROOT, "sql", "V173__adapter_evidence_history.sql")

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
    def __init__(self):
        self.dbname = f"nexus_v173_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
        # scalar-first: single row + single column yields the bare value,
        # matching the test file's unpacking convention
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def expect_error(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the statement to fail")

    def apply_v172(self):
        with open(V172_PATH) as fh:
            self.sql(fh.read())

    def apply_v173(self):
        with open(V173_PATH) as fh:
            self.sql(fh.read())

    def add_capability(self, name, lease_id=None):
        # sql() is scalar-first: RETURNING id comes back as the bare uuid
        return self.sql("""
INSERT INTO nebula.capabilities (name, protocol_spec, registered_by, registered_lease)
VALUES (%s, '{}'::jsonb, 'dba', %s) RETURNING id;
""", (name, lease_id))

    def add_adapter(self, capability_id, provider, status='declared',
                    evidence=None, checked=True, lease_id=None):
        return self.sql("""
INSERT INTO nebula.adapters
    (capability_id, provider, adapter_status, evidence, registered_by,
     registered_lease, last_checked_at)
VALUES (%s, %s, %s, %s, 'dba', %s,
        CASE WHEN %s THEN now() ELSE NULL END)
RETURNING id;
""", (capability_id, provider, status,
      psycopg2.extras.Json(evidence if evidence is not None else []),
      lease_id, checked))


class V173E2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        # real-world sequencing: V172 (registry) applied and populated first,
        # then V173 (repair/normalization) lands on top
        self.db.apply_v172()

    # ── evidence normalization (drill finding 2) ────────────────────────────

    def test_flat_object_normalizes_to_array(self):
        cap = self.db.add_capability("has-active-shrapnel-protocol")
        ad = self.db.sql("""
INSERT INTO nebula.adapters (capability_id, provider, adapter_status, evidence, last_checked_at)
VALUES (%s, 'postgresql', 'active', %s, now()) RETURNING id;
""", (cap, psycopg2.extras.Json({"kind": "legacy-registration",
                                 "result": "PASS"})))
        self.db.apply_v173()
        shape = self.db.sql(
            "SELECT jsonb_typeof(evidence) FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(shape, "array")
        n = self.db.sql(
            "SELECT jsonb_array_length(evidence) FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(n, 1)
        kind = self.db.sql(
            "SELECT evidence->0->>'kind' FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(kind, "legacy-registration")  # data preserved, not dropped

    def test_empty_object_normalizes_to_empty_array(self):
        cap = self.db.add_capability("cap-b")
        ad = self.db.sql("""
INSERT INTO nebula.adapters (capability_id, provider, evidence)
VALUES (%s, 'mysql', '{}'::jsonb) RETURNING id;
""", (cap,))
        self.db.apply_v173()
        shape = self.db.sql(
            "SELECT jsonb_typeof(evidence) FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(shape, "array")
        n = self.db.sql(
            "SELECT jsonb_array_length(evidence) FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(n, 0)

    def test_check_rejects_flat_object_ever_after(self):
        cap = self.db.add_capability("cap-c")
        self.db.apply_v173()
        err = self.db.expect_error("""
INSERT INTO nebula.adapters (capability_id, provider, evidence)
VALUES (%s, 'mysql', '{"kind": "flat"}'::jsonb);
""", (cap,))
        self.assertIn("chk_adapters_evidence_array", err)

    def test_array_appends_still_pass_the_check(self):
        cap = self.db.add_capability("cap-d")
        ad = self.db.add_adapter(cap, "postgresql", "active",
                                 evidence=[{"kind": "reg", "result": "PASS"}])
        self.db.apply_v173()
        self.db.sql("""
UPDATE nebula.adapters
SET evidence = evidence || %s::jsonb
WHERE id = %s;
""", ('[{"kind": "health-probe", "result": "PASS", "observed_at": "2026-09-17T00:00:00Z"}]',
            ad))
        n = self.db.sql(
            "SELECT jsonb_array_length(evidence) FROM nebula.adapters WHERE id=%s",
            (ad,))
        self.assertEqual(n, 2)  # newest last, guard satisfied

    # ── view: COALESCE + staleness (drill finding 1 + design slice) ─────────

    def test_satisfying_providers_never_null(self):
        cap = self.db.add_capability("cap-e")
        self.db.add_adapter(cap, "postgresql", "degraded", checked=True)
        self.db.apply_v173()
        row = self.db.sql("""
SELECT satisfied, satisfying_providers, satisfaction_state
FROM nebula.v_capability_satisfaction WHERE capability='cap-e';
""")[0]
        self.assertFalse(row[0])
        self.assertEqual(row[1], [])          # '{}' not NULL — the drill wart
        self.assertEqual(row[2], "unsatisfied")

    def test_three_state_fresh_satisfied(self):
        cap = self.db.add_capability("cap-f")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[{"kind": "reg", "result": "PASS"}],
                            checked=True)
        self.db.apply_v173()
        state = self.db.sql("""
SELECT satisfaction_state FROM nebula.v_capability_satisfaction
WHERE capability='cap-f';
""")
        self.assertEqual(state, "satisfied")

    def test_three_state_stale_satisfied(self):
        cap = self.db.add_capability("cap-g")
        ad = self.db.add_adapter(cap, "postgresql", "active",
                                 evidence=[{"kind": "reg", "result": "PASS"}],
                                 checked=True)
        self.db.apply_v173()
        # age the last observation past the 7-day threshold
        self.db.sql("UPDATE nebula.adapters SET last_checked_at = now() - interval '8 days' WHERE id=%s", (ad,))
        row = self.db.sql("""
SELECT satisfaction_state, evidence_age IS NOT NULL
FROM nebula.v_capability_satisfaction WHERE capability='cap-g';
""")[0]
        self.assertEqual(row[0], "satisfied-stale")
        self.assertTrue(row[1])

    def test_never_checked_is_stale_not_satisfied_clean(self):
        # an active claim that has NEVER been observed is stale by definition
        cap = self.db.add_capability("cap-h")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[{"kind": "reg", "result": "PASS"}],
                            checked=False)
        self.db.apply_v173()
        state = self.db.sql("""
SELECT satisfaction_state FROM nebula.v_capability_satisfaction
WHERE capability='cap-h';
""")
        self.assertEqual(state, "satisfied-stale")

    # ── V172 regression under the V173 view ────────────────────────────────

    def test_v172_truth_table_intact(self):
        cap = self.db.add_capability("cap-i")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[{"kind": "reg", "result": "PASS"}])
        cap2 = self.db.add_capability("cap-j")
        self.db.add_adapter(cap2, "postgresql", "degraded", checked=True)
        self.db.add_adapter(cap2, "mysql", "declared", checked=False)
        self.db.apply_v173()
        rows = dict(self.db.sql("""
SELECT capability, satisfaction_state FROM nebula.v_capability_satisfaction
WHERE capability IN ('cap-i','cap-j');
"""))
        self.assertEqual(rows["cap-i"], "satisfied")
        self.assertEqual(rows["cap-j"], "unsatisfied")

    def test_active_with_empty_history_refused_at_first_apply(self):
        # the gate runs at the END of V173's own transaction: normalization
        # has already converted the flat/empty evidence, so an ACTIVE adapter
        # with zero observations trips the gate on THIS first apply
        cap = self.db.add_capability("cap-k")
        self.db.add_adapter(cap, "postgresql", "active", evidence=[])
        err = self.db.expect_error(open(V173_PATH).read())
        self.assertIn("empty evidence history", err)

    def test_idempotent_reapply(self):
        cap = self.db.add_capability("cap-l")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[{"kind": "reg", "result": "PASS"}])
        self.db.apply_v173()
        self.db.apply_v173()  # second application: no error, gate clean
        n = self.db.sql("SELECT count(*) FROM nebula.adapters")
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
