import asyncio
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from losm_store.models import (
    PlanningTask,
    WorkStatus,
    GovernanceEvent,
    LifecycleEvent as LifecycleEventModel,
)
from losm_store.governed_triggers import GovernedTriggerAdapter
from losm_store.repository import (
    create_work_request,
    get_work_request_by_wr_id,
    list_work_requests,
    update_work_request,
)
from losm_store.session import SessionLocal, get_db

from nexus_core.wrp.identity import ccnf_input_from_intent_string, emit_identity

from losm_ir.transition import validate_transition, TransitionError
from losm_shell.lifecycle.orchestrator import PipelineCoordinator

router = APIRouter(prefix="/work-requests", tags=["work_requests"])


class WorkRequestCreate(BaseModel):
    intent: str
    constraints: Optional[Dict[str, Any]] = None
    priority: int = 5
    context: Optional[Dict[str, Any]] = None


class WorkRequestResponse(BaseModel):
    id: int
    wr_id: str
    intent: str
    constraints: Optional[Dict[str, Any]] = None
    priority: int
    context: Optional[Dict[str, Any]] = None
    status: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_metadata(cls, wr: PlanningTask) -> "WorkRequestResponse":
        return cls(
            id=wr.id,
            wr_id=wr.wr_id,
            intent=wr.intent,
            constraints=wr.constraints,
            priority=wr.priority,
            context=wr.context_data,
            status=wr.status.value if hasattr(wr.status, "value") else wr.status,
            created_at=wr.created_at.isoformat() if hasattr(wr.created_at, "isoformat") else str(wr.created_at),
            updated_at=wr.updated_at.isoformat() if hasattr(wr.updated_at, "isoformat") else str(wr.updated_at),
        )


def _stamp_entity_key(db, wr):
    """Derive the CCNF content identity at WR birth and persist it on the
    WR record (context). The canonical WR shape is always emittable (the
    action is the controlled verb ``execute``), so the write path never
    null-defaults — every WR born here carries its entity_key.
    """
    entity_key, _, _ = emit_identity(
        ccnf_input_from_intent_string(wr.intent, wr.wr_id))
    context = dict(wr.context_data or {})
    context["entity_key"] = entity_key
    return update_work_request(db, wr.wr_id, context_data=context)


@router.post("/", response_model=WorkRequestResponse, status_code=status.HTTP_201_CREATED)
def create_wr(payload: WorkRequestCreate, db: Session = Depends(get_db)):
    wr = create_work_request(
        db,
        intent=payload.intent,
        constraints=payload.constraints,
        priority=payload.priority,
        context_data=dict(payload.context or {}),
    )
    updated = _stamp_entity_key(db, wr)
    return WorkRequestResponse.from_orm_with_metadata(updated or wr)


@router.get("/{wr_id}", response_model=WorkRequestResponse)
def read_wr(wr_id: str, db: Session = Depends(get_db)):
    # Decision 5d8e10fd: consumers address work requests by the business-key
    # string UUID (wr_id), never the internal integer PK. The /api surface
    # (compat.py) serves the vision-srv wire shapes; this native surface
    # returns the typed WorkRequestResponse model.
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        raise HTTPException(status_code=404, detail="Work request not found")
    return WorkRequestResponse.from_orm_with_metadata(wr)


@router.get("/", response_model=List[WorkRequestResponse])
def list_wr(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    wrs = list_work_requests(db, skip=skip, limit=limit)
    return [WorkRequestResponse.from_orm_with_metadata(wr) for wr in wrs]


@router.post("/{wr_id}/orchestrate")
def orchestrate_wr(wr_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    wr = get_work_request_by_wr_id(db, wr_id)
    current_state = wr.status.value if hasattr(wr.status, "value") else str(wr.status)

    async def run_orchestrator(execution_id: str, state: str):
        coordinator = PipelineCoordinator()
        await coordinator.coordinate(execution_id, state, {})

    def _run_async(execution_id: str, state: str):
        asyncio.run(run_orchestrator(execution_id, state))

    background_tasks.add_task(_run_async, str(wr_id), current_state)
    return {"message": "Orchestration started", "wr_id": str(wr_id)}


class TransitionRequest(BaseModel):
    to_state: str
    actor: str = "api"
    reason: Optional[str] = None


@router.post("/{wr_id}/transition", response_model=WorkRequestResponse)
def transition_wr(wr_id: str, payload: TransitionRequest, db: Session = Depends(get_db)):
    wr = get_work_request_by_wr_id(db, wr_id)
    from_state = wr.status.value if hasattr(wr.status, "value") else wr.status
    adapter = GovernedTriggerAdapter()

    validation = validate_transition(from_state, payload.to_state)
    if not validation.allowed:
        # Refusals are durable evidence but never mutate lifecycle state or
        # create a Keychains checkpoint. The digest makes repeated identical
        # refusals source-idempotent while preserving the requested context.
        refusal_material = ":".join([
            str(wr_id), str(from_state), payload.to_state,
            payload.actor, payload.reason or "",
        ])
        refusal_id = "transition-refused:" + hashlib.sha256(
            refusal_material.encode("utf-8")
        ).hexdigest()
        governance = GovernanceEvent(
            event_type="TRANSITION_REFUSED",
            work_request_id=str(wr_id),
            payload={
                "from_state": str(from_state),
                "requested_state": payload.to_state,
                "actor": payload.actor,
                "reason": payload.reason,
                "validation_reason": validation.reason,
            },
        )
        db.add(governance)
        db.flush()
        adapter.emit(
            db,
            adapter.lifecycle_refused(
                source_event_id=refusal_id,
                wr_id=str(wr_id),
                from_state=str(from_state),
                to_state=payload.to_state,
                actor=payload.actor,
                reason=validation.reason,
            ),
        )
        db.commit()
        raise HTTPException(status_code=400, detail=validation.reason)

    wr.status = payload.to_state
    lifecycle = LifecycleEventModel(
        wr_id=str(wr_id),
        from_state=from_state,
        to_state=payload.to_state,
        actor=payload.actor,
        reason=payload.reason,
    )
    db.add(lifecycle)
    db.flush()
    adapter.emit(
        db,
        adapter.lifecycle_committed(
            event_id=str(lifecycle.event_id),
            wr_id=str(wr_id),
            from_state=str(from_state),
            to_state=payload.to_state,
            actor=payload.actor,
            reason=payload.reason,
        ),
    )
    db.commit()
    db.refresh(wr)

    return WorkRequestResponse.from_orm_with_metadata(wr)


# Note: the legacy /{wr_id}/compile route (PlanIR -> SpecIR) was absorbed by
# the vision-srv consolidation (decision 5d8e10fd) and intentionally NOT
# carried over: no consumer used it, and it was unauthenticated mutation
# surface over the plan compiler. Re-add via a reviewed contract op if a
# consumer ever appears.
