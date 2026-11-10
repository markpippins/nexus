"""event_store.py — Immutable event ledger for WorkRequest lifecycle.

Provides append-only event storage, full and partial replay, and state
projection rebuild. Backs both Conduit (control plane) and Vision
(execution plane).

Architecture::

    work_request_events  →  work_request_state  (projection)
          ↓                       ↑
    append_event()         trigger / rebuild_state()

Usage::

    store = EventStore(pg_conn)
    store.append(wr_id, "WORKREQUEST.CREATED", {"title": "..."})
    events = store.replay(wr_id)
    state = store.rebuild_state(wr_id)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LedgerEventType(str, Enum):
    WORKREQUEST_CREATED = "WORKREQUEST.CREATED"
    VISION_IR_PRODUCED = "VISION.IR_PRODUCED"
    STATE_TRANSITION_PROPOSED = "STATE.TRANSITION_PROPOSED"
    STATE_TRANSITION_APPROVED = "STATE.TRANSITION_APPROVED"
    STATE_TRANSITION_COMMITTED = "STATE.TRANSITION_COMMITTED"
    EXECUTION_STARTED = "EXECUTION.STARTED"
    EXECUTION_COMPLETED = "EXECUTION.COMPLETED"
    EXECUTION_FAILED = "EXECUTION.FAILED"
    SYSTEM_CRON_TRIGGERED = "SYSTEM.CRON_TRIGGERED"


# ── Canonical storage vocabulary (WRP event-vocabulary cutover) ─────
#
# conduit.work_request_events.event_type accepts EXACTLY these values
# (migration 034). The WRP kernel domain keeps speaking WR_*; this store is the
# write boundary, so it transcodes WR_* → canonical. The exact runtime verb is
# preserved in payload["runtime_event_type"] so readers can map back, and
# transition events carry the ledger new_state the DB projector requires.
# A stray WR_* insert fails loudly at the CHECK — there is no DB-side shim.

CANONICAL_EVENT_TYPES = frozenset(
    {
        "WORKREQUEST.CREATED",
        "VISION.IR_PRODUCED",
        "STATE.TRANSITION_PROPOSED",
        "STATE.TRANSITION_APPROVED",
        "STATE.TRANSITION_COMMITTED",
        "EXECUTION.STARTED",
        "EXECUTION.COMPLETED",
        "EXECUTION.FAILED",
        "SYSTEM.CRON_TRIGGERED",
    }
)

RUNTIME_TO_CANONICAL_EVENT: dict[str, str] = {
    "WR_SUBMITTED": "WORKREQUEST.CREATED",
    "WR_VALIDATED": "STATE.TRANSITION_COMMITTED",
    "WR_QUEUED": "STATE.TRANSITION_COMMITTED",
    "WR_CLAIMED": "STATE.TRANSITION_COMMITTED",
    "WR_ACKED": "STATE.TRANSITION_COMMITTED",
    "WR_SETTLED": "STATE.TRANSITION_COMMITTED",
    "WR_REJECTED": "STATE.TRANSITION_COMMITTED",
    "WR_FAILED": "STATE.TRANSITION_COMMITTED",
    "WR_NOOP": "STATE.TRANSITION_COMMITTED",
    "WR_DEFERRED": "STATE.TRANSITION_COMMITTED",
}

# Ledger state each runtime event commits (mirrors runtime-kernel.ts, which is
# itself derived from the kernel TRANSITION_TABLE).
RUNTIME_EVENT_LEDGER_STATE: dict[str, str] = {
    "WR_SUBMITTED": "PROPOSED",
    "WR_VALIDATED": "PLANNING",
    "WR_QUEUED": "PENDING",
    "WR_CLAIMED": "IMPLEMENTING",
    "WR_ACKED": "REVIEW",
    "WR_SETTLED": "COMPLETED",
    "WR_REJECTED": "CANCELLED",
    "WR_FAILED": "FAILED",
    "WR_NOOP": "CANCELLED",
    "WR_DEFERRED": "CANCELLED",
}


def to_canonical_event_type(event_type: str) -> str:
    """Transcode a kernel WR_* verb to the canonical storage vocabulary."""
    return RUNTIME_TO_CANONICAL_EVENT.get(event_type, event_type)


def canonicalize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve the runtime verb and add the ledger new_state at the boundary."""
    canonical = to_canonical_event_type(event_type)
    if canonical == event_type:
        return payload
    stored = dict(payload)
    stored["runtime_event_type"] = event_type
    if canonical == "STATE.TRANSITION_COMMITTED":
        stored["new_state"] = RUNTIME_EVENT_LEDGER_STATE[event_type]
    return stored


