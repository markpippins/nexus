"""Positive builder work evidence must precede implementation settlement.

Plan 8261664. W-A 8261653 / W-B 8261654 showed that a zero process exit
code and an IMPLEMENTATION receipt could be produced by an attempt whose
harness emitted no work at all. These tests pin the three-outcome split:
process outcome (preserved verbatim), work outcome (``work_evidence``),
and governance (settlement).

The PostgreSQL test creates a throwaway DATABASE (not a schema) because
the adapter deliberately writes literal ``execution.*`` relations; it
never touches the live execution schema.
"""

import contextlib
import json
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import execution_worker as worker  # noqa: E402
import executor_cloud  # noqa: E402
from db_adapter import DBAdapter, ReviewPassReconciliationError  # noqa: E402
from work_request_factory import work_evidence_contract  # noqa: E402

try:
    import psycopg2
except ImportError:  # pragma: no cover - psycopg2 is a runtime dependency
    psycopg2 = None

DSN = os.environ.get(
    "CONDUIT_PG_DSN",
    "postgresql://pguser:pgpass@localhost:5432/nexus",
)
MIGRATION_025 = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "typescript", "conduit-mcp", "migrations",
    "025-execution-schema.sql",
))


# ── pure contract / extraction helpers ──────────────────────────────

def test_work_evidence_contract_projects_declared_target_files():
    contract = work_evidence_contract({
        "id": "wr-1",
        "artifacts": {"produced_files": [
            {"path": "python/conduit/execution_worker.py"},
            {"path": "python/conduit/db_adapter.py"},
            {"path": "python/conduit/db_adapter.py"},  # de-duplicated
        ]},
        "success_criteria": {
            "completion_conditions": [{"condition": "tests pass", "evaluator": "reviewer"}],
            "acceptance_tests": ["pytest tests/test_execution_work_evidence.py"],
            "validation_rules": ["no schema change"],
        },
    })

    assert contract["target_files"] == [
        "python/conduit/execution_worker.py",
        "python/conduit/db_adapter.py",
    ]
    assert contract["requires"]["kind"] == "target_artifact_or_reviewable_reference"
    assert contract["completion_conditions"] == ["tests pass"]
    assert contract["acceptance_tests"] == ["pytest tests/test_execution_work_evidence.py"]


def test_work_evidence_contract_falls_back_to_output_for_no_artifacts():
    contract = work_evidence_contract({"id": "wr-legacy", "artifacts": {}})
    assert contract["target_files"] == []
    assert contract["requires"]["kind"] == "substantive_output_or_reviewable_reference"
    assert "output" in contract["requires"]["accepted_evidence"]


def test_extract_reviewable_refs_finds_pr_commit_and_agent_record():
    record = "123e4567-e89b-12d3-a456-426614174000"
    text = (
        f"Opened https://github.com/owner/repo/pull/810 and committed "
        f"commit 815a959aa. Record agent_record:{record}. "
        f"Duplicate https://github.com/owner/repo/pull/810"
    )
    refs = executor_cloud.extract_reviewable_refs(text)
    kinds = {ref["kind"] for ref in refs}
    assert kinds == {"pr", "commit", "agent_record"}
    assert len(refs) == 3
    assert f"agent_record:{record}" in [ref["ref"] for ref in refs]


@pytest.mark.parametrize(
    "text",
    [
        "permission denied: cannot write file",
        "tool execution rejected by policy",
        "read-only file system",
    ],
)
def test_detect_evidence_rejection(text):
    assert executor_cloud.detect_evidence_rejection(text)["signal"] in text


def test_detect_evidence_rejection_none_for_normal_output():
    assert executor_cloud.detect_evidence_rejection("wrote 3 files, tests pass") is None


# ── evidence evaluation ─────────────────────────────────────────────

def _contract(target_files=None):
    return work_evidence_contract({
        "id": "wr-1",
        "artifacts": {"produced_files": [{"path": p} for p in (target_files or [])]},
    })


def _evaluate(tmp_path, stdout, stderr="", target_files=None, baseline=None, error=""):
    return worker._evaluate_work_evidence(
        contract=_contract(target_files),
        baseline=baseline or {},
        process_outcome={"returncode": 0},
        stdout=stdout,
        stderr=stderr,
        error=error,
        root=str(tmp_path),
        plan_id="8261664",
        request_id="request-1",
        attempt_id="attempt-1",
        session_id="session-1",
        work_request_id="wr-1",
    )


