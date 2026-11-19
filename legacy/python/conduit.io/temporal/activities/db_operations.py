"""Temporal Activities wrapping DBAdapter methods for Conduit pipeline operations.

Each Activity is a thin wrapper around a DBAdapter method.  They run
synchronously (psycopg2 is sync) — Temporal handles thread-pooling for
sync Activities automatically.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from temporalio import activity

# Add conduit to path so we can import db_adapter
import sys
from pathlib import Path
_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from db_adapter import DBAdapter, DEFAULT_TICKET_TTL_HOURS

# Success/failure receipt type mappings (from main.py)
_SUCCESS_RECEIPTS = {
    "builder": "IMPLEMENTATION",
    "reviewer": "REVIEW_PASS",
    "planner": "PLAN_CREATE",
    "critic": "CRITIQUE",
}
_FAIL_RECEIPTS = {
    "builder": "BLOCK",
    "reviewer": "REVIEW_REJECT",
    "planner": "PLAN_BLOCK",
    "critic": "CRITIQUE_REJECT",
}


@activity.defn
async def claim_ticket_activity(plan_id: str, role: str, session_id: str) -> Optional[str]:
    """Atomically claim an open Ticket for a plan/role pair."""
    db = DBAdapter()
    return db.claim_ticket(plan_id, role, session_id)


@activity.defn
async def insert_receipt_activity(
    plan_id: str,
    receipt_type: str,
    agent_role: str,
    session_id: str,
    ticket_id: str,
    summary: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    tokens_used: int = 0,
) -> None:
    """Insert a receipt linked to a Ticket."""
    db = DBAdapter()
    db.insert_receipt(
        plan_id=plan_id,
        receipt_type=receipt_type,
        agent_role=agent_role,
        session_id=session_id,
        ticket_id=ticket_id,
        summary=summary,
        metadata=metadata,
        tokens_used=tokens_used,
    )


@activity.defn
async def advance_cursor_activity(role: str, plan_id: str, wr_id: str) -> None:
    """Advance the pipeline cursor for a role."""
    db = DBAdapter()
    db.advance_cursor(role, plan_id, wr_id)


@activity.defn
async def create_next_tickets_activity(
    plan_id: str,
    role: str,
    terminal_status: str,
    parent_ticket_id: str = "",
    objective: str = "",
    owner: str = "",
) -> int:
    """Spawn next Tickets after a Ticket reaches a terminal state."""
    db = DBAdapter()
    return db.create_next_tickets(
        plan_id=plan_id,
        ticket_role=role,
        terminal_status=terminal_status,
        parent_ticket_id=parent_ticket_id,
        objective=objective,
        owner=owner,
    )


@activity.defn
async def create_session_activity(
    session_id: str,
    agent_role: str,
    plan_ids: List[str],
    pid: Optional[int] = None,
    workflow_id: Optional[str] = None,
    run_id: Optional[str] = None,
    workflow_start_time: Optional[str] = None,
) -> None:
    """Create a session record in the DB with Temporal workflow metadata."""
    db = DBAdapter()
    db.create_session(session_id, agent_role, plan_ids, pid=pid,
                      workflow_id=workflow_id, run_id=run_id,
                      workflow_start_time=workflow_start_time)


@activity.defn
async def close_session_activity(
    session_id: str,
    exit_code: int,
    workflow_close_time: Optional[str] = None,
    workflow_run_time_ms: Optional[float] = None,
    workflow_result: Optional[str] = None,
) -> None:
    """Close a session record with Temporal workflow close metadata."""
    db = DBAdapter()
    db.close_session(session_id, exit_code,
                     workflow_close_time=workflow_close_time,
                     workflow_run_time_ms=workflow_run_time_ms,
                     workflow_result=workflow_result)


@activity.defn
async def close_ticket_activity(
    plan_id: str, role: str, session_id: str, terminal_status: str = "completed"
) -> bool:
    """Close a claimed Ticket into a terminal state."""
    db = DBAdapter()
    return db.close_ticket(plan_id, role, session_id, terminal_status)


@activity.defn
async def update_work_request_status_activity(wr_id: str, status: str) -> None:
    """Update a WorkRequest's status."""
    db = DBAdapter()
    db.update_work_request_status(wr_id, status)


@activity.defn
async def add_work_request_activity(wr_id: str, plan_id: str, dco_json: str) -> None:
    """Insert a WorkRequest record."""
    db = DBAdapter()
    db.add_work_request(wr_id, plan_id, dco_json)


@activity.defn
async def get_eligible_plans_activity(role: str) -> List[Dict[str, Any]]:
    """Get all eligible plans for a role."""
    db = DBAdapter()
    return db.get_eligible_plans(role)


@activity.defn
async def get_plan_by_id_activity(plan_id: str) -> Optional[Dict[str, Any]]:
    """Get a plan by ID."""
    db = DBAdapter()
    return db.get_plan_by_id(plan_id)


@activity.defn
async def get_role_model_config_activity(role: str) -> Optional[Dict[str, str]]:
    """Get the model config for a role."""
    db = DBAdapter()
    return db.get_role_model_config(role)


@activity.defn
async def get_fallback_models_activity(role: str) -> List[Dict[str, Any]]:
    """Get fallback models for a role."""
    db = DBAdapter()
    return db.get_fallback_models(role)


@activity.defn
async def get_failure_recovery_config_activity() -> Dict[str, Any]:
    """Get failure recovery config from circuit_breaker table."""
    db = DBAdapter()
    return db.get_failure_recovery_config()


@activity.defn
async def is_circuit_breaker_tripped_activity() -> bool:
    """Check if the circuit breaker is tripped."""
    db = DBAdapter()
    return db.is_circuit_breaker_tripped()


@activity.defn
async def is_conduit_paused_activity() -> bool:
    """Check if Conduit is paused."""
    db = DBAdapter()
    return db.is_conduit_paused()


@activity.defn
async def trip_and_requeue_activity(
    plan_id: str,
    role: str,
    session_id: str,
    error: str,
    detail: str = "",
    model_cfg: Optional[Dict[str, Any]] = None,
) -> None:
    """Trip circuit breaker AND requeue the plan for retry."""
    db = DBAdapter()
    db.trip_and_requeue(
        plan_id=plan_id,
        role=role,
        session_id=session_id,
        error=error,
        detail=detail,
        source="temporal-conduit",
        model_cfg=model_cfg,
    )


@activity.defn
async def detect_stale_tickets_activity() -> int:
    """Detect and mark stale tickets."""
    db = DBAdapter()
    return db.detect_stale_tickets()


@activity.defn
async def detect_expired_tickets_activity() -> int:
    """Detect and mark expired tickets."""
    db = DBAdapter()
    return db.detect_expired_tickets()


@activity.defn
async def increment_ticket_tokens_activity(ticket_id: str, tokens: int) -> None:
    """Increment token usage on a ticket."""
    if tokens > 0:
        db = DBAdapter()
        db.increment_ticket_tokens(ticket_id, tokens)


@activity.defn
async def get_requeue_count_activity(plan_id: str) -> int:
    """Count how many times this plan has been requeued (REQUEUED receipts)."""
    db = DBAdapter()
    return db.get_requeue_count(plan_id)


@activity.defn
async def release_ticket_activity(plan_id: str, role: str, session_id: str) -> bool:
    """Release a claimed Ticket back to open."""
    db = DBAdapter()
    return db.release_ticket(plan_id, role, session_id)
