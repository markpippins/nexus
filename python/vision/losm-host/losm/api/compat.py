"""vision-srv compatibility surface (the ``/api`` prefix).

Absorbed from ``python/vision-srv/src/vision_srv/main.py`` per architect
decision 5d8e10fd (vision-srv -> losm-host consolidation):

    Target = losm-host (:8006) becomes the single LOSM serving surface.
    Absorb under /api prefix to preserve the wire contract for vision-ui /
    vision-mcp / slash commands without touching consumer code; /health
    stays outside /api.

Wire-compat rules enforced here (decision point 6):
  * request/response shapes are byte-identical to vision-srv — routes return
    the raw ORM rows (FastAPI's jsonable_encoder) exactly like vision-srv did,
    including the ``context`` key (the PlanningTask column name), NOT
    ``context_data`` (the request-body key);
  * string ``wr_id`` addressing everywhere (int PK stays internal);
  * error bodies are FastAPI ``{"detail": ...}`` envelopes, as vision-srv;
  * no trailing slashes on collection paths (vision-srv had none).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from losm_store import (
    Artifact,
    create_branch,
    create_work_request,
    delete_work_request,
    get_edges_by_parent,
    get_work_request_by_wr_id,
    list_all_artifacts,
    list_all_branches,
    list_work_requests,
    update_work_request,
)
from losm_store.session import get_db
from losm_ir.compiler import (
    compile_dag,
    find_shortest_path,
    pass_dag_construct,
    pass_normalize,
    pass_structural_validate,
)
from nexus_core.wrp.identity import ccnf_input_from_intent_string, emit_identity

router = APIRouter(prefix="/api", tags=["vision-srv-compat"])


# ── Request models (identical field names to vision-srv) ────────────────────


class WorkRequestCreate(BaseModel):
    intent: str
    constraints: Optional[Dict[str, Any]] = None
    priority: int = 5
    context_data: Optional[Dict[str, Any]] = None


class WorkRequestUpdate(BaseModel):
    intent: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    context_data: Optional[Dict[str, Any]] = None


class BranchCreate(BaseModel):
    branch_id: Optional[str] = None
    wr_id: str
    label: Optional[str] = None
    parent_branch_id: Optional[str] = None
    fork_point: Optional[str] = None


class ArtifactCreate(BaseModel):
    artifact_id: Optional[str] = None
    type: str
    content: Dict[str, Any]
    confidence: Optional[float] = None
    provenance: Optional[Dict[str, Any]] = None
    wr_id: Optional[str] = None
    parent_artifact_id: Optional[str] = None
    template_metadata: Optional[Dict[str, Any]] = None


# ── Shared write-path helper (verbatim behavior from vision-srv) ────────────


def _stamp_entity_key(db: Session, wr) -> Any:
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


# ── Work Requests ───────────────────────────────────────────────────────────


@router.get("/work-requests")
def list_wr(limit: int = 100, skip: int = 0, db: Session = Depends(get_db)):
    return list_work_requests(db, skip=skip, limit=limit)


@router.get("/work-requests/{wr_id}")
def get_wr(wr_id: str, db: Session = Depends(get_db)):
    wr = get_work_request_by_wr_id(db, wr_id)
    if wr is None:
        raise HTTPException(status_code=404, detail="Work request not found")
    return wr


@router.post("/work-requests", status_code=201)
def create_wr(body: WorkRequestCreate, db: Session = Depends(get_db)):
    wr = create_work_request(
        db,
        intent=body.intent,
        constraints=body.constraints,
        priority=body.priority,
        context_data=dict(body.context_data or {}),
    )
    return _stamp_entity_key(db, wr) or wr


@router.patch("/work-requests/{wr_id}")
def patch_wr(wr_id: str, body: WorkRequestUpdate, db: Session = Depends(get_db)):
    wr = update_work_request(
        db,
        wr_id,
        intent=body.intent,
        constraints=body.constraints,
        priority=body.priority,
        context_data=body.context_data,
    )
    if wr is None:
        raise HTTPException(status_code=404, detail="Work request not found")
    return wr


@router.delete("/work-requests/{wr_id}", status_code=200)
def delete_wr(wr_id: str, db: Session = Depends(get_db)):
    deleted = delete_work_request(db, wr_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Work request not found")
    return {"detail": f"Work request {wr_id} deleted"}


# ── Branches ────────────────────────────────────────────────────────────────


@router.get("/branches")
def list_br(
    wr_id: Optional[str] = Query(None),
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
):
    return list_all_branches(db, wr_id=wr_id, skip=skip, limit=limit)


@router.post("/branches", status_code=201)
def create_br(body: BranchCreate, db: Session = Depends(get_db)):
    return create_branch(
        db,
        wr_id=body.wr_id,
        label=body.label,
        parent_branch_id=body.parent_branch_id,
        fork_point=body.fork_point,
    )


# ── Artifacts ───────────────────────────────────────────────────────────────


@router.get("/artifacts")
def list_art(
    wr_id: Optional[str] = Query(None),
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
):
    return list_all_artifacts(db, wr_id=wr_id, skip=skip, limit=limit)


@router.post("/artifacts", status_code=201)
def create_art(body: ArtifactCreate, db: Session = Depends(get_db)):
    art = Artifact(
        artifact_id=body.artifact_id,
        type=body.type,
        content=body.content,
        confidence=body.confidence,
        provenance=body.provenance,
        wr_id=body.wr_id,
        parent_artifact_id=body.parent_artifact_id,
        template_metadata=body.template_metadata,
    )
    db.add(art)
    db.commit()
    db.refresh(art)
    return art


# ── DAG API (6-pass compilation pipeline) ───────────────────────────────────


def _gather_subtree(db: Session, root, include_context: bool = True):
    """BFS the WR subtree from ``root`` via parent edges.

    Mirrors vision-srv's gathering exactly: nodes carry the raw row fields the
    compiler expects, edges carry parent/child/type (+ metadata for the full
    DAG route).
    """
    raw_nodes = []
    raw_edges = []
    collected = set()
    queue = [root]
    while queue:
        wr = queue.pop(0)
        if wr.wr_id in collected:
            continue
        collected.add(wr.wr_id)
        raw_nodes.append({
            "wr_id": wr.wr_id,
            "parent_request_id": getattr(wr, "parent_request_id", None),
            "intent": wr.intent,
            "status": wr.status.value if hasattr(wr.status, "value") else wr.status,
            "priority": wr.priority,
            "context": getattr(wr, "context_data", None) if include_context else None,
        })
        for edge in get_edges_by_parent(db, wr.wr_id):
            child = get_work_request_by_wr_id(db, edge.child_wr_id)
            if child and child.wr_id not in collected:
                queue.append(child)
    for nid in collected:
        for edge in get_edges_by_parent(db, nid):
            raw_edges.append({
                "parent_wr_id": edge.parent_wr_id,
                "child_wr_id": edge.child_wr_id,
                "edge_type": edge.edge_type,
                "metadata": getattr(edge, "metadata_json", None),
            })
    return raw_nodes, raw_edges


@router.get("/work-requests/{wr_id}/dag", response_model=dict)
def get_wr_dag(wr_id: str, db: Session = Depends(get_db)):
    """Compile and return the full WorkRequestDAG rooted at this WR."""
    root = get_work_request_by_wr_id(db, wr_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Work request not found")

    raw_nodes, raw_edges = _gather_subtree(db, root, include_context=True)
    result = compile_dag(raw_nodes, raw_edges, tenant_id="vision-srv")
    out = result.model_dump(mode="json")
    out["_metadata"] = {
        "node_count": len(raw_nodes),
        "edge_count": len(raw_edges),
        "compilation_time_ms": result.duration_ms,
    }
    return out


@router.get("/work-requests/{wr_id}/dag/path/{target_wr_id}")
def get_dag_path(wr_id: str, target_wr_id: str, db: Session = Depends(get_db)):
    """Find the shortest path between two WRs in the compiled DAG."""
    root = get_work_request_by_wr_id(db, wr_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Source work request not found")
    target = get_work_request_by_wr_id(db, target_wr_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target work request not found")

    raw_nodes, raw_edges = _gather_subtree(db, root, include_context=False)
    result = compile_dag(raw_nodes, raw_edges)
    if not result.dag:
        raise HTTPException(status_code=500, detail="DAG compilation failed")

    path = find_shortest_path(result.dag, wr_id, target_wr_id)
    return path.model_dump(mode="json")


@router.get("/work-requests/{wr_id}/dag/validate")
def validate_wr_dag(wr_id: str, db: Session = Depends(get_db)):
    """Re-run structural validation on a WR's DAG without full compilation."""
    root = get_work_request_by_wr_id(db, wr_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Work request not found")

    raw_nodes, raw_edges = _gather_subtree(db, root, include_context=False)
    nodes, edges, _ = pass_normalize(raw_nodes, raw_edges)
    dag, _ = pass_dag_construct(nodes, edges)
    issues, warnings = pass_structural_validate(dag)

    return {
        "wr_id": wr_id,
        "valid": len(issues) == 0,
        "issues": [i.model_dump(mode="json") for i in issues],
        "warnings": warnings,
        "node_count": len(raw_nodes),
        "edge_count": len(raw_edges),
    }
