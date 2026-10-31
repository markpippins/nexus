"""Receipt-integrity regression tests (review 48f213bc, F2 + F3).

F2 — blank artifact_path must never persist as if a path were claimed:
  * the insert_receipt write funnel normalizes ""/whitespace to SQL NULL,
  * the V206 CHECK guard makes a re-introduced blank claim impossible,
  * the V206 backfill renormalizes legacy blank rows,
  * the evidence validator rejects a blank claim BEFORE filesystem
    resolution (Path("") is Path("."), whose exists() is True — the
    pass-open hazard the review named).

F3 — one NON-projection IMPLEMENTATION receipt per (request_id, attempt_id,
type); the lineage='conduit' projection row stays legal (the V140 unified
view and the ticket fan-out read only that branch) but now carries the
caller's attempt linkage so consumers can dedupe on (attempt_id, type).

Test channels (all hermetic):
  * Adapter-level: DBAdapter.__new__ + injected stub connection (SQL/params
    captured — no live surfaces touched).
  * Validator-level: pure functions against a temp contract root.
  * Store-level: V206 applied to THROWAWAY prefix-rewritten copies of
    execution.receipts / vision.receipts (convention from
    test_c5_projection_surface / test_c3_receipt_redirect).
"""

import contextlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from db_adapter import DBAdapter  # noqa: E402
from execution_worker import (  # noqa: E402
    WORK_EVIDENCE_MARKER,
    capture_artifact_baseline,
    validate_work_evidence,
)

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    raise RuntimeError("CONDUIT_PG_DSN must be set to run tests (PG is mandatory)")

_V206_DDL = (Path(__file__).resolve().parents[3] / "sql" / "V206__receipt_integrity_f2_f3_guards.sql").read_text()
_V207_DDL = (Path(__file__).resolve().parents[3] / "sql" / "V207__execution_receipt_impl_uniqueness.sql").read_text()


class _Cursor:
    """Minimal cursor: row + rowcount, dict variant on demand."""

    def __init__(self, row=None):
        self._row = row
        self.rowcount = 1 if row else 0

    def fetchone(self):
        return self._row

    def dict_fetchone(self):
        if isinstance(self._row, dict):
            return self._row
        return None


class _CapturingConnection:
    """Handles the statements insert_receipt issues in the request-backed
    path; records params for assertions."""

    def __init__(self, known_attempts=()):
        self.known_attempts = set(known_attempts)
        self.sql = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "SELECT id FROM execution.requests WHERE source_plan_id" in sql:
            return _Cursor({"id": "req-1"})
        if "SELECT 1 FROM execution.attempts WHERE id = %s" in sql:
            attempt = params[0] if params else None
            return _Cursor((1,) if attempt in self.known_attempts else None)
        return _Cursor(None)

    def commit(self):
        self.commits += 1


def _request_backed_db(known_attempts=()):
    """DBAdapter with only _get_connection injected — insert_receipt runs
    through its real code path against the capturing stub."""
    db = DBAdapter.__new__(DBAdapter)
    connection = _CapturingConnection(known_attempts)

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    return db, connection


def _pop_writer_env():
    """Never inherit a writer-mode default from the environment."""
    for key in ("CONDUIT_RECEIPT_REDIRECT", "CONDUIT_CANARY_ENFORCEMENT",
                "CONDUIT_LILAC_SCHEMA", "CONDUIT_LILAC_SHADOW"):
        os.environ.pop(key, None)


def _captured_insert(connection):
    insert_calls = [
        (sql, params) for sql, params in connection.sql
        if "INSERT INTO execution.receipts" in sql
    ]
    assert len(insert_calls) == 1, "funnel must insert exactly once"
    return insert_calls[0]