def test_empty_output_is_evidence_failed_even_at_exit_zero(tmp_path):
    evidence = _evaluate(tmp_path, "", target_files=["out.txt"])
    assert evidence["status"] == "evidence_failed"
    assert evidence["reason"] == "empty_output"
    assert evidence["valid"] is False
    assert evidence["process_exit_code"] == 0
    assert evidence["output"]["length_chars"] == 0


def test_rejected_tool_execution_is_evidence_failed(tmp_path):
    evidence = _evaluate(
        tmp_path,
        "I finished the work.\npermission denied writing src/app.py\n",
        target_files=["src/app.py"],
    )
    assert evidence["status"] == "evidence_failed"
    assert evidence["reason"] == "tool_execution_rejected"
    assert evidence["rejection"]["signal"] == "permission denied"


def test_declared_target_artifact_change_is_valid(tmp_path):
    target = tmp_path / "out.txt"

    # Create the file between snapshot and evaluation.
    baseline = worker._snapshot_target_files(["out.txt"], str(tmp_path))
    target.write_text("created\n")
    evidence = _evaluate(
        tmp_path,
        "implemented the plan\n",
        target_files=["out.txt"],
        baseline=baseline,
    )
    assert evidence["status"] == "valid"
    assert evidence["reason"] == "artifact_changed"
    assert evidence["artifacts"]["changes"][0]["path"] == "out.txt"
    assert evidence["artifacts"]["changes"][0]["created"] is True


def test_declared_target_without_change_is_evidence_failed(tmp_path):
    (tmp_path / "out.txt").write_text("unchanged\n")
    baseline = worker._snapshot_target_files(["out.txt"], str(tmp_path))
    evidence = _evaluate(
        tmp_path,
        "I claim I did the work\n",
        target_files=["out.txt"],
        baseline=baseline,
    )
    assert evidence["status"] == "evidence_failed"
    assert evidence["reason"] == "no_reviewable_evidence"


def test_reviewable_reference_satisfies_declared_targets(tmp_path):
    baseline = worker._snapshot_target_files(["out.txt"], str(tmp_path))
    evidence = _evaluate(
        tmp_path,
        "PR: https://github.com/owner/repo/pull/810\n",
        target_files=["out.txt"],
        baseline=baseline,
    )
    assert evidence["status"] == "valid"
    assert evidence["reason"] == "reviewable_reference"
    assert evidence["references"][0]["kind"] == "pr"


def test_partial_substantive_output_is_valid_without_declared_files(tmp_path):
    evidence = _evaluate(tmp_path, "partial work done\n")
    assert evidence["status"] == "valid"
    assert evidence["reason"] == "substantive_output"


# ── worker settlement integration ──────────────────────────────────

class _EmptyCursor:
    rowcount = 1
    description = []

    def dict_fetchall(self):
        return []

    def dict_fetchone(self):
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _EvidenceConn:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        self.db.statements.append((sql, params))
        if "SET status = 'FAILED'" in sql and self.db.request_status == "READY":
            self.db.request_status = "FAILED"
        return _EmptyCursor()

    def commit(self):
        pass


