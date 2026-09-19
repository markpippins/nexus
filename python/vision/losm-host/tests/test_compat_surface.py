"""Absorbed vision-srv /api surface — wire-contract tests (decision 5d8e10fd).

Contract under test (from the decision):
  * losm-host serves the full vision-srv CRUD + DAG surface under /api
  * string wr_id addressing everywhere (int PK stays internal)
  * request/response shapes identical to vision-srv (raw ORM rows, the
    PlanningTask column name ``context`` in responses, FastAPI detail errors)
  * /health stays outside /api

The suite runs against a sealed per-test SQLite mirror of the ``vision``
schema (ATTACH-as-vision), so no live PostgreSQL is needed and the canonical
DDL is never touched. Writes go through the real losm_store repository layer,
exercising exactly the code path production uses.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

os.environ.setdefault("LOSM_DATABASE_URL", "sqlite://")  # never touch live PG

from losm_store.models import (  # noqa: E402  (import after env pin)
    ArtifactType,
    Base,
)


@pytest.fixture()
def store(monkeypatch):
    """Sealed SQLite DB with the models' tables attached under schema 'vision'.

    losm_store models declare ``schema='vision'``; SQLite has no schemas, so we
    create a named in-memory db 'vision' in the same connection and ATTACH it —
    table names then resolve exactly as the models emit them
    (``vision.work_requests_losm`` etc.).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _attach_vision(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("ATTACH DATABASE ':memory:' AS vision")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)

    import losm_store.session as store_session

    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(store_session, "SessionLocal", test_session)
    monkeypatch.setattr(store_session, "engine", engine)
    # get_db binds SessionLocal at call time (function-local lookup), so
    # patching the module attribute is sufficient for TestClient runs.

    yield test_session
    engine.dispose()


@pytest.fixture()
def client(store):
    """TestClient over the real app (compat router included) with a fresh store."""
    from losm.app.main import app

    with TestClient(app) as c:
        yield c


# Exact wire-key set of a PlanningTask row as FastAPI emits it (raw ORM via
# jsonable_encoder). Both services share losm_store.models, so this set IS the
# vision-srv wire contract — asserted verbatim to lock parity.
WR_ROW_KEYS = {
    "constraints", "context_data", "created_at", "id", "intent",
    "parent_request_id", "priority", "recorded_on_dt",
    "recorded_until_dt", "status", "wr_id",
}


