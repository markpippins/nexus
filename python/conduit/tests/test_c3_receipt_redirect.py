"""Hermetic C3 cutover test: staged writer redirection flag (plan 8261639).

Exercises DBAdapter.insert_receipt under CONDUIT_RECEIPT_REDIRECT:

  off      (default) legacy write only — behavior byte-identical to pre-flag
  shadow   legacy write + canonical record forced on (no LILAC_SHADOW flip)
  enforce  canonical write FIRST (R4 outcomes gate the write); the legacy
           synthetic courtesy copy is SKIPPED for in-scope producers once
           the canonical write commits (R2, To Do ca5941ca — Stage D freeze
           requires zero direct writers); conflict/grant-refusal FAILS
           CLOSED (no legacy-only fork)

Isolation (repo convention):
- Legacy surface: vision.receipts is shared-live (test_lifecycle pattern);
  every probe row uses the canary-prefixed id namespace `rec-zz-redirect-`
  and is deleted in teardown.
- Canonical surface: V139 DDL applied into a THROWAWAY schema, wired via
  CONDUIT_LILAC_SCHEMA — the adapter's enforce/shadow paths target it, so
  zero live canonical rows are ever touched.
- Redirect stage registry in the throwaway schema has the seeded
  producers, so grant semantics are the REAL DB-enforced ones.

Run:
  CONDUIT_PG_DSN='host=localhost port=5432 user=pguser password=pgpass dbname=nexus' \
      python3 -m pytest conduit/tests/test_c3_receipt_redirect.py -v
"""
import json
import os
import sys
import unittest
import uuid

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_helpers import (  # noqa: E402
    cleanup_orphaned_test_schemas,
    create_test_schema,
    drop_test_schema,
)
from db_adapter import DBAdapter  # noqa: E402

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    raise RuntimeError("CONDUIT_PG_DSN must be set to run tests (PG is mandatory)")

_ORPHANED = cleanup_orphaned_test_schemas(_DSN)
if _ORPHANED:
    print(f"Cleaned up {_ORPHANED} orphaned test schema(s)", file=sys.stderr)

_V139_SQL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "sql", "V139__lilac_resolution_canonical_persistence.sql",
)
_V140_SQL = _V139_SQL.replace(
    "V139__lilac_resolution_canonical_persistence.sql",
    "V140__receipts_unified_lilac_dual_read.sql")
_V141_SQL = _V139_SQL.replace(
    "V139__lilac_resolution_canonical_persistence.sql",
    "V141__lilac_c6_soak_and_retirement_gate.sql")

# Canary namespace for the shared-live legacy surface (teardown-guaranteed).
RECEIPT_ID_PREFIX = "rec-zz-redirect-"


def _apply_v139(conn, schema: str) -> None:
    with open(_V139_SQL) as f:
        raw = f.read()
    sql = raw.replace("resolution.", f"{schema}.").replace("$resolution$", f"${schema}$")
    with conn.cursor() as cur:
        cur.execute(sql)
    # V142 seed parity: register the python-direct producer the adapter
    # actually declares (nexus-conduit-python) so enforce-mode grants match
    # production. (V142 does the same live.)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.producer_registry "
            f"(producer_id, name, allowed_kinds, contract_version_min, "
            f" contract_version_max, registered_by) "
            f"VALUES ('nexus-conduit-python', 'Conduit Python kernel (test seed)', "
            f" ARRAY['plan_create','planning','implementation','review','review_pass',"
            f"         'review_reject','critique','critique_pass','critique_reject','block',"
            f"         'hold','ccnf_execution','requeued','api_limit','abandoned',"
            f"         'cancelled','plan_block'], 1, 1, 'test-seed') "
            f"ON CONFLICT (producer_id) DO NOTHING"
        )
    conn.commit()


def _apply_sql_reschema(conn, sql_path: str, schema: str) -> None:
    """Apply a migration file into a throwaway schema by prefix-rewriting
    its canonical-schema references (resolution./nebula. — V140's legacy
    branch references to execution./vision. stay LIVE but read-only)."""
    with open(sql_path) as f:
        raw = f.read()
    sql = raw.replace("resolution.", f"{schema}.").replace("nebula.", f"{schema}.")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