class _EvidenceDB:
    """Minimal DBAdapter stand-in for the worker settlement path."""

    def __init__(self):
        self.attempt_id = "attempt-ev-1"
        self.attempt_status = None
        self.attempt_exit_code = None
        self.attempt_result = {}
        self.request_status = "READY"
        self.session_id = None
        self.session_exit_code = None
        self.execution_receipts = {}
        self.plan_receipts = {}
        self.closed_tickets = []
        self.advanced = []
        self.lease_release_calls = 0
        self.statements = []
        self.finalized = 0

    def acquire_lease(self, **kwargs):
        return {"id": "lease-ev-1"}

    def create_session(self, session_id, role, plan_ids):
        self.session_id = session_id

    def close_worker_session(self, session_id, exit_code):
        self.session_exit_code = exit_code
        return True

    def claim_ticket(self, plan_id, role, session_id):
        return "builder-ticket-ev-1"

    def create_attempt(self, **kwargs):
        self.attempt_result = dict(kwargs.get("result") or {})
        return {"id": self.attempt_id}

    def start_attempt(self, attempt_id):
        return True

    def add_work_request(self, *args, **kwargs):
        pass

    def complete_attempt(self, attempt_id, status, exit_code=None, result=None, error=None):
        self.attempt_status = status
        self.attempt_exit_code = exit_code
        self.attempt_result = dict(result or {})
        return True

    def update_attempt_result(self, attempt_id, result):
        self.attempt_result = dict(result)
        return True

    def issue_execution_receipt(self, attempt_id, request_id, receipt_type, **kwargs):
        self.execution_receipts[(attempt_id, receipt_type)] = {
            "attempt_id": attempt_id, "type": receipt_type,
        }
        return self.execution_receipts[(attempt_id, receipt_type)]

    def has_attempt_execution_receipt(self, attempt_id, request_id):
        return (attempt_id, "IMPLEMENTATION") in self.execution_receipts

    def mark_request_completed_after_attempt(self, attempt_id, request_id):
        if (self.attempt_status, self.attempt_exit_code) != ("SUCCEEDED", 0):
            return False
        work_evidence = self.attempt_result.get("work_evidence")
        if work_evidence is not None and work_evidence.get("status") != "valid":
            return False
        self.request_status = "COMPLETED"
        return True

    def release_lease(self, lease_id):
        self.lease_release_calls += 1
        return True

    def insert_receipt(self, *, receipt_id=None, **receipt):
        self.plan_receipts.setdefault(receipt_id, receipt)

    def has_plan_receipt(self, plan_id, receipt_id, receipt_type):
        receipt = self.plan_receipts.get(receipt_id)
        return bool(receipt and receipt.get("receipt_type") == receipt_type)

    def close_ticket(self, plan_id, role, session_id, status):
        self.closed_tickets.append((plan_id, role, status))

    def advance_cursor(self, *args):
        self.advanced.append(args)

    def create_next_tickets(self, *args, **kwargs):
        return 1

    def ensure_reviewer_ticket_completion_criteria(self, *args):
        return True

    def get_pending_governance_finalizations(self, limit=100):
        return []

    def get_unsettled_evidence_failures(self, limit=100):
        if (self.attempt_result.get("work_evidence") or {}).get("status") == "evidence_failed":
            governance = (self.attempt_result.get("governance_finalization") or {})
            if governance.get("status") == "evidence_failed" and self.request_status == "FAILED":
                return []  # already settled
            return [{
                "attempt_id": self.attempt_id,
                "request_id": "request-1",
                "result": dict(self.attempt_result),
                "attempt_status": self.attempt_status,
                "exit_code": self.attempt_exit_code,
                "source_plan_id": "8261664",
                "request_status": self.request_status,
            }]
        return []

    @contextlib.contextmanager
    def _get_connection(self):
        yield _EvidenceConn(self)


def _run_worker(monkeypatch, db, req, outcome):
    request = {"id": "request-1", "source_plan_id": "8261664", "title": "evidence"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_a, **_k: req)
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_a: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_a, **_k: outcome)
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda *_a, **_k: None)
    return worker._process_one(db, request, "test-executor", False)


def test_worker_empty_output_settles_evidence_failed_without_implementation(monkeypatch):
    db = _EvidenceDB()
    result = _run_worker(
        monkeypatch, db,
        {"id": "wr-1", "metadata": {"model": "", "harness": "opencode"}},
        executor_cloud.HarnessExecutionResult(0, "", ""),
    )

    assert result == "evidence-failed"
    # Process outcome preserved verbatim.
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.session_exit_code == 0
    # Work outcome is separate and failed.
    assert db.attempt_result["work_evidence"]["status"] == "evidence_failed"
    assert db.attempt_result["work_evidence"]["reason"] == "empty_output"
    # Governance refused IMPLEMENTATION/COMPLETED.
    assert db.request_status == "FAILED"
    assert "IMPLEMENTATION" not in {t for _, t in db.execution_receipts}
    assert any(r.get("receipt_type") == "BLOCK" for r in db.plan_receipts.values())
    assert db.closed_tickets == [("8261664", "builder", "failed")]


def test_worker_rejected_output_settles_evidence_failed(monkeypatch):
    db = _EvidenceDB()
    result = _run_worker(
        monkeypatch, db,
        {"id": "wr-1", "metadata": {"model": "", "harness": "opencode"}},
        executor_cloud.HarnessExecutionResult(
            0, "trying to write\ntool execution rejected\n", ""
        ),
    )

    assert result == "evidence-failed"
    assert db.attempt_result["work_evidence"]["reason"] == "tool_execution_rejected"
    assert db.request_status == "FAILED"


