"""Bounded execution-outcome and retry contract tests (plan 8261663)."""

import contextlib
import copy
import json
import os
import sys
import threading

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import routes_receipts
import execution_worker as worker
import executor_cloud
import db_adapter as db_adapter_module
from db_adapter import DBAdapter, ReviewPassReconciliationError
from work_request_factory import WorkRequestFactory


class _RequestConnection:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params):
        if "UPDATE execution.requests SET status = 'COMPLETED'" in sql:
            self.db.request_status = "COMPLETED"
        elif "UPDATE execution.requests SET status = 'FAILED'" in sql:
            self.db.request_status = "FAILED"

    def commit(self):
        pass


class _WorkerDB:
    """In-memory DB boundary with uniqueness behavior for retry effects."""

    def __init__(self):
        self.attempt_id = "attempt-1"
        self.attempt_status = None
        self.attempt_exit_code = None
        self.attempt_result = {}
        self.request_status = "READY"
        self.session_id = None
        self.session_closed = False
        self.session_close_code = None
        self.initial_attempt_result = None
        self.session_exit_code = None
        self.session_close_calls = []
        self.execution_receipts = {}
        self.execution_receipt_visible = True
        self.plan_receipts = {}
        self.reviewer_tickets = {}
        self.ticket_write_committed_then_failed = True
        self.receipt_visible = True
        self.reviewer_criteria_safe = True
        self.reviewer_criteria = None
        self.request_status_at_reviewer_creation = None
        self.reject_attempt_completion = False
        self.lease_release_calls = 0
        self.request_completion_failures = 0
        self.ticket_release_calls = []
        self.attempt_create_calls = 0

    def acquire_lease(self, **kwargs):
        return {"id": "lease-1"}

    def create_session(self, session_id, role, plan_ids):
        self.session_id = session_id
        self.session_closed = False
        self.session_close_code = None
        self.session_exit_code = None

    def close_worker_session(self, session_id, exit_code):
        self.session_close_calls.append((session_id, exit_code))
        if self.session_closed or self.session_exit_code is not None:
            return False
        self.session_closed = True
        self.session_close_code = exit_code
        self.session_exit_code = exit_code
        return True

    def claim_ticket(self, plan_id, role, session_id):
        return "builder-ticket-1"

    def create_attempt(self, **kwargs):
        self.attempt_create_calls += 1
        self.initial_attempt_result = copy.deepcopy(kwargs.get("result") or {})
        if kwargs.get("session_id"):
            self.initial_attempt_result.setdefault("session_id", kwargs["session_id"])
        return {"id": self.attempt_id}

    def release_ticket(self, plan_id, role, session_id):
        self.ticket_release_calls.append((plan_id, role, session_id))
        return True

    def start_attempt(self, attempt_id):
        return True

    def add_work_request(self, *args, **kwargs):
        pass

    def complete_attempt(self, attempt_id, status, exit_code=None, result=None, error=None):
        if self.reject_attempt_completion:
            return False
        self.attempt_status = status
        self.attempt_exit_code = exit_code
        self.attempt_result = copy.deepcopy(result or {})
        self.attempt_error = error
        return True

    def issue_execution_receipt(self, attempt_id, request_id, receipt_type, **kwargs):
        key = (attempt_id, receipt_type)
        self.execution_receipts.setdefault(key, {"attempt_id": attempt_id, "type": receipt_type})
        return self.execution_receipts[key]

    def has_attempt_execution_receipt(self, attempt_id, request_id):
        return self.execution_receipt_visible and any(
            rid == attempt_id and receipt_type in ("EXECUTION_COMPLETE", "IMPLEMENTATION")
            for rid, receipt_type in self.execution_receipts
        )

    def mark_request_completed_after_attempt(self, attempt_id, request_id):
        if (self.attempt_status != "SUCCEEDED" or self.attempt_exit_code != 0
                or not self.has_attempt_execution_receipt(attempt_id, request_id)):
            return False
        if self.request_completion_failures:
            self.request_completion_failures -= 1
            return False
        self.request_status = "COMPLETED"
        return True

    def release_lease(self, lease_id):
        self.lease_release_calls += 1
        return True

    def update_attempt_result(self, attempt_id, result):
        self.attempt_result = copy.deepcopy(result)
        return True

    @contextlib.contextmanager
    def _get_connection(self):
        yield _RequestConnection(self)

    def insert_receipt(self, *, receipt_id=None, **receipt):
        receipt_id = receipt_id or f"receipt-{len(self.plan_receipts) + 1}"
        self.plan_receipts.setdefault(receipt_id, receipt)

    def has_plan_receipt(self, plan_id, receipt_id, receipt_type):
        return self.receipt_visible and receipt_id in self.plan_receipts

    def close_ticket(self, *args):
        return True

    def advance_cursor(self, *args):
        pass

    def create_next_tickets(self, *args, idempotency_key=None, **kwargs):
        self.request_status_at_reviewer_creation = self.request_status
        self.reviewer_criteria = kwargs.get("completion_criteria")
        self.reviewer_tickets.setdefault(idempotency_key, "reviewer-ticket-1")
        if self.ticket_write_committed_then_failed:
            self.ticket_write_committed_then_failed = False
            raise RuntimeError("simulated lost response after reviewer ticket commit")
        return 1

    def ensure_reviewer_ticket_completion_criteria(self, *args):
        self.reviewer_criteria = args[-1]
        return self.reviewer_criteria_safe

    def get_pending_governance_finalizations(self, limit=100):
        if (self.attempt_status == "SUCCEEDED"
                and self.attempt_result.get("governance_finalization", {}).get("status") == "pending"):
            return [{
                "attempt_id": self.attempt_id,
                "request_id": "request-1",
                "source_plan_id": "plan-1",
                "attempt_status": self.attempt_status,
                "exit_code": self.attempt_exit_code,
                "result": copy.deepcopy(self.attempt_result),
            }]
        return []

    def get_worker_session_outcomes(self, limit=100):
        if not self.session_id:
            return []
        return [{
            "attempt_id": self.attempt_id,
            "request_id": "request-1",
            "plan_id": "plan-1",
            "session_id": self.session_id,
            "attempt_exit_code": self.attempt_exit_code,
            "session_exit_code": 1,
            "attempt_result": copy.deepcopy(self.attempt_result),
        }]

    def get_eligible_plans(self, role):
        return []


class _AdvisoryLockPool:
    """Small PostgreSQL boundary fake for plan-lock ordering tests."""

    def __init__(
        self, latest_receipt="PLAN_CREATE", request_statuses=None,
        plan_derived_status="PLAN_CREATE",
    ):
        self.latest_receipt = latest_receipt
        self.request_statuses = list(request_statuses or ["READY"])
        self.last_request_status = self.request_statuses[-1]
        self.plan_derived_status = plan_derived_status
        self.pending_finalization = False
        self.rework_authorized = False
        self.attempt_inserts = 0
        self.receipt_inserts = 0
        self.synthetic_inserts = 0
        self.canonical_inserts = 0
        self.plan_lock = threading.Lock()
        self.review_validation_entered = threading.Event()
        self.release_review_validation = threading.Event()
        self.builder_lock_waiting = threading.Event()
        self.lock_keys = []
        self.released_lock_keys = []
        self.checked_out = 0
        self.returned = 0

    def getconn(self):
        self.checked_out += 1
        return _AdvisoryLockConnection(self)

    def putconn(self, _conn, close=False):
        self.returned += 1
        assert close is False