class WorkRequestState(str, Enum):
    PROPOSED = "PROPOSED"
    PLANNING = "PLANNING"
    PENDING = "PENDING"
    IMPLEMENTING = "IMPLEMENTING"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VisionIRStage(str, Enum):
    PLAN_IR = "PLAN_IR"
    SPEC_IR = "SPEC_IR"
    EXECUTION_IR = "EXECUTION_IR"
    VALIDATION_IR = "VALIDATION_IR"


TRANSITION_MATRIX: dict[WorkRequestState, list[WorkRequestState]] = {
    WorkRequestState.PROPOSED: [WorkRequestState.PLANNING, WorkRequestState.CANCELLED],
    WorkRequestState.PLANNING: [WorkRequestState.PENDING, WorkRequestState.CANCELLED],
    WorkRequestState.PENDING: [WorkRequestState.IMPLEMENTING, WorkRequestState.CANCELLED],
    WorkRequestState.IMPLEMENTING: [WorkRequestState.REVIEW, WorkRequestState.FAILED, WorkRequestState.CANCELLED],
    WorkRequestState.REVIEW: [WorkRequestState.COMPLETED, WorkRequestState.IMPLEMENTING, WorkRequestState.FAILED, WorkRequestState.CANCELLED],
    WorkRequestState.COMPLETED: [],
    WorkRequestState.FAILED: [],
    WorkRequestState.CANCELLED: [],
}

TERMINAL_STATES = frozenset([WorkRequestState.COMPLETED, WorkRequestState.FAILED, WorkRequestState.CANCELLED])


def is_terminal(state: WorkRequestState) -> bool:
    return state in TERMINAL_STATES


def validate_transition(from_state: WorkRequestState, to_state: WorkRequestState) -> bool:
    allowed = TRANSITION_MATRIX.get(from_state, [])
    return to_state in allowed


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    work_request_id: str
    event_type: str
    event_version: int = 1
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)
    actor_type: str = "system"
    actor_id: str = ""
    sequence_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "LedgerEvent":
        return cls(
            event_id=str(row["event_id"]),
            work_request_id=str(row["work_request_id"]),
            event_type=row["event_type"],
            event_version=row.get("event_version", 1),
            correlation_id=str(row["correlation_id"]) if row.get("correlation_id") else None,
            causation_id=str(row["causation_id"]) if row.get("causation_id") else None,
            occurred_at=str(row["occurred_at"]),
            payload=row.get("payload", {}),
            actor_type=row.get("actor_type", "system"),
            actor_id=row.get("actor_id", ""),
            sequence_number=row.get("sequence_number", 0),
        )


@dataclass(frozen=True)
class LedgerState:
    work_request_id: str
    current_state: WorkRequestState = WorkRequestState.PROPOSED
    vision_stage: VisionIRStage | None = None
    vision_ir_version: int = 0
    last_event_id: str | None = None
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["current_state"] = self.current_state.value
        if self.vision_stage:
            d["vision_stage"] = self.vision_stage.value
        return d


def reduce_event(state: LedgerState, event: LedgerEvent) -> LedgerState:
    next_state = LedgerState(
        work_request_id=state.work_request_id,
        current_state=state.current_state,
        vision_stage=state.vision_stage,
        vision_ir_version=state.vision_ir_version,
        last_event_id=event.event_id,
        version=state.version + 1,
    )

    if event.event_type == LedgerEventType.WORKREQUEST_CREATED.value:
        return LedgerState(
            work_request_id=state.work_request_id,
            current_state=WorkRequestState.PROPOSED,
            vision_stage=state.vision_stage,
            vision_ir_version=state.vision_ir_version,
            last_event_id=event.event_id,
            version=state.version + 1,
        )

    if event.event_type == LedgerEventType.STATE_TRANSITION_COMMITTED.value:
        new_state_str = event.payload.get("new_state")
        if new_state_str:
            try:
                new_state = WorkRequestState(new_state_str)
                if validate_transition(state.current_state, new_state):
                    return LedgerState(
                        work_request_id=state.work_request_id,
                        current_state=new_state,
                        vision_stage=state.vision_stage,
                        vision_ir_version=state.vision_ir_version,
                        last_event_id=event.event_id,
                        version=state.version + 1,
                    )
            except ValueError:
                pass

    if event.event_type == LedgerEventType.VISION_IR_PRODUCED.value:
        stage_str = event.payload.get("ir_stage")
        ir_ver = event.payload.get("ir_version")
        new_stage = state.vision_stage
        new_ver = state.vision_ir_version
        if stage_str:
            try:
                new_stage = VisionIRStage(stage_str)
            except ValueError:
                pass
        if isinstance(ir_ver, int):
            new_ver = ir_ver
        return LedgerState(
            work_request_id=state.work_request_id,
            current_state=state.current_state,
            vision_stage=new_stage,
            vision_ir_version=new_ver,
            last_event_id=event.event_id,
            version=state.version + 1,
        )

    return next_state


