"""Proxy routes for the typed Lilac surface (plan 8261669).

This module is a TRANSLATOR, not an implementation. Every mutation is handed
to ``LilacClient`` and the answer is returned unchanged; there is no SQL here,
no fan-out logic, and no fallback that writes canonically by another route.
That is asserted statically in
``python/conduit/tests/test_lilac_service_contract.py`` (AC5) — a proxy that
can write on its own is not a proxy.

STAGED-INERT (AC6). The router is mounted only when ``CONDUIT_LILAC_URL`` is
set (see ``lilac_enabled_from_env`` and the guarded include in app/main.py).
With the variable unset, none of these paths exist and the default conduit
behaviour is unchanged.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

_log = logging.getLogger("kernel.api.lilac")
router = APIRouter()

ENV_BASE_URL = "CONDUIT_LILAC_URL"


def lilac_enabled_from_env(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when a Lilac base URL is configured. Default: False."""
    env = environ if environ is not None else os.environ
    return bool((env.get(ENV_BASE_URL) or "").strip())


def _client(request: Request):
    """Resolve the client from app state; the app builds it once at startup."""
    client = getattr(request.app.state, "lilac_client", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "LILAC_UNAVAILABLE",
                    "message": "lilac-srv is not configured on this conduit instance",
                }
            },
        )
    return client


def _refusal(exc: Exception) -> HTTPException:
    """Map a service refusal onto the same status and code it reported."""
    from lilac_client import LilacServiceError

    if isinstance(exc, LilacServiceError):
        return HTTPException(
            status_code=exc.status or 502,
            detail={"error": {"code": exc.code, "message": str(exc), "details": exc.detail}},
        )
    return HTTPException(
        status_code=502,
        detail={"error": {"code": "LILAC_PROXY_FAILURE", "message": str(exc)}},
    )


# ── request models ────────────────────────────────────────────────────
class ProvenanceModel(BaseModel):
    source_implementation: str
    runtime_route: str
    contract_version: int = 1
    source_identity: str
    idempotency_key: str


class ReceiptRequest(BaseModel):
    provenance: ProvenanceModel
    producer_id: str
    kind: str
    source_system: str
    source_receipt_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    refs: Dict[str, Any] = Field(default_factory=dict)


class TicketRequest(BaseModel):
    provenance: ProvenanceModel
    workflow_ref: str
    role: str
    position: int
    generation: int = 0
    objective: str = ""
    predecessor_receipt_id: Optional[str] = None


class TransitionRequest(BaseModel):
    provenance: ProvenanceModel
    input_receipt_id: str
    from_status: str
    to_status: str
    fanout_policy_version: int = 1
    outcome_class: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class FanoutRequest(BaseModel):
    provenance: ProvenanceModel
    input_receipt_id: str
    kind: str
    fan_out_policy_version: int = 1
    produced: list = Field(default_factory=list)
    outcome: Optional[str] = None


def _outcome(result: Any) -> Dict[str, Any]:
    return {
        "outcome": result.outcome,
        "target_id": result.target_id,
        "surface": result.surface,
    }


# ── mutations (delegation only) ───────────────────────────────────────
@router.post("/receipts", status_code=201)
def post_receipt(body: ReceiptRequest, request: Request) -> Dict[str, Any]:
    client = _client(request)
    try:
        result = client.issue_receipt(
            producer_id=body.producer_id,
            kind=body.kind,
            source_system=body.source_system,
            source_receipt_id=body.source_receipt_id,
            payload=body.payload,
            refs=body.refs,
            idempotency_key=body.provenance.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a typed HTTP refusal
        raise _refusal(exc) from exc
    return _outcome(result)


@router.post("/tickets", status_code=201)
def post_ticket(body: TicketRequest, request: Request) -> Dict[str, Any]:
    client = _client(request)
    try:
        result = client.issue_ticket(
            workflow_ref=body.workflow_ref,
            role=body.role,
            position=body.position,
            generation=body.generation,
            objective=body.objective,
            predecessor_receipt_id=body.predecessor_receipt_id,
            idempotency_key=body.provenance.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _refusal(exc) from exc
    return _outcome(result)


@router.post("/tickets/{ticket_id}/transitions", status_code=201)
def post_transition(ticket_id: str, body: TransitionRequest, request: Request) -> Dict[str, Any]:
    client = _client(request)
    try:
        result = client.record_transition(
            ticket_id=ticket_id,
            input_receipt_id=body.input_receipt_id,
            from_status=body.from_status,
            to_status=body.to_status,
            fanout_policy_version=body.fanout_policy_version,
            outcome_class=body.outcome_class,
            payload=body.payload,
            idempotency_key=body.provenance.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _refusal(exc) from exc
    return _outcome(result)


@router.post("/fanouts", status_code=201)
def post_fanout(body: FanoutRequest, request: Request) -> Dict[str, Any]:
    client = _client(request)
    try:
        result = client.record_fanout(
            input_receipt_id=body.input_receipt_id,
            kind=body.kind,
            produced=body.produced,
            fan_out_policy_version=body.fan_out_policy_version,
            outcome=body.outcome,
            idempotency_key=body.provenance.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _refusal(exc) from exc
    return _outcome(result)


# ── reads ─────────────────────────────────────────────────────────────
@router.get("/tickets/{ticket_id}/timeline")
def get_timeline(ticket_id: str, request: Request) -> Dict[str, Any]:
    client = _client(request)
    try:
        return client.ticket_timeline(ticket_id)
    except Exception as exc:  # noqa: BLE001
        raise _refusal(exc) from exc


@router.get("/parity")
def get_parity(request: Request, limit: int = 5) -> Dict[str, Any]:
    client = _client(request)
    try:
        return client.parity_diagnostics(limit)
    except Exception as exc:  # noqa: BLE001
        raise _refusal(exc) from exc
