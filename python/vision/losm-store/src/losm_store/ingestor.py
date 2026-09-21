from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

from losm_store.models import PlanningTask, WorkStatus
from losm_store.canonical_bridge import (
    dual_write_enabled,
    canonical_update_for_status,
    mirror_insert_ingest_record,
    mirror_insert_governance_event,
    mirror_update_work_request,
)
from losm_store.governed_triggers import GovernedTriggerAdapter
from losm_ir.execution_receipt import ExecutionReceipt
from losm_ir.transition import validate_transition

# Map receipt results to target lifecycle states.
_RESULT_TO_STATE = {
    "SUCCESS": "COMPLETION",
    "FAILED": "FAILED",
    "PARTIAL": "BLOCKED",
}


class ExecutionReceiptIngestor:
    def __init__(self, trigger_adapter: GovernedTriggerAdapter | None = None):
        self.trigger_adapter = trigger_adapter or GovernedTriggerAdapter()

    def ingest(self, db: Session, receipt_payload: Dict[str, Any]) -> Dict[str, Any]:
        receipt = ExecutionReceipt.model_validate(receipt_payload)
        canonical = receipt.model_dump(mode="json", by_alias=True)
        receipt_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        # W2a substrate correction: dedupe against the history base (any
        # version — a hash seen in a closed version is still a duplicate).
        existing = db.connection().execute(
            text(
                "SELECT receipt_id, work_request_id FROM "
                "vision.receipt_ingest_records_history "
                "WHERE receipt_hash = :h LIMIT 1"
            ),
            {"h": receipt_hash},
        ).mappings().first()
        if existing is not None:
            return {
                "status": "duplicate",
                "receipt_id": existing["receipt_id"],
                "work_request_id": existing["work_request_id"],
                "event_type": "RECEIPT_DUPLICATE",
            }

        # W2a substrate correction: receipt_ingest_records is a read-only
        # view on live — the row lands in receipt_ingest_records_history via
        # the mirror primitive; ingest_row is a plain namespace for returns.
        ingest_row = SimpleNamespace(
            receipt_id=str(uuid.uuid4()),
            work_request_id=receipt.work_request_id,
        )
        mirror_insert_ingest_record(
            db,
            receipt_id=ingest_row.receipt_id,
            work_request_id=receipt.work_request_id,
            executor_id=receipt.executor_id,
            receipt_hash=receipt_hash,
            result=receipt.result,
            lineage_parent=receipt.lineage_parent,
            payload=canonical,
        )

        planning_task = db.query(PlanningTask).filter_by(wr_id=receipt.work_request_id).first()
        if planning_task is None:
            governance_event_id = str(uuid.uuid4())
            governance_payload = {
                "reason": "planning_task_not_found",
                "executor_id": receipt.executor_id,
            }
            mirror_insert_governance_event(
                db,
                event_id=governance_event_id,
                event_type="RECEIPT_ORPHANED",
                work_request_id=receipt.work_request_id,
                lineage_parent=receipt.lineage_parent,
                payload=governance_payload,
            )
            self.trigger_adapter.emit(
                db,
                self.trigger_adapter.receipt_outcome(
                    event_id=governance_event_id,
                    wr_id=receipt.work_request_id,
                    event_type="RECEIPT_ORPHANED",
                    outcome="rejected",
                    payload=governance_payload,
                ),
            )
            db.commit()
            return {
                "status": "orphaned",
                "receipt_id": ingest_row.receipt_id,
                "work_request_id": receipt.work_request_id,
                "event_type": "RECEIPT_ORPHANED",
            }

        # W2a: read the current projection (view), merge receipt context,
        # then canonical-first status mutation + bitemporal mirror update.
        context = planning_task.context_data or {}
        context["last_receipt_id"] = ingest_row.receipt_id
        context["last_receipt_hash"] = receipt_hash
        context["last_lineage_parent"] = receipt.lineage_parent
        context["receipt_results"] = (context.get("receipt_results") or []) + [receipt.result]

        # Resolve target state from receipt result, then validate the transition.
        target_state = _RESULT_TO_STATE.get(receipt.result)
        if target_state is None:
            return self._reject(db, ingest_row, receipt, planning_task,
                                f"Unknown receipt result: '{receipt.result}'")

        current_state = planning_task.status.value
        validation = validate_transition(current_state, target_state)
        if not validation.allowed:
            return self._reject(db, ingest_row, receipt, planning_task,
                                f"Receipt result '{receipt.result}' invalid: "
                                f"{current_state} → {target_state}. {validation.reason}")

        # W2a (plan 4c3b74bd): canonical-first status mutation (mapped via
        # STATUS_MAP, losm state preserved in context.losm_status), then the
        # bitemporal mirror update (verbatim target state) — same txn.
        if dual_write_enabled():
            canonical_update_for_status(
                db,
                wr_id=receipt.work_request_id,
                new_status_value=target_state,
            )
        mirror_update_work_request(
            db,
            wr_id=receipt.work_request_id,
            intent=None,
            constraints=None,
            priority=None,
            context_data=context,
            status=target_state,
        )

        governance_event_id = str(__import__("uuid").uuid4())
        governance_payload = {
            "executor_id": receipt.executor_id,
            "result": receipt.result,
            "receipt_hash": receipt_hash,
        }
        mirror_insert_governance_event(
            db,
            event_id=governance_event_id,
            event_type="RECEIPT_INGESTED",
            work_request_id=receipt.work_request_id,
            lineage_parent=receipt.lineage_parent,
            payload=governance_payload,
        )
        self.trigger_adapter.emit(
            db,
            self.trigger_adapter.receipt_outcome(
                event_id=governance_event_id,
                wr_id=receipt.work_request_id,
                event_type="RECEIPT_INGESTED",
                outcome="committed",
                payload=governance_payload,
            ),
        )

        db.commit()
        return {
            "status": "ingested",
            "receipt_id": ingest_row.receipt_id,
            "work_request_id": receipt.work_request_id,
            "event_type": "RECEIPT_INGESTED",
        }

    def _reject(self, db, ingest_row, receipt, planning_task, reason: str) -> dict:
        """Record a rejection governance event. Does NOT mutate task status."""
        governance_event_id = str(__import__("uuid").uuid4())
        governance_payload = {
            "reason": reason,
            "executor_id": receipt.executor_id,
            "receipt_result": receipt.result,
            "current_status": planning_task.status.value if planning_task else None,
        }
        mirror_insert_governance_event(
            db,
            event_id=governance_event_id,
            event_type="RECEIPT_REJECTED",
            work_request_id=receipt.work_request_id,
            lineage_parent=receipt.lineage_parent,
            payload=governance_payload,
        )
        self.trigger_adapter.emit(
            db,
            self.trigger_adapter.receipt_outcome(
                event_id=governance_event_id,
                wr_id=receipt.work_request_id,
                event_type="RECEIPT_REJECTED",
                outcome="rejected",
                payload=governance_payload,
            ),
        )
        db.commit()
        return {
            "status": "rejected",
            "receipt_id": ingest_row.receipt_id,
            "work_request_id": receipt.work_request_id,
            "event_type": "RECEIPT_REJECTED",
            "reason": reason,
        }


__all__ = ["ExecutionReceiptIngestor"]