def test_worker_valid_artifact_evidence_completes(monkeypatch, tmp_path):
    target = tmp_path / "out.txt"
    db = _EvidenceDB()
    req = {
        "id": "wr-1",
        "metadata": {"model": "", "harness": "opencode"},
        "artifacts": {"produced_files": [{"path": "out.txt"}]},
        "success_criteria": {},
    }
    monkeypatch.setattr(worker, "PROJECT_ROOT", str(tmp_path))

    def launch(*_args, **_kwargs):
        target.write_text("implemented\n")
        return executor_cloud.HarnessExecutionResult(0, "implemented the plan\n", "")

    request = {"id": "request-1", "source_plan_id": "8261664", "title": "evidence"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_a, **_k: req)
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_a: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", launch)
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda *_a, **_k: None)

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.attempt_result["work_evidence"]["status"] == "valid"
    assert db.attempt_result["work_evidence"]["reason"] == "artifact_changed"
    assert db.request_status == "COMPLETED"
    assert ("attempt-ev-1", "IMPLEMENTATION") in db.execution_receipts


def test_worker_partial_output_completes_without_declared_files(monkeypatch):
    db = _EvidenceDB()
    result = _run_worker(
        monkeypatch, db,
        {"id": "wr-1", "metadata": {"model": "", "harness": "opencode"}},
        executor_cloud.HarnessExecutionResult(0, "partial work done\n", ""),
    )
    assert result == "completed"
    assert db.attempt_result["work_evidence"]["reason"] == "substantive_output"
    assert db.request_status == "COMPLETED"


def test_retry_settlement_is_idempotent_and_creates_one_block(monkeypatch):
    db = _EvidenceDB()
    db.attempt_status = "SUCCEEDED"
    db.attempt_exit_code = 0
    db.request_status = "READY"
    db.attempt_result = {
        "attempt_id": db.attempt_id,
        "request_id": "request-1",
        "plan_id": "8261664",
        "session_id": "session-1",
        "ticket_id": "builder-ticket-ev-1",
        "work_request_id": "wr-1",
        "executor_id": "test-executor",
        "work_evidence": {"status": "evidence_failed", "reason": "empty_output"},
        "governance_finalization": {"status": "pending"},
    }

    assert worker._retry_evidence_failure_settlements(db) == (1, 0)
    first = dict(db.plan_receipts)
    assert len(first) == 1
    assert db.request_status == "FAILED"
    assert db.closed_tickets == [("8261664", "builder", "failed")]

    # Second pass: the query no longer returns the settled row — no duplicate
    # receipt, no second ticket close.
    assert worker._retry_evidence_failure_settlements(db) == (0, 0)
    assert db.plan_receipts == first
    assert db.closed_tickets == [("8261664", "builder", "failed")]


def test_ensure_success_evidence_rejects_failed_work_evidence_but_allows_legacy():
    db = _EvidenceDB()
    with pytest.raises(worker.WorkEvidenceError):
        worker._ensure_success_execution_evidence(
            db, "attempt-1", "request-1",
            {"work_evidence": {"status": "evidence_failed", "reason": "empty_output"}},
            "test-executor",
        )

    # Legacy attempts predate the field: unchanged behaviour.
    db.attempt_status = "SUCCEEDED"
    db.attempt_exit_code = 0
    db.execution_receipts[("attempt-1", "IMPLEMENTATION")] = {"type": "IMPLEMENTATION"}
    worker._ensure_success_execution_evidence(
        db, "attempt-1", "request-1", {"process_outcome": {"returncode": 0}},
        "test-executor",
    )


# ── review gate ────────────────────────────────────────────────────

class _ReviewCursor:
    def __init__(self, rows):
        self.rows = rows

    def dict_fetchall(self):
        return list(self.rows)

    def dict_fetchone(self):
        return self.rows[0] if self.rows else None


class _ReviewConn:
    def __init__(self, pending, records):
        self.pending = pending
        self.records = records
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        if "FROM nebula.agent_records" in sql:
            record_id, plan_id = params
            record = self.records.get(record_id)
            eligible = (
                record
                and record.get("plan_ref") == plan_id
                and record.get("record_type") in {"report", "assessment", "inspection"}
                and record.get("role") in {"reviewer", "tester"}
            )
            return _ReviewCursor([{"id": record_id}] if eligible else [])
        assert "LEFT JOIN conduit.sessions s" in sql
        return _ReviewCursor(self.pending)