class RedirectTestBase(unittest.TestCase):
    """Test-schema DBAdapter + throwaway canonical schema, per test."""

    def setUp(self):
        self._env_stack = []

        self._raw_conn = psycopg2.connect(_DSN)
        self._raw_cur = self._raw_conn.cursor()
        try:
            self.schema_name = create_test_schema(self._raw_conn, "test_redirect")
            self._create_plan_tables()
            self.plan_id = f"zz-redirect-{uuid.uuid4().hex[:8]}"
            now = "2026-09-07T00:00:00Z"
            self._raw_cur.execute(
                "INSERT INTO plans (id, file_name, title, created_at, updated_at) "
                "VALUES (%s, 'redirect-test.md', 'Redirect Test', %s, %s)",
                (self.plan_id, now, now),
            )
            self._raw_conn.commit()
            self.db = DBAdapter(schema=self.schema_name)
        except Exception:
            drop_test_schema(_DSN, self.schema_name)
            self._raw_conn.close()
            raise

        # Throwaway canonical (V139) schema for enforce/shadow paths.
        self.canon_schema = create_test_schema(self._raw_conn, "test_redirect_canon")
        _apply_v139(self._raw_conn, self.canon_schema)
        self._set_env("CONDUIT_LILAC_SCHEMA", self.canon_schema)
        # Tests must never inherit a shadow default from the environment.
        self._pop_env("CONDUIT_LILAC_SHADOW")

        self.addCleanup(self._teardown)

    def _set_env(self, key, value):
        self._env_stack.append((key, os.environ.get(key)))
        os.environ[key] = value

    def _pop_env(self, key):
        self._env_stack.append((key, os.environ.get(key)))
        os.environ.pop(key, None)

    def _create_plan_tables(self):
        """Full conduit-schema bootstrap mirroring tests/test_lifecycle.py —
        DBAdapter._init_db validates the table set on construction."""
        c = self._raw_cur
        c.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, file_name TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
                files_affected TEXT NOT NULL DEFAULT '[]',
                acceptance_criteria TEXT NOT NULL DEFAULT '[]',
                dependencies TEXT NOT NULL DEFAULT '[]',
                prompt_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id),
                role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','claimed','completed','failed',
                        'abandoned','superseded','cancelled','stale','expired')),
                session_id TEXT,
                created_by_receipt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, claimed_at TEXT, closed_at TEXT,
                token_budget INTEGER, tokens_used INTEGER,
                objective TEXT, completion_criteria TEXT,
                owner TEXT NOT NULL DEFAULT '',
                parent_ticket_id TEXT REFERENCES tickets(id),
                spawn_reason TEXT, last_activity TEXT, expires_at TEXT,
                confidence REAL, closure_reason TEXT,
                replacement_of TEXT REFERENCES tickets(id)
            )
        """)
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_open
            ON tickets(plan_id, role) WHERE status = 'open'
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id),
                type TEXT NOT NULL CHECK(type IN (
                    'PLAN_CREATE','IMPLEMENTATION','REVIEW_PASS','REVIEW_REJECT','BLOCK',
                    'PROPOSED','PLANNING','REVIEW','CRITIQUE','CRITIQUE_PASS',
                    'CRITIQUE_REJECT','PLAN_BLOCK','API_LIMIT'
                )),
                agent_role TEXT NOT NULL, session_id TEXT,
                artifact_path TEXT, summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                ticket_id TEXT REFERENCES tickets(id),
                tokens_used INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, agent_role TEXT NOT NULL,
                start_iso TEXT NOT NULL, end_iso TEXT, exit_code INTEGER,
                retries_used INTEGER DEFAULT 0,
                plans_processed TEXT NOT NULL DEFAULT '[]',
                plan_count INTEGER DEFAULT 0, pid INTEGER,
                is_running INTEGER DEFAULT 1, last_activity TEXT,
                model TEXT, fallback_used INTEGER DEFAULT 0,
                cost_usd REAL, created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breaker (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
                tripped INTEGER DEFAULT 0, tripped_at TEXT,
                retry_after INTEGER DEFAULT 1800, error TEXT,
                detail TEXT, source TEXT, fallback_model TEXT,
                paused INTEGER DEFAULT 0, updated_at TEXT
            )
        """)
        c.execute(
            "INSERT INTO circuit_breaker (id, tripped, updated_at) "
            "VALUES (1, 0, '2026-09-07T00:00:00Z') ON CONFLICT (id) DO NOTHING"
        )

    def _teardown(self):
        # 1. Legacy-surface cleanup FIRST (canary namespace, shared-live).
        try:
            cur = self._raw_conn.cursor()
            cur.execute("DELETE FROM vision.receipts WHERE id LIKE %s",
                        (RECEIPT_ID_PREFIX + "%",))
            self._raw_conn.commit()
        except Exception:
            self._raw_conn.rollback()
        # 2. Drop throwaway schemas (canonical rows die with them).
        try:
            drop_test_schema(_DSN, self.canon_schema)
        except Exception:
            pass
        try:
            drop_test_schema(_DSN, self.schema_name)
        except Exception:
            pass
        try:
            self._raw_cur.close()
            self._raw_conn.close()
        except Exception:
            pass
        # 3. Restore environment last.
        for key, old in reversed(self._env_stack):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    # ── helpers ──────────────────────────────────────────────────────

    def _legacy_count(self, receipt_id: str) -> int:
        self._raw_cur.execute("SELECT count(*) FROM vision.receipts WHERE id = %s",
                              (receipt_id,))
        n = self._raw_cur.fetchone()[0]
        self._raw_conn.commit()
        return n

    def _canonical_count(self, receipt_id: str) -> int:
        cur = self._raw_conn.cursor()
        cur.execute(f"SELECT count(*) FROM {self.canon_schema}.receipt "
                    f"WHERE source_receipt_id = %s", (receipt_id,))
        n = cur.fetchone()[0]
        self._raw_conn.commit()
        return n

    def _canonical_row(self, receipt_id: str):
        cur = self._raw_conn.cursor()
        cur.execute(f"SELECT kind, producer_id, contract_version "
                    f"FROM {self.canon_schema}.receipt WHERE source_receipt_id = %s",
                    (receipt_id,))
        row = cur.fetchone()
        self._raw_conn.commit()
        return row

    def _insert(self, receipt_id=None, receipt_type="PLANNING",
                summary="redirect probe", metadata=None):
        rid = receipt_id or (RECEIPT_ID_PREFIX + uuid.uuid4().hex[:12])
        self.db.insert_receipt(
            plan_id=self.plan_id, receipt_type=receipt_type,
            agent_role="engineer", session_id=f"sess-{uuid.uuid4().hex[:6]}",
            ticket_id="", summary=summary,
            metadata=dict(metadata or {}), receipt_id=rid,
        )
        return rid