class F2WriteFunnelTest(unittest.TestCase):
    """insert_receipt persists no-claim as NULL; a blank claim impossible."""

    def setUp(self):
        _pop_writer_env()

    def _insert(self, artifact_path):
        db, connection = _request_backed_db()
        db.insert_receipt(
            plan_id="plan-1", receipt_type="IMPLEMENTATION",
            agent_role="engineer", session_id="sess-1", ticket_id="",
            summary="F2 probe", artifact_path=artifact_path,
            metadata={"attempt_id": "attempt-1"},
            receipt_id="rec-f2f3-" + uuid.uuid4().hex[:12],
        )
        return connection

    def _captured_exec_meta(self, connection):
        sql, params = _captured_insert(connection)
        self.assertIn("attempt_id, type", sql, "attempt column must be parameterized")
        return json.loads(params[5])

    def test_blank_artifact_path_persists_null(self):
        exec_meta = self._captured_exec_meta(self._insert(""))
        self.assertIsNone(exec_meta["artifact_path"])

    def test_whitespace_artifact_path_persists_null(self):
        exec_meta = self._captured_exec_meta(self._insert("   "))
        self.assertIsNone(exec_meta["artifact_path"])

    def test_real_artifact_path_is_preserved(self):
        exec_meta = self._captured_exec_meta(
            self._insert("docs/promotion-flow/wc-rules.md"))
        self.assertEqual(exec_meta["artifact_path"], "docs/promotion-flow/wc-rules.md")


class F3MirrorAttemptLinkageTest(unittest.TestCase):
    """insert_receipt carries metadata attempt_id onto the projection row.

    The review-48f213bc duplicate (8261655 attempt bacef8dd) rode in with
    attempt_id NULL on the mirrored row — invisible to any attempt-anchored
    dedupe. The linkage makes (attempt_id, type) dedupable.
    """

    def setUp(self):
        _pop_writer_env()

    def _insert(self, metadata, known_attempts=()):
        db, connection = _request_backed_db(known_attempts)
        db.insert_receipt(
            plan_id="plan-1", receipt_type="IMPLEMENTATION",
            agent_role="engineer", session_id="sess-1", ticket_id="",
            summary="F3 probe", metadata=metadata,
            receipt_id="rec-f2f3-" + uuid.uuid4().hex[:12],
        )
        return _captured_insert(connection)[1]

    def test_known_attempt_is_carried_onto_mirror(self):
        params = self._insert(
            {"attempt_id": "attempt-1"}, known_attempts=["attempt-1"])
        self.assertEqual(params[1], "attempt-1")

    def test_unresolvable_attempt_is_dropped_not_guessed(self):
        params = self._insert(
            {"attempt_id": "no-such-attempt"}, known_attempts=["attempt-1"])
        self.assertIsNone(params[1])

    def test_no_attempt_metadata_leaves_mirror_attempt_null(self):
        params = self._insert(
            {"work_request_id": "wr-1"}, known_attempts=["attempt-1"])
        self.assertIsNone(params[1])


class F2EvidenceValidatorTest(unittest.TestCase):
    """Blank claims are rejected BEFORE any filesystem resolution."""

    class _StubDB:
        def has_builder_work_evidence(self, *args, **kwargs):
            return True

    def test_blank_expected_path_rejected(self):
        with _TempRoot() as root:
            req = {
                "metadata": {"evidence_contract": {
                    "expected_files": ["", "exists.txt"],
                    "allowed_root": str(root),
                }},
                "artifacts": {"produced_files": []},
            }
            with self.assertRaisesRegex(
                    Exception, "blank artifact path in evidence contract"):
                validate_work_evidence(
                    self._StubDB(), req, output="", plan_id=None,
                    work_request_id="wr-1", attempt_id="attempt-1",
                    session_id="sess-1",
                )

    def test_blank_reported_artifact_rejected(self):
        with _TempRoot() as root:
            req = {
                "metadata": {"evidence_contract": {"allowed_root": str(root)}},
                "artifacts": {"produced_files": []},
            }
            marker = WORK_EVIDENCE_MARKER + json.dumps({
                "work_request_id": "wr-1",
                "attempt_id": "attempt-1",
                "session_id": "sess-1",
                "artifacts": ["", "exists.txt"],
            })
            with self.assertRaisesRegex(
                    Exception, "blank artifact path in work evidence marker"):
                validate_work_evidence(
                    self._StubDB(), req, output=marker, plan_id=None,
                    work_request_id="wr-1", attempt_id="attempt-1",
                    session_id="sess-1",
                )

    def test_blank_baseline_capture_falls_back_to_null(self):
        with _TempRoot() as root:
            req = {
                "metadata": {"evidence_contract": {
                    "expected_files": ["", "exists.txt"],
                    "allowed_root": str(root),
                }},
                "artifacts": {"produced_files": []},
            }
            baseline = capture_artifact_baseline(req, project_root=str(root))
            self.assertIsNone(baseline[""])
            self.assertIsNotNone(baseline["exists.txt"])


