"""Integration tests for the Temporal Conduit pipeline.

Two tests:
- test_workflow_direct: submits PlanExecutionWorkflow directly, asserts failure path
- test_scheduler_dispatch: runs a scheduler cycle, asserts workflows are dispatched

Uses isolated PostgreSQL schemas, short failure-recovery config, and
/bin/false as harness binary so tests complete in seconds.

Requires: Temporal dev server on localhost:7233, PostgreSQL accessible.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
import pytest
from temporalio.client import Client
from temporalio.worker import Worker

# ── Path setup ─────────────────────────────────────────────────────
_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from temporal.activities.db_operations import (
    claim_ticket_activity, insert_receipt_activity, advance_cursor_activity,
    create_next_tickets_activity, create_session_activity, close_session_activity,
    close_ticket_activity, update_work_request_status_activity,
    add_work_request_activity, get_eligible_plans_activity,
    get_plan_by_id_activity, get_role_model_config_activity,
    get_fallback_models_activity, get_failure_recovery_config_activity,
    is_circuit_breaker_tripped_activity, is_conduit_paused_activity,
    trip_and_requeue_activity, detect_stale_tickets_activity,
    detect_expired_tickets_activity, increment_ticket_tokens_activity,
    release_ticket_activity,
)
from temporal.activities.execute_model import execute_with_model
from temporal.activities.work_request import (
    build_work_request_dco_activity, resolve_model_chain_activity,
)
from temporal.workflows.plan_execution import PlanExecutionWorkflow
from temporal.scheduler import Scheduler

# ── Constants ───────────────────────────────────────────────────────

_DSN = os.environ.get("CONDUIT_PG_DSN") or (
    "host=localhost port=5433 user=pguser password=pgpass dbname=nexus"
)
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEST_NAMESPACE = "conduit"

ALL_ACTIVITIES = [
    claim_ticket_activity, insert_receipt_activity, advance_cursor_activity,
    create_next_tickets_activity, create_session_activity, close_session_activity,
    close_ticket_activity, update_work_request_status_activity,
    add_work_request_activity, get_eligible_plans_activity,
    get_plan_by_id_activity, get_role_model_config_activity,
    get_fallback_models_activity, get_failure_recovery_config_activity,
    is_circuit_breaker_tripped_activity, is_conduit_paused_activity,
    trip_and_requeue_activity, detect_stale_tickets_activity,
    detect_expired_tickets_activity, increment_ticket_tokens_activity,
    release_ticket_activity, execute_with_model,
    build_work_request_dco_activity, resolve_model_chain_activity,
]

_FAST_FAILURE_SQL = """
    INSERT INTO circuit_breaker (id, tripped, paused, updated_at,
        max_retries_per_model, retry_delay_seconds, max_fallbacks,
        push_back_to_pending)
    VALUES (1, 0, 0, %(now)s, 1, 1, 0, 0)
    ON CONFLICT (id) DO UPDATE SET
        max_retries_per_model=1, retry_delay_seconds=1, max_fallbacks=0,
        push_back_to_pending=0, updated_at=%(now)s