class _AdvisoryLockConnection:
    def __init__(self, pool):
        self.pool = pool
        self.lock_key = None

    def cursor(self):
        return _AdvisoryLockCursor(self.pool, self)

    def commit(self):
        self._release_lock()

    def rollback(self):
        self._release_lock()

    def _release_lock(self):
        if self.lock_key is not None:
            self.pool.plan_lock.release()
            self.pool.released_lock_keys.append(self.lock_key)
            self.lock_key = None


class _AdvisoryLockCursor:
    def __init__(self, pool, connection):
        self.pool = pool
        self.connection = connection
        self.row = None
        self.description = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if sql.startswith("SET "):
            return
        if sql.startswith(("SAVEPOINT ", "ROLLBACK TO SAVEPOINT ", "RELEASE SAVEPOINT ")):
            return
        if "pg_advisory_xact_lock(" in sql:
            self.pool.lock_keys.append(params)
            if threading.current_thread().name == "builder-attempt":
                self.pool.builder_lock_waiting.set()
            self.pool.plan_lock.acquire()
            self.connection.lock_key = params
            self.row = (None,)
            return
        if "SELECT status, source_plan_id FROM execution.requests" in sql:
            if self.pool.request_statuses:
                self.pool.last_request_status = self.pool.request_statuses.pop(0)
            self.row = (self.pool.last_request_status, "plan-1")
            return
        if "SELECT derived_status FROM plan_status" in sql:
            self.row = (self.pool.plan_derived_status,)
            return
        if "SELECT id, completed_at FROM execution.attempts" in sql:
            self.row = ("prior-attempt", "2026-10-25T20:00:00Z") if self.pool.pending_finalization else None
            return
        if "type IN ('REQUEUED', 'REVIEW_REJECT', 'CRITIQUE_PASS')" in sql:
            self.row = (1,) if self.pool.rework_authorized else None
            return
        if "SELECT type FROM nebula.receipts_unified" in sql:
            self.row = (self.pool.latest_receipt,) if self.pool.latest_receipt else None
            return
        if "SELECT id FROM execution.requests WHERE source_plan_id" in sql:
            self.description = [type("Column", (), {"name": "id"})()]
            self.row = ("request-1",)
            return
        if "INSERT INTO execution.attempts" in sql:
            self.pool.attempt_inserts += 1
            columns = ["id", "status", "result"]
            self.description = [type("Column", (), {"name": name})() for name in columns]
            self.row = ("attempt-1", "CREATED", params[-1])
            self.rowcount = 1
            return
        if "INSERT INTO execution.receipts" in sql:
            self.pool.receipt_inserts += 1
            self.pool.latest_receipt = params[1]
            self.row = None
            self.rowcount = 1
            return
        if "INSERT INTO vision.receipts" in sql:
            self.pool.synthetic_inserts += 1
            self.pool.latest_receipt = params[2]
            self.row = None
            self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL in advisory-lock test: {sql}")

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        pass


def test_child_nonzero_exit_retains_exact_code_and_streams():
    command = [
        sys.executable,
        "-c",
        "import sys; print('child stdout'); print('child stderr', file=sys.stderr); sys.exit(23)",
    ]

    with pytest.raises(executor_cloud.HarnessExecutionError) as raised:
        executor_cloud._run_harness_subprocess(command, None, 10, "test-child")

    assert raised.value.returncode == 23
    assert raised.value.stdout == "child stdout\n"
    assert raised.value.stderr == "child stderr\n"
    assert raised.value.outcome.returncode == 23


def test_worker_failed_attempt_keeps_exact_child_exit(monkeypatch):
    db = _WorkerDB()
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}

    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1",
        "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/only"},
    ])
    monkeypatch.setattr(
        worker.executor_cloud,
        "run_opencode_outcome",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            executor_cloud.HarnessExecutionError(
                "child returned 23",
                executor_cloud.HarnessExecutionResult(23, "child output\n", "child failure\n"),
            )
        ),
    )

    assert worker._process_one(db, request, "test-executor", False) == "failed"
    assert db.attempt_status == "FAILED"
    assert db.attempt_exit_code == 23
    assert db.attempt_result["process_outcome"]["returncode"] == 23
    assert db.attempt_result["model_attempts"][0]["stdout"]["excerpt"] == "child output\n"
    assert db.attempt_result["model_attempts"][0]["stderr"]["excerpt"] == "child failure\n"
    assert db.initial_attempt_result["session_id"] == db.session_id
    assert db.session_close_code == 23
    assert db.session_close_calls == [(db.session_id, 23)]
    assert db.session_exit_code == 23


@pytest.mark.parametrize(
    "race_kind,request_status",
    [("terminal plan", "READY"), ("cancelled request", "CANCELLED")],
)
def test_worker_cleans_up_without_execution_when_admission_race_refuses_attempt(
    monkeypatch, race_kind, request_status,
):
    db = _WorkerDB()
    def refuse_attempt(**_kwargs):
        if request_status == "CANCELLED":
            db.request_status = "CANCELLED"
        return None

    db.create_attempt = refuse_attempt
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: pytest.fail(
        f"{race_kind} must be rejected before executor construction"
    ))

    assert worker._process_one(db, request, "test-executor", False) == "admission-race"
    assert db.plan_receipts == {}
    assert db.execution_receipts == {}
    assert db.request_status == request_status
    assert db.attempt_status is None
    assert db.lease_release_calls == 1
    assert db.ticket_release_calls == [("plan-1", worker.ROLE, db.session_id)]
    assert db.session_close_calls == [(db.session_id, None)]
    assert db.session_exit_code is None


def test_worker_rejects_untyped_stdout_only_executor_result(monkeypatch):
    db = _WorkerDB()
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs: "stdout only")

    assert worker._process_one(db, request, "test-executor", False) == "failed"
    assert db.attempt_status == "FAILED"
    assert db.attempt_exit_code is None
    assert db.attempt_result["process_outcome"]["returncode"] is None
    assert db.session_exit_code == 1


def test_worker_success_closes_session_after_governance_finalization(monkeypatch):
    db = _WorkerDB()
    db.ticket_write_committed_then_failed = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.attempt_result["governance_finalization"]["status"] == "complete"
    assert db.session_exit_code == 0
    assert db.session_close_calls == [(db.session_id, 0)]


def test_governance_finalization_error_closes_session_nonzero_without_changing_attempt(monkeypatch):
    db = _WorkerDB()
    db.receipt_visible = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))

    assert worker._process_one(db, request, "test-executor", False) == "finalization-pending"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.attempt_result["process_outcome"]["returncode"] == 0
    assert db.attempt_result["governance_finalization"]["status"] == "pending"
    assert db.session_exit_code == 1
    assert db.session_close_calls == [(db.session_id, 1)]


def test_worker_preserves_session_closed_by_another_writer(monkeypatch):
    db = _WorkerDB()
    db.ticket_write_committed_then_failed = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))
    finalize = worker._finalize_governance_row

    def close_from_other_writer_then_finalize(database, row):
        database.session_exit_code = 19
        return finalize(database, row)

    monkeypatch.setattr(worker, "_finalize_governance_row", close_from_other_writer_then_finalize)

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.session_exit_code == 19
    assert db.session_close_calls == [(db.session_id, 0)]


def test_complete_attempt_refuses_terminal_overwrite_and_worker_handles_false(monkeypatch):
    class Cursor:
        rowcount = 0

    class Connection:
        sql = ""
        params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor()

        def commit(self):
            pass

    adapter = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    adapter._get_connection = get_connection
    assert adapter.complete_attempt("attempt-terminal", "FAILED", exit_code=1) is False
    assert "status IN ('CREATED', 'RUNNING')" in connection.sql

    db = _WorkerDB()
    db.reject_attempt_completion = True
    db.attempt_status = "FAILED"
    db.attempt_exit_code = 8
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))

    assert worker._process_one(db, request, "test-executor", False) == "attempt-terminal"
    assert db.attempt_status == "FAILED"
    assert db.attempt_exit_code == 8
    assert db.plan_receipts == {}
    assert db.lease_release_calls == 1