class V206StoreGuardTest(unittest.TestCase):
    """V206 (F2 only) semantics applied to a throwaway vision.receipts copy."""

    VIS = "test_f2f3_vis"

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(_DSN)
        cls.cur = cls.conn.cursor()
        try:
            with cls.conn.cursor() as c:
                c.execute(f"CREATE SCHEMA {cls.VIS}")
                c.execute(f"""
                    CREATE TABLE {cls.VIS}.receipts (
                        id TEXT PRIMARY KEY,
                        plan_id TEXT,
                        type TEXT,
                        agent_role TEXT,
                        session_id TEXT,
                        ticket_id TEXT,
                        summary TEXT NOT NULL DEFAULT '',
                        artifact_path TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{{}}',
                        tokens_used INTEGER DEFAULT 0,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )""")
            # V206 convention: legacy blank row predates the migration —
            # the backfill must renormalize it to NULL.
            with cls.conn.cursor() as c:
                c.execute(
                    f"INSERT INTO {cls.VIS}.receipts (id, type) VALUES ('br-1', 'PLANNING')"
                )
                c.execute(
                    f"UPDATE {cls.VIS}.receipts SET artifact_path = ' ' WHERE id = 'br-1'"
                )
            rewritten = _V206_DDL.replace("vision.", f"{cls.VIS}.")
            with cls.conn.cursor() as c:
                c.execute(rewritten)
            cls.conn.commit()
        except Exception:
            cls.conn.rollback()
            cls._drop()
            raise

    @classmethod
    def _drop(cls):
        with cls.conn.cursor() as c:
            c.execute(f"DROP SCHEMA IF EXISTS {cls.VIS} CASCADE")
        cls.conn.commit()
        cls.conn.close()

    @classmethod
    def tearDownClass(cls):
        cls._drop()

    def test_f2_backfill_renormalized_legacy_blank_row(self):
        self.cur.execute(
            f"SELECT artifact_path FROM {self.VIS}.receipts WHERE id = 'br-1'")
        self.assertIsNone(self.cur.fetchone()[0])

    def test_f2_check_refuses_blank_claim_after_backfill(self):
        with self.assertRaises(psycopg2.IntegrityError):
            self.cur.execute(
                f"INSERT INTO {self.VIS}.receipts (id, type, artifact_path) "
                f"VALUES ('br-bad', 'PLANNING', '')")
        self.conn.rollback()

    def test_f2_real_claim_still_allowed(self):
        try:
            self.cur.execute(
                f"INSERT INTO {self.VIS}.receipts (id, type, artifact_path) "
                f"VALUES ('br-ok', 'PLANNING', 'docs/x.md')")
        finally:
            self.conn.rollback()


