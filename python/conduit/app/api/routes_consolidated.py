"""
Consolidated kernel router — the TypeSpec-aligned bare ``/api`` surface.

Implements the consolidated paths declared in
``typespec/v1/conduit-kernel/python/operations.tsp``:

    receipts  GET    /api/{plan_id}
              GET    /api/{plan_id}/raw
              GET    /api/{plan_id}/latest-type
              POST   /api
              DELETE /api/{plan_id}
    breaker   GET    /api/breaker
              POST   /api/trip | /api/reset | /api/pause | /api/resume
              GET    /api/failure-recovery
              POST   /api/failure-recovery
    sessions  GET    /api
              GET    /api/running | /api/stale | /api/{session_id}
              PATCH  /api/{session_id}/cost
              POST   /api/{session_id}/heartbeat | /api/{session_id}/kill

The legacy prefixed routers (``/api/receipts``, ``/api/sessions``,
``/api/breaker``) stay mounted in ``app.main``; this module only adds the
consolidated forms the caller repoint (WorkRequest 8261651) depends on.
Rollback stays "checkout the previous commit and restart".

Every entry below is a thin wrapper over the existing handler, so there is one
implementation per operation. The handlers are declared with ``@router.<verb>``
decorators (not ``add_api_route``) on purpose: ``typespec/v1/scripts/
reconcile-python.py`` extracts the route surface statically from decorators, and
the three-way alignment proof for 8261651 reads that surface.

Collisions resolved here, deliberately
--------------------------------------
1. ``GET /api/{x}`` is claimed by BOTH ``getPlanReceipts(plan_id)`` and
   ``getSession(session_id)`` — the contract declares two operations on the
   same path shape. Both declared paths are registered so the implementation
   matches TypeSpec path-for-path, and each resolves to ONE shared dispatcher
   keyed on identifier shape: ``sessions.id`` is a UUID, a plan is addressed by
   its numeric ``plan_number`` string. A UUID segment is a session lookup,
   anything else is a plan receipt lookup. Registering them as independent
   handlers is not possible — FastAPI serves whichever was registered first and
   silently shadows the other — so the dispatcher is the single source of that
   decision.
2. Static segments must win over the parameterized routes, or ``/api/running``,
   ``/api/stale``, ``/api/breaker`` and ``/api/failure-recovery`` would be
   parsed as plan ids. Static routes are declared first in this module.
3. ``/api/sessions``, ``/api/breaker`` and ``/api/receipts`` (no trailing
   slash) are legacy names whose canonical form keeps the prefix. They are
   refused with 404 rather than being answered as a plan receipt read.
"""

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.routes_breaker import (
    FailureRecoveryConfig,
    TripRequest,
    get_breaker,
    get_failure_recovery,
    pause_conduit,
    reset_breaker,
    resume_conduit,
    save_failure_recovery,
    trip_breaker,
)
from app.api.routes_receipts import (
    ReceiptInsertRequest,
    delete_receipts_by_plan_and_type,
    get_latest_receipt_type,
    get_plan_receipts,
    get_receipts_raw,
    insert_receipt,
)
from app.api.routes_sessions import (
    SessionCostUpdate,
    SessionHeartbeatRequest,
    get_running_sessions,
    get_session,
    get_stale_sessions,
    kill_session,
    list_all_sessions,
    update_session_cost,
    update_session_heartbeat,
)

_log = logging.getLogger("kernel.api.consolidated")

router = APIRouter(prefix="/api", tags=["consolidated"])

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Legacy prefixed names; the consolidated surface has no bare form for these.
RESERVED_LEGACY_SEGMENTS = frozenset({"sessions", "breaker", "receipts"})


def is_session_id(identifier: str) -> bool:
    """True when a bare ``/api/{identifier}`` segment addresses a session.

    Sessions are keyed by UUID (``sessions.id``); plans by numeric
    ``plan_number`` text. This is the discriminator for the contract's
    ``GET /api/{plan_id}`` vs ``GET /api/{session_id}`` collision.
    """
    return bool(_UUID_RE.match(identifier))


def get_api_resource(identifier: str) -> Dict[str, Any]:
    """Resolve a bare ``/api/{identifier}`` read to a session or plan receipts."""
    if identifier in RESERVED_LEGACY_SEGMENTS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No consolidated resource at /api/{identifier}; "
                f"use the legacy /api/{identifier}/ surface"
            ),
        )
    if is_session_id(identifier):
        return get_session(identifier)
    return get_plan_receipts(identifier)


# ── Sessions ────────────────────────────────────────────────────────


@router.get("", name="consolidated_list_sessions")
def consolidated_list_sessions(
    running_only: bool = Query(False, description="Only return running sessions"),
):
    """GET /api — list all sessions (contract: listSessions)."""
    return list_all_sessions(running_only=running_only)