def test_create_attempt_persists_session_correlation_in_initial_insert():
    class Cursor:
        def __init__(self, row):
            self.row = row

        def dict_fetchone(self):
            return self.row

    class Connection:
        sql = ""
        params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor({"id": "attempt-1", "status": "CREATED", "result": params[-1]})

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    attempt = db.create_attempt(
        "lease-1", "request-1", "test-executor", session_id="session-worker-1",
        result={"plan_id": "plan-1"},
    )

    assert "status, result" in connection.sql
    assert attempt["id"] == "attempt-1"
    assert json.loads(connection.params[-1]) == {
        "plan_id": "plan-1", "session_id": "session-worker-1",
    }


def test_review_pass_and_builder_attempt_share_lock_through_receipt_write(monkeypatch):
    pool = _AdvisoryLockPool()
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    monkeypatch.setenv("CONDUIT_RECEIPT_REDIRECT", "off")
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"

    def pause_validation(plan_id, metadata):
        assert plan_id == "plan-1"
        assert pool.plan_lock.locked()
        pool.review_validation_entered.set()
        assert pool.release_review_validation.wait(timeout=5)

    adapter.validate_review_pass_reconciliation = pause_validation
    review_errors = []

    def issue_review_pass():
        try:
            adapter.insert_receipt(
                plan_id="plan-1", receipt_type="REVIEW_PASS", agent_role="reviewer",
                session_id=None, ticket_id="", receipt_id="review-pass-1",
            )
        except Exception as exc:  # surfaced in the main test thread
            review_errors.append(exc)

    review_thread = threading.Thread(target=issue_review_pass, name="review-pass")
    review_thread.start()
    assert pool.review_validation_entered.wait(timeout=5)

    attempt_results = []

    def create_builder_attempt():
        attempt_results.append(adapter.create_attempt(
            "lease-1", "request-1", "builder", session_id="session-1",
            result={"plan_id": "plan-1"},
        ))

    builder_thread = threading.Thread(target=create_builder_attempt, name="builder-attempt")
    builder_thread.start()
    assert pool.builder_lock_waiting.wait(timeout=5)
    assert pool.attempt_inserts == 0

    pool.release_review_validation.set()
    review_thread.join(timeout=5)
    builder_thread.join(timeout=5)

    assert not review_thread.is_alive()
    assert not builder_thread.is_alive()
    assert review_errors == []
    assert attempt_results == [None]
    assert pool.latest_receipt == "REVIEW_PASS"
    assert pool.receipt_inserts == 1
    assert pool.attempt_inserts == 0
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.lock_keys[0] == pool.lock_keys[1]
    assert pool.checked_out == pool.returned
    assert not pool.plan_lock.locked()


def test_review_pass_synthetic_receipt_write_remains_under_transaction_lock(monkeypatch):
    pool = _AdvisoryLockPool()
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    monkeypatch.setenv("CONDUIT_RECEIPT_REDIRECT", "off")
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"
    adapter.resolve_request_for_receipt = lambda _plan_id: None
    adapter.validate_review_pass_reconciliation = lambda _plan_id, _metadata: (
        pool.plan_lock.locked() or pytest.fail("validation ran outside plan lock")
    )

    adapter.insert_receipt(
        plan_id="plan-1", receipt_type="REVIEW_PASS", agent_role="reviewer",
        session_id=None, ticket_id="", receipt_id="review-pass-synthetic",
    )

    assert pool.synthetic_inserts == 1
    assert pool.latest_receipt == "REVIEW_PASS"
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned


def test_review_pass_canonical_redirect_uses_the_locking_transaction(monkeypatch):
    import lilac as lilac_module

    pool = _AdvisoryLockPool()
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    monkeypatch.setenv("CONDUIT_RECEIPT_REDIRECT", "enforce")
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"
    adapter.resolve_request_for_receipt = lambda _plan_id: None
    adapter.validate_review_pass_reconciliation = lambda _plan_id, _metadata: (
        pool.plan_lock.locked() or pytest.fail("validation ran outside plan lock")
    )

    class CanonicalWriter:
        def __init__(self, _connection_factory, **_kwargs):
            pass

        def insert_receipt(self, _connection, *, manage_transaction, **_kwargs):
            assert pool.plan_lock.locked()
            assert manage_transaction is False
            pool.canonical_inserts += 1
            pool.latest_receipt = "REVIEW_PASS"
            return "accepted", "canonical-receipt-1"

    monkeypatch.setattr(lilac_module, "LilacAdapter", CanonicalWriter)
    adapter.insert_receipt(
        plan_id="plan-1", receipt_type="REVIEW_PASS", agent_role="reviewer",
        session_id=None, ticket_id="", receipt_id="review-pass-canonical",
    )

    assert pool.canonical_inserts == 1
    assert pool.synthetic_inserts == 0
    assert pool.latest_receipt == "REVIEW_PASS"
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned


@pytest.mark.parametrize(
    "latest_receipt,allowed",
    [
        ("REVIEW_PASS", False),
        ("BLOCK", False),
        ("PLAN_BLOCK", False),
        ("CANCELLED", False),
        ("ABANDONED", False),
        ("API_LIMIT", True),
        ("REVIEW_REJECT", True),
        ("REQUEUED", True),
        ("PLAN_CREATE", True),
    ],
)
def test_builder_attempt_rechecks_terminal_receipt_state_under_plan_lock(
    monkeypatch, latest_receipt, allowed,
):
    pool = _AdvisoryLockPool(latest_receipt=latest_receipt)
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"

    attempt = adapter.create_attempt(
        "lease-1", "request-1", "builder", session_id="session-1",
        result={"plan_id": "plan-1"},
    )

    assert (attempt is not None) is allowed
    assert pool.attempt_inserts == int(allowed)
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned


def test_builder_attempt_rechecks_cancelled_request_under_plan_lock(monkeypatch):
    pool = _AdvisoryLockPool(request_statuses=["READY", "CANCELLED"])
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"

    attempt = adapter.create_attempt(
        "lease-1", "request-1", "builder", session_id="session-1",
        result={"plan_id": "plan-1"},
    )

    assert attempt is None
    assert pool.attempt_inserts == 0
    assert pool.receipt_inserts == 0
    assert pool.last_request_status == "CANCELLED"
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned


def test_builder_attempt_refuses_plan_that_is_no_longer_builder_eligible(monkeypatch):
    pool = _AdvisoryLockPool(plan_derived_status="IMPLEMENTATION")
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"

    attempt = adapter.create_attempt(
        "lease-1", "request-1", "builder", session_id="session-1",
        result={"plan_id": "plan-1"},
    )

    assert attempt is None
    assert pool.attempt_inserts == 0
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned


def test_builder_attempt_requires_newer_rework_authorization_after_pending_success(monkeypatch):
    pool = _AdvisoryLockPool()
    pool.pending_finalization = True
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"

    assert adapter.create_attempt(
        "lease-1", "request-1", "conduit-worker", work_role="builder",
        session_id="session-1", result={"plan_id": "plan-1"},
    ) is None
    assert pool.attempt_inserts == 0

    pool.rework_authorized = True
    assert adapter.create_attempt(
        "lease-1", "request-1", "conduit-worker", work_role="builder",
        session_id="session-2", result={"plan_id": "plan-1"},
    ) is not None
    assert pool.attempt_inserts == 1