class TestModeParser(unittest.TestCase):
    """Fail-safe stage parsing — unknown values are OFF, never a guess."""

    def test_default_off(self):
        self.assertEqual(DBAdapter._receipt_redirect_mode(), "off")

    def test_unknown_value_is_off(self):
        os.environ["CONDUIT_RECEIPT_REDIRECT"] = "ENFORC"
        self.addCleanup(os.environ.pop, "CONDUIT_RECEIPT_REDIRECT", None)
        self.assertEqual(DBAdapter._receipt_redirect_mode(), "off")

    def test_case_insensitive(self):
        os.environ["CONDUIT_RECEIPT_REDIRECT"] = "  ENFORCE "
        self.addCleanup(os.environ.pop, "CONDUIT_RECEIPT_REDIRECT", None)
        self.assertEqual(DBAdapter._receipt_redirect_mode(), "enforce")


class TestRedirectOff(RedirectTestBase):

    def test_off_writes_legacy_only(self):
        rid = self._insert()
        self.assertEqual(self._legacy_count(rid), 1)
        self.assertEqual(self._canonical_count(rid), 0,
                         "off mode must not write canonical rows")


class TestRedirectShadow(RedirectTestBase):

    def test_shadow_writes_legacy_and_canonical(self):
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "shadow")
        rid = self._insert()
        self.assertEqual(self._legacy_count(rid), 1)
        row = self._canonical_row(rid)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "planning")
        self.assertEqual(row[1], "nexus-conduit-python",
                         "shadow rows must carry the declaring producer")
        self.assertEqual(row[2], 1)

    def test_shadow_does_not_require_lilac_shadow_env(self):
        """force=True semantics: redirect=shadow alone records canonically."""
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "shadow")
        rid = self._insert()
        self.assertEqual(self._canonical_count(rid), 1)


