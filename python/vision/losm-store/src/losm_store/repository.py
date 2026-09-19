from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound

from losm_store.models import PlanningTask, Artifact, Branch, BranchArtifact, CURRENT_TENSE


def create_work_request(
    db: Session,
    intent: str,
    constraints: Optional[Dict[str, Any]] = None,
    priority: int = 5,
    context_data: Optional[Dict[str, Any]] = None
) -> PlanningTask:
    wr = PlanningTask(
        intent=intent,
        constraints=constraints,
        priority=priority,
        context_data=context_data
    )
    db.add(wr)
    db.commit()
    db.refresh(wr)
    return wr


def get_work_request(db: Session, wr_id: int) -> PlanningTask:
    # Current-tense filter: on the base history table an id may in principle
    # carry superseded versions; reads must resolve the live one (the
    # semantics the work_requests_losm view's WHERE clause used to carry).
    wr = (
        db.query(PlanningTask)
        .filter(
            PlanningTask.id == wr_id,
            PlanningTask.recorded_until_dt == CURRENT_TENSE,
        )
        .first()
    )
    if wr is None:
        raise NoResultFound(f"PlanningTask id={wr_id} not found")
    return wr


def get_work_request_by_wr_id(db: Session, wr_id: str) -> Optional[PlanningTask]:
    """Look up a work request by its business-key UUID (wr_id column)."""
    return (
        db.query(PlanningTask)
        .filter(
            PlanningTask.wr_id == wr_id,
            PlanningTask.recorded_until_dt == CURRENT_TENSE,
        )
        .first()
    )


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
    if intent is not None:
        wr.intent = intent
    if constraints is not None:
        wr.constraints = constraints
    if priority is not None:
        wr.priority = priority
    if context_data is not None:
        wr.context_data = context_data
    if status is not None:
        wr.status = status
    db.commit()
    db.refresh(wr)
    return wr


def delete_work_request(db: Session, wr_id: str) -> bool:
    """Hard-delete a work request by its business-key UUID.

    Returns True if a row was deleted, False if not found.
    """
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        return False
    db.delete(wr)
    db.commit()
    return True


def list_work_requests(db: Session, skip: int = 0, limit: int = 100) -> List[PlanningTask]:
    return (
        db.query(PlanningTask)
        .filter(PlanningTask.recorded_until_dt == CURRENT_TENSE)
        .order_by(PlanningTask.id)
        .offset(skip)
        .limit(limit)
        .all()
    )


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