def test_worker_session_close_is_conditional_and_preserves_prior_close():
    class Cursor:
        rowcount = 0

        def fetchone(self):
            return None

    class Connection:
        sql = ""
        params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor()

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    assert db.close_worker_session("session-1", 0) is False
    assert "WHERE id=%s AND end_iso IS NULL AND is_running=1" in connection.sql
    db.close_session("session-legacy", 4)
    assert "WHERE id=%s" in connection.sql
    assert "AND end_iso IS NULL" not in connection.sql
    assert connection.params[-1] == "session-legacy"


def test_worker_session_close_only_updates_open_sessions():
    class Cursor:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = None
            self.rowcounts = [1, 0]

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor(self.rowcounts.pop(0))

        def commit(self):
            pass

    adapter = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    adapter._get_connection = get_connection
    assert adapter.close_worker_session("worker-session", 0) is True
    assert "WHERE id=%s AND end_iso IS NULL AND is_running=1" in connection.sql
    assert connection.params[1] == 0
    assert adapter.close_worker_session("worker-session", 7) is False
    assert connection.params[1] == 7


def test_session_close_is_first_writer_wins():
    class Cursor:
        def __init__(self, rowcount=0, row=None):
            self.rowcount = rowcount
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = 0
            self.params = []

        def execute(self, sql, params):
            self.params.append(params)
            if sql.startswith("UPDATE sessions"):
                self.calls += 1
                return Cursor(rowcount=1 if self.calls == 1 else 0)
            return Cursor(row=("2026-10-25T20:00:00Z", 0))

        def commit(self):
            pass

    adapter = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    adapter._get_connection = get_connection
    assert adapter.close_session("worker-session", 0) is True
    assert adapter.close_session("worker-session", 1) is False
    assert connection.params[0][1] == 0
    assert connection.params[1][1] == 1


def test_unavailable_plan_receipt_keeps_finalization_pending(monkeypatch):
    db = _WorkerDB()
    db.receipt_visible = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))

    assert worker._process_one(db, request, "test-executor", False) == "finalization-pending"
    assert db.attempt_status == "SUCCEEDED"
    assert db.request_status == "COMPLETED"
    assert db.attempt_exit_code == 0
    assert db.session_close_code == 1
    assert db.session_close_calls == [(db.session_id, 1)]
    assert db.attempt_result["governance_finalization"]["status"] == "pending"
    assert db.attempt_result["governance_finalization"]["review_settlement_criterion"]["status"] == "pending"
    assert "not visible" in db.attempt_result["governance_finalization"]["error"]
    assert db.reviewer_tickets == {}


def test_unsafe_reviewer_criteria_update_is_persistently_not_enforced(monkeypatch):
    db = _WorkerDB()
    db.ticket_write_committed_then_failed = False
    db.reviewer_criteria_safe = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))

    assert worker._process_one(db, request, "test-executor", False) == "finalization-pending"
    criteria = db.attempt_result["governance_finalization"]["review_settlement_criterion"]
    assert criteria["status"] == "not_enforced"
    assert "safely updated" in criteria["reason"]
    assert db.attempt_status == "SUCCEEDED"


def test_request_completion_failure_recovers_without_builder_and_repairs_missing_receipt(monkeypatch):
    db = _WorkerDB()
    db.request_completion_failures = 2
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    launches = []
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        (launches.append("builder") or
                         executor_cloud.HarnessExecutionResult(0, "done", "")))

    assert worker._process_one(db, request, "test-executor", False) == "finalization-pending"
    assert db.lease_release_calls >= 1
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.request_status == "READY"
    assert db.session_close_code == 1
    assert db.session_close_calls == [(db.session_id, 1)]
    assert db.attempt_result["governance_finalization"]["status"] == "pending"
    assert not any(item.get("receipt_type") == "BLOCK" for item in db.plan_receipts.values())

    # Simulate a lost/missing attempt receipt before recovery. The finalizer
    # repairs the attempt evidence and request status before plan fan-out.
    db.ticket_write_committed_then_failed = False
    db.execution_receipts.clear()
    assert worker._retry_pending_governance_finalizations(db) == (1, 0)
    assert db.request_status == "COMPLETED"
    assert db.request_status_at_reviewer_creation == "COMPLETED"
    assert len(db.execution_receipts) == 1
    assert db.attempt_result["governance_finalization"]["status"] == "complete"
    assert len(launches) == 1


def test_successful_work_and_governance_closes_worker_session_zero(monkeypatch):
    db = _WorkerDB()
    db.ticket_write_committed_then_failed = False
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", lambda *_args, **_kwargs:
                        executor_cloud.HarnessExecutionResult(0, "done", ""))
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda *_args, **_kwargs: None)

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.attempt_result["process_outcome"]["returncode"] == 0
    assert db.session_close_code == 0
    assert db.session_close_calls == [(db.session_id, 0)]


def test_worker_close_preserves_session_closed_by_another_owner():
    db = _WorkerDB()
    db.create_session("session-owned-elsewhere", "builder", ["plan-1"])
    assert db.close_worker_session("session-owned-elsewhere", 73) is True

    worker._close_worker_session_safely(db, "session-owned-elsewhere", 0)

    assert db.session_close_code == 73
    assert db.session_close_calls == [
        ("session-owned-elsewhere", 73), ("session-owned-elsewhere", 0),
    ]


def test_missing_attempt_receipt_keeps_finalization_pending():
    db = _WorkerDB()
    db.attempt_status = "SUCCEEDED"
    db.attempt_exit_code = 0
    db.execution_receipt_visible = False
    db.attempt_result = {
        "attempt_id": db.attempt_id,
        "request_id": "request-1",
        "plan_id": "plan-1",
        "session_id": "session-1",
        "ticket_id": "builder-ticket-1",
        "work_request_id": "wr-1",
        "model": "provider/model",
        "executor_id": "test-executor",
        "process_outcome": {"returncode": 0},
        "governance_finalization": {"status": "pending"},
    }

    assert worker._retry_pending_governance_finalizations(db) == (0, 1)
    assert db.execution_receipts
    assert db.request_status == "READY"
    assert db.attempt_result["governance_finalization"]["status"] == "pending"


def test_session_mismatch_persists_failure_to_update_reviewer_ticket():
    db = _WorkerDB()
    db.session_id = "worker-session"
    db.reviewer_criteria_safe = False
    db.attempt_status = "SUCCEEDED"
    db.attempt_exit_code = 0
    db.attempt_result = {
        "session_id": db.session_id,
        "attempt_id": db.attempt_id,
        "request_id": "request-1",
        "plan_id": "plan-1",
        "ticket_id": "builder-ticket-1",
        "work_request_id": "wr-1",
        "governance_finalization": {"status": "complete"},
    }

    assert worker._surface_session_exit_observations(db) == 1
    reconciliation = db.attempt_result["outcome_reconciliation"]
    assert reconciliation["review_required"] is True
    assert reconciliation["review_ticket_criterion"]["status"] == "not_enforced"
    assert db.attempt_result["governance_finalization"]["review_settlement_criterion"]["status"] == "not_enforced"
    assert db.attempt_result["governance_finalization"]["status"] == "pending"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0


def test_equal_session_and_attempt_codes_are_not_reconciled():
    db = _WorkerDB()
    db.session_id = "worker-session"
    db.attempt_exit_code = 1
    db.update_attempt_result = lambda *_args: pytest.fail("matching outcomes must not be rewritten")

    assert worker._surface_session_exit_observations(db) == 0