def _review_db(pending, records=None):
    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"
    conn = _ReviewConn(pending, records or {})

    @contextlib.contextmanager
    def get_connection():
        yield conn

    db._get_connection = get_connection
    db._review_conn = conn
    return db


def _evidence_failed_row():
    return {
        "attempt_id": "attempt-ev-1",
        "session_id": "session-1",
        "attempt_status": "SUCCEEDED",
        "attempt_exit_code": 0,
        "work_evidence_status": "evidence_failed",
        "work_evidence_reason": "empty_output",
        "work_evidence_evaluated_at": "2026-10-27T00:00:00Z",
        "session_present": True,
        "session_closed": True,
        "session_exit_code": 0,
    }


def test_review_pass_requires_evidence_disposition_for_failed_work_evidence():
    db = _review_db([_evidence_failed_row()])
    with pytest.raises(ReviewPassReconciliationError, match="disposition=evidence_confirmed"):
        db.validate_review_pass_reconciliation("8261664", {})


def test_review_pass_accepts_evidence_disposition_with_eligible_record():
    record_id = "123e4567-e89b-12d3-a456-426614174000"
    db = _review_db([_evidence_failed_row()], {
        record_id: {"plan_ref": "8261664", "record_type": "report", "role": "reviewer"},
    })
    db.validate_review_pass_reconciliation("8261664", {
        "work_evidence": {
            "dispositions": [{
                "attempt_id": "attempt-ev-1",
                "disposition": "evidence_confirmed",
                "evidence_ref": f"agent_record:{record_id}",
            }],
        },
    })


@pytest.mark.parametrize(
    "evidence_ref",
    [
        "agent_record:not-a-uuid",
        "agent_record:123e4567-e89b-12d3-a456-426614174000",
        "plain-reference",
    ],
)
def test_review_pass_rejects_invalid_evidence_disposition_ref(evidence_ref):
    db = _review_db([_evidence_failed_row()])
    with pytest.raises(ReviewPassReconciliationError, match="REVIEW_PASS rejected"):
        db.validate_review_pass_reconciliation("8261664", {
            "work_evidence": {
                "dispositions": [{
                    "attempt_id": "attempt-ev-1",
                    "disposition": "evidence_confirmed",
                    "evidence_ref": evidence_ref,
                }],
            },
        })


def test_review_gate_query_exposes_work_evidence_columns():
    db = _review_db([_evidence_failed_row()])
    rows = db.get_unresolved_attempt_session_outcomes("8261664")
    assert rows[0]["work_evidence_status"] == "evidence_failed"
    assert "work_evidence'->>'status' = 'evidence_failed'" in db._review_conn.sql


# ── adapter SQL predicate ──────────────────────────────────────────

def test_request_completion_sql_requires_valid_or_absent_work_evidence():
    class Cursor:
        rowcount = 0

        def fetchone(self):
            return ("READY", False)

    class Conn:
        sql = []

        def execute(self, sql, params=()):
            self.sql.append(sql)
            return Cursor()

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    conn = Conn()

    @contextlib.contextmanager
    def get_connection():
        yield conn

    db._get_connection = get_connection
    assert db.mark_request_completed_after_attempt("attempt-1", "request-1") is False
    assert all("a.result->'work_evidence' IS NULL" in sql for sql in conn.sql)
    assert all("'status' = 'valid'" in sql for sql in conn.sql)


# ── throwaway PostgreSQL database ──────────────────────────────────

pytestmark_pg = pytest.mark.skipif(
    psycopg2 is None, reason="psycopg2 not available"
)


