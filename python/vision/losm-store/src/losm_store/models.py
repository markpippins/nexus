import os
import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime, Enum as SAEnum, Integer, String, Text, Float, JSON, text
from sqlalchemy.orm import declarative_base

from losm_ir.states import WorkStatus

# ── Schema ───────────────────────────────────────────────────────────────────
# All models map to the "vision" schema in PostgreSQL.
_SCHEMA = "vision"

# The bitemporal "current tense" sentinel on *._history tables: rows carrying
# this recorded_until_dt are the live version; anything else is history.
# Must match the DB default ('9999-12-31 23:59:59+00') exactly, microseconds
# included (i.e. zero).
CURRENT_TENSE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _table_args() -> dict:
    return {"schema": _SCHEMA}


Base = declarative_base()


class ArtifactType(str, Enum):
    PLAN = "PLAN"
    CRITIQUE = "CRITIQUE"
    SPEC = "SPEC"
    EXECUTION = "EXECUTION"
    PATCH = "PATCH"
    SUMMARY = "SUMMARY"


# ── PlanningTask (base: work_requests_history) ──────────────────────────

class PlanningTask(Base):
    # Option-A repair (migration 016, incident e772b969): map the ORM at the
    # BASE history table, not the work_requests_losm view. The view is a plain
    # pass-through again after 016, but writing through it is unnecessary view
    # machinery; the base is the durable surface and needs no auto-updatability
    # guarantee. Current-tense filtering is enforced in the repository read
    # paths via CURRENT_TENSE (the view's WHERE clause, carried into Python).
    __tablename__ = "work_requests_history"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    wr_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    parent_request_id = Column(String(36), nullable=True)
    intent = Column(Text, nullable=False)
    constraints = Column(JSON, nullable=True)
    priority = Column(Integer, default=5, nullable=False)
    context_data = Column("context", JSON, nullable=True)
    status = Column(SAEnum(WorkStatus), default=WorkStatus.NEW, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Semi-bitemporal columns. The DB owns the authoritative values (server
    # defaults now() / sentinel), but Python-side defaults mirror them so the
    # same ORM works on the sealed SQLite test stores and every ORM-created
    # row is current-tense by construction.
    recorded_on_dt = Column(
        DateTime(timezone=True), nullable=True,
        default=datetime.utcnow, server_default=text("now()"),
    )
    recorded_until_dt = Column(
        DateTime(timezone=True), nullable=True,
        default=lambda: CURRENT_TENSE,
    )

    @property
    def updated_at(self):
        """Backwards-compat: mapped to recorded_on_dt in PostgreSQL."""
        return self.recorded_on_dt or self.created_at

    def __repr__(self) -> str:
        return f"<PlanningTask wr_id={self.wr_id} status={self.status} intent={self.intent[:30]!r}>"


# ── Artifact (view: artifacts) ──────────────────────────────────────────────

class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    type = Column(SAEnum(ArtifactType), nullable=False)
    content = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=True)
    provenance = Column(JSON, nullable=True)
    wr_id = Column(String(36), nullable=True, index=True)
    parent_artifact_id = Column(String(36), nullable=True, index=True)
    template_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Artifact artifact_id={self.artifact_id} type={self.type}>"


# ── ReceiptIngestRecord (view: receipt_ingest_records) ──────────────────────

class ReceiptIngestRecord(Base):
    __tablename__ = "receipt_ingest_records"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    work_request_id = Column(String(64), nullable=False, index=True)
    executor_id = Column(String(128), nullable=False)
    receipt_hash = Column(String(64), unique=True, nullable=False, index=True)
    result = Column(String(16), nullable=False)
    lineage_parent = Column(String(128), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)


# ── GovernanceEvent (view: governance_events) ───────────────────────────────

class GovernanceEvent(Base):
    __tablename__ = "governance_events"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(64), nullable=False)
    work_request_id = Column(String(64), nullable=False, index=True)
    lineage_parent = Column(String(128), nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)


# ── LifecycleEvent (view: lifecycle_events) ─────────────────────────────────

class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    wr_id = Column(String(36), nullable=False, index=True)
    from_state = Column(SAEnum(WorkStatus), nullable=True)
    to_state = Column(SAEnum(WorkStatus), nullable=False)
    actor = Column(String(128), nullable=False)
    reason = Column(String(256), nullable=True)
    metadata_payload = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<LifecycleEvent wr_id={self.wr_id} {self.from_state}->{self.to_state}>"


# ── Branch (view: branches) ─────────────────────────────────────────────────

class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    wr_id = Column(String(36), nullable=False, index=True)
    parent_branch_id = Column(String(36), nullable=True)
    fork_point = Column(String(36), nullable=True)
    label = Column(String(64), nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String(32), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)

    @property
    def updated_at(self):
        """Backwards-compat: mapped to recorded_on_dt in PostgreSQL."""
        return self.recorded_on_dt or self.created_at

    def __repr__(self) -> str:
        return f"<Branch {self.branch_id} wr={self.wr_id} label={self.label}>"


# ── BranchArtifact (view: branch_artifacts) ─────────────────────────────────

class BranchArtifact(Base):
    __tablename__ = "branch_artifacts"
    __table_args__ = _table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    branch_id = Column(String(36), nullable=False, index=True)
    wr_id = Column(String(36), nullable=False, index=True)
    artifact_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    parent_artifact_id = Column(String(36), nullable=True)
    score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<BranchArtifact {self.artifact_id} branch={self.branch_id} type={self.artifact_type}>"


# ── WorkRequestEdge (view: work_request_edges) ─────────────────────────────

class WorkRequestEdge(Base):
    __tablename__ = "work_request_edges"
    __table_args__ = {"schema": _SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    edge_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    parent_wr_id = Column(String(36), nullable=False, index=True)
    child_wr_id = Column(String(36), nullable=False, index=True)
    edge_type = Column(String(32), default="depends_on", nullable=False)
    metadata_json = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recorded_on_dt = Column(DateTime(timezone=True), nullable=True)
    recorded_until_dt = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<WorkRequestEdge {self.edge_id} {self.parent_wr_id}→{self.child_wr_id} [{self.edge_type}]>"