def _review_pass_db(mismatches, evidence_records=None):
    evidence_records = evidence_records or {}

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def dict_fetchall(self):
            return self.rows

        def dict_fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def execute(self, sql, params):
            if "FROM nebula.agent_records" in sql:
                assert "id = %s::uuid" in sql
                assert "plan_ref = %s" in sql
                assert "record_type IN ('report', 'assessment', 'inspection')" in sql
                assert "role IN ('reviewer', 'tester')" in sql
                record_id, plan_ref = params
                record = evidence_records.get(record_id)
                eligible = (
                    record
                    and record.get("plan_ref") == plan_ref
                    and record.get("record_type") in {"report", "assessment", "inspection"}
                    and record.get("role") in {"reviewer", "tester"}
                )
                return Cursor([{"id": record_id}] if eligible else [])
            assert "LEFT JOIN conduit.sessions s" in sql
            assert "s.id IS NULL" in sql
            assert "s.end_iso IS NULL" in sql
            assert "a.exit_code IS DISTINCT FROM s.exit_code" in sql
            assert params == ("plan-1",)
            return Cursor(mismatches)

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Connection()

    db._get_connection = get_connection
    return db


def test_review_pass_requires_per_mismatch_disposition_and_canonical_evidence():
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }
    evidence_id = "123e4567-e89b-12d3-a456-426614174000"
    db = _review_pass_db([mismatch], {
        evidence_id: {
            "plan_ref": "plan-1", "record_type": "report", "role": "reviewer",
        },
    })
    with pytest.raises(ReviewPassReconciliationError, match="REVIEW_PASS rejected"):
        db.validate_review_pass_reconciliation("plan-1", {})

    db.validate_review_pass_reconciliation("plan-1", {
        "outcome_reconciliation": {
            "dispositions": [{
                "attempt_id": "attempt-1",
                "session_id": "session-1",
                "disposition": "work_complete",
                "evidence_ref": f"agent_record:{evidence_id}",
            }],
        },
    })


@pytest.mark.parametrize(
    ("evidence_ref", "records"),
    [
        ("agent_record:not-a-uuid", {}),
        ("agent_record:123e4567-e89b-12d3-a456-426614174000", {}),
        (
            "agent_record:123e4567-e89b-12d3-a456-426614174000",
            {"123e4567-e89b-12d3-a456-426614174000": {
                "plan_ref": "another-plan", "record_type": "report", "role": "reviewer",
            }},
        ),
        (
            "agent_record:123e4567-e89b-12d3-a456-426614174000",
            {"123e4567-e89b-12d3-a456-426614174000": {
                "plan_ref": "plan-1", "record_type": "report", "role": "builder",
            }},
        ),
        (
            "agent_record:123e4567-e89b-12d3-a456-426614174000",
            {"123e4567-e89b-12d3-a456-426614174000": {
                "plan_ref": "plan-1", "record_type": "prompt", "role": "reviewer",
            }},
        ),
    ],
    ids=["malformed-id", "nonexistent-id", "wrong-plan", "wrong-role", "wrong-type"],
)
def test_review_pass_rejects_invalid_agent_record_evidence(evidence_ref, records):
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }
    db = _review_pass_db([mismatch], records)
    metadata = {
        "outcome_reconciliation": {
            "dispositions": [{
                "attempt_id": "attempt-1",
                "session_id": "session-1",
                "disposition": "work_complete",
                "evidence_ref": evidence_ref,
            }],
        },
    }

    with pytest.raises(ReviewPassReconciliationError, match="REVIEW_PASS rejected"):
        db.validate_review_pass_reconciliation("plan-1", metadata)


@pytest.mark.parametrize("disposition", ["", "work_incomplete", "resolved", "WORK_COMPLETE"])
def test_review_pass_requires_work_complete_disposition(disposition):
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }
    db = _review_pass_db([mismatch])
    with pytest.raises(ReviewPassReconciliationError, match="disposition=work_complete"):
        db.validate_review_pass_reconciliation("plan-1", {
            "outcome_reconciliation": {"dispositions": [{
                "attempt_id": "attempt-1", "session_id": "session-1",
                "disposition": disposition,
                "evidence_ref": "agent_record:123e4567-e89b-12d3-a456-426614174000",
            }]},
        })


def test_review_ticket_criteria_reaches_reviewer_prompt():
    criteria = "Disposition every linked attempt/session exit mismatch before REVIEW_PASS."
    dco = WorkRequestFactory.create_from_plan(
        {"id": "plan-1", "title": "review", "goal": "review implementation",
         "acceptance_criteria": ["base plan criterion"]},
        role="reviewer",
        ticket_completion_criteria=criteria,
    )
    prompt_content = executor_cloud._serialize_dco_for_prompt(dco.model_dump(by_alias=True))

    assert any(criteria in criterion for criterion in dco.requirements.functional)
    assert criteria in prompt_content
    reviewer_instructions = executor_cloud._build_role_instructions("reviewer")
    assert "outcome_reconciliation.dispositions" in reviewer_instructions
    assert "evidence_ref" in reviewer_instructions


def test_receipt_route_returns_conflict_for_unreconciled_review_pass(monkeypatch):
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }

    class Cursor:
        def dict_fetchall(self):
            return [mismatch]

    class Connection:
        def execute(self, sql, params):
            assert "a.status IN ('CREATED', 'RUNNING')" in sql
            assert "LEFT JOIN conduit.sessions s" in sql
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Connection()

    db._get_connection = get_connection
    db._holds_plan_advisory_lock = lambda _plan_id: True
    monkeypatch.setattr(routes_receipts, "_new_db_adapter", lambda: db)
    body = routes_receipts.ReceiptInsertRequest(
        id="receipt-review-pass",
        plan_id="plan-1",
        type="REVIEW_PASS",
        agent_role="reviewer",
        created_at="2026-10-25T00:00:00Z",
        metadata_json="{}",
    )

    with pytest.raises(HTTPException) as raised:
        routes_receipts.insert_receipt(body)
    assert raised.value.status_code == 409
    assert "attempt-1" in raised.value.detail


def test_rework_review_pass_is_blocked_while_builder_attempt_is_running(monkeypatch):
    in_flight = {
        "attempt_id": "attempt-running",
        "session_id": "session-running",
        "attempt_status": "RUNNING",
        "attempt_exit_code": None,
        "session_present": True,
        "session_closed": False,
        "session_exit_code": None,
    }

    class Cursor:
        def dict_fetchall(self):
            return [in_flight]

    class Connection:
        def execute(self, _sql, _params):
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Connection()

    db._get_connection = get_connection
    db._holds_plan_advisory_lock = lambda _plan_id: True
    monkeypatch.setattr(routes_receipts, "_new_db_adapter", lambda: db)
    body = routes_receipts.ReceiptInsertRequest(
        id="receipt-racing-review-pass",
        plan_id="plan-after-review-reject",
        type="REVIEW_PASS",
        agent_role="reviewer",
        created_at="2026-10-25T00:00:00Z",
        metadata_json=json.dumps({
            "outcome_reconciliation": {
                "dispositions": [{
                    "attempt_id": "attempt-running",
                    "session_id": "session-running",
                    "disposition": "Looks successful",
                    "evidence_ref": "attempt:attempt-running",
                }],
            },
        }),
    )

    with pytest.raises(HTTPException) as raised:
        routes_receipts.insert_receipt(body)
    assert raised.value.status_code == 409
    assert "not closed and complete" in raised.value.detail

    # Even a matching, already-closed session cannot settle a still-RUNNING attempt.
    in_flight.update({
        "attempt_exit_code": 0,
        "session_closed": True,
        "session_exit_code": 0,
    })
    with pytest.raises(ReviewPassReconciliationError, match="not closed and complete"):
        db.validate_review_pass_reconciliation("plan-after-review-reject", {})