def _pg_available() -> bool:
    if psycopg2 is None:
        return False
    try:
        conn = psycopg2.connect(DSN, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def pg_database():
    if not _pg_available():
        pytest.skip("PostgreSQL is not reachable")
    name = f"conduit_test_ev_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(DSN)
    admin.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    parts = DSN.split("/")
    dsn = "/".join(parts[:-1] + [name])
    conn = psycopg2.connect(dsn)
    try:
        with open(MIGRATION_025) as handle:
            with conn.cursor() as cur:
                cur.execute(handle.read())
        conn.commit()
        yield dsn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _adapter_for(dsn):
    db = DBAdapter.__new__(DBAdapter)
    db._schema = "execution"
    db.db_path = dsn

    @contextlib.contextmanager
    def get_connection():
        conn = psycopg2.connect(dsn)
        from db_adapter import _ConnectionProxy
        proxy = _ConnectionProxy(conn, schema="execution")
        try:
            yield proxy
        finally:
            try:
                proxy.commit()
            except Exception:  # noqa: BLE001
                pass
            pool_conn = getattr(proxy, "_conn", conn)
            try:
                pool_conn.close()
            except Exception:  # noqa: BLE001
                pass

    db._get_connection = get_connection
    return db


def _seed_attempt(dsn, *, work_evidence, request_status="READY"):
    attempt_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    lease_id = str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO execution.requests
                   (id, business_key, title, status, source_plan_id)
                   VALUES (%s, %s, 'evidence', %s, '8261664')""",
                (request_id, f"bk-{request_id}", request_status),
            )
            cur.execute(
                """INSERT INTO execution.leases
                   (id, request_id, executor_id, status, expires_at)
                   VALUES (%s, %s, 'test', 'ACTIVE', NOW() + INTERVAL '10 minutes')""",
                (lease_id, request_id),
            )
            result = {"work_evidence": work_evidence} if work_evidence is not None else {}
            cur.execute(
                """INSERT INTO execution.attempts
                   (id, lease_id, request_id, executor_id, status, exit_code, result, completed_at)
                   VALUES (%s, %s, %s, 'test', 'SUCCEEDED', 0, %s, NOW())""",
                (attempt_id, lease_id, request_id, json.dumps(result)),
            )
            cur.execute(
                """INSERT INTO execution.receipts
                   (request_id, attempt_id, type, agent_role, summary, issued_at)
                   VALUES (%s, %s, 'IMPLEMENTATION', 'builder', 'seed', NOW())""",
                (request_id, attempt_id),
            )
        conn.commit()
    finally:
        conn.close()
    return request_id, attempt_id


def _request_status(dsn, request_id):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM execution.requests WHERE id = %s", (request_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytestmark_pg
def test_pg_evidence_failure_cannot_complete_request(pg_database):
    request_id, attempt_id = _seed_attempt(pg_database, work_evidence={
        "status": "evidence_failed", "reason": "empty_output",
    })
    db = _adapter_for(pg_database)
    assert db.mark_request_completed_after_attempt(str(attempt_id), str(request_id)) is False
    assert _request_status(pg_database, request_id) == "READY"


@pytestmark_pg
def test_pg_valid_work_evidence_completes_once(pg_database):
    request_id, attempt_id = _seed_attempt(pg_database, work_evidence={
        "status": "valid", "reason": "artifact_changed",
    })
    db = _adapter_for(pg_database)
    assert db.mark_request_completed_after_attempt(str(attempt_id), str(request_id)) is True
    assert _request_status(pg_database, request_id) == "COMPLETED"
    # Idempotent: the COMPLETED branch also enforces the evidence predicate.
    assert db.mark_request_completed_after_attempt(str(attempt_id), str(request_id)) is True


@pytestmark_pg
def test_pg_legacy_attempt_without_work_evidence_still_completes(pg_database):
    request_id, attempt_id = _seed_attempt(pg_database, work_evidence=None)
    db = _adapter_for(pg_database)
    assert db.mark_request_completed_after_attempt(str(attempt_id), str(request_id)) is True
    assert _request_status(pg_database, request_id) == "COMPLETED"


# ── recovery/consumption guards ────────────────────────────────────

def test_pending_governance_query_excludes_evidence_failed_attempts():
    captured = {}

    class Cursor:
        def dict_fetchall(self):
            return []

    class Conn:
        def execute(self, sql, params=()):
            captured["sql"] = sql
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Conn()

    db._get_connection = get_connection
    assert db.get_pending_governance_finalizations() == []
    # A crash between complete_attempt and evidence settlement leaves
    # governance=pending; the success retry must never pick it up.
    assert "work_evidence" in captured["sql"]
    assert "'status' <> 'evidence_failed'" in captured["sql"]


def test_list_ready_excludes_unsettled_evidence_failure():
    captured = {}

    class Cursor:
        def dict_fetchall(self):
            return []

    class Conn:
        def execute(self, sql, params=()):
            captured["sql"] = sql
            captured["params"] = params
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Conn()

    db._get_connection = get_connection
    worker._list_ready(db, include_legacy=False, eligible_plan_ids={"8261664"})
    assert "work_evidence'->>'status' = 'evidence_failed'" in captured["sql"]
    assert "NOT EXISTS" in captured["sql"]
    assert captured["params"] == ("8261664",)
