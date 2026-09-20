"""
Receipts API — GET/POST/DELETE /api/receipts

Provides receipt lifecycle operations from conduit-mcp's db.ts:
  GET  /api/receipts/:planId           — getPlanReceipts (formatted)
  GET  /api/receipts/:planId/raw       — getReceiptsForPlan (raw rows)
  GET  /api/receipts/:planId/latest-type — getLatestReceiptType
  POST /api/receipts                   — insertReceipt
  DELETE /api/receipts/:planId         — deleteReceiptsByPlanAndType (query: types)

Design: Plan 1055 — conduit-mcp SQL consolidation into Python conduit
Validation (validateReceipt) stays in conduit-mcp's receipts.ts.
"""

import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_log = logging.getLogger("kernel.api.receipts")
router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────

class ReceiptInsertRequest(BaseModel):
    id: str
    plan_id: str
    type: str
    agent_role: str
    session_id: str = ""
    ticket_id: Optional[str] = None
    artifact_path: Optional[str] = None
    summary: str = ""
    metadata_json: str = "{}"
    tokens_used: int = 0
    created_at: str
    # C1 gate 1 (Lilac plan 8261639): declaring-producer identity for the
    # TS front-door channel. Optional + defaulting to the Python channel so
    # the request shape stays backward compatible.
    producer_id: Optional[str] = None
    source_channel: Optional[str] = None
    correlation_id: Optional[str] = None


class DeleteReceiptsRequest(BaseModel):
    types: list[str]


# ── Routes ──────────────────────────────────────────────────────────

@router.get("/{plan_id}")
def get_plan_receipts(plan_id: str):
    """Get formatted receipts for a plan. Equivalent to db.ts getPlanReceipts()."""
    from db_adapter import DBAdapter
    db = DBAdapter()

    with db._get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM nebula.receipts_unified WHERE plan_id = %s ORDER BY created_at ASC",
            (plan_id,),
        )
        rows = cursor.dict_fetchall()

    formatted = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r.get("metadata_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        formatted.append({
            "id": r["id"],
            "type": r["type"],
            "agent_role": r.get("agent_role", ""),
            "session_id": r.get("session_id", ""),
            "artifact_path": r.get("artifact_path"),
            "summary": r.get("summary", ""),
            "metadata": meta,
            "created_at": r.get("created_at"),
        })

    return {"plan_id": plan_id, "count": len(formatted), "receipts": formatted}


@router.get("/{plan_id}/raw")
def get_receipts_raw(plan_id: str):
    """Get raw receipt rows for a plan. Equivalent to db.ts getReceiptsForPlan()."""
    from db_adapter import DBAdapter
    db = DBAdapter()

    with db._get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM nebula.receipts_unified WHERE plan_id = %s ORDER BY created_at ASC",
            (plan_id,),
        )
        rows = cursor.dict_fetchall()

    return {"plan_id": plan_id, "count": len(rows), "receipts": rows}


@router.get("/{plan_id}/latest-type")
def get_latest_receipt_type(plan_id: str):
    """Get the latest receipt type for a plan. Equivalent to db.ts getLatestReceiptType()."""
    from db_adapter import DBAdapter
    db = DBAdapter()

    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT type FROM nebula.receipts_unified WHERE plan_id = %s ORDER BY created_at DESC LIMIT 1",
            (plan_id,),
        ).dict_fetchone()

    return {
        "plan_id": plan_id,
        "latest_type": row["type"] if row else None,
    }


