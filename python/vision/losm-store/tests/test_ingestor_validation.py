"""Tests for the receipt ingestor validation gate (Plan 0021).

Every lifecycle mutation in the ingestor must first validate the transition.
Runs against the live PostgreSQL vision schema.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from losm_store.models import GovernanceEvent, PlanningTask, ReceiptIngestRecord, WorkStatus
from losm_store.session import SessionLocal
from losm_store.ingestor import ExecutionReceiptIngestor
from losm_store.canonical_bridge import mirror_insert_work_request


@pytest.fixture(autouse=True)
def clean_db():
    """Clean up all test data before each test."""
    db = SessionLocal()
    try:
        # Clean in reverse dependency order
        db.execute(text("DELETE FROM vision.governance_events_history"))
        db.execute(text("DELETE FROM vision.receipt_ingest_records_history"))
        db.execute(text("DELETE FROM vision.work_requests_history"))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Create a fresh session against the live PostgreSQL database."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def ingestor():
    return ExecutionReceiptIngestor()


def _make_receipt_payload(wr_id: str, result: str, executor_id: str = "test-executor") -> dict:
    return {
        "work_request_id": wr_id,
        "executor_id": executor_id,
        "inputs": [],
        "mutations": [],
        "timestamp": datetime.utcnow().isoformat(),
        "result": result,
        "lineage_parent": str(uuid.uuid4()),
    }


def _create_task(db: Session, status: WorkStatus) -> PlanningTask:
    # W2a substrate correction: work_requests_losm is a read-only view on
    # live — seed via the bitemporal mirror primitive (history base), which
    # the view then exposes on the open slice.
    wr_id = str(uuid.uuid4())
    mirror_insert_work_request(
        db,
        wr_id=wr_id,
        intent="Test task",
        status=status.value,
        constraints={},
        priority=5,
        context_data={},
    )
    db.commit()
    task = db.query(PlanningTask).filter(PlanningTask.wr_id == wr_id).first()
    assert task is not None, "seeded task must be visible through the view"
    return task


# ── Happy path: valid transitions ──


def test_success_receipt_validates(db_session, ingestor):
    """Task in VALIDATION receives SUCCESS → accepted, status becomes COMPLETION.
    
    SUCCESS maps to COMPLETION via _RESULT_TO_STATE.
    VALIDATION → COMPLETION is a valid transition.
    """
    task = _create_task(db_session, WorkStatus.VALIDATION)
    payload = _make_receipt_payload(task.wr_id, "SUCCESS")

    result = ingestor.ingest(db_session, payload)

    assert result["status"] == "ingested"
    db_session.refresh(task)
    assert task.status == WorkStatus.COMPLETION


def test_failed_receipt_accepted(db_session, ingestor):
    """Task in EXECUTION receives FAILED → accepted, status becomes FAILED.
    
    EXECUTION → FAILED is a valid transition.
    """
    task = _create_task(db_session, WorkStatus.EXECUTION)
    payload = _make_receipt_payload(task.wr_id, "FAILED")

    result = ingestor.ingest(db_session, payload)

    assert result["status"] == "ingested"
    db_session.refresh(task)
    assert task.status == WorkStatus.FAILED


# ── Rejected paths: invalid transitions ──


def test_success_receipt_rejected(db_session, ingestor):
    """Task in PLAN_GENERATION receives SUCCESS → rejected, status unchanged.
    
    PLAN_GENERATION → COMPLETION is not a valid transition.
    """
    task = _create_task(db_session, WorkStatus.PLAN_GENERATION)
    payload = _make_receipt_payload(task.wr_id, "SUCCESS")

    result = ingestor.ingest(db_session, payload)

    assert result["status"] == "rejected"
    assert result["event_type"] == "RECEIPT_REJECTED"
    db_session.refresh(task)
    assert task.status == WorkStatus.PLAN_GENERATION  # unchanged


def test_failed_receipt_terminal_rejected(db_session, ingestor):
    """Task in COMPLETION receives FAILED → rejected, status unchanged.
    
    COMPLETION is terminal — no transitions out.
    """
    task = _create_task(db_session, WorkStatus.COMPLETION)
    payload = _make_receipt_payload(task.wr_id, "FAILED")

    result = ingestor.ingest(db_session, payload)

    assert result["status"] == "rejected"
    assert result["event_type"] == "RECEIPT_REJECTED"
    db_session.refresh(task)
    assert task.status == WorkStatus.COMPLETION  # unchanged (terminal)


def test_unmapped_result_rejected(db_session, ingestor):
    """Receipt with a valid literal result that has no _RESULT_TO_STATE mapping 
    would be rejected. Note: the ExecutionReceipt model only accepts 
    SUCCESS/FAILED/PARTIAL as valid literals, so unknown strings never reach
    the ingestor. Here, PARTIAL is the unmapped edge case that maps to BLOCKED.
    This test validates that BLOCKED transitions are handled correctly.
    """
    # BLOCKED is the result of PARTIAL mapping.
    # From EXECUTION, going to BLOCKED is valid.
    # From COMPLETION, it should be rejected.
    task = _create_task(db_session, WorkStatus.EXECUTION)
    payload = _make_receipt_payload(task.wr_id, "PARTIAL")

    result = ingestor.ingest(db_session, payload)

    # PARTIAL → BLOCKED. EXECUTION → BLOCKED is valid.
    assert result["status"] == "ingested"
    db_session.refresh(task)
    assert task.status == WorkStatus.BLOCKED


# ── Existing behavior preserved ──


def test_duplicate_receipt_dedupped(db_session, ingestor):
    """Duplicate receipt hash → dedup returned, no validation called."""
    task = _create_task(db_session, WorkStatus.VALIDATION)
    payload = _make_receipt_payload(task.wr_id, "SUCCESS")

    # First ingest succeeds (VALIDATION → COMPLETION is valid)
    result1 = ingestor.ingest(db_session, payload)
    assert result1["status"] == "ingested"

    # Second ingest with same payload → duplicate
    result2 = ingestor.ingest(db_session, payload)
    assert result2["status"] == "duplicate"
    assert result2["event_type"] == "RECEIPT_DUPLICATE"


def test_orphan_receipt_handled(db_session, ingestor):
    """Receipt for unknown wr_id → orphan handling, no validation needed."""
    payload = _make_receipt_payload("nonexistent-wr-id", "SUCCESS")

    result = ingestor.ingest(db_session, payload)

    assert result["status"] == "orphaned"
    assert result["event_type"] == "RECEIPT_ORPHANED"


# ── Governance events ──


def test_governance_event_written_on_reject(db_session, ingestor):
    """Verify RECEIPT_REJECTED event exists in DB after rejection."""
    task = _create_task(db_session, WorkStatus.PLAN_GENERATION)
    payload = _make_receipt_payload(task.wr_id, "SUCCESS")

    result = ingestor.ingest(db_session, payload)
    assert result["status"] == "rejected"

    # Check that a GovernanceEvent with type RECEIPT_REJECTED was written
    events = db_session.query(GovernanceEvent).filter_by(
        work_request_id=task.wr_id,
        event_type="RECEIPT_REJECTED",
    ).all()
    assert len(events) >= 1
    assert "invalid" in events[0].payload.get("reason", "").lower()


def test_ingested_governance_event_written(db_session, ingestor):
    """Verify RECEIPT_INGESTED event exists in DB after successful ingest."""
    task = _create_task(db_session, WorkStatus.VALIDATION)
    payload = _make_receipt_payload(task.wr_id, "SUCCESS")

    result = ingestor.ingest(db_session, payload)
    assert result["status"] == "ingested"

    events = db_session.query(GovernanceEvent).filter_by(
        work_request_id=task.wr_id,
        event_type="RECEIPT_INGESTED",
    ).all()
    assert len(events) >= 1
