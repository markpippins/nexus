"""FastAPI application — exposes vision schema tables as REST endpoints."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from losm_store import (
    SessionLocal, engine,
    PlanningTask, Artifact, WorkRequestEdge,
    create_work_request, get_work_request_by_wr_id,
    update_work_request, delete_work_request, list_work_requests,
    list_all_branches, create_branch, list_all_artifacts,
    create_branch_artifact,
    create_edge, get_edges_by_parent, get_edges_by_child,
)
from losm_ir.compiler import (
    compile_dag,
    find_shortest_path,
    get_subtree,
    pass_structural_validate,
)
from losm_ir.dag import (
    CompilationResult, DAGPath,
    EventEnvelope, WorkRequestDAG,
)

from nexus_core.wrp.identity import ccnf_input_from_intent_string, emit_identity

app = FastAPI(title="vision-srv", version="0.1.0")

# ── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dependencies ────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Models ──────────────────────────────────────────────────────────────────

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


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# Work Requests

@app.get("/api/work-requests")
def list_wr(limit: int = 100, skip: int = 0):
    db = next(get_db())
    try:
        wrs = list_work_requests(db, skip=skip, limit=limit)
        return wrs
    finally:
        db.close()


@app.get("/api/work-requests/{wr_id}")
def get_wr(wr_id: str):
    db = next(get_db())
    try:
        wr = get_work_request_by_wr_id(db, wr_id)
        if wr is None:
            raise HTTPException(status_code=404, detail="Work request not found")
        return wr
    finally:
        db.close()


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


@app.post("/api/work-requests", status_code=201)
def create_wr(body: WorkRequestCreate):
    db = next(get_db())
    try:
        wr = create_work_request(
            db,
            intent=body.intent,
            constraints=body.constraints,
            priority=body.priority,
            context_data=dict(body.context_data or {}),
        )
        return _stamp_entity_key(db, wr) or wr
    finally:
        db.close()


@app.patch("/api/work-requests/{wr_id}")
def patch_wr(wr_id: str, body: WorkRequestUpdate):
    db = next(get_db())
    try:
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
    finally:
        db.close()


@app.delete("/api/work-requests/{wr_id}", status_code=200)
def delete_wr(wr_id: str):
    db = next(get_db())
    try:
        deleted = delete_work_request(db, wr_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Work request not found")
        return {"detail": f"Work request {wr_id} deleted"}
    finally:
        db.close()


# Branches

@app.get("/api/branches")
def list_br(wr_id: Optional[str] = Query(None), limit: int = 100, skip: int = 0):
    db = next(get_db())
    try:
        return list_all_branches(db, wr_id=wr_id, skip=skip, limit=limit)
    finally:
        db.close()


@app.post("/api/branches", status_code=201)
def create_br(body: BranchCreate):
    db = next(get_db())
    try:
        br = create_branch(
            db,
            wr_id=body.wr_id,
            label=body.label,
            parent_branch_id=body.parent_branch_id,
            fork_point=body.fork_point,
        )
        return br
    finally:
        db.close()


# Artifacts

@app.get("/api/artifacts")
def list_art(wr_id: Optional[str] = Query(None), limit: int = 100, skip: int = 0):
    db = next(get_db())
    try:
        return list_all_artifacts(db, wr_id=wr_id, skip=skip, limit=limit)
    finally:
        db.close()


@app.post("/api/artifacts", status_code=201)
def create_art(body: ArtifactCreate):
    db = next(get_db())
    try:
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
    finally:
        db.close()


# ── DAG API Routes ────────────────────────────────────────────────────────────


@app.get("/api/work-requests/{wr_id}/dag", response_model=dict)
def get_wr_dag(wr_id: str):
    """Compile and return the full WorkRequestDAG rooted at this WR.

    Gathers the work request + its children via edges and runs the
    6-pass compilation pipeline.
    """
    db = next(get_db())
    try:
        root = get_work_request_by_wr_id(db, wr_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Work request not found")

        # Gather all nodes: root + descendants via edges
        raw_nodes = []
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
                "context": getattr(wr, "context_data", None),
            })
            # Fetch children via edges
            children = get_edges_by_parent(db, wr.wr_id)
            for edge in children:
                child = get_work_request_by_wr_id(db, edge.child_wr_id)
                if child and child.wr_id not in collected:
                    queue.append(child)

        # Gather edges
        raw_edges = []
        for nid in collected:
            for edge in get_edges_by_parent(db, nid):
                raw_edges.append({
                    "parent_wr_id": edge.parent_wr_id,
                    "child_wr_id": edge.child_wr_id,
                    "edge_type": edge.edge_type,
                    "metadata": getattr(edge, "metadata_json", None),
                })

        # Run the 6-pass compilation pipeline
        result = compile_dag(raw_nodes, raw_edges, tenant_id="vision-srv")

        out = result.model_dump(mode="json")
        out["_metadata"] = {
            "node_count": len(raw_nodes),
            "edge_count": len(raw_edges),
            "compilation_time_ms": result.duration_ms,
        }
        return out
    finally:
        db.close()


@app.get("/api/work-requests/{wr_id}/dag/path/{target_wr_id}")
def get_dag_path(wr_id: str, target_wr_id: str):
    """Find the shortest path between two WRs in the compiled DAG."""
    db = next(get_db())
    try:
        root = get_work_request_by_wr_id(db, wr_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Source work request not found")
        target = get_work_request_by_wr_id(db, target_wr_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Target work request not found")

        # Build minimal raw DAG from DB
        raw_nodes = []
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
            })
            for edge in get_edges_by_parent(db, wr.wr_id):
                child = get_work_request_by_wr_id(db, edge.child_wr_id)
                if child and child.wr_id not in collected:
                    queue.append(child)

        raw_edges = []
        for nid in collected:
            for edge in get_edges_by_parent(db, nid):
                raw_edges.append({
                    "parent_wr_id": edge.parent_wr_id,
                    "child_wr_id": edge.child_wr_id,
                    "edge_type": edge.edge_type,
                })

        result = compile_dag(raw_nodes, raw_edges)
        if not result.dag:
            raise HTTPException(status_code=500, detail="DAG compilation failed")

        path = find_shortest_path(result.dag, wr_id, target_wr_id)
        return path.model_dump(mode="json")
    finally:
        db.close()


@app.get("/api/work-requests/{wr_id}/dag/validate")
def validate_wr_dag(wr_id: str):
    """Re-run structural validation on a WR's DAG without full compilation."""
    db = next(get_db())
    try:
        root = get_work_request_by_wr_id(db, wr_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Work request not found")

        raw_nodes = []
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
            })
            for edge in get_edges_by_parent(db, wr.wr_id):
                child = get_work_request_by_wr_id(db, edge.child_wr_id)
                if child and child.wr_id not in collected:
                    queue.append(child)

        raw_edges = []
        for nid in collected:
            for edge in get_edges_by_parent(db, nid):
                raw_edges.append({
                    "parent_wr_id": edge.parent_wr_id,
                    "child_wr_id": edge.child_wr_id,
                    "edge_type": edge.edge_type,
                })

        # Quick compile to get a DAG, then validate
        from losm_ir.compiler import pass_normalize, pass_dag_construct
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
    finally:
        db.close()
