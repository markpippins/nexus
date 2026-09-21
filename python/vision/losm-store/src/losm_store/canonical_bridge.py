"""W2a canonical bridge — flagged dual-write between losm_store and resolution.work_request.

Per plan-of-record 4c3b74bd (Stage-2 tranche W2, WR canonicalization plan 8261650).

**Substrate correction (verified live 2026-09-21, supersedes the plan premise):**
``vision.work_requests_losm`` — the ORM's PlanningTask target — is a **read-only
VIEW over ``vision.work_requests_history``** (open bitemporal slice only; the
``status`` column is a CASE projection, so the view is not writable for status,
and no INSTEAD OF triggers exist). The same holds for the other losm ORM
targets: ``receipt_ingest_records``, ``governance_events``, ``lifecycle_events``,
``artifacts``, ``branches``, ``branch_artifacts``, ``work_request_edges`` are all
views over ``*_history`` base tables whose integer ``id`` has **no default**.
The live base table ``vision.work_requests`` is a DIFFERENT population (the
canonical landing zone, guarded by ``trg_canonical_wr_landing_guard``) — not the
losm mirror substrate.

Therefore the legacy write path is implemented as **raw bitemporal SQL against
the ``*_history`` base tables** (create → insert an open version; update →
close the old version + insert a new open version; delete → close the slice =
bitemporal tombstone). This makes the legacy-only mode genuinely functional
and keeps the views (and everything reading them — artifacts, branches,
work_request_edges, the losm-host REST read surface) read-your-writes
coherent, without touching the guard-governed base table and without any
schema change.

* Canonical store: ``resolution.work_request`` (post-V186 bitemporal shape).
* Writes are CANONICAL-FIRST, then the history mirror in the SAME transaction
  (the governed_triggers.py pattern: raw param SQL on the caller's connection,
  no extra commit). A canonical write failure aborts the whole operation; a
  mirror failure aborts too — never silent.
* Rollback = flag flip: ``LOSM_W2_DUAL_WRITE=0`` restores legacy-only behavior
  (which still works, because it now writes the history base directly).
* PK-carried identity (V186 convention): one UUID is BOTH the losm ``wr_id``
  and the canonical ``id``; canonical ``legacy_id`` =
  ``vision.work_requests_history:<uuid>``.
* Status mapping is TOTAL over ``losm_ir.states.WorkStatus`` and maps into the
  canonical CHECK vocabulary (work_request_business_status_check:
  DRAFT/APPROVED/DISPATCHED/COMPLETED/CANCELLED) following the V186 backfill
  precedent (non-settled → DRAFT; no silent ELSE — the authoritative losm
  state is preserved in canonical ``context.losm_status`` and stored VERBATIM
  in the history mirror's status column).
* Canonical rows are never hard-deleted through this path; deleting via the
  losm API maps to a CANCELLED disposition plus a bitemporal tombstone in the
  mirror.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

CANONICAL_LEGACY_SOURCE = "vision.work_requests_history"

# The canonical CHECK vocabulary (work_request_business_status_check).
CANONICAL_STATUS_VOCABULARY = frozenset(
    {"DRAFT", "APPROVED", "DISPATCHED", "COMPLETED", "CANCELLED"}
)

# Total map losm WorkStatus -> canonical business_status.
# Precedent: V186 backfill mapped every non-settled legacy state to DRAFT and
# refused silent ELSEs. The losm state remains authoritative in
# context.losm_status; BLOCKED/FAILED are honest non-settled states, not
# cancellations, so they map to DRAFT rather than lying in either direction.
STATUS_MAP: Dict[str, str] = {
    "NEW": "DRAFT",
    "INTAKE": "DRAFT",
    "PLAN_GENERATION": "DRAFT",
    "PLAN_REVIEW": "DRAFT",
    "BLOCKED": "DRAFT",
    "FAILED": "DRAFT",
    "PLAN_APPROVAL_GATE": "APPROVED",
    "SPEC_GENERATION": "APPROVED",
    "EXECUTION": "DISPATCHED",
    "VALIDATION": "DISPATCHED",
    "COMPLETION": "COMPLETED",
}

# Advisory-lock key for mirror id allocation (arbitrary constant, namespaced).
MIRROR_ID_LOCK_KEY = 727201
EPOCH_OPEN = "9999-12-31 23:59:59+00"

_TITLE_MAX = 200


def dual_write_enabled() -> bool:
    """Flag gate: LOSM_W2_DUAL_WRITE (default ON; '0'/'off'/'false' disables)."""
    raw = os.environ.get("LOSM_W2_DUAL_WRITE", "1")
    return raw.strip().lower() not in {"0", "off", "false", "no"}


def map_status(losm_status: str) -> str:
    """Total, checked mapping losm WorkStatus value -> canonical status."""
    mapped = STATUS_MAP.get(losm_status)
    if mapped is None:
        # No silent ELSE: an unknown state is a loud programming error.
        raise ValueError(
            f"W2a bridge: no canonical mapping for losm status {losm_status!r}; "
            f"extend STATUS_MAP explicitly (plan 4c3b74bd forbids silent ELSE)."
        )
    if mapped not in CANONICAL_STATUS_VOCABULARY:
        raise ValueError(
            f"W2a bridge: mapped status {mapped!r} violates "
            f"work_request_business_status_check"
        )
    return mapped


def _title_from_intent(intent: str) -> str:
    return (intent or "").strip()[:_TITLE_MAX]


# ── Legacy bitemporal mirror primitives (vision.*_history bases) ─────────────


def _next_history_id(db: Session, table: str) -> int:
    """Allocate a history id under an advisory lock (no default, no sequence).

    SELECT ... FOR UPDATE on the max row does not serialize against concurrent
    inserts, so the house idiom here is a transaction-scoped advisory lock
    around max(id)+1. History is low-volume (WR lifecycle events), so the
    contention cost is negligible.
    """
    conn = db.connection()
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIRROR_ID_LOCK_KEY})
    row = conn.execute(
        text(f"SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM vision.{table}")
    ).mappings().first()
    return int(row["nid"])


def mirror_insert_work_request(
    db: Session,
    *,
    wr_id: str,
    intent: str,
    status: str,
    constraints: Optional[Dict[str, Any]],
    priority: int,
    context_data: Optional[Dict[str, Any]],
    parent_request_id: Optional[str] = None,
) -> None:
    """Insert an OPEN bitemporal version into vision.work_requests_history."""
    nid = _next_history_id(db, "work_requests_history")
    db.connection().execute(
        text(
            "INSERT INTO vision.work_requests_history "
            "(id, wr_id, parent_request_id, intent, constraints, priority, "
            " context, status, created_at, recorded_on_dt, recorded_until_dt) "
            "VALUES (:id, :wr_id, :parent_request_id, :intent, "
            " CAST(:constraints AS jsonb), :priority, CAST(:context AS jsonb), "
            " :status, now(), now(), CAST(:open AS timestamptz))"
        ),
        {
            "id": nid,
            "wr_id": wr_id,
            "parent_request_id": parent_request_id,
            "intent": intent,
            "constraints": json.dumps(constraints or {}),
            "priority": int(priority),
            "context": json.dumps(context_data or {}),
            "status": status,
            "open": EPOCH_OPEN,
        },
    )


def mirror_close_work_request(db: Session, *, wr_id: str) -> int:
    """Close ALL open versions for a wr_id (recorded_until_dt := now()).

    Returns the number of versions closed. Used before inserting a new
    version (update) or tombstoning (delete).
    """
    result = db.connection().execute(
        text(
            "UPDATE vision.work_requests_history SET recorded_until_dt = now() "
            "WHERE wr_id = :wr_id AND recorded_until_dt = CAST(:open AS timestamptz)"
        ),
        {"wr_id": wr_id, "open": EPOCH_OPEN},
    )
    return result.rowcount or 0


def mirror_update_work_request(
    db: Session,
    *,
    wr_id: str,
    intent: Optional[str],
    constraints: Optional[Dict[str, Any]],
    priority: Optional[int],
    context_data: Optional[Dict[str, Any]],
    status: str,
) -> None:
    """Bitemporal update: close the open version, insert a new open version.

    ``status`` here is the VERBATIM losm WorkStatus value (the history base
    has no CHECK — the view's CASE maps legacy spellings for old readers).
    """
    mirror_close_work_request(db, wr_id=wr_id)
    # New version carries the merged current projection. Scalar fields the
    # caller did not change fall back to the last version's values.
    row = db.connection().execute(
        text(
            "SELECT intent, constraints, priority, context FROM "
            "vision.work_requests_history WHERE wr_id = :wr_id "
            "ORDER BY recorded_on_dt DESC LIMIT 1"
        ),
        {"wr_id": wr_id},
    ).mappings().first()
    new_intent = intent if intent is not None else (row["intent"] if row else "")
    new_constraints = constraints if constraints is not None else (
        row["constraints"] if row else {}
    )
    new_priority = int(priority) if priority is not None else (
        int(row["priority"]) if row else 5
    )
    new_context = context_data if context_data is not None else (
        row["context"] if row else {}
    )
    mirror_insert_work_request(
        db,
        wr_id=wr_id,
        intent=new_intent,
        status=status,
        constraints=new_constraints,
        priority=new_priority,
        context_data=new_context,
    )


def mirror_tombstone_work_request(db: Session, *, wr_id: str) -> int:
    """Bitemporal delete: close the open slice (recorded_until_dt := now()).

    The view filters to the open slice, so the row disappears from every
    reader — honoring the existing hard-delete wire contract — while the
    history retains the full trail. Returns versions closed.
    """
    return mirror_close_work_request(db, wr_id=wr_id)


def mirror_insert_ingest_record(
    db: Session,
    *,
    receipt_id: str,
    work_request_id: str,
    executor_id: str,
    receipt_hash: str,
    result: str,
    lineage_parent: str,
    payload: Dict[str, Any],
) -> None:
    """Insert into vision.receipt_ingest_records_history (open version)."""
    nid = _next_history_id(db, "receipt_ingest_records_history")
    db.connection().execute(
        text(
            "INSERT INTO vision.receipt_ingest_records_history "
            "(id, receipt_id, work_request_id, executor_id, receipt_hash, "
            " result, lineage_parent, payload, created_at, recorded_on_dt, "
            " recorded_until_dt) "
            "VALUES (:id, :receipt_id, :work_request_id, :executor_id, "
            " :receipt_hash, :result, :lineage_parent, "
            " CAST(:payload AS jsonb), now(), now(), CAST(:open AS timestamptz))"
        ),
        {
            "id": nid,
            "receipt_id": receipt_id,
            "work_request_id": work_request_id,
            "executor_id": executor_id,
            "receipt_hash": receipt_hash,
            "result": result,
            "lineage_parent": lineage_parent,
            "payload": json.dumps(payload),
            "open": EPOCH_OPEN,
        },
    )


def mirror_insert_lifecycle_event(
    db: Session,
    *,
    event_id: str,
    wr_id: str,
    from_state: Optional[str],
    to_state: str,
    actor: str,
    reason: Optional[str],
) -> None:
    """Insert into vision.lifecycle_events_history (open version)."""
    nid = _next_history_id(db, "lifecycle_events_history")
    db.connection().execute(
        text(
            "INSERT INTO vision.lifecycle_events_history "
            "(id, event_id, wr_id, from_state, to_state, actor, reason, "
            " metadata, created_at, recorded_on_dt, recorded_until_dt) "
            "VALUES (:id, :event_id, :wr_id, :from_state, :to_state, :actor, "
            " :reason, CAST(:metadata AS jsonb), now(), now(), "
            " CAST(:open AS timestamptz))"
        ),
        {
            "id": nid,
            "event_id": event_id,
            "wr_id": wr_id,
            "from_state": from_state,
            "to_state": to_state,
            "actor": actor,
            "reason": reason,
            "metadata": json.dumps({}),
            "open": EPOCH_OPEN,
        },
    )


def mirror_insert_governance_event(
    db: Session,
    *,
    event_id: str,
    event_type: str,
    work_request_id: str,
    lineage_parent: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """Insert into vision.governance_events_history (open version)."""
    nid = _next_history_id(db, "governance_events_history")
    db.connection().execute(
        text(
            "INSERT INTO vision.governance_events_history "
            "(id, event_id, event_type, work_request_id, lineage_parent, "
            " payload, created_at, recorded_on_dt, recorded_until_dt) "
            "VALUES (:id, :event_id, :event_type, :work_request_id, "
            " :lineage_parent, CAST(:payload AS jsonb), now(), now(), "
            " CAST(:open AS timestamptz))"
        ),
        {
            "id": nid,
            "event_id": event_id,
            "event_type": event_type,
            "work_request_id": work_request_id,
            "lineage_parent": lineage_parent,
            "payload": json.dumps(payload),
            "open": EPOCH_OPEN,
        },
    )


# ── Canonical write primitives (resolution.work_request) ────────────────────


def canonical_insert_for_create(
    db: Session,
    *,
    wr_id: str,
    intent: str,
    constraints: Optional[Dict[str, Any]],
    priority: int,
    context_data: Optional[Dict[str, Any]],
    created_by: str = "losm_store.w2a",
) -> None:
    """Insert the canonical row FIRST (same txn as the mirror write).

    Shape per 767abf1b / V186: plan_id column NULL (plan affinity, when any,
    lives in context), bitemporal defaults from the table, legacy_id carrying
    the mirror source table + shared uuid.
    """
    context: Dict[str, Any] = dict(context_data or {})
    context["losm_wr_id"] = wr_id
    context["losm_status"] = "NEW"
    context["losm_priority"] = priority
    context["w2a_source"] = "losm_store.create"
    db.connection().execute(
        text(
            "INSERT INTO resolution.work_request "
            "(id, title, business_status, intent, context, constraints, "
            " legacy_id, created_by) "
            "VALUES (:id, :title, :business_status, :intent, "
            " CAST(:context AS jsonb), CAST(:constraints AS jsonb), "
            " :legacy_id, :created_by) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": wr_id,
            "title": _title_from_intent(intent),
            "business_status": map_status("NEW"),
            "intent": intent,
            "context": json.dumps(context),
            "constraints": json.dumps(constraints or {}),
            "legacy_id": f"{CANONICAL_LEGACY_SOURCE}:{wr_id}",
            "created_by": created_by,
        },
    )


def canonical_update_for_status(
    db: Session,
    *,
    wr_id: str,
    new_status_value: str,
    intent: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
) -> None:
    """Canonical-first status (and scalar) update; losm state kept in context.

    ``new_status_value`` is a losm WorkStatus value (e.g. 'EXECUTION'); it is
    mapped through STATUS_MAP and recorded verbatim in context.losm_status.
    """
    canonical_status = map_status(new_status_value)
    context_merge: Dict[str, Any] = {
        "losm_status": new_status_value,
        "w2a_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if priority is not None:
        context_merge["losm_priority"] = priority
    set_clauses = [
        "business_status = :business_status",
        "context = context || CAST(:context_merge AS jsonb)",
        "updated_at = now()",
    ]
    params: Dict[str, Any] = {
        "id": wr_id,
        "business_status": canonical_status,
        "context_merge": json.dumps(context_merge),
    }
    if intent is not None:
        set_clauses.append("intent = :intent")
        params["intent"] = intent
        # Keep the title projection (intent[:200] at create) in sync.
        set_clauses.append("title = :title")
        params["title"] = _title_from_intent(intent)
    if constraints is not None:
        set_clauses.append("constraints = CAST(:constraints AS jsonb)")
        params["constraints"] = json.dumps(constraints or {})
    db.connection().execute(
        text(
            f"UPDATE resolution.work_request SET {', '.join(set_clauses)} "
            f"WHERE id = :id"
        ),
        params,
    )


def canonical_disposition_for_delete(db: Session, *, wr_id: str) -> None:
    """Map a losm API delete to a canonical CANCELLED disposition.

    Canonical rows are not deleted. A COMPLETED row is never regressed; the
    disposition is still recorded in context for the audit trail.
    """
    db.connection().execute(
        text(
            "UPDATE resolution.work_request "
            "SET business_status = CASE WHEN business_status = 'COMPLETED' "
            "  THEN business_status ELSE 'CANCELLED' END, "
            "context = context || CAST(:context_merge AS jsonb), updated_at = now() "
            "WHERE id = :id"
        ),
        {
            "id": wr_id,
            "context_merge": json.dumps(
                {
                    "losm_disposition": "deleted_via_losm_api",
                    "w2a_disposition_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        },
    )


__all__ = [
    "CANONICAL_LEGACY_SOURCE",
    "CANONICAL_STATUS_VOCABULARY",
    "STATUS_MAP",
    "dual_write_enabled",
    "map_status",
    "canonical_insert_for_create",
    "canonical_update_for_status",
    "canonical_disposition_for_delete",
    "mirror_insert_work_request",
    "mirror_update_work_request",
    "mirror_tombstone_work_request",
    "mirror_insert_ingest_record",
    "mirror_insert_governance_event",
    "mirror_insert_lifecycle_event",
]
