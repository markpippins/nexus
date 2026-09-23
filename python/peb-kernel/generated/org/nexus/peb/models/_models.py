# coding=utf-8
# pylint: disable=useless-super-delegation

from typing import Any, Mapping, Optional, TYPE_CHECKING, Union, overload

from .._utils.model_base import Model as _Model, rest_field

if TYPE_CHECKING:
    from .. import models as _models


class PebAdmissionResult(_Model):
    """Envelope-aware admission result (W1.12).

    Carries the envelope identity + evaluation fingerprint alongside the
    PEB admission result, aligning with the W1.05 governance envelope
    contract. The ``envelope_id`` and ``evaluation_fingerprint`` reference the
    governance envelope that was evaluated — they are identity references,
    not authority (authority remains with PEB per W1.03).

    :ivar transaction_id: PEB transaction UUID (same as PebTransaction.id). Required.
    :vartype transaction_id: str
    :ivar envelope_id: Governance envelope identity (W1.05 contract reference).
    :vartype envelope_id: str
    :ivar evaluation_fingerprint: Evaluation fingerprint from the governance envelope
     (sha256:<hex>).
    :vartype evaluation_fingerprint: str
    :ivar admission_result: PEB admission result — ALLOWED / REJECTED / ROUTED. Required. Known
     values are: "ALLOWED", "REJECTED", and "ROUTED".
    :vartype admission_result: str or ~org.nexus.peb.models.AdmissionResult
    :ivar message: Human-readable outcome message. Required.
    :vartype message: str
    :ivar admitted: Whether the request was admitted (true) or denied (false). Required.
    :vartype admitted: bool
    """

    transaction_id: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """PEB transaction UUID (same as PebTransaction.id). Required."""
    envelope_id: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Governance envelope identity (W1.05 contract reference)."""
    evaluation_fingerprint: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Evaluation fingerprint from the governance envelope (sha256:<hex>)."""
    admission_result: Union[str, "_models.AdmissionResult"] = rest_field(
        visibility=["read", "create", "update", "delete", "query"]
    )
    """PEB admission result — ALLOWED / REJECTED / ROUTED. Required. Known values are: \"ALLOWED\",
     \"REJECTED\", and \"ROUTED\"."""
    message: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Human-readable outcome message. Required."""
    admitted: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Whether the request was admitted (true) or denied (false). Required."""

    @overload
    def __init__(
        self,
        *,
        transaction_id: str,
        admission_result: Union[str, "_models.AdmissionResult"],
        message: str,
        admitted: bool,
        envelope_id: Optional[str] = None,
        evaluation_fingerprint: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PebHealthResponse(_Model):
    """Actuator-compatible health response for the PEB database boundary.

    :ivar status: Health status, normally UP or DOWN. Required.
    :vartype status: str
    :ivar database: Database connectivity summary.
    :vartype database: str
    :ivar schema: PEB schema name.
    :vartype schema: str
    :ivar catalog: Database catalog when available.
    :vartype catalog: str
    :ivar error: Diagnostic detail when the database is unavailable.
    :vartype error: str
    """

    status: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Health status, normally UP or DOWN. Required."""
    database: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Database connectivity summary."""
    schema: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """PEB schema name."""
    catalog: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Database catalog when available."""
    error: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Diagnostic detail when the database is unavailable."""

    @overload
    def __init__(
        self,
        *,
        status: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        catalog: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PebTransactionRequest(_Model):
    """Request payload for the PEB MCP facade endpoint.

    W1.12: optionally carries governance envelope identity + evaluation
    fingerprint, referencing the W1.05 governance envelope that authorized
    this transaction. When present, PEB records the envelope reference on
    the persisted PebTransaction. When absent, the transaction proceeds
    without envelope linkage (backward-compatible).

    :ivar id: Optional caller-supplied UUID; the Python domain assigns one when omitted.
    :vartype id: str
    :ivar envelope_id: Governance envelope identity (W1.05 contract reference).
    :vartype envelope_id: str
    :ivar evaluation_fingerprint: Evaluation fingerprint from the governance envelope
     (sha256:<hex>).
    :vartype evaluation_fingerprint: str
    :ivar idempotency_key: Caller-provided idempotency key for safe retry. Required.
    :vartype idempotency_key: str
    :ivar entity_id: Entity identifier initiating this request. Required.
    :vartype entity_id: str
    :ivar tool_name: MCP facade tool name to dispatch. Required.
    :vartype tool_name: str
    :ivar input: Arbitrary JSON payload for the tool. Required.
    :vartype input: any
    """

    id: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Optional caller-supplied UUID; the Python domain assigns one when omitted."""
    envelope_id: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Governance envelope identity (W1.05 contract reference)."""
    evaluation_fingerprint: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Evaluation fingerprint from the governance envelope (sha256:<hex>)."""
    idempotency_key: str = rest_field(name="idempotencyKey", visibility=["read", "create", "update", "delete", "query"])
    """Caller-provided idempotency key for safe retry. Required."""
    entity_id: str = rest_field(name="entityId", visibility=["read", "create", "update", "delete", "query"])
    """Entity identifier initiating this request. Required."""
    tool_name: str = rest_field(name="toolName", visibility=["read", "create", "update", "delete", "query"])
    """MCP facade tool name to dispatch. Required."""
    input: Any = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Arbitrary JSON payload for the tool. Required."""

    @overload
    def __init__(
        self,
        *,
        idempotency_key: str,
        entity_id: str,
        tool_name: str,
        input: Any,
        id: Optional[str] = None,  # pylint: disable=redefined-builtin
        envelope_id: Optional[str] = None,
        evaluation_fingerprint: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