class TestRedirectEnforce(RedirectTestBase):

    def setUp(self):
        super().setUp()
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "enforce")

    def test_enforce_writes_canonical_only_skips_legacy_courtesy(self):
        """R2 (To Do ca5941ca): enforce + in-scope producer writes the
        canonical twin and SKIPS the legacy courtesy copy — the freeze
        gate observes zero direct writers on vision.receipts."""
        rid = self._insert()
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0,
                         "no legacy courtesy copy in enforce mode")

    def test_enforce_conflict_fails_closed_no_legacy_fork(self):
        rid = self._insert(summary="original payload")
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0,
                         "enforce writes canonical only — no legacy row at all")
        # Replay: same source id, different payload → R4 conflict. It must
        # raise without writing anything anywhere.
        with self.assertRaises(Exception):
            self._insert(receipt_id=rid, summary="DIVERGENT payload")
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0,
                         "conflict must fail closed — no legacy-only write")

    def test_enforce_grant_refusal_fails_closed(self):
        """peb-srv holds admission only — PLANNING must be refused (Q3)."""
        rid = RECEIPT_ID_PREFIX + uuid.uuid4().hex[:12]
        with self.assertRaises(Exception):
            self._insert(receipt_id=rid,
                         metadata={"producer_id": "peb-srv"})
        self.assertEqual(self._canonical_count(rid), 0)
        self.assertEqual(self._legacy_count(rid), 0,
                         "grant refusal must fail closed — no legacy write")

    def test_enforce_unmapped_type_legacy_only(self):
        """PROPOSED has no ratified kind — documented divergence, not drift."""
        rid = self._insert(receipt_type="PROPOSED")
        self.assertEqual(self._legacy_count(rid), 1)
        self.assertEqual(self._canonical_count(rid), 0)

    def test_per_producer_allowlist_degrades_to_off(self):
        """Q-A containment: non-listed producers stay in off mode."""
        self._set_env("CONDUIT_RECEIPT_REDIRECT_PRODUCERS", "conduit-mcp")
        rid = self._insert()  # default provenance = nexus-conduit-python
        self.assertEqual(self._legacy_count(rid), 1)
        self.assertEqual(self._canonical_count(rid), 0)

    def test_per_producer_allowlist_honors_listed(self):
        self._set_env("CONDUIT_RECEIPT_REDIRECT_PRODUCERS",
                      "conduit-mcp,nexus-conduit-python")
        rid = self._insert()
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0,
                         "listed producer in enforce mode skips legacy")


class TestEnforceSkipsLegacyCourtesy(RedirectTestBase):
    """R2 (To Do ca5941ca): the enforce-mode legacy courtesy copy is gone —
    canonical success means NO legacy attempt at all (the freeze gate
    observes zero direct writers). The legacy_shadow_failed observability
    class remains for historical rows; the drift checker coverage below
    seeds it directly."""

    def setUp(self):
        super().setUp()
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "enforce")
        # V141 soak surface inside the throwaway schema (schema-rewritten).
        _apply_sql_reschema(self._raw_conn, _V141_SQL, self.canon_schema)

    def test_enforce_success_writes_no_legacy_and_records_nothing(self):
        rid = self._insert()
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0)
        cur = self._raw_conn.cursor()
        cur.execute(
            f"SELECT report->'legacy_shadow_failed' "
            f"FROM {self.canon_schema}.soak_evidence "
            f"WHERE evidence_date = CURRENT_DATE")
        row = cur.fetchone()
        self._raw_conn.commit()
        events = []
        if row is not None and row[0] is not None:
            import json as _json
            events = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
        self.assertFalse(any(e.get("source_receipt_id") == rid for e in events),
                         "skipped legacy write must not record a shadow failure")

    def test_drift_report_surfaces_class(self):
        """F2 end-to-end: the drift checker surfaces legacy_shadow_failed
        from the soak surface — a clean legacy scan must not mask it.
        (Seeded directly: the courtesy-copy failure mode that used to
        record these events no longer exists.)"""
        import json as _json
        import lilac_drift
        rid = RECEIPT_ID_PREFIX + uuid.uuid4().hex[:12]
        cur = self._raw_conn.cursor()
        cur.execute(
            f"INSERT INTO {self.canon_schema}.soak_evidence "
            f"(evidence_date, report, green, recorded_by) VALUES "
            f"(CURRENT_DATE, %s, false, 'redirect-test') "
            f"ON CONFLICT (evidence_date) DO UPDATE SET report = EXCLUDED.report",
            (_json.dumps({"legacy_shadow_failed": [
                {"source_receipt_id": rid}]}),))
        self._raw_conn.commit()
        report = lilac_drift.check_legacy_surface(self._raw_conn,
                                                  schema=self.canon_schema)
        self.assertEqual(report["classes"].get("legacy_shadow_failed"), 1,
                         f"expected the recorded event, got {report['classes']}")
        self.assertTrue(any(
            r.get("class") == "legacy_shadow_failed"
            and r.get("source_receipt_id") == rid
            for r in report["rows"]))