@pytest.mark.parametrize(
    "session_row",
    [
        {"attempt_id": "attempt-1", "session_id": "session-1", "attempt_exit_code": 0,
         "session_present": False, "session_closed": False, "session_exit_code": None},
        {"attempt_id": "attempt-1", "session_id": "session-1", "attempt_exit_code": 0,
         "session_present": True, "session_closed": False, "session_exit_code": None},
        {"attempt_id": "attempt-1", "session_id": "session-1", "attempt_exit_code": 0,
         "session_present": True, "session_closed": True, "session_exit_code": None},
    ],
)
def test_review_pass_rejects_missing_or_pending_correlated_session(session_row):
    class Cursor:
        def dict_fetchall(self):
            return [session_row]

    class Connection:
        def execute(self, _sql, _params):
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Connection()

    db._get_connection = get_connection
    with pytest.raises(ReviewPassReconciliationError, match="not closed and complete"):
        db.validate_review_pass_reconciliation("plan-1", {})


def test_matching_closed_session_codes_need_no_disposition():
    class Cursor:
        def dict_fetchall(self):
            return []

    class Connection:
        def execute(self, _sql, _params):
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"

    @contextlib.contextmanager
    def get_connection():
        yield Connection()

    db._get_connection = get_connection
    db.validate_review_pass_reconciliation("plan-1", {})


def test_receipt_route_accepts_review_pass_with_linked_disposition(monkeypatch):
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }

    evidence_id = "123e4567-e89b-12d3-a456-426614174000"
    db = _review_pass_db([mismatch], {
        evidence_id: {
            "plan_ref": "plan-1", "record_type": "assessment", "role": "tester",
        },
    })
    persisted = {}

    def insert_receipt(**kwargs):
        db.validate_review_pass_reconciliation(kwargs["plan_id"], kwargs["metadata"])
        persisted.update(kwargs)

    db.insert_receipt = insert_receipt
    monkeypatch.setattr(routes_receipts, "_new_db_adapter", lambda: db)
    body = routes_receipts.ReceiptInsertRequest(
        id="receipt-review-pass-with-disposition",
        plan_id="plan-1",
        type="REVIEW_PASS",
        agent_role="reviewer",
        created_at="2026-10-25T00:00:00Z",
        metadata_json=json.dumps({
            "outcome_reconciliation": {
                "dispositions": [{
                    "attempt_id": "attempt-1",
                    "session_id": "session-1",
                    "disposition": "work_complete",
                    "evidence_ref": f"agent_record:{evidence_id}",
                }],
            },
        }),
    )

    assert routes_receipts.insert_receipt(body)["ok"] is True
    assert persisted["metadata"]["outcome_reconciliation"]["dispositions"][0]["evidence_ref"]


def test_receipt_route_returns_conflict_for_unresolvable_review_evidence(monkeypatch):
    mismatch = {
        "attempt_id": "attempt-1", "session_id": "session-1",
        "attempt_status": "SUCCEEDED", "attempt_exit_code": 0,
        "session_present": True, "session_closed": True, "session_exit_code": 1,
    }
    db = _review_pass_db([mismatch])
    db.insert_receipt = lambda **kwargs: db.validate_review_pass_reconciliation(
        kwargs["plan_id"], kwargs["metadata"]
    )
    monkeypatch.setattr(routes_receipts, "_new_db_adapter", lambda: db)
    body = routes_receipts.ReceiptInsertRequest(
        id="receipt-review-pass-invalid-evidence",
        plan_id="plan-1",
        type="REVIEW_PASS",
        agent_role="reviewer",
        created_at="2026-10-25T00:00:00Z",
        metadata_json=json.dumps({
            "outcome_reconciliation": {"dispositions": [{
                "attempt_id": "attempt-1",
                "session_id": "session-1",
                "disposition": "work_complete",
                "evidence_ref": "agent_record:123e4567-e89b-12d3-a456-426614174000",
            }]},
        }),
    )

    with pytest.raises(HTTPException) as raised:
        routes_receipts.insert_receipt(body)
    assert raised.value.status_code == 409
    assert "canonical agent_record" in raised.value.detail


def test_review_reject_remains_available_through_receipt_route(monkeypatch):
    class ReceiptWriter:
        inserted = None

        def insert_receipt(self, **kwargs):
            self.inserted = kwargs

    db = ReceiptWriter()
    monkeypatch.setattr(routes_receipts, "_new_db_adapter", lambda: db)
    body = routes_receipts.ReceiptInsertRequest(
        id="receipt-review-reject",
        plan_id="plan-1",
        type="REVIEW_REJECT",
        agent_role="reviewer",
        created_at="2026-10-25T00:00:00Z",
    )

    assert routes_receipts.insert_receipt(body)["ok"] is True
    assert db.inserted["receipt_type"] == "REVIEW_REJECT"


def test_run_pass_counts_finalization_pending_separately(monkeypatch):
    db = _WorkerDB()
    monkeypatch.setattr(worker, "_recover_orphans", lambda *_args: 0)
    monkeypatch.setattr(worker, "_retry_pending_governance_finalizations", lambda *_args: (0, 2))
    monkeypatch.setattr(worker, "_surface_session_exit_observations", lambda *_args: 1)
    monkeypatch.setattr(worker, "_role_lease_blocked", lambda *_args: None)
    monkeypatch.setattr(worker, "_reconcile_plans", lambda *_args: [])
    monkeypatch.setattr(worker, "_list_ready", lambda *_args: [{"id": "request-1"}])
    monkeypatch.setattr(worker, "_process_one", lambda *_args, **_kwargs: "finalization-pending")
    monkeypatch.setattr(db, "get_eligible_plans", lambda *_args: [])

    summary = worker._run_pass(db, "test-executor", False, False)

    assert summary["finalization_pending"] == 3
    assert summary["governance_finalized"] == 0
    assert summary["session_mismatches"] == 1
    assert summary["consumed"] == 0


