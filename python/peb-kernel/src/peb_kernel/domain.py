"""Pure PEB domain types.

The module intentionally has no framework or database imports. Persistence and
HTTP concerns are kept in separate modules so the governance rules can be
exercised deterministically in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ExecutionClaimAdmission:
    """Result of asking resolution whether a verified execution claim/evidence
    pair is eligible for PEB admission.

    This is an eligibility assessment, not a PEB settlement. PEB still owns
    the transaction admission result and its durable governance record.
    """

    is_admitted: bool
    reason: str | None = None
    resolution_receipt_id: UUID | None = None

    @property
    def admitted(self) -> bool:
        return self.is_admitted

    @classmethod
    def admitted_claim(cls, reason: str, receipt_id: UUID | None = None) -> "ExecutionClaimAdmission":
        return cls(True, reason, receipt_id)

    @classmethod
    def rejected(cls, reason: str) -> "ExecutionClaimAdmission":
        return cls(False, reason, None)


class AdmissionPath(str, Enum):
    VALIDATE = "VALIDATE"
    MUTATE = "MUTATE"
    REPORT_VIOLATION = "REPORT_VIOLATION"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_tool_name(cls, tool_name: str | None) -> "AdmissionPath":
        if tool_name is None:
            return cls.UNKNOWN
        if tool_name in {
            "peb_validate_transition",
            "peb_check_invariants",
            "peb_validate_transform",
        }:
            return cls.VALIDATE
        if tool_name in {
            "peb_record_decision",
            "peb_append_trace_segment",
            "peb_request_clarification",
            "peb_extension_proposal",
        }:
            return cls.MUTATE
        # Git-verifier execution-claim admission (ruling 6677c394 R2): the
        # producer path that correlates a verified execution attempt with a
        # PEB transaction. The engine routes any transaction carrying an
        # execution_claim envelope through admit_verified_execution_claim;
        # this mapping makes the dedicated producer toolName a known path so
        # the validator accepts it and admission defaults to ALLOWED (the
        # resolution-side gate still decides admitted/rejected independently).
        if tool_name == "peb_admit_git_execution_claim":
            return cls.MUTATE
        if tool_name == "peb_report_violation":
            return cls.REPORT_VIOLATION
        return cls.UNKNOWN

    def default_admission_result(self) -> "AdmissionResult":
        if self in (self.VALIDATE, self.MUTATE):
            return AdmissionResult.ALLOWED
        if self is self.REPORT_VIOLATION:
            return AdmissionResult.REJECTED
        return AdmissionResult.ROUTED


class AdmissionResult(str, Enum):
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"
    ROUTED = "ROUTED"


class DecisionStatus(str, Enum):
    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"

    @classmethod
    def _missing_(cls, value: object) -> "DecisionStatus | None":
        if isinstance(value, str):
            normalized = value.strip().upper()
            try:
                return cls(normalized)
            except ValueError:
                pass
        return None


class EntropyClass(str, Enum):
    COLLAPSER = "COLLAPSER"
    SHAPER = "SHAPER"
    NEUTRAL = "NEUTRAL"


class ViolationSeverity(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"

    @classmethod
    def from_mcp_value(cls, raw: str | None) -> "ViolationSeverity":
        if raw is None or not raw.strip():
            raise MalformedAdmissionRequest("severity is required and cannot be blank")
        try:
            return cls(raw.strip().upper())
        except ValueError as exc:
            raise MalformedAdmissionRequest(
                f"severity is not a known ViolationSeverity: {raw}"
            ) from exc


class ViolationType(str, Enum):
    AUTHORITY_LEAKAGE = "AUTHORITY_LEAKAGE"
    STATE_DEPENDENCY = "STATE_DEPENDENCY"
    SEMANTIC_NORMALIZATION = "SEMANTIC_NORMALIZATION"
    RCL = "RCL"
    TRANSFORM_INVALID = "TRANSFORM_INVALID"

    @classmethod
    def from_mcp_value(cls, raw: str | None) -> "ViolationType":
        if raw is None or not raw.strip():
            raise MalformedAdmissionRequest("violation_type is required and cannot be blank")
        normalized = raw.strip().upper()
        if normalized.endswith("_VIOLATION"):
            normalized = normalized.removesuffix("_VIOLATION")
        try:
            return cls(normalized)
        except ValueError as exc:
            raise MalformedAdmissionRequest(
                f"violation_type is not a known ViolationType: {raw}"
            ) from exc


class ViolationResolution(str, Enum):
    REJECTED = "REJECTED"
    ROUTED = "ROUTED"
    CLARIFIED = "CLARIFIED"


class MalformedAdmissionRequest(ValueError):
    """A domain-shape error that maps to HTTP 422, not a generic server error."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    """Return stable JSON suitable for content-addressable hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _uuid(value: UUID | str | None) -> UUID | None:
    return None if value is None else (value if isinstance(value, UUID) else UUID(str(value)))


@dataclass(frozen=True)
class CapabilityToken:
    value: str

    def __post_init__(self) -> None:
        if not self.value.startswith("cap:"):
            raise ValueError("Token must start with 'cap:'")

    @property
    def action(self) -> str:
        parts = self.value.split(":")
        return parts[1] if len(parts) > 1 else ""


@dataclass(frozen=True)
class PebStateHash:
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", self.value):
            raise ValueError(f"Hash must be 64-char lowercase hex string: {self.value}")

    @classmethod
    def compute(cls, content: str) -> "PebStateHash":
        return cls(hashlib.sha256(content.encode("utf-8")).hexdigest())

    @classmethod
    def compute_json(cls, content: Any) -> "PebStateHash":
        return cls.compute(canonical_json(content))

    def prefixed(self) -> str:
        return f"sha256:{self.value}"


