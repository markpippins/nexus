from typing import Dict, Any, List, Optional

import os
import uuid as _uuid

from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound
from sqlalchemy import text

from losm_store.models import PlanningTask, Artifact, Branch, BranchArtifact, WorkStatus
from losm_store.canonical_bridge import (
    dual_write_enabled,
    legacy_history_available,
    canonical_insert_for_create,
    canonical_update_for_status,
    canonical_disposition_for_delete,
    mirror_insert_work_request,
    mirror_update_work_request,
    mirror_tombstone_work_request,
)

# C1 gate 1 (Lilac plan 8261670): five-field provenance envelope stamped on
# every ticket/receipt mutation. Same field names as the conduit db_adapter
# and cascade admission_subscriber write-sites:
# producer_id, source_channel, source_system, process_id, invocation_id.
_PROVENANCE_FIELDS = (
    "producer_id",
    "source_channel",
    "source_system",
    "process_id",
    "invocation_id",
)

_INVOCATION_ID = os.environ.get("INVOCATION_ID", "unsupervised") or "unsupervised"


_CANONICAL_STATUS_TO_LOSM = {
    "DRAFT": "NEW",
    "APPROVED": "PLAN_APPROVAL_GATE",
    "DISPATCHED": "EXECUTION",
    "COMPLETED": "COMPLETION",
}


def _canonical_task_from_row(row) -> PlanningTask:
    """Project a resolution.work_request row into the legacy PlanningTask shape.

    V187 removes the legacy history base, so rows created after the cutover no
    longer appear in vision.work_requests_losm. The losm API contract still
    addresses WRs by the stable UUID and returns the same attributes; this
    projection keeps that contract while canonical is the only writer.
    """
    context = row["context"] or {}
    losm_status = context.get("losm_status") or _CANONICAL_STATUS_TO_LOSM.get(
        row["business_status"], "NEW"
    )
    try:
        status = WorkStatus(losm_status)
    except ValueError:
        status = WorkStatus.NEW
    return PlanningTask(
        id=(_uuid.UUID(str(row["id"])).int % 2_147_483_647) + 1,
        wr_id=str(row["id"]),
        parent_request_id=context.get("parent_request_id"),
        intent=row["intent"] or row["title"] or "",
        constraints=row["constraints"],
        priority=int(context.get("losm_priority", 5)),
        context_data=context,
        status=status,
        created_at=row["created_at"],
        recorded_on_dt=row["recorded_on_dt"],
        recorded_until_dt=row["recorded_until_dt"],
    )