def test_reconcile_rearms_only_authorized_states_and_preserves_update_races(caplog):
    requests = {
        "cancelled": {"id": "cancelled", "status": "CANCELLED"},
        "cancelled-requeued": {"id": "cancelled-requeued", "status": "CANCELLED"},
        "completed": {"id": "completed", "status": "COMPLETED"},
        "completed-review-reject": {"id": "completed-review-reject", "status": "COMPLETED"},
        "completed-critique-pass": {"id": "completed-critique-pass", "status": "COMPLETED"},
        "completed-old-rework": {"id": "completed-old-rework", "status": "COMPLETED"},
        "completed-race": {"id": "completed-race", "status": "COMPLETED"},
        "failed-no-requeue": {"id": "failed-no-requeue", "status": "FAILED"},
        "failed-requeued": {"id": "failed-requeued", "status": "FAILED"},
        "draft": {"id": "draft", "status": "DRAFT"},
    }

    class ReconcileDB:
        def __init__(self):
            self.requests = copy.deepcopy(requests)
            self.updated = []
            self.race_request_id = "completed-race"

        def get_eligible_plans(self, role):
            return [{"id": key, "title": key} for key in requests]

        def get_or_create_execution_request(self, plan_id, **_kwargs):
            return dict(self.requests[plan_id])

        def has_plan_requeued_after_last_attempt(self, plan_id, request_id):
            return plan_id == "failed-requeued"

        def has_plan_requeued_after_cancellation(self, plan_id, request_id):
            return plan_id == "cancelled-requeued"

        def has_plan_rework_after_last_success(self, plan_id, request_id):
            return plan_id in {
                "completed-review-reject", "completed-critique-pass", "completed-race",
            }

        @contextlib.contextmanager
        def _get_connection(self):
            db = self

            class Cursor:
                def __init__(self, rowcount=0, row=None):
                    self.rowcount = rowcount
                    self.row = row

                def fetchone(self):
                    return self.row

            class Conn:
                def execute(self, sql, params):
                    if sql.lstrip().startswith("UPDATE execution.requests"):
                        request_id, expected_status = params[:2]
                        db.updated.append((request_id, expected_status, sql, params))
                        if request_id == db.race_request_id:
                            db.requests[request_id]["status"] = "CANCELLED"
                            return Cursor(rowcount=0)
                        if db.requests[request_id]["status"] == expected_status:
                            db.requests[request_id]["status"] = "READY"
                            return Cursor(rowcount=1)
                        return Cursor(rowcount=0)
                    if sql.lstrip().startswith("SELECT status FROM execution.requests"):
                        return Cursor(row=(db.requests[params[0]]["status"],))
                    raise AssertionError(f"unexpected SQL: {sql}")

                def commit(self):
                    pass

            yield Conn()

    db = ReconcileDB()
    reconciled = worker._reconcile_plans(db)

    assert {row["id"] for row in reconciled} == set(requests)
    updated_ids = [item[0] for item in db.updated]
    assert updated_ids == [
        "cancelled-requeued", "completed-review-reject", "completed-critique-pass",
        "completed-race", "failed-requeued", "draft",
    ]
    assert all("WHERE r.id = %s AND r.status = %s" in item[2] for item in db.updated)
    statuses = {item["id"]: item["status"] for item in reconciled}
    assert statuses["failed-requeued"] == "READY"
    assert statuses["draft"] == "READY"
    assert statuses["cancelled"] == "CANCELLED"
    assert statuses["completed"] == "COMPLETED"
    assert statuses["cancelled-requeued"] == "READY"
    assert statuses["completed-review-reject"] == "READY"
    assert statuses["completed-critique-pass"] == "READY"
    assert statuses["completed-old-rework"] == "COMPLETED"
    assert statuses["failed-no-requeue"] == "FAILED"
    assert statuses["completed-race"] == "CANCELLED"
    assert db.requests["completed-race"]["status"] == "CANCELLED"
    assert db.requests["cancelled-requeued"]["status"] == "READY"
    assert db.requests["completed-review-reject"]["status"] == "READY"
    assert db.requests["completed-critique-pass"]["status"] == "READY"
    assert db.requests["completed-old-rework"]["status"] == "COMPLETED"
    assert "READY transition lost race/authorization" in caplog.text


def test_fallback_and_pending_governance_retry_preserve_success(monkeypatch, caplog):
    db = _WorkerDB()
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "contract"}
    launches = []

    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1",
        "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/primary"},
        {"harness": "opencode", "model": "provider/fallback"},
    ])

    def launch(req, *_args, **_kwargs):
        model = req["metadata"]["model"]
        launches.append(model)
        if model.endswith("primary"):
            raise executor_cloud.HarnessExecutionError(
                "child returned 17",
                executor_cloud.HarnessExecutionResult(17, "primary output\n", "primary error\n"),
            )
        return executor_cloud.HarnessExecutionResult(0, "fallback work result\n" + "x" * 2500, "")

    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", launch)
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda *_args, **_kwargs: None)

    outcome = worker._process_one(db, request, "test-executor", False)

    assert outcome == "finalization-pending"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.request_status == "COMPLETED"
    assert db.attempt_result["work_result"]["excerpt"].startswith("fallback work result\n")
    assert db.attempt_result["work_result"]["length_chars"] == len("fallback work result\n") + 2500
    assert db.attempt_result["work_result"]["truncated"] is True
    assert [entry["returncode"] for entry in db.attempt_result["model_attempts"]] == [17, 0]
    assert db.attempt_result["model_attempts"][0]["stdout"]["excerpt"] == "primary output\n"
    assert db.attempt_result["model_attempts"][0]["stdout"]["length_chars"] == len("primary output\n")
    assert db.attempt_result["model_attempts"][0]["stderr"]["excerpt"] == "primary error\n"
    assert len(db.attempt_result["process_outcome"]["stdout"]["excerpt"]) == worker.OUTCOME_EXCERPT_CHARS
    assert db.attempt_result["process_outcome"]["stdout"]["length_chars"] == len("fallback work result\n") + 2500
    assert db.attempt_result["session_log_path"].endswith(f"{db.session_id}.log")
    assert db.attempt_result["governance_finalization"]["status"] == "pending"
    assert "lost response" in db.attempt_result["governance_finalization"]["error"]
    assert len(db.execution_receipts) == 1
    assert len(db.plan_receipts) == 1
    assert len(db.reviewer_tickets) == 1

    # The session row is an independent observation; the worker has correlated
    # it by ID but does not copy its exit code into the attempt outcome.
    assert db.attempt_result["process_outcome"]["returncode"] == 0
    assert db.attempt_result["session_id"] == db.session_id
    assert db.reviewer_criteria and "disposition" in db.reviewer_criteria
    worker._surface_session_exit_observations(db)
    assert "attempt_exit_code=0 session_exit_code=1" in caplog.text
    assert db.attempt_result["session_outcome"]["exit_code"] == 1
    assert db.attempt_result["outcome_reconciliation"]["review_required"] is True
    assert db.attempt_result["outcome_reconciliation"]["disposition"] is None
    warning_count = caplog.text.count("review required: independent attempt/session exit mismatch")
    worker._surface_session_exit_observations(db)
    assert caplog.text.count("review required: independent attempt/session exit mismatch") == warning_count

    # The retry runs only finalization. The simulated ticket commit happened
    # before its response was lost; deterministic receipt/ticket identities
    # leave both counts unchanged on retry and no second builder is launched.
    assert worker._retry_pending_governance_finalizations(db) == (1, 0)
    assert db.attempt_result["governance_finalization"]["status"] == "complete"
    assert len(db.execution_receipts) == 1
    assert len(db.plan_receipts) == 1
    assert len(db.reviewer_tickets) == 1
    assert len(launches) == 2


def test_reviewer_ticket_id_is_stable_for_finalization_key():
    class Cursor:
        def __init__(self, rowcount=0):
            self.rowcount = rowcount

        def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.ticket_ids = set()

        def execute(self, sql, params=None):
            if "INSERT INTO tickets" in sql:
                ticket_id = params[0]
                inserted = ticket_id not in self.ticket_ids
                self.ticket_ids.add(ticket_id)
                return Cursor(1 if inserted else 0)
            return Cursor()

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    db.close_orphaned_tickets = lambda *_args, **_kwargs: None

    first = db.create_next_tickets(
        "plan-1", "builder", "completed", parent_ticket_id="builder-ticket",
        idempotency_key="execution-worker:attempt-1:reviewer",
    )
    retry = db.create_next_tickets(
        "plan-1", "builder", "completed", parent_ticket_id="builder-ticket",
        idempotency_key="execution-worker:attempt-1:reviewer",
    )

    assert first == 1
    assert retry == 0
    assert len(connection.ticket_ids) == 1