class TestEnforceFanOutOnCanonical(RedirectTestBase):
    """Review F3: caller-owned fan-out (advanceTicketsOnReceipt /
    create_next_tickets) reads nebula.receipts_unified. The V140 canonical
    branch surfaces canonical rows with no legacy twin — proven here with
    the REAL V140 view applied in the throwaway schema."""

    def setUp(self):
        super().setUp()
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "enforce")
        _apply_sql_reschema(self._raw_conn, _V140_SQL, self.canon_schema)

    def _unified_types(self, plan_id):
        cur = self._raw_conn.cursor()
        cur.execute(
            f"SELECT type FROM {self.canon_schema}.receipts_unified "
            f"WHERE plan_id = %s ORDER BY created_at ASC", (plan_id,))
        types = [r[0] for r in cur.fetchall()]
        self._raw_conn.commit()
        return types

    def test_canonical_row_surfaces_unified_when_legacy_skipped(self):
        # Canonical write succeeds; no legacy row is attempted (R2) — the
        # V140 unified view (the fan-out's source) surfaces the canonical
        # row despite the missing legacy twin.
        rid = self._insert()
        self.assertEqual(self._canonical_count(rid), 1)
        self.assertEqual(self._legacy_count(rid), 0)
        # The V140 unified view — the fan-out's source — surfaces the
        # canonical row despite the missing legacy twin.
        self.assertIn("PLANNING", self._unified_types(self.plan_id),
                      "canonical row must surface through the unified "
                      "projection when no legacy twin exists")


class TestShadowProducerPassthrough(RedirectTestBase):
    """Review F4: shadow stamping passes the declaring producer through
    UNFILTERED — the DB grant trigger is the per-write authority. A
    payload declaring an UNREGISTERED producer must be refused by the
    trigger (fail-closed), never silently re-labeled as the worker lane."""

    def setUp(self):
        super().setUp()
        self._set_env("CONDUIT_RECEIPT_REDIRECT", "shadow")

    def test_unregistered_producer_refused_not_relabeled(self):
        """peb-srv holds admission only: declaring it for PLANNING must be
        refused (P0004) by the grant trigger — not re-stamped as
        nexus-execution-worker by any code-side whitelist."""
        rid = RECEIPT_ID_PREFIX + uuid.uuid4().hex[:12]
        self.db.insert_receipt(
            plan_id=self.plan_id, receipt_type="PLANNING",
            agent_role="engineer", session_id="sess-shadow-f4",
            ticket_id="", summary="F4 passthrough probe",
            metadata={"producer_id": "peb-srv"}, receipt_id=rid,
        )
        self.assertEqual(self._legacy_count(rid), 1,
                         "legacy write is authoritative in shadow mode")
        self.assertEqual(self._canonical_count(rid), 0,
                         "unregistered producer/kind pair must be refused "
                         "by the trigger, never silently re-labeled")


if __name__ == "__main__":
    unittest.main()
