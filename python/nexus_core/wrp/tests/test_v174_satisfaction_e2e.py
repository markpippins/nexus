#!/usr/bin/env python3
"""E2E: V174 satisfaction-states extension against the REAL migration chain.

wr-conf-024 companion, house throwaway-DB pattern: creates a throwaway
database, builds the skeleton, applies the REAL V172 → V173 → V174, and
asserts the invariants live:

  - vocabulary: every V173 state keeps its meaning (regression cells)
  - fresh UNREACHABLE verdicts 'unreachable'; ages out to 'unsatisfied'
  - fresh REFUSED verdicts 'refused'; ages out to 'unsatisfied'
  - fresh FAIL verdicts 'unsatisfied' (positive non-provision, any age)
  - SKIP verdicts 'unknown' at any age — never a measurement
  - declared seed (no observations at all) verdicts 'unknown' — the V173
    cell read 'unsatisfied' here, the false comfort this migration removes
  - active/degraded participate in the not-satisfied argmax; active-only
    PASS evidence satisfies exactly as before (V173 regression)
  - view remains non-NULL in satisfying_providers with zero active
  - idempotent re-apply
"""
import datetime
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
V174_PATH = os.path.join(_REPO_ROOT, "sql", "V174__satisfaction_states.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")


def _days_ago(n: int) -> str:
    """Fixture observation timestamp N days before THIS run (UTC, ISO-8601 Z).

    The V174 freshness window is ``interval '7 days'``
    (sql/V174__satisfaction_states.sql), so fixture timestamps must be
    computed relative to the clock, not hardcoded: the original literal
    ``2026-09-16T00:00:00Z`` aged out of the 7-day window at
    2026-09-23T00:00:00Z and every 'fresh' cell started failing repo-wide
    (both open PRs went red in the same hour, 6 minutes past the boundary).
    1 day ago = fresh with 6 days of slack; 30 days ago = stale with 23.
    """
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# 'fresh' observation (was 2026-09-16T00:00:00Z — 7 days before authoring)
FRESH = _days_ago(1)
# 'aged out' observation (was 2026-09-01T00:00:00Z — 22 days before authoring)
STALE = _days_ago(30)

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
        self.dbname = f"nexus_v174_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
        # scalar-first: single row + single column yields the bare value
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def expect_error(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the statement to fail")

    def apply_chain(self):
        for path in (V172_PATH, V173_PATH, V174_PATH):
            with open(path) as fh:
                self.sql(fh.read())

    def add_capability(self, name, lease_id=None):
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


def _obs(result, at):
    return {"kind": "health-probe", "observer": "test", "result": result,
            "check": "t", "synthetic": False, "observed_at": at}


class V174E2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        # real-world sequencing: V172 populated, V173 repairs, then V174
        self.db.apply_chain()

    def state(self, cap):
        return self.db.sql("""
SELECT satisfaction_state FROM nebula.v_capability_satisfaction
WHERE capability = %s;
""", (cap,))

    # ── V173 regression: the three original states keep their meaning ──────

    def test_fresh_active_pass_still_satisfied(self):
        cap = self.db.add_capability("cap-a")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[_obs("PASS", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-a"), "satisfied")

    def test_aged_active_pass_still_satisfied_stale(self):
        cap = self.db.add_capability("cap-b")
        # V174 note: staleness prefers the EMBEDDED observed_at over
        # last_checked_at, so the aging must happen in the evidence itself
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[_obs("PASS", STALE)],
                            checked=False)  # NULL checked → fallback path
        self.assertEqual(self.state("cap-b"), "satisfied-stale")

    def test_staleness_prefers_embedded_timestamp(self):
        # last_checked_at advanced by a later SKIP run must NOT refresh
        # the age of the last successful PASS — observed_at is the truth
        cap = self.db.add_capability("cap-b2")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[_obs("PASS", STALE),
                                      _obs("SKIP", FRESH)],
                            checked=True)  # last_checked_at = now()
        self.assertEqual(self.state("cap-b2"), "satisfied-stale")

    def test_degraded_only_still_unsatisfied(self):
        cap = self.db.add_capability("cap-c")
        self.db.add_adapter(cap, "postgresql", "degraded",
                            evidence=[_obs("FAIL", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-c"), "unsatisfied")

    def test_satisfying_providers_never_null(self):
        cap = self.db.add_capability("cap-d")
        self.db.add_adapter(cap, "postgresql", "degraded",
                            evidence=[_obs("FAIL", FRESH)],
                            checked=True)
        row = self.db.sql("""
SELECT satisfied, satisfying_providers
FROM nebula.v_capability_satisfaction WHERE capability='cap-d';
""")[0]
        self.assertFalse(row[0])
        self.assertEqual(row[1], [])

    # ── V174: the new vocabulary ────────────────────────────────────────────

    def test_fresh_unreachable_verdicts_unreachable(self):
        cap = self.db.add_capability("cap-e")
        self.db.add_adapter(cap, "mysql", "degraded",
                            evidence=[_obs("UNREACHABLE", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-e"), "unreachable")

    def test_unreachable_ages_out_to_unsatisfied(self):
        cap = self.db.add_capability("cap-f")
        self.db.add_adapter(cap, "mysql", "degraded",
                            evidence=[_obs("UNREACHABLE", STALE)],
                            checked=False)  # last_checked NULL → stale
        self.assertEqual(self.state("cap-f"), "unsatisfied")

    def test_fresh_refused_verdicts_refused(self):
        cap = self.db.add_capability("cap-g")
        self.db.add_adapter(cap, "http", "declared",
                            evidence=[_obs("REFUSED", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-g"), "refused")

    def test_refused_ages_out_to_unsatisfied(self):
        cap = self.db.add_capability("cap-h")
        self.db.add_adapter(cap, "http", "declared",
                            evidence=[_obs("REFUSED", STALE)],
                            checked=False)
        self.assertEqual(self.state("cap-h"), "unsatisfied")

    def test_fail_at_any_age_verdicts_unsatisfied(self):
        cap = self.db.add_capability("cap-i")
        self.db.add_adapter(cap, "postgresql", "declared",
                            evidence=[_obs("FAIL", STALE)],
                            checked=False)
        self.assertEqual(self.state("cap-i"), "unsatisfied")

    def test_skip_at_any_age_verdicts_unknown(self):
        cap = self.db.add_capability("cap-j")
        self.db.add_adapter(cap, "convex", "declared",
                            evidence=[_obs("SKIP", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-j"), "unknown")

    def test_never_observed_declared_seed_verdicts_unknown(self):
        # THE false comfort this migration removes: V173 called this
        # 'unsatisfied' (we know it's absent). We never looked — 'unknown'.
        cap = self.db.add_capability("cap-k")
        self.db.add_adapter(cap, "mysql", "declared", evidence=[])
        self.assertEqual(self.state("cap-k"), "unknown")

    def test_active_unreachable_shadows_stale_pass(self):
        # precedence, debug-verified: when an ACTIVE adapter exists, the
        # CLAIM's epistemics govern — the active row's last PASS is 16d
        # stale, so the verdict is satisfied-stale (a stale claim of
        # satisfaction), NOT 'unreachable': the not-satisfied branch is
        # where unreachable/refused/unknown live. The degraded measurement
        # failure stays visible via degraded_adapters=1 and the argmax
        # (freshest) column.
        cap = self.db.add_capability("cap-l")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[_obs("PASS", STALE)],
                            checked=False)  # aged out
        self.db.add_adapter(cap, "mysql", "degraded",
                            evidence=[_obs("UNREACHABLE", FRESH)],
                            checked=True)
        row = self.db.sql("""
SELECT satisfaction_state, degraded_adapters
FROM nebula.v_capability_satisfaction WHERE capability='cap-l';
""")[0]
        self.assertEqual(row[0], "satisfied-stale")
        self.assertEqual(row[1], 1)

    def test_no_active_adapter_freshest_unreachable_decides(self):
        # with NO active adapter the not-satisfied branch governs and the
        # freshest observation decides: UNREACHABLE → 'unreachable'
        cap = self.db.add_capability("cap-l2")
        self.db.add_adapter(cap, "mysql", "degraded",
                            evidence=[_obs("UNREACHABLE", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-l2"), "unreachable")

    def test_multi_adapter_freshest_decides(self):
        # oldest FAIL + fresher SKIP → freshest observation (SKIP) decides
        cap = self.db.add_capability("cap-m")
        self.db.add_adapter(cap, "postgresql", "declared",
                            evidence=[_obs("FAIL", STALE)],
                            checked=False)
        self.db.add_adapter(cap, "convex", "declared",
                            evidence=[_obs("SKIP", FRESH)],
                            checked=True)
        self.assertEqual(self.state("cap-m"), "unknown")

    # ── structural invariants ───────────────────────────────────────────────

    def test_vocabulary_gate_clean_on_live_data(self):
        cap = self.db.add_capability("cap-n")
        self.db.add_adapter(cap, "postgresql", "active",
                            evidence=[_obs("PASS", FRESH)],
                            checked=True)
        bad = self.db.sql("""
SELECT count(*) FROM nebula.v_capability_satisfaction
WHERE satisfaction_state IS NULL
   OR satisfaction_state NOT IN ('satisfied','satisfied-stale',
                                 'unsatisfied','unreachable',
                                 'refused','unknown');
""")
        self.assertEqual(bad, 0)

    def test_idempotent_reapply(self):
        cap = self.db.add_capability("cap-o")
        self.db.add_adapter(cap, "mysql", "declared", evidence=[])
        with open(V174_PATH) as fh:
            self.db.sql(fh.read())  # second application: no error
        self.assertEqual(self.state("cap-o"), "unknown")


if __name__ == "__main__":
    unittest.main()