@router.post("/")
def insert_receipt(body: ReceiptInsertRequest):
    """Insert a receipt. D-T19-2(b): execution.receipts (request-scoped) when the
    plan resolves to an execution.requests row; vision.receipts fallback for
    test/synthetic plans.
    Validation (validateReceipt) is done client-side in conduit-mcp.

    C1 (Lilac plan 8261639): this route is the single persistence path for
    the HTTP channel — it delegates to ``DBAdapter.insert_receipt`` so both
    channels share one canonical identity/idempotency contract. The caller's
    ``id`` is honored verbatim (idempotency key parity with the direct
    Python path); no receipt id is generated here anymore. Provenance
    (producer/source channel/correlation) is stamped inside the adapter."""
    from db_adapter import DBAdapter
    db = DBAdapter()

    base_meta: dict = {}
    if body.metadata_json:
        try:
            base_meta = json.loads(body.metadata_json)
            if not isinstance(base_meta, dict):
                raise HTTPException(
                    status_code=400,
                    detail="metadata_json must decode to a JSON object",
                )
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"metadata_json is not valid JSON: {exc}",
            )

    db.insert_receipt(
        plan_id=body.plan_id,
        receipt_type=body.type,
        agent_role=body.agent_role,
        session_id=body.session_id or "",
        ticket_id=body.ticket_id,
        summary=body.summary or "",
        artifact_path=body.artifact_path,
        metadata={**base_meta,
                  "producer_id": body.producer_id,
                  "source_channel": body.source_channel},
        tokens_used=body.tokens_used or 0,
        correlation_id=body.correlation_id,
        receipt_id=body.id,
    )

    _log.info("insert_receipt: plan=%s type=%s id=%s channel=%s",
              body.plan_id, body.type, body.id,
              body.source_channel or "conduit-python")
    return {"ok": True, "id": body.id, "plan_id": body.plan_id}


# Sole legitimate caller of the DELETE surface is conduit-mcp's unblock_plan
# tool (typescript/conduit-mcp/src/tools.ts ~L1905), which purges the unblock
# receipt family so a previously-blocked plan can be re-ticketed cleanly.
# Per architect ruling fcec95a2 (#2): the endpoint must NOT remain a
# general-purpose receipt deleter — any type outside the unblock family is
# rejected. This is the receipt-immutability exception, scoped and typed to
# the unblock workflow (see unblock_plan). Do not widen without an architect
# decision.
UNBLOCK_RECEIPT_TYPES = {"BLOCK", "PLAN_BLOCK", "CANCELLED", "ABANDONED"}


@router.delete("/{plan_id}")
def delete_receipts_by_plan_and_type(
    plan_id: str,
    types: str = Query(..., description="Comma-separated receipt types to delete"),
):
    """Delete receipts by plan and type (unblock family only).

    Constrained to the unblock receipt family {BLOCK, PLAN_BLOCK, CANCELLED,
    ABANDONED} — the sole legitimate caller is conduit-mcp's unblock_plan tool
    (typescript/conduit-mcp/src/tools.ts ~L1905). This is a scoped, typed,
    operator-action-backed purge (the unblock workflow), not arbitrary history
    rewriting. Receipt-immutability doctrine is untouched. Any type outside the
    family is rejected with 400.
    """
    from db_adapter import DBAdapter
    db = DBAdapter()

    type_list = [t.strip() for t in types.split(",") if t.strip()]
    if not type_list:
        raise HTTPException(status_code=400, detail="No receipt types provided")

    # HARDENING (ruling fcec95a2 #2): reject anything outside the unblock family.
    disallowed = [t for t in type_list if t not in UNBLOCK_RECEIPT_TYPES]
    if disallowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Receipt type(s) {disallowed} are outside the unblock family "
                f"({sorted(UNBLOCK_RECEIPT_TYPES)}). This endpoint exists solely for "
                "unblock_plan and must not be used as a general-purpose receipt deleter."
            ),
        )

    placeholders = ", ".join(["%s"] * len(type_list))
    with db._get_connection() as conn:
        cursor = conn.execute(
            f"DELETE FROM vision.receipts WHERE plan_id = %s AND type IN ({placeholders})",
            (plan_id, *type_list),
        )
        conn.commit()
        deleted = cursor.rowcount or 0

    _log.info("delete_receipts: plan=%s types=%s deleted=%d", plan_id, type_list, deleted)
    return {"deleted": deleted, "plan_id": plan_id, "types": type_list}