def fold_events(work_request_id: str, events: list[LedgerEvent]) -> LedgerState:
    sorted_events = sorted(events, key=lambda e: e.sequence_number)
    state = LedgerState(work_request_id=work_request_id)
    for event in sorted_events:
        state = reduce_event(state, event)
    return state


class EventStore:
    def __init__(self, pg_conn: Any) -> None:
        self._conn = pg_conn

    def append(
        self,
        work_request_id: str,
        event_type: str | LedgerEventType,
        payload: dict[str, Any] | None = None,
        actor_type: str = "system",
        actor_id: str = "",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> LedgerEvent:
        if isinstance(event_type, LedgerEventType):
            event_type = event_type.value

        event_id = str(uuid.uuid4())
        payload = canonicalize_event_payload(event_type, payload or {})
        event_type = to_canonical_event_type(event_type)

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conduit.work_request_events (
                    event_id, work_request_id, event_type, payload,
                    actor_type, actor_id, correlation_id, causation_id
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s::jsonb,
                    %s, %s, %s::uuid, %s::uuid
                )
                RETURNING sequence_number, occurred_at
                """,
                (
                    event_id,
                    work_request_id,
                    event_type,
                    json.dumps(payload),
                    actor_type,
                    actor_id,
                    correlation_id,
                    causation_id,
                ),
            )
            row = cur.fetchone()

        return LedgerEvent(
            event_id=event_id,
            work_request_id=work_request_id,
            event_type=event_type,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            sequence_number=row[0] if row else 0,
            occurred_at=str(row[1]) if row else datetime.now(timezone.utc).isoformat(),
        )

    def replay(self, work_request_id: str) -> list[LedgerEvent]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, work_request_id, event_type, event_version,
                       correlation_id, causation_id, occurred_at, payload,
                       actor_type, actor_id, sequence_number
                FROM conduit.work_request_events
                WHERE work_request_id = %s::uuid
                ORDER BY sequence_number
                """,
                (work_request_id,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        return [LedgerEvent.from_row(dict(zip(cols, r))) for r in rows]

    def replay_from_checkpoint(self, work_request_id: str, checkpoint: int) -> list[LedgerEvent]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, work_request_id, event_type, event_version,
                       correlation_id, causation_id, occurred_at, payload,
                       actor_type, actor_id, sequence_number
                FROM conduit.work_request_events
                WHERE work_request_id = %s::uuid
                  AND sequence_number > %s
                ORDER BY sequence_number
                """,
                (work_request_id, checkpoint),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        return [LedgerEvent.from_row(dict(zip(cols, r))) for r in rows]

    def rebuild_state(self, work_request_id: str) -> LedgerState:
        events = self.replay(work_request_id)
        return fold_events(work_request_id, events)

    def rebuild_all_projections(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT conduit.rebuild_all_projections()")
            row = cur.fetchone()
        return row[0] if row else 0

    def get_state(self, work_request_id: str) -> LedgerState | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT work_request_id, current_state, vision_stage,
                       vision_ir_version, last_event_id, updated_at
                FROM conduit.work_request_state
                WHERE work_request_id = %s::uuid
                """,
                (work_request_id,),
            )
            row = cur.fetchone()

        if not row:
            return None

        return LedgerState(
            work_request_id=str(row[0]),
            current_state=WorkRequestState(row[1]),
            vision_stage=VisionIRStage(row[2]) if row[2] else None,
            vision_ir_version=row[3] or 0,
            last_event_id=str(row[4]) if row[4] else None,
        )

    def store_ir_artifact(
        self,
        work_request_id: str,
        event_id: str,
        ir_stage: str | VisionIRStage,
        artifact_type: str,
        content: dict[str, Any],
        ir_version: int = 1,
    ) -> str:
        if isinstance(ir_stage, VisionIRStage):
            ir_stage = ir_stage.value

        artifact_id = str(uuid.uuid4())

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conduit.vision_ir_artifacts (
                    artifact_id, work_request_id, event_id,
                    ir_stage, ir_version, artifact_type, content
                ) VALUES (
                    %s::uuid, %s::uuid, %s::uuid,
                    %s, %s, %s, %s::jsonb
                )
                """,
                (
                    artifact_id,
                    work_request_id,
                    event_id,
                    ir_stage,
                    ir_version,
                    artifact_type,
                    json.dumps(content),
                ),
            )

        return artifact_id