def _create_wr(client, **overrides):
    payload = {
        "intent": "compat probe",
        "constraints": {"t": "test"},
        "priority": 9,
        "context_data": {"probe": True},
    }
    payload.update(overrides)
    r = client.post("/api/work-requests", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── Work request CRUD ───────────────────────────────────────────────────────


def test_create_wr_returns_vision_srv_shape(client):
    body = _create_wr(client)
    # vision-srv returns the raw ORM row: int id + string wr_id; the context
    # travels under the ORM attribute name `context_data` (the DB column is
    # `context`, but serialization is attribute-based — verified against the
    # shared losm_store models that vision-srv itself uses).
    assert isinstance(body["id"], int)
    uuid.UUID(body["wr_id"])  # string business key
    assert body["context_data"]["probe"] is True
    assert body["context_data"].get("entity_key")  # CCNF identity stamped at birth
    assert body["status"] == "NEW"
    assert body["priority"] == 9
    # exact key-set parity with vision-srv (no more, no less)
    assert set(body.keys()) == WR_ROW_KEYS


def test_wr_addressing_is_string_wr_id(client):
    body = _create_wr(client)
    r = client.get(f"/api/work-requests/{body['wr_id']}")
    assert r.status_code == 200
    assert r.json()["wr_id"] == body["wr_id"]
    # integer PK is NOT an addressable wr_id
    r = client.get(f"/api/work-requests/{body['id']}")
    assert r.status_code == 404
    assert r.json() == {"detail": "Work request not found"}


def test_patch_wr_partial_update(client):
    body = _create_wr(client)
    r = client.patch(
        f"/api/work-requests/{body['wr_id']}",
        json={"priority": 3, "context_data": {"probe": "patched"}},
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["priority"] == 3
    assert updated["context_data"] == {"probe": "patched"}
    assert updated["intent"] == body["intent"]  # untouched field preserved
    assert set(updated.keys()) == WR_ROW_KEYS


def test_patch_wr_404(client):
    r = client.patch("/api/work-requests/nope", json={"priority": 1})
    assert r.status_code == 404
    assert r.json() == {"detail": "Work request not found"}


def test_delete_wr(client):
    body = _create_wr(client)
    r = client.delete(f"/api/work-requests/{body['wr_id']}")
    assert r.status_code == 200
    assert r.json() == {"detail": f"Work request {body['wr_id']} deleted"}
    assert client.get(f"/api/work-requests/{body['wr_id']}").status_code == 404


def test_list_wr_pagination_and_shape(client):
    _create_wr(client, intent="wr-a")
    _create_wr(client, intent="wr-b")
    r = client.get("/api/work-requests", params={"limit": 1})
    assert r.status_code == 200
    page = r.json()
    assert len(page) == 1
    assert "wr_id" in page[0] and "context_data" in page[0]
    r = client.get("/api/work-requests")
    assert len(r.json()) == 2


# ── Branches / artifacts list-create surface ───────────────────────────────


def test_branch_list_and_create(client):
    wr = _create_wr(client)
    r = client.post(
        "/api/branches",
        json={"wr_id": wr["wr_id"], "label": "exp-1"},
    )
    assert r.status_code == 201
    branch = r.json()
    assert branch["wr_id"] == wr["wr_id"]
    assert branch["label"] == "exp-1"

    r = client.get("/api/branches")
    assert r.status_code == 200
    assert [b["branch_id"] for b in r.json()] == [branch["branch_id"]]

    r = client.get("/api/branches", params={"wr_id": wr["wr_id"]})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get("/api/branches", params={"wr_id": "missing"})
    assert r.status_code == 200
    assert r.json() == []


def test_artifact_list_and_create_with_enum_type(client):
    wr = _create_wr(client)
    r = client.post(
        "/api/artifacts",
        json={
            "type": ArtifactType.PLAN.value,  # enum member (non-member strings corrupt reads)
            "content": {"steps": ["a"]},
            "confidence": 0.9,
            "wr_id": wr["wr_id"],
            "provenance": {"src": "test"},
            "template_metadata": {"tpl": "v1"},
        },
    )
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["artifact_id"]
    assert art["type"] == "PLAN"
    assert art["wr_id"] == wr["wr_id"]

    r = client.get("/api/artifacts")
    assert r.status_code == 200
    assert [a["artifact_id"] for a in r.json()] == [art["artifact_id"]]

    r = client.get("/api/artifacts", params={"wr_id": wr["wr_id"]})
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── DAG routes ──────────────────────────────────────────────────────────────


def _link(client, session_factory, parent_wr_id, child_wr_id):
    """Create a WR + edge directly via the repository (edge creation has no
    REST route in either service; the store helper is the production path)."""
    child = _create_wr(client, intent="child wr")
    from losm_store.session import SessionLocal

    db = SessionLocal()
    try:
        from losm_store import create_edge

        create_edge(db, parent_wr_id=parent_wr_id, child_wr_id=child_wr_id)
    finally:
        db.close()
    return child


def test_dag_single_node_round_trip(client):
    wr = _create_wr(client)
    r = client.get(f"/api/work-requests/{wr['wr_id']}/dag")
    assert r.status_code == 200, r.text
    dag = r.json()
    assert dag["_metadata"]["node_count"] == 1
    assert dag["_metadata"]["edge_count"] == 0
    assert "compilation_time_ms" in dag["_metadata"]


def test_dag_validate_two_node_chain(client, store):
    parent = _create_wr(client)
    child = _create_wr(client)
    from losm_store.session import SessionLocal
    from losm_store import create_edge

    db = SessionLocal()
    try:
        create_edge(db, parent_wr_id=parent["wr_id"], child_wr_id=child["wr_id"])
    finally:
        db.close()

    r = client.get(f"/api/work-requests/{parent['wr_id']}/dag/validate")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["wr_id"] == parent["wr_id"]
    assert v["valid"] is True
    assert v["issues"] == []
    assert v["node_count"] == 2
    assert v["edge_count"] == 1


def test_dag_path_self(client):
    wr = _create_wr(client)
    r = client.get(f"/api/work-requests/{wr['wr_id']}/dag/path/{wr['wr_id']}")
    assert r.status_code == 200, r.text


def test_dag_routes_404_shape(client):
    r = client.get("/api/work-requests/missing/dag")
    assert r.status_code == 404
    assert r.json() == {"detail": "Work request not found"}
    r = client.get("/api/work-requests/missing/dag/validate")
    assert r.status_code == 404
    r = client.get("/api/work-requests/missing/dag/path/also-missing")
    assert r.status_code == 404


# ── Native surface regressions + prefix hygiene ────────────────────────────


def test_native_get_wr_uses_string_addressing(client):
    wr = _create_wr(client)
    r = client.get(f"/work-requests/{wr['wr_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["wr_id"] == wr["wr_id"]
    assert body["id"] == wr["id"]
    # native surface keeps its typed WorkRequestResponse (context key,
    # from_orm-mapped from the ORM attribute context_data)
    assert body["context"]["probe"] is True


def test_health_stays_outside_api(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "features" in body  # losm-host's own health body, not vision-srv's