"""


# ── Fixtures ────────────────────────────────────────────────────────


class TestSchema:
    """Pytest fixture helper: isolated PostgreSQL schema with seeded data."""
    __test__ = False  # Not a test class

    def __init__(self):
        self.schema_name = f"test_temporal_{uuid.uuid4().hex[:8]}"
        self._prev_schema = os.environ.get("CONDUIT_PG_SCHEMA", "conduit")
        self._conn = psycopg2.connect(_DSN)
        self._conn.autocommit = True
        self._cur = self._conn.cursor()
        self._cur.execute(f"CREATE SCHEMA {self.schema_name}")
        self._cur.execute(f"SET search_path TO {self.schema_name}")
        os.environ["CONDUIT_PG_SCHEMA"] = self.schema_name
        self._create_tables()
        self._seed_data()

    def cleanup(self):
        self._cur.close()
        self._conn.close()
        c = psycopg2.connect(_DSN)
        c.autocommit = True
        cur = c.cursor()
        cur.execute(f"DROP SCHEMA {self.schema_name} CASCADE")
        cur.close()
        c.close()
        os.environ["CONDUIT_PG_SCHEMA"] = self._prev_schema

    def query(self, sql, params=()):
        conn = psycopg2.connect(_DSN)
        cur = conn.cursor()
        cur.execute(f"SET search_path TO {self.schema_name}")
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ── DDL ─────────────────────────────────────────────────────

    def _create_tables(self):
        c = self._cur
        c.execute("""
            CREATE TABLE plans (
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
            CREATE TABLE work_requests (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                status TEXT NOT NULL, dco_json TEXT NOT NULL,
                step_outputs TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE pipeline_cursor (
                role TEXT PRIMARY KEY,
                last_processed_plan_id TEXT,
                last_work_request_id TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE tickets (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                session_id TEXT, created_by_receipt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, claimed_at TEXT, closed_at TEXT,
                token_budget INTEGER, tokens_used INTEGER,
                objective TEXT, completion_criteria TEXT,
                owner TEXT NOT NULL DEFAULT '',
                parent_ticket_id TEXT, spawn_reason TEXT,
                last_activity TEXT, expires_at TEXT,
                confidence REAL, closure_reason TEXT, replacement_of TEXT
            )
        """)
        c.execute("""
            CREATE UNIQUE INDEX idx_tickets_open_test
            ON tickets(plan_id, role) WHERE status = 'open'
        """)
        c.execute("""
            CREATE TABLE receipts (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                type TEXT NOT NULL, agent_role TEXT NOT NULL,
                session_id TEXT, artifact_path TEXT,
                summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                ticket_id TEXT, tokens_used INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, agent_role TEXT NOT NULL,
                start_iso TEXT NOT NULL, end_iso TEXT, exit_code INTEGER,
                plans_processed TEXT NOT NULL DEFAULT '[]',
                plan_count INTEGER DEFAULT 0, pid INTEGER,
                is_running INTEGER DEFAULT 1, last_activity TEXT,
                model TEXT, cost_usd REAL,
                total_work_seconds REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                workflow_id TEXT, run_id TEXT,
                workflow_start_time TEXT, workflow_close_time TEXT,
                workflow_run_time_ms REAL, workflow_result TEXT
            )
        """)
        c.execute("""
            CREATE TABLE plan_status (
                id TEXT PRIMARY KEY, derived_status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE circuit_breaker (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
                tripped INTEGER DEFAULT 0, tripped_at TEXT,
                retry_after INTEGER DEFAULT 1800, error TEXT, detail TEXT,
                source TEXT, paused INTEGER DEFAULT 0, updated_at TEXT,
                max_retries_per_model INTEGER DEFAULT 3,
                retry_delay_seconds INTEGER DEFAULT 120,
                max_fallbacks INTEGER DEFAULT 3,
                push_back_to_pending INTEGER DEFAULT 1
            )
        """)
        for tbl_sql in [
            """CREATE TABLE providers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
                endpoint_url TEXT, api_key TEXT,
                config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE harnesses (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                invocation_semantics TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE models (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                harness_id TEXT NOT NULL, provider_id TEXT NOT NULL DEFAULT '',
                model_identifier TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE role_config (
                id TEXT PRIMARY KEY, role TEXT NOT NULL UNIQUE,
                provider_id TEXT NOT NULL, harness_id TEXT NOT NULL,
                model_id TEXT NOT NULL, extra_params TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE role_models (
                id TEXT PRIMARY KEY, role TEXT NOT NULL, model_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                harness_id TEXT, provider_id TEXT,
                UNIQUE(role, model_id))""",
        ]:
            c.execute(tbl_sql)

    # ── Seed data ────────────────────────────────────────────────

    def _seed_data(self):
        now = _iso_now()
        c = self._cur

        c.execute(
            "INSERT INTO plans (id, file_name, title, goal, project, "
            "files_affected, acceptance_criteria, dependencies, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("TEST-001", "test.md", "Integration Test Plan",
             "Verify pipeline end-to-end", "conduit-test",
             json.dumps(["src/main.py"]), json.dumps(["Completes"]),
             json.dumps([]), now, now),
        )

        self._ticket_id = f"ticket-TEST-001-builder-{uuid.uuid4().hex[:8]}"
        c.execute(
            "INSERT INTO tickets (id, plan_id, role, status, "
            "created_by_receipt, created_at, objective, owner, "
            "last_activity, expires_at) "
            "VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)",
            (self._ticket_id, "TEST-001", "builder", "integration-test",
             now, "Integration Test Plan", "builder", now,
             (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z"),
        )

        # Seed plan_status (required by get_eligible_plans for scheduler dispatch)
        c.execute(
            "INSERT INTO plan_status (id, derived_status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s)",
            ("TEST-001", "PLAN_CREATE", now, now),
        )

        # Fast failure config: 1 retry, 1s delay, 0 fallbacks, no requeue
        c.execute(_FAST_FAILURE_SQL, {"now": now})

        # AI config (needed by resolve_model_chain)
        harness_semantics = json.dumps({
            "binary": "opencode",
            "capabilities": {"model": True, "agent": True, "working_directory": True},
            "execution": {"mode": "interactive", "subcommand": "run"},
            "semantics": {
                "model": {"type": "flag", "flag": "--model"},
                "agent": {"type": "flag", "flag": "--agent"},
                "working_directory": {"type": "flag", "flag": "--dir"},
            },
            "role_mapping": {"strategy": "agent"},
        })
        c.execute(
            "INSERT INTO providers (id, name, type, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("prov-test", "Test Provider", "opencode", now, now),
        )
        c.execute(
            "INSERT INTO harnesses (id, name, invocation_semantics, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
            ("harness-test", "test-opencode", harness_semantics, now, now),
        )
        c.execute(
            "INSERT INTO models (id, name, harness_id, provider_id, "
            "model_identifier, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (            "model-test", "test-model", "harness-test", "prov-test",
            "test-model-v1", now, now),
        )
        c.execute(
            "INSERT INTO role_config (id, role, provider_id, harness_id, "
            "model_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("rc-builder", "builder", "prov-test", "harness-test", "model-test",
             now, now),
        )
        c.execute(
            "INSERT INTO role_models (id, role, model_id, priority, "
            "harness_id, provider_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("rm-builder-1", "builder", "model-test", 0, "harness-test", "prov-test"),
        )


def _iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ── Test: Direct workflow submission (tests the workflow itself) ────


@pytest.mark.asyncio
async def test_workflow_failure_path():
    """Submit PlanExecutionWorkflow directly and verify the failure lifecycle.

    Executor fails → HarnessError/LaunchError → BLOCK receipt →
    ticket='failed', session closed, work request created.
    """
    schema = TestSchema()
    try:
        client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEST_NAMESPACE)
        test_queue = f"test-direct-{uuid.uuid4().hex[:6]}"

        worker = Worker(
            client, task_queue=test_queue,
            workflows=[PlanExecutionWorkflow], activities=ALL_ACTIVITIES,
            max_concurrent_activities=4,
        )
        worker_task = asyncio.create_task(worker.run())
        await asyncio.sleep(1)

        wf_id = f"test-direct-{uuid.uuid4().hex[:6]}"
        handle = await client.start_workflow(
            "PlanExecutionWorkflow",
            args=["TEST-001", "builder", False],
            id=wf_id, task_queue=test_queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=45)
        assert result == "failed", f"Expected 'failed', got '{result}'"

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # ── Exact assertions ────────────────────────────────────
        ticket = schema.query_one(
            "SELECT status, session_id FROM tickets "
            "WHERE plan_id = 'TEST-001' AND role = 'builder' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        assert ticket[0] == "failed"
        session_id = ticket[1]
        assert session_id is not None

        # Exactly 1 BLOCK receipt, 0 IMPLEMENTATION
        receipts = schema.query(
            "SELECT type FROM receipts WHERE plan_id = 'TEST-001' "
            "ORDER BY created_at ASC"
        )
        receipt_types = [r[0] for r in receipts]
        assert receipt_types == ["BLOCK"], (
            f"Expected ['BLOCK'], got {receipt_types}"
        )

        # Session closed with exit_code = -1
        session = schema.query_one(
            "SELECT is_running, exit_code FROM sessions WHERE id = %s",
            (session_id,),
        )
        assert session is not None
        assert session[0] == 0, "Session should be closed"
        assert session[1] == -1, f"Session exit_code should be -1, got {session[1]}"

        # Work request created with status='pending'
        wr = schema.query_one(
            "SELECT status FROM work_requests WHERE plan_id = 'TEST-001'"
        )
        assert wr is not None
        assert wr[0] == "pending", f"Work request status should be 'pending', got {wr[0]}"

    finally:
        schema.cleanup()


# ── Test: Session metadata (tests workflow metadata in sessions table) ─


@pytest.mark.asyncio
async def test_workflow_session_metadata():
    """Submit PlanExecutionWorkflow directly and verify session metadata.

    Asserts that Temporal workflow metadata (workflow_id, run_id,
    workflow_start_time, workflow_close_time, workflow_run_time_ms,
    workflow_result) is correctly populated in the sessions table.
    """
    schema = TestSchema()
    try:
        client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEST_NAMESPACE)
        test_queue = f"test-session-meta-{uuid.uuid4().hex[:6]}"

        worker = Worker(
            client, task_queue=test_queue,
            workflows=[PlanExecutionWorkflow], activities=ALL_ACTIVITIES,
            max_concurrent_activities=4,
        )
        worker_task = asyncio.create_task(worker.run())
        await asyncio.sleep(1)

        wf_id = f"test-session-meta-{uuid.uuid4().hex[:6]}"
        handle = await client.start_workflow(
            "PlanExecutionWorkflow",
            args=["TEST-001", "builder", False],
            id=wf_id, task_queue=test_queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=45)
        assert result == "failed", f"Expected 'failed', got '{result}'"

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # ── Session metadata assertions ────────────────────────
        session = schema.query_one(
            "SELECT workflow_id, run_id, workflow_start_time, "
            "workflow_close_time, workflow_run_time_ms, workflow_result "
            "FROM sessions"
        )
        assert session is not None, "No session found"

        wid, rid, start, close, run_ms, wf_res = session

        assert wid is not None, "workflow_id should not be None"
        assert wid != "", "workflow_id should not be empty"
        assert wid == wf_id, f"workflow_id should match, got '{wid}'"

        assert rid is not None, "run_id should not be None"
        assert rid != "", "run_id should not be empty"

        assert start is not None, "workflow_start_time should not be None"
        assert start != "", "workflow_start_time should not be empty"

        assert close is not None, "workflow_close_time should not be None"
        assert close != "", "workflow_close_time should not be empty"

        assert run_ms is not None, "workflow_run_time_ms should not be None"
        assert run_ms > 0, f"workflow_run_time_ms should be positive, got {run_ms}"

        assert wf_res == "failed", f"workflow_result should be 'failed', got '{wf_res}'"

        # Also verify standard session fields
        session_full = schema.query_one(
            "SELECT id, agent_role, is_running, exit_code FROM sessions"
        )
        assert session_full is not None
        assert session_full[2] == 0, "Session should be closed (is_running=0)"
        assert session_full[3] == -1, f"Exit code should be -1, got {session_full[3]}"

    finally:
        schema.cleanup()


# ── Test: Scheduler dispatch (tests the scheduler + worker) ─────────


@pytest.mark.asyncio
async def test_scheduler_dispatch():
    """Run a scheduler cycle and verify workflows are dispatched + executed.

    Uses the Scheduler class directly (same code path as `scheduler.py --once`).
    The worker picks up dispatched workflows; we wait for completion via
    workflow handle (not a fixed sleep).  Unconditional assertions — the
    test fails if the scheduler doesn't dispatch or the worker doesn't process.
    """
    schema = TestSchema()
    try:
        client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEST_NAMESPACE)

        # Start worker FIRST so it's ready when the scheduler dispatches
        worker = Worker(
            client, task_queue="builder",
            workflows=[PlanExecutionWorkflow], activities=ALL_ACTIVITIES,
            max_concurrent_activities=4,
        )
        worker_task = asyncio.create_task(worker.run())
        await asyncio.sleep(2)  # Give worker time to start polling

        # Run one scheduler cycle (same as `scheduler.py --once`)
        scheduler = Scheduler(
            temporal_address=TEMPORAL_ADDRESS,
            temporal_namespace=TEST_NAMESPACE,
            roles=["builder"],
            interval=1, idle_backoff=1, once=True,
        )
        await scheduler.start()

        # Wait deterministically for the workflow to complete.
        # The scheduler dispatches with ID pattern "plan-{plan_id}-{role}".
        wf_id = "plan-TEST-001-builder"
        handle = client.get_workflow_handle(wf_id)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=45)
        except asyncio.TimeoutError as exc:
            raise AssertionError(
                f"Workflow {wf_id} timed out — scheduler may not have dispatched: {exc}"
            ) from exc
        assert result == "failed", f"Expected 'failed', got '{result}'"

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # ── Exact assertions (same as direct test) ──────────────
        ticket = schema.query_one(
            "SELECT status, session_id FROM tickets "
            "WHERE plan_id = 'TEST-001' AND role = 'builder' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        assert ticket is not None, "No ticket found after dispatch"
        assert ticket[0] == "failed", f"Expected 'failed', got '{ticket[0]}'"
        session_id = ticket[1]
        assert session_id is not None

        receipts = schema.query(
            "SELECT type FROM receipts WHERE plan_id = 'TEST-001' "
            "ORDER BY created_at ASC"
        )
        receipt_types = [r[0] for r in receipts]
        assert receipt_types == ["BLOCK"], (
            f"Expected ['BLOCK'], got {receipt_types}"
        )

        session = schema.query_one(
            "SELECT is_running, exit_code FROM sessions WHERE id = %s",
            (session_id,),
        )
        assert session is not None
        assert session[0] == 0, "Session should be closed"
        assert session[1] == -1, f"Session exit_code should be -1, got {session[1]}"

        # ── Session metadata assertions (E2E) ───────────────────
        session_meta = schema.query_one(
            "SELECT workflow_id, run_id, workflow_start_time, "
            "workflow_close_time, workflow_run_time_ms, workflow_result "
            "FROM sessions WHERE id = %s",
            (session_id,),
        )
        assert session_meta is not None, "No session metadata found"

        wid, rid, start, close, run_ms, wf_res = session_meta

        assert wid is not None, "workflow_id should not be None"
        assert wid != "", "workflow_id should not be empty"
        assert wid == wf_id, f"workflow_id should match '{wf_id}', got '{wid}'"

        assert rid is not None, "run_id should not be None"
        assert rid != "", "run_id should not be empty"

        assert start is not None, "workflow_start_time should not be None"
        assert start != "", "workflow_start_time should not be empty"

        assert close is not None, "workflow_close_time should not be None"
        assert close != "", "workflow_close_time should not be empty"

        assert run_ms is not None, "workflow_run_time_ms should not be None"
        assert run_ms > 0, f"workflow_run_time_ms should be positive, got {run_ms}"

        assert wf_res == "failed", f"workflow_result should be 'failed', got '{wf_res}'"

    finally:
        schema.cleanup()