class V207ImplIndexTest(unittest.TestCase):
    """V207 (F3) index semantics on a throwaway execution.receipts copy.

    The index is scoped as reviewed: one NON-projection IMPLEMENTATION
    receipt per (request_id, attempt_id). Other receipt types carry
    historical multi-receipt noise (re-reviews, retry cycles) that is
    explicitly out of F3's scope and must remain unindexed.
    """

    EXE = "test_f2f3_exe"

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(_DSN)
        cls.cur = cls.conn.cursor()
        # Previous pre-existing duplicate group must be in place BEFORE the
        # migration so the preflight refusal is proven against real state.
        try:
            c = cls.conn.cursor()
            c.execute(f"CREATE SCHEMA {cls.EXE}")
            c.execute(f"""
                CREATE TABLE {cls.EXE}.receipts (
                    id TEXT PRIMARY KEY,
                    attempt_id TEXT,
                    request_id TEXT,
                    type TEXT,
                    agent_role TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    metadata JSONB NOT NULL DEFAULT '{{}}',
                    lineage_source TEXT,
                    lineage_original_id TEXT,
                    issued_at TIMESTAMPTZ DEFAULT now()
                )""")
            c.execute(f"""
                INSERT INTO {cls.EXE}.receipts
                    (id, attempt_id, request_id, type)
                VALUES ('ex-pre-1', 'att-pre', 'req-1', 'IMPLEMENTATION'),
                       ('ex-pre-2', 'att-pre', 'req-1', 'IMPLEMENTATION')""")
            cls.conn.commit()
            rewritten = _V207_DDL.replace("execution.", f"{cls.EXE}.")
            refused = None
            try:
                c.execute(rewritten)
            except psycopg2.Error as exc:
                refused = exc
            finally:
                cls.conn.rollback()
            if refused is None:
                raise AssertionError(
                    "V207 preflight must refuse an existing duplicate group")
            cls.preflight_refused = "V207 PREFLIGHT FAIL" in str(refused)
            # Reconciliation in the test: disposition (drop) the dup group.
            c.execute(f"DELETE FROM {cls.EXE}.receipts WHERE id = 'ex-pre-2'")
            c.execute(rewritten)
            cls.conn.commit()
        except Exception:
            cls.conn.rollback()
            cls._drop()
            raise

    @classmethod
    def _drop(cls):
        with cls.conn.cursor() as c:
            c.execute(f"DROP SCHEMA IF EXISTS {cls.EXE} CASCADE")
        cls.conn.commit()
        cls.conn.close()

    @classmethod
    def tearDownClass(cls):
        cls._drop()

    def test_preflight_refused_existing_duplicate_group(self):
        self.assertTrue(self.preflight_refused)

    def test_f3_unique_index_refuses_non_projection_duplicate(self):
        with self.assertRaises(psycopg2.IntegrityError):
            self.cur.execute(f"""
                INSERT INTO {self.EXE}.receipts
                    (id, attempt_id, request_id, type)
                VALUES ('ex-1', 'att-1', 'req-1', 'IMPLEMENTATION'),
                       ('ex-2', 'att-1', 'req-1', 'IMPLEMENTATION')""")
        self.conn.rollback()

    def test_f3_other_types_are_not_indexed(self):
        try:
            self.cur.execute(f"""
                INSERT INTO {self.EXE}.receipts
                    (id, attempt_id, request_id, type)
                VALUES ('ex-5', 'att-3', 'req-2', 'REVIEW_REJECT'),
                       ('ex-6', 'att-3', 'req-2', 'REVIEW_REJECT')""")
        finally:
            self.conn.rollback()

    def test_f3_projection_duplicate_remains_legal(self):
        try:
            self.cur.execute(f"""
                INSERT INTO {self.EXE}.receipts
                    (id, attempt_id, request_id, type, lineage_source,
                     lineage_original_id)
                VALUES ('ex-3', 'att-2', 'req-1', 'IMPLEMENTATION', 'conduit',
                        'rec-f3-projection-1'),
                       ('ex-4', 'att-2', 'req-1', 'IMPLEMENTATION', 'conduit',
                        'rec-f3-projection-2')""")
        finally:
            self.conn.rollback()

    def test_f3_single_direct_write_allowed(self):
        try:
            self.cur.execute(f"""
                INSERT INTO {self.EXE}.receipts
                    (id, attempt_id, request_id, type)
                VALUES ('ex-7', 'att-4', 'req-1', 'IMPLEMENTATION')""")
        finally:
            self.conn.rollback()


class _TempRoot:
    """Temp directory context manager for the evidence fixtures."""

    def __init__(self):
        self._path = None

    def __enter__(self):
        import tempfile
        self._path = Path(tempfile.mkdtemp(prefix="f2f3-"))
        (self._path / "exists.txt").write_text("placeholder")
        return self._path

    def __exit__(self, *exc):
        import shutil
        shutil.rmtree(self._path, ignore_errors=True)
        return False


if __name__ == "__main__":
    unittest.main()
