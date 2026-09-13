"""HTTP boundary for the Python PEB kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .domain import AdmissionPath, PebAdmissionResult, PebCapability, PebTransaction, PebStateHash, MalformedAdmissionRequest
from .engine import PebGovernanceEngine
from .ports import PebStore


@dataclass(frozen=True)
class ApiResult:
    status_code: int
    body: dict[str, Any]


class AdmissionController:
    """Framework-free controller logic, mirroring the Java boundary checks."""

    REQUIRED_FIELDS = ("idempotencyKey", "entityId", "toolName", "input")

    def __init__(self, governance_engine: PebGovernanceEngine) -> None:
        self.governance_engine = governance_engine

    def submit_transaction(self, payload: Mapping[str, Any] | None) -> ApiResult:
        if payload is None:
            return ApiResult(400, {"message": "Malformed admission request: missing required field(s): transaction body"})

        missing = self._missing_fields(payload)
        if missing:
            return ApiResult(
                400,
                {"message": "Malformed admission request: missing required field(s): " + ", ".join(missing)},
            )

        transaction = PebTransaction.from_payload(payload)
        path = AdmissionPath.from_tool_name(transaction.tool_name)

        # Idempotent replay (JVM kernel parity; ruling 6677c394): a replayed
        # submission under the same idempotency key returns the RECORDED
        # outcome instead of a raw UniqueViolation (500). A different payload
        # under the same key is a conflicting replay and is refused.
        store = self.governance_engine.store
        finder = getattr(store, "find_by_idempotency_key", None)
        if callable(finder):
            existing = finder(transaction.idempotency_key)
            if existing is not None:
                same_payload = existing.input == transaction.input
                if same_payload:
                    # Return the RECORDED outcome: a violation report replay is
                    # endpoint-admitted (the violation exists); anything else
                    # replays its recorded admission_result.
                    if existing.tool_name == "peb_report_violation":
                        replay_admitted = True
                        message = "Idempotent replay: violation already recorded"
                    else:
                        recorded = existing.admission_result
                        replay_admitted = recorded is not None and recorded.value == "ALLOWED"
                        message = (
                            "Idempotent replay: recorded admission result "
                            + (recorded.value if recorded else "UNKNOWN")
                        )
                    result = PebAdmissionResult.from_transaction(
                        existing,
                        message,
                        admitted=replay_admitted,
                    )
                    return ApiResult(200 if replay_admitted else 422, result.to_dict())
                return ApiResult(
                    409,
                    {
                        "message": "Conflicting replay: idempotency key already used with a different payload",
                        "transaction_id": str(existing.id),
                    },
                )

        try:
            response = self.governance_engine.process_for_path(transaction, path)
        except MalformedAdmissionRequest as exc:
            # Build a fail-closed result carrying envelope refs if present
            result = PebAdmissionResult.from_transaction(
                transaction, f"Malformed admission request: {exc}", admitted=False
            )
            return ApiResult(422, result.to_dict())

        # Build the envelope-aware result (W1.12: PebAdmissionResult).
        # `admitted` is the endpoint-level outcome from the engine response,
        # NOT the governance result — a REPORT_VIOLATION transaction has
        # admission_result=REJECTED but admitted=True (violation recorded).
        result = PebAdmissionResult.from_transaction(
            transaction, response.message, admitted=response.admitted
        )
        return ApiResult(200 if result.admitted else 422, result.to_dict())

    def register_capability(self, payload: Mapping[str, Any] | None) -> ApiResult:
        """Register (grant) a capability in the PEB registry."""
        from datetime import datetime as _dt

        if not isinstance(payload, Mapping):
            return ApiResult(400, {"message": "capability body required"})
        capability = payload.get("capability") or payload.get("name")
        entity_id = payload.get("entityId") or payload.get("entity_id")
        if not isinstance(capability, str) or not capability.strip():
            return ApiResult(400, {"message": "capability (string) is required"})
        if not isinstance(entity_id, str) or not entity_id.strip():
            return ApiResult(400, {"message": "entityId (string) is required"})
        expires_at = payload.get("expiresAt") or payload.get("expires_at")
        parsed_expiry = None
        if isinstance(expires_at, str) and expires_at:
            try:
                parsed_expiry = _dt.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                return ApiResult(400, {"message": f"invalid expiresAt: {expires_at}"})
        record = PebCapability(
            entity_id=entity_id,
            capability=capability,
            granted_by=(
                payload.get("grantedBy")
                if isinstance(payload.get("grantedBy"), str)
                else None
            ),
            expires_at=parsed_expiry,
            active=bool(payload.get("active", True)),
        )
        store = self.governance_engine.store
        try:
            with store.transaction():
                saved = store.save_capability(record)
                active_count = sum(
                    1 for c in store.list_capabilities() if c.active
                )
        except Exception as exc:
            return ApiResult(503, {"message": f"capability registry write failed: {exc}"})
        return ApiResult(201, {
            "id": str(saved.id),
            "entityId": saved.entity_id,
            "capability": saved.capability,
            "active": saved.active,
            "activeCapabilities": active_count,
        })

    def list_capabilities(self) -> ApiResult:
        store = self.governance_engine.store
        try:
            with store.transaction():
                caps = store.list_capabilities()
        except Exception as exc:
            return ApiResult(503, {"message": f"capability registry read failed: {exc}"})
        return ApiResult(200, {
            "items": [
                {
                    "id": str(c.id),
                    "entityId": c.entity_id,
                    "capability": c.capability,
                    "grantedBy": c.granted_by,
                    "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
                    "createdAt": c.created_at.isoformat() if c.created_at else None,
                    "active": c.active,
                }
                for c in caps
            ],
            "total": len(caps),
        })

    def state_hash(self) -> ApiResult:
        """Build the PebStateResponse envelope served at GET /api/v1/peb/state/hash.

        The envelope matches the ``PebStateResponse`` contract in the peb-mcp
        client (``typescript/peb-mcp/src/api/apiClient.ts``). Field semantics:

        - ``peb_state_hash``: the canonical sorted-leaf Merkle root over all
          peb state rows plus the latest decision's ``after_hash`` (same
          algorithm as the Java kernel's peb-hash).
        - ``document_hashes``: ``{state_key: checksum}`` for every state row,
          keys sorted for determinism.
        - ``last_decision_hash``: the latest decision's ``after_hash``
          (falling back to ``before_hash``), or ``""`` when no decision exists.
        - ``thought_context_hash``: the kernel has no separate thought-context
          store, so this is a deterministic digest over the current epistemic
          context: ``sha256(peb_state_hash + ":" + last_decision_hash)``.
        - ``cognitive_mode``: static ``"operational"`` — no cognitive-mode
          store exists; the field is kept for contract compatibility.
        """
        store = self.governance_engine.store
        hash_service = self.governance_engine.transaction_engine.hash_service
        try:
            # PostgresPebStore requires reads inside store.transaction() (the
            # connection is bound to the context); the read-only commit is a
            # no-op for InMemoryPebStore.
            with store.transaction():
                states = store.list_states()
                latest_decision = store.latest_decision()
        except Exception as exc:
            return ApiResult(
                503,
                {"status": "DOWN", "database": "unreachable", "schema": "peb", "error": str(exc)},
            )
        system_hash = hash_service.compute_system_hash(states, latest_decision).value
        document_hashes = {state.key: state.checksum for state in sorted(states, key=lambda s: s.key or "")}
        last_decision_hash = ""
        if latest_decision is not None:
            last_decision_hash = latest_decision.after_hash or latest_decision.before_hash or ""
        thought_context_hash = PebStateHash.compute(f"{system_hash}:{last_decision_hash}").value
        return ApiResult(
            200,
            {
                "peb_state_hash": system_hash,
                "document_hashes": document_hashes,
                "last_decision_hash": last_decision_hash,
                "thought_context_hash": thought_context_hash,
                "cognitive_mode": "operational",
            },
        )

    @classmethod
    def _missing_fields(cls, payload: Mapping[str, Any]) -> list[str]:
        missing: list[str] = []
        aliases = {
            "idempotencyKey": ("idempotencyKey", "idempotency_key"),
            "entityId": ("entityId", "entity_id"),
            "toolName": ("toolName", "tool_name"),
            "input": ("input",),
        }
        for canonical, names in aliases.items():
            value = next((payload[name] for name in names if name in payload), None)
            if canonical == "input":
                if value is None:
                    missing.append(canonical)
            elif not isinstance(value, str) or not value.strip():
                missing.append(canonical)
        return missing


def create_app(store: PebStore | None = None, controller: AdmissionController | None = None):
    """Create the optional FastAPI app without making FastAPI a domain dependency."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("create_app requires fastapi") from exc

    if controller is None:
        from .adapters import ResolutionExecutionClaimAdapter
        from .engine import PebGovernanceEngine
        from .store import store_from_environment
        store = store or store_from_environment()
        # Ruling 6677c394 R2: wire the resolution admission adapter so the
        # producer path (git-claim admission) is reachable over HTTP at all.
        # Previously the engine was built without it, so any transaction
        # carrying an execution_claim envelope failed closed with
        # RESOLUTION_ADMISSION_UNAVAILABLE — the write side of the witnessed-
        # runs correlation was structurally unreachable.
        controller = AdmissionController(
            PebGovernanceEngine(
                store,
                resolution_claim_adapter=ResolutionExecutionClaimAdapter(
                    dsn=getattr(store, "dsn", None)
                ),
            )
        )

    app = FastAPI(title="Nexus PEB Kernel", version="0.1.0")

    @app.post("/api/v1/peb/transaction")
    def submit_transaction(payload: dict[str, Any] | None = None):
        result = controller.submit_transaction(payload)
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.get("/actuator/health")
    def health():
        assert store is not None or controller is not None
        active_store = store or controller.governance_engine.store
        try:
            return active_store.health()
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={"status": "DOWN", "database": "unreachable", "schema": "peb", "error": str(exc)},
            )

    @app.get("/api/v1/peb/state/hash")
    def state_hash():
        result = controller.state_hash()
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.post("/api/v1/peb/capabilities")
    def register_capability(payload: dict[str, Any] | None = None):
        result = controller.register_capability(payload)
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.get("/api/v1/peb/capabilities")
    def list_capabilities():
        result = controller.list_capabilities()
        return JSONResponse(status_code=result.status_code, content=result.body)

    return app