def test_reviewer_criteria_update_is_limited_to_matching_open_ticket():
    class Cursor:
        def __init__(self, row=None, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class Connection:
        status = "completed"
        criteria = "old criterion"

        def execute(self, sql, params):
            if "SELECT id, status, completion_criteria" in sql:
                return Cursor(("reviewer-1", self.status, self.criteria))
            if "UPDATE tickets SET completion_criteria" in sql:
                if self.status == "open":
                    self.criteria = params[0]
                    return Cursor(rowcount=1)
                return Cursor(rowcount=0)
            raise AssertionError(f"unexpected SQL: {sql}")

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    criteria = "disposition of linked attempt/session mismatches required before REVIEW_PASS"

    assert db.ensure_reviewer_ticket_completion_criteria(
        "plan-1", "builder-ticket", "wr-1", criteria
    ) is False
    assert connection.criteria == "old criterion"

    connection.status = "open"
    assert db.ensure_reviewer_ticket_completion_criteria(
        "plan-1", "builder-ticket", "wr-1", criteria
    ) is True
    assert connection.criteria == criteria

    connection.status = "claimed"
    assert db.ensure_reviewer_ticket_completion_criteria(
        "plan-1", "builder-ticket", "wr-1", criteria
    ) is True


def test_plan_receipt_visibility_uses_source_identity_for_each_storage_path():
    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, mode):
            self.mode = mode
            self.sql = []

        def execute(self, sql, params):
            self.sql.append((sql, params))
            if self.mode == "request-backed" and "FROM execution.receipts e" in sql:
                return Cursor((1,))
            if self.mode == "synthetic" and "FROM vision.receipts" in sql:
                return Cursor((1,))
            return Cursor()

    for mode, expected_source in (
        ("request-backed", "lineage_original_id = %s"),
        ("synthetic", "FROM vision.receipts"),
    ):
        db = DBAdapter.__new__(DBAdapter)
        connection = Connection(mode)

        @contextlib.contextmanager
        def get_connection():
            yield connection

        db._get_connection = get_connection
        assert db.has_plan_receipt("plan-1", "receipt-1", "IMPLEMENTATION") is True
        assert any(expected_source in sql for sql, _params in connection.sql)
        if mode == "request-backed":
            query, params = connection.sql[0]
            assert "JOIN execution.requests r" in query
            assert "r.source_plan_id = %s" in query
            assert params == ("plan-1", "receipt-1", "IMPLEMENTATION")


def test_pending_finalization_lookup_does_not_require_request_completed():
    class Cursor:
        rowcount = 1

        def dict_fetchall(self):
            return []

    class Connection:
        sql = ""
        params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor()

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    assert db.get_pending_governance_finalizations() == []
    assert "a.status = 'SUCCEEDED'" in connection.sql
    assert "governance_finalization'->>'status' = 'pending'" in connection.sql
    assert "r.status" not in connection.sql

    assert db.mark_request_completed_after_attempt("attempt-1", "request-1") is True
    assert "a.status = 'SUCCEEDED' AND a.exit_code = 0" in connection.sql
    assert "er.attempt_id = %s" in connection.sql
    assert "er.type IN ('EXECUTION_COMPLETE', 'IMPLEMENTATION')" in connection.sql


@pytest.mark.parametrize(
    "current_status,expected",
    [("CANCELLED", False), ("FAILED", False), ("COMPLETED", True)],
)
def test_request_completion_preserves_terminal_status(current_status, expected):
    class Cursor:
        rowcount = 0

        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))
            if sql.lstrip().startswith("UPDATE execution.requests"):
                assert "r.status = 'READY'" in sql
                return Cursor()
            return Cursor((current_status, True))

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    assert db.mark_request_completed_after_attempt("attempt-1", "request-1") is expected
    if current_status == "CANCELLED":
        assert all("SET status = 'COMPLETED'" not in sql or "r.status = 'READY'" in sql
                   for sql, _params in connection.calls)


def test_terminal_requeue_sql_requires_newer_authorizing_receipts():
    class Cursor:
        def fetchone(self):
            return (True,)

        rowcount = 1

    class Connection:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params):
            self.sql.append((sql, params))
            return Cursor()

        def commit(self):
            pass

    db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    assert db.has_plan_requeued_after_last_attempt("plan-1", "request-1") is True
    assert "r.type = 'REQUEUED'" in connection.sql[-1][0]
    assert "MAX(a.completed_at)" in connection.sql[-1][0]

    assert db.has_plan_requeued_after_cancellation("plan-1", "request-1") is True
    cancel_sql = connection.sql[-1][0]
    assert "r.status = 'CANCELLED'" in cancel_sql
    assert "rr.type = 'REQUEUED'" in cancel_sql
    assert "rr.created_at > r.updated_at" in cancel_sql

    assert db.has_plan_rework_after_last_success("plan-1", "request-1") is True
    completed_sql = connection.sql[-1][0]
    assert "r.status = 'COMPLETED'" in completed_sql
    assert "rr.type IN ('REVIEW_REJECT', 'CRITIQUE_PASS')" in completed_sql
    assert "MAX(a.completed_at)" in completed_sql


def test_session_scan_query_filters_to_unobserved_mismatches_before_limit():
    class Cursor:
        def dict_fetchall(self):
            return []

    class Connection:
        sql = ""

        def execute(self, sql, params):
            self.sql = sql
            return Cursor()

    db = DBAdapter.__new__(DBAdapter)
    db._schema = "conduit"
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    db._get_connection = get_connection
    assert db.get_worker_session_outcomes(limit=100) == []
    assert "s.end_iso IS NOT NULL" in connection.sql
    assert "a.exit_code IS DISTINCT FROM s.exit_code" in connection.sql
    assert "outcome_reconciliation'->>'attempt_exit_code' = a.exit_code::text" in connection.sql
    assert "AND NOT COALESCE((" in connection.sql
    assert "ORDER BY a.completed_at ASC" in connection.sql
    assert "LIMIT %s" in connection.sql


def test_database_pending_row_shape_is_accepted_by_recovery_finalizer():
    result = {
        "attempt_id": "attempt-1",
        "request_id": "request-1",
        "plan_id": "plan-1",
        "session_id": "session-1",
        "ticket_id": "builder-ticket-1",
        "work_request_id": "wr-1",
        "model": "provider/model",
        "executor_id": "test-executor",
        "process_outcome": {"returncode": 0},
        "governance_finalization": {"status": "pending"},
    }
    pending_row = {
        "attempt_id": "attempt-1",
        "request_id": "request-1",
        "result": result,
        "attempt_status": "SUCCEEDED",
        "exit_code": 0,
        "source_plan_id": "plan-1",
    }

    class Cursor:
        rowcount = 1

        def dict_fetchall(self):
            return [copy.deepcopy(pending_row)]

    class Connection:
        sql = ""

        def execute(self, sql, params):
            self.sql = sql
            return Cursor()

        def commit(self):
            pass

    query_db = DBAdapter.__new__(DBAdapter)
    connection = Connection()

    @contextlib.contextmanager
    def get_connection():
        yield connection

    query_db._get_connection = get_connection
    rows = query_db.get_pending_governance_finalizations()
    assert rows == [pending_row]
    assert "a.status AS attempt_status" in connection.sql
    assert rows[0]["attempt_status"] == "SUCCEEDED"

    db = _WorkerDB()
    db.attempt_status = "SUCCEEDED"
    db.attempt_exit_code = 0
    db.attempt_result = copy.deepcopy(result)
    db.ticket_write_committed_then_failed = False
    finalized = worker._finalize_governance_row(db, rows[0])

    assert db.request_status == "COMPLETED"
    assert db.request_status_at_reviewer_creation == "COMPLETED"
    assert db.has_attempt_execution_receipt("attempt-1", "request-1")
    assert finalized["governance_finalization"]["status"] == "complete"