@router.get("/running", name="consolidated_running_sessions")
def consolidated_running_sessions():
    """GET /api/running — sessions currently running."""
    return get_running_sessions()


@router.get("/stale", name="consolidated_stale_sessions")
def consolidated_stale_sessions(
    threshold_seconds: int = Query(3600, description="Staleness threshold in seconds"),
):
    """GET /api/stale — sessions with no heartbeat inside the threshold."""
    return get_stale_sessions(threshold_seconds=threshold_seconds)


# ── Circuit breaker ─────────────────────────────────────────────────


@router.get("/breaker", name="consolidated_get_breaker")
def consolidated_get_breaker():
    """GET /api/breaker — breaker state."""
    return get_breaker()


@router.post("/trip", name="consolidated_trip_breaker")
def consolidated_trip_breaker(body: TripRequest):
    """POST /api/trip — trip the breaker."""
    return trip_breaker(body)


@router.post("/reset", name="consolidated_reset_breaker")
def consolidated_reset_breaker():
    """POST /api/reset — reset the breaker and abandoned tickets."""
    return reset_breaker()


@router.post("/pause", name="consolidated_pause")
def consolidated_pause():
    """POST /api/pause — pause conduit orchestration."""
    return pause_conduit()


@router.post("/resume", name="consolidated_resume")
def consolidated_resume():
    """POST /api/resume — resume conduit orchestration."""
    return resume_conduit()


@router.get("/failure-recovery", name="consolidated_get_failure_recovery")
def consolidated_get_failure_recovery():
    """GET /api/failure-recovery — failure-recovery configuration."""
    return get_failure_recovery()


@router.post("/failure-recovery", name="consolidated_save_failure_recovery")
def consolidated_save_failure_recovery(body: FailureRecoveryConfig):
    """POST /api/failure-recovery — save failure-recovery configuration."""
    return save_failure_recovery(body)


# ── Receipts ────────────────────────────────────────────────────────


@router.post("", name="consolidated_insert_receipt")
def consolidated_insert_receipt(body: ReceiptInsertRequest):
    """POST /api — insert a receipt (single persistence path)."""
    return insert_receipt(body)


@router.get("/{plan_id}/raw", name="consolidated_get_receipts_raw")
def consolidated_get_receipts_raw(plan_id: str):
    """GET /api/{plan_id}/raw — raw receipt rows for a plan."""
    return get_receipts_raw(plan_id)


@router.get("/{plan_id}/latest-type", name="consolidated_get_latest_receipt_type")
def consolidated_get_latest_receipt_type(plan_id: str):
    """GET /api/{plan_id}/latest-type — latest receipt type for a plan."""
    return get_latest_receipt_type(plan_id)


@router.delete("/{plan_id}", name="consolidated_delete_receipts")
def consolidated_delete_receipts(
    plan_id: str,
    types: str = Query(..., description="Comma-separated receipt types to delete"),
):
    """DELETE /api/{plan_id} — unblock-family receipt purge (ruling fcec95a2 #2)."""
    return delete_receipts_by_plan_and_type(plan_id, types)


# ── Resource reads (declared after every static path) ───────────────


@router.get("/{plan_id}", name="consolidated_get_plan_receipts")
def consolidated_get_plan_receipts(plan_id: str):
    """GET /api/{plan_id} — formatted receipts for a plan.

    A UUID segment addresses a session instead; see ``get_api_resource``.
    """
    return get_api_resource(plan_id)


@router.get("/{session_id}", name="consolidated_get_session")
def consolidated_get_session(session_id: str):
    """GET /api/{session_id} — a session by id.

    A non-UUID segment addresses a plan's receipts instead; see
    ``get_api_resource``.
    """
    return get_api_resource(session_id)


# ── Session mutations ───────────────────────────────────────────────


@router.patch("/{session_id}/cost", name="consolidated_update_session_cost")
def consolidated_update_session_cost(session_id: str, body: SessionCostUpdate):
    """PATCH /api/{session_id}/cost — add session token cost."""
    return update_session_cost(session_id, body)


@router.post("/{session_id}/heartbeat", name="consolidated_update_session_heartbeat")
def consolidated_update_session_heartbeat(
    session_id: str, body: Optional[SessionHeartbeatRequest] = None
):
    """POST /api/{session_id}/heartbeat — update heartbeat + activity."""
    return update_session_heartbeat(session_id, body)


@router.post("/{session_id}/kill", name="consolidated_kill_session")
def consolidated_kill_session(session_id: str):
    """POST /api/{session_id}/kill — kill a running session."""
    return kill_session(session_id)


_log.debug("consolidated router: %d route(s) registered", len(router.routes))
