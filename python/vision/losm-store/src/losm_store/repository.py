from typing import Dict, Any, List, Optional

import uuid as _uuid

from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound

from losm_store.models import PlanningTask, Artifact, Branch, BranchArtifact, WorkStatus
from losm_store.canonical_bridge import (
    dual_write_enabled,
    canonical_insert_for_create,
    canonical_update_for_status,
    canonical_disposition_for_delete,
    mirror_insert_work_request,
    mirror_update_work_request,
    mirror_tombstone_work_request,
)


def create_work_request(
    db: Session,
    intent: str,
    constraints: Optional[Dict[str, Any]] = None,
    priority: int = 5,
    context_data: Optional[Dict[str, Any]] = None
) -> PlanningTask:
    # W2a (plan 4c3b74bd, substrate-corrected): canonical-first, then the
    # legacy mirror as raw bitemporal SQL against vision.work_requests_history
    # in the SAME transaction. One uuid is both the losm wr_id and the
    # canonical id (PK-carried identity, V186 convention); legacy_id carries
    # the mirror source. The PlanningTask model stays the read projection.
    wr_id = str(_uuid.uuid4())
    if dual_write_enabled():
        canonical_insert_for_create(
            db,
            wr_id=wr_id,
            intent=intent,
            constraints=constraints,
            priority=priority,
            context_data=context_data,
        )
    mirror_insert_work_request(
        db,
        wr_id=wr_id,
        intent=intent,
        status=WorkStatus.NEW.value,
        constraints=constraints,
        priority=priority,
        context_data=context_data,
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
    """Look up a work request by its business-key UUID (wr_id column)."""
    return db.query(PlanningTask).filter(PlanningTask.wr_id == wr_id).first()


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
    # W2a: canonical-first status mutation, then bitemporal mirror update
    # (close + reopen) — same transaction, plan-of-record ordering. ORM
    # objects stay read-projections.
    new_status_value = (
        status.value if hasattr(status, "value") else str(status)
    ) if status is not None else (
        wr.status.value if hasattr(wr.status, "value") else str(wr.status)
    )
    if dual_write_enabled():
        canonical_update_for_status(
            db,
            wr_id=wr_id,
            new_status_value=new_status_value,
            intent=intent,
            constraints=constraints,
            priority=priority,
        )
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
    # never regressed); the legacy mirror is tombstoned bitemporally (slice
    # closed), honoring the existing wire contract — artifacts/branches/edges
    # keep full lineage in the history bases.
    if dual_write_enabled():
        canonical_disposition_for_delete(db, wr_id=wr_id)
    mirror_tombstone_work_request(db, wr_id=wr_id)
    # Detach BEFORE commit so the caller's instance keeps its loaded state
    # (commit would expire it, leaving a dangling detached-expired object
    # whose next attribute access raises DetachedInstanceError).
    db.expunge(wr)  # tombstoned: the instance is gone from the open slice
    db.commit()
    return True


def list_work_requests(db: Session, skip: int = 0, limit: int = 100) -> List[PlanningTask]:
    return db.query(PlanningTask).offset(skip).limit(limit).all()


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