@dataclass
class PebTransaction:
    idempotency_key: str
    entity_id: str
    tool_name: str
    input: Any
    id: UUID | None = None
    admission_result: AdmissionResult | None = None
    output: Any | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    state_delta: Any | None = None
    created_at: datetime | None = None
    committed_at: datetime | None = None
    kernel_event_id: UUID | None = None
    kernel_event_type: str | None = None
    # W1.12: governance envelope reference fields (W1.05 contract)
    envelope_id: UUID | None = None
    evaluation_fingerprint: str | None = None
    contract_digest: str | None = None

    def on_create(self) -> None:
        if self.id is None:
            self.id = uuid4()
        if self.created_at is None:
            self.created_at = utc_now()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PebTransaction":
        def get(name: str, alternate: str | None = None) -> Any:
            if name in payload:
                return payload[name]
            return payload.get(alternate) if alternate else None

        return cls(
            idempotency_key=get("idempotencyKey", "idempotency_key"),
            entity_id=get("entityId", "entity_id"),
            tool_name=get("toolName", "tool_name"),
            input=payload.get("input"),
            id=_uuid(payload.get("id")),
            envelope_id=_uuid(payload.get("envelope_id")),
            evaluation_fingerprint=payload.get("evaluation_fingerprint"),
            contract_digest=payload.get("contract_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id) if self.id else None,
            "envelope_id": str(self.envelope_id) if self.envelope_id else None,
            "evaluation_fingerprint": self.evaluation_fingerprint,
            "contract_digest": self.contract_digest,
            "idempotencyKey": self.idempotency_key,
            "entityId": self.entity_id,
            "toolName": self.tool_name,
            "input": self.input,
            "admissionResult": self.admission_result.value if self.admission_result else None,
            "output": self.output,
            "beforeHash": self.before_hash,
            "afterHash": self.after_hash,
            "stateDelta": self.state_delta,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "committedAt": self.committed_at.isoformat() if self.committed_at else None,
            "kernelEventId": str(self.kernel_event_id) if self.kernel_event_id else None,
            "kernelEventType": self.kernel_event_type,
        }


@dataclass
class PebState:
    key: str
    content: Any
    checksum: str
    id: UUID | None = None
    metadata: Any | None = None
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PebDecision:
    title: str
    author_id: str
    transaction_id: UUID
    id: UUID | None = None
    adr_number: str | None = None
    status: DecisionStatus | None = DecisionStatus.DRAFT
    summary: Any | None = None
    affected_keys: list[str] = field(default_factory=list)
    entropy_class: EntropyClass | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    parent_decision_id: UUID | None = None
    rollback_of: UUID | None = None
    created_at: datetime | None = None


@dataclass
class PebTrace:
    transaction_id: UUID
    work_request_id: str
    stage: str
    confidence: float
    id: UUID | None = None
    parent_trace_id: UUID | None = None
    inputs: Any | None = None
    causal_entries: Any | None = None
    rejected_alternatives: Any | None = None
    status: str = "observational"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.status != "observational":
            raise ValueError("trace status must be 'observational'")


@dataclass
class PebViolation:
    violation_type: ViolationType
    severity: ViolationSeverity
    id: UUID | None = None
    transaction_id: UUID | None = None
    entity_id: str | None = None
    capability_attempted: str | None = None
    context: Any | None = None
    resolution: ViolationResolution | None = None
    created_at: datetime | None = None


@dataclass
class PebCapability:
    entity_id: str
    capability: str
    id: UUID | None = None
    granted_by: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    active: bool = True


@dataclass(frozen=True)
class AdmissionResponse:
    """Legacy admission response.

    Deprecated (W1.12): replaced by `PebAdmissionResult` which carries the
    governance envelope identity + evaluation fingerprint. Retained for
    backward compatibility until all callers migrate.
    """

    message: str
    admitted: bool

    @classmethod
    def accepted(cls, message: str) -> "AdmissionResponse":
        return cls(message, True)

    @classmethod
    def denied(cls, message: str) -> "AdmissionResponse":
        return cls(message, False)

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "admitted": self.admitted}


@dataclass(frozen=True)
class PebAdmissionResult:
    """Envelope-aware admission result (W1.12).

    Carries the PEB transaction identity alongside the governance envelope
    identity + evaluation fingerprint, aligning with the W1.05 contract.
    The envelope fields are identity references — authority remains with PEB
    (W1.03).
    """

    transaction_id: UUID
    admission_result: AdmissionResult
    message: str
    admitted: bool
    envelope_id: UUID | None = None
    evaluation_fingerprint: str | None = None

    @classmethod
    def from_transaction(
        cls,
        transaction: "PebTransaction",
        message: str,
        admitted: bool,
    ) -> "PebAdmissionResult":
        """Build a result from a processed PebTransaction, carrying envelope refs.

        ``admitted`` is the endpoint-level outcome (did the PEB accept and
        persist this transaction?), NOT the governance result. A REPORT_VIOLATION
        transaction has ``admission_result=REJECTED`` but ``admitted=True``
        because the violation was successfully recorded.
        """
        return cls(
            transaction_id=transaction.id,  # type: ignore[arg-type]
            admission_result=transaction.admission_result or AdmissionResult.ROUTED,
            message=message,
            admitted=admitted,
            envelope_id=transaction.envelope_id,
            evaluation_fingerprint=transaction.evaluation_fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": str(self.transaction_id),
            "envelope_id": str(self.envelope_id) if self.envelope_id else None,
            "evaluation_fingerprint": self.evaluation_fingerprint,
            "admission_result": self.admission_result.value,
            "message": self.message,
            "admitted": self.admitted,
        }