def _stamp_provenance_into_context(
    context_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Add the five-field provenance envelope to context_data.

    Called by write-side paths so every ticket mutation carries
    deterministic source identity. Falls back to process-identity defaults
    when caller-supplied values are absent.
    """
    context = dict(context_data or {})
    context["provenance"] = {
        "producer_id": context_data.get("producer_id", "nexus-losm-python")
        if context_data else "nexus-losm-python",
        "source_channel": context_data.get("source_channel", "losm-python")
        if context_data else "losm-python",
        "source_system": context_data.get("source_system", "losm")
        if context_data else "losm",
        "process_id": context_data.get("process_id", str(os.getpid()))
        if context_data else str(os.getpid()),
        "invocation_id": context_data.get("invocation_id", _INVOCATION_ID)
        if context_data else _INVOCATION_ID,
    }
    return context


def create_work_request(
    db: Session,
    intent: str,
    constraints: Optional[Dict[str, Any]] = None,
    priority: int = 5,
    context_data: Optional[Dict[str, Any]] = None
) -> PlanningTask:
    # W2a/W2c (plan 4c3b74bd, substrate-corrected): canonical-first. While the
    # legacy history base exists the mirror is written in the SAME transaction;
    # after V187 the mirror is absent and canonical is the only write. One uuid
    # is both the losm wr_id and the canonical id (PK-carried identity, V186).
    wr_id = str(_uuid.uuid4())
    # C1 gate 1 (Lilac plan 8261670): stamp the five-field provenance envelope
    # on the context carried by both the canonical and mirror writes.
    stamped_context = _stamp_provenance_into_context(context_data)
    if dual_write_enabled() or not legacy_history_available(db):
        canonical_insert_for_create(
            db,
            wr_id=wr_id,
            intent=intent,
            constraints=constraints,
            priority=priority,
            context_data=stamped_context,
        )
    if legacy_history_available(db):
        mirror_insert_work_request(
            db,
            wr_id=wr_id,
            intent=intent,
            status=WorkStatus.NEW.value,
            constraints=constraints,
            priority=priority,
            context_data=stamped_context,
        )
    db.commit()
    wr = get_work_request_by_wr_id(db, wr_id)
    return wr


def get_work_request(db: Session, wr_id: str) -> PlanningTask:
    """Fetch a work request by its stable business key (wr_id).

    W2a: the ORM identity is wr_id (per-version integer ids make the old
    integer-PK lookup meaningless across bitemporal updates).
    """
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        raise NoResultFound(f"PlanningTask wr_id={wr_id} not found")
    return wr


def get_work_request_by_wr_id(db: Session, wr_id: str) -> Optional[PlanningTask]:
    """Look up a work request by its stable business-key UUID.

    The legacy view is consulted first so pre-V187 rows keep their integer id
    and historical mirror state. Post-V187 rows are read from canonical and
    projected into the same PlanningTask contract.
    """
    wr = db.query(PlanningTask).filter(PlanningTask.wr_id == wr_id).first()
    if wr is not None:
        return wr
    try:
        canonical_id = str(_uuid.UUID(str(wr_id)))
    except (ValueError, AttributeError, TypeError):
        return None
    row = db.execute(
        text(
            "SELECT id, intent, title, constraints, context, business_status, "
            "       created_at, recorded_on_dt, recorded_until_dt "
            "FROM resolution.work_request "
            "WHERE id = :id AND valid_until = 'infinity'::timestamptz "
            "  AND business_status <> 'CANCELLED'"
        ),
        {"id": canonical_id},
    ).mappings().first()
    return _canonical_task_from_row(row) if row is not None else None


def update_work_request(
    db: Session,
    wr_id: str,
    intent: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
    context_data: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> Optional[PlanningTask]:
    """Partially update a work request by its business-key UUID."""
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        return None
    # W2a/W2c: canonical-first status mutation; the legacy mirror is updated
    # only while its history base exists.
    new_status_value = (
        status.value if hasattr(status, "value") else str(status)
    ) if status is not None else (
        wr.status.value if hasattr(wr.status, "value") else str(wr.status)
    )
    if dual_write_enabled() or not legacy_history_available(db):
        canonical_update_for_status(
            db,
            wr_id=wr_id,
            new_status_value=new_status_value,
            intent=intent,
            constraints=constraints,
            priority=priority,
        )
    if legacy_history_available(db):
        mirror_update_work_request(
            db,
            wr_id=wr_id,
            intent=intent,
            constraints=constraints,
            priority=priority,
            context_data=context_data,
            status=new_status_value,
        )
    db.commit()
    db.expire_all()
    return get_work_request_by_wr_id(db, wr_id)


def delete_work_request(db: Session, wr_id: str) -> bool:
    """Hard-delete a work request by its business-key UUID.

    Returns True if a row was deleted, False if not found.
    """
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        return False
    # W2a (plan 4c3b74bd §W2a.4): canonical rows are never deleted. The losm
    # API delete maps to a canonical CANCELLED disposition (COMPLETED rows are
    # never regressed); the mirror tombstone is written only while the legacy
    # history base exists.
    if dual_write_enabled() or not legacy_history_available(db):
        canonical_disposition_for_delete(db, wr_id=wr_id)
    if legacy_history_available(db):
        mirror_tombstone_work_request(db, wr_id=wr_id)
    # Detach BEFORE commit so the caller's instance keeps its loaded state
    # (commit would expire it, leaving a dangling detached-expired object
    # whose next attribute access raises DetachedInstanceError).
    if wr in db:
        db.expunge(wr)  # tombstoned: the instance is gone from the open slice
    db.commit()
    return True


def list_work_requests(db: Session, skip: int = 0, limit: int = 100) -> List[PlanningTask]:
    """List legacy-view rows plus canonical-only losm rows (post-V187)."""
    rows = db.query(PlanningTask).order_by(PlanningTask.created_at.desc()).all()
    seen = {row.wr_id for row in rows}
    canonical_rows = db.execute(
        text(
            "SELECT id, intent, title, constraints, context, business_status, "
            "       created_at, recorded_on_dt, recorded_until_dt "
            "FROM resolution.work_request "
            "WHERE valid_until = 'infinity'::timestamptz "
            "  AND business_status <> 'CANCELLED' "
            "  AND created_by LIKE 'losm_store%' "
            "ORDER BY created_at DESC"
        )
    ).mappings().all()
    for row in canonical_rows:
        if str(row["id"]) not in seen:
            rows.append(_canonical_task_from_row(row))
    return rows[skip: skip + limit]


def list_all_artifacts(
    db: Session,
    wr_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Artifact]:
    q = db.query(Artifact)
    if wr_id is not None:
        q = q.filter(Artifact.wr_id == wr_id)
    return q.order_by(Artifact.created_at.desc()).offset(skip).limit(limit).all()


def get_artifacts_by_wr(db: Session, wr_id: str) -> List[Artifact]:
    return db.query(Artifact).filter(Artifact.wr_id == wr_id).all()


def get_artifact_lineage(db: Session, artifact_id: str) -> List[Artifact]:
    lineage = []
    current_id = artifact_id
    while current_id:
        artifact = db.query(Artifact).filter(Artifact.artifact_id == current_id).first()
        if not artifact:
            break
        lineage.append(artifact)
        current_id = artifact.parent_artifact_id
    return lineage


# ── Branch CRUD ──────────────────────────────────────────────────────────────


def list_all_branches(
    db: Session,
    wr_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> list:
    q = db.query(Branch)
    if wr_id is not None:
        q = q.filter(Branch.wr_id == wr_id)
    return q.order_by(Branch.created_at.desc()).offset(skip).limit(limit).all()


def create_branch(
    db: Session,
    wr_id: str,
    label: Optional[str] = None,
    parent_branch_id: Optional[str] = None,
    fork_point: Optional[str] = None,
) -> Branch:
    branch = Branch(
        wr_id=wr_id,
        label=label,
        parent_branch_id=parent_branch_id,
        fork_point=fork_point,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


def get_branch(db: Session, branch_id: str) -> Optional[Branch]:
    return db.query(Branch).filter(Branch.branch_id == branch_id).first()


def get_branches_by_wr_id(db: Session, wr_id: str) -> list:
    return db.query(Branch).filter(Branch.wr_id == wr_id).order_by(Branch.created_at).all()


def update_branch_score(db: Session, branch_id: str, score: float) -> Optional[Branch]:
    branch = get_branch(db, branch_id)
    if branch:
        branch.score = score
        db.commit()
        db.refresh(branch)
    return branch


def merge_branch(db: Session, branch_id: str, merge_strategy: str = "select_best") -> Optional[Branch]:
    branch = get_branch(db, branch_id)
    if branch:
        branch.status = "merged"
        db.commit()
        db.refresh(branch)
    return branch


def discard_branch(db: Session, branch_id: str) -> Optional[Branch]:
    branch = get_branch(db, branch_id)
    if branch:
        branch.status = "discarded"
        db.commit()
        db.refresh(branch)
    return branch


def create_branch_artifact(
    db: Session,
    branch_id: str,
    wr_id: str,
    artifact_type: str,
    content: str,
    parent_artifact_id: Optional[str] = None,
    score: Optional[float] = None,
) -> BranchArtifact:
    artifact = BranchArtifact(
        branch_id=branch_id,
        wr_id=wr_id,
        artifact_type=artifact_type,
        content=content,
        parent_artifact_id=parent_artifact_id,
        score=score,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def get_branch_artifacts(db: Session, branch_id: str) -> list:
    return db.query(BranchArtifact).filter(BranchArtifact.branch_id == branch_id).order_by(BranchArtifact.created_at).all()


# ── WorkRequestEdge CRUD ────────────────────────────────────────────


def create_edge(
    db: Session,
    parent_wr_id: str,
    child_wr_id: str,
    edge_type: str = "depends_on",
    metadata_json: Optional[Dict[str, Any]] = None,
) -> "WorkRequestEdge":
    from losm_store.models import WorkRequestEdge
    edge = WorkRequestEdge(
        parent_wr_id=parent_wr_id,
        child_wr_id=child_wr_id,
        edge_type=edge_type,
        metadata_json=metadata_json,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)
    return edge


def get_edges_by_parent(db: Session, wr_id: str) -> list:
    from losm_store.models import WorkRequestEdge
    return db.query(WorkRequestEdge).filter(
        WorkRequestEdge.parent_wr_id == wr_id
    ).all()


def get_edges_by_child(db: Session, wr_id: str) -> list:
    from losm_store.models import WorkRequestEdge
    return db.query(WorkRequestEdge).filter(
        WorkRequestEdge.child_wr_id == wr_id
    ).all()


def delete_edge(db: Session, edge_id: str) -> bool:
    from losm_store.models import WorkRequestEdge
    edge = db.query(WorkRequestEdge).filter(
        WorkRequestEdge.edge_id == edge_id
    ).first()
    if edge is None:
        return False
    db.delete(edge)
    db.commit()
    return True


__all__ = [
    "create_work_request", "get_work_request", "get_work_request_by_wr_id",
    "update_work_request", "delete_work_request", "list_work_requests",
    "list_all_artifacts", "get_artifacts_by_wr", "get_artifact_lineage",
    "list_all_branches", "create_branch", "get_branch", "get_branches_by_wr_id",
    "update_branch_score", "merge_branch", "discard_branch",
    "create_branch_artifact", "get_branch_artifacts",
    "create_edge", "get_edges_by_parent", "get_edges_by_child", "delete_edge",
]
