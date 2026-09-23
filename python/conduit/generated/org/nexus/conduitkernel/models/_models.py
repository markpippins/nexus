# pylint: disable=too-many-lines
# coding=utf-8
# pylint: disable=useless-super-delegation

from typing import Any, Literal, Mapping, Optional, TYPE_CHECKING, overload

from .._utils.model_base import Model as _Model, rest_field

if TYPE_CHECKING:
    from .. import models as _models


class BreakerFailureRecoveryConfig(_Model):
    """BreakerFailureRecoveryConfig.

    :ivar max_retries_per_model:
    :vartype max_retries_per_model: int
    :ivar retry_delay_seconds:
    :vartype retry_delay_seconds: int
    :ivar max_fallbacks:
    :vartype max_fallbacks: int
    :ivar push_back_to_pending:
    :vartype push_back_to_pending: bool
    :ivar circuit_breaker_retry_after:
    :vartype circuit_breaker_retry_after: int
    """

    max_retries_per_model: Optional[int] = rest_field(
        name="maxRetriesPerModel", visibility=["read", "create", "update", "delete", "query"]
    )
    retry_delay_seconds: Optional[int] = rest_field(
        name="retryDelaySeconds", visibility=["read", "create", "update", "delete", "query"]
    )
    max_fallbacks: Optional[int] = rest_field(
        name="maxFallbacks", visibility=["read", "create", "update", "delete", "query"]
    )
    push_back_to_pending: Optional[bool] = rest_field(
        name="pushBackToPending", visibility=["read", "create", "update", "delete", "query"]
    )
    circuit_breaker_retry_after: Optional[int] = rest_field(
        name="circuitBreakerRetryAfter", visibility=["read", "create", "update", "delete", "query"]
    )

    @overload
    def __init__(
        self,
        *,
        max_retries_per_model: Optional[int] = None,
        retry_delay_seconds: Optional[int] = None,
        max_fallbacks: Optional[int] = None,
        push_back_to_pending: Optional[bool] = None,
        circuit_breaker_retry_after: Optional[int] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class BreakerStateResponse(_Model):
    """BreakerStateResponse.

    :ivar state: Required.
    :vartype state: str
    :ivar failures:
    :vartype failures: int
    :ivar open:
    :vartype open: bool
    :ivar paused:
    :vartype paused: bool
    :ivar retry_after:
    :vartype retry_after: int
    :ivar source:
    :vartype source: str
    :ivar detail:
    :vartype detail: str
    :ivar error:
    :vartype error: str
    :ivar max_retries_per_model:
    :vartype max_retries_per_model: int
    :ivar retry_delay_seconds:
    :vartype retry_delay_seconds: int
    :ivar max_fallbacks:
    :vartype max_fallbacks: int
    :ivar push_back_to_pending:
    :vartype push_back_to_pending: bool
    :ivar circuit_breaker_retry_after:
    :vartype circuit_breaker_retry_after: int
    """

    state: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    failures: Optional[int] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    open: Optional[bool] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    paused: Optional[bool] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    retry_after: Optional[int] = rest_field(
        name="retryAfter", visibility=["read", "create", "update", "delete", "query"]
    )
    source: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    detail: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    error: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    max_retries_per_model: Optional[int] = rest_field(
        name="maxRetriesPerModel", visibility=["read", "create", "update", "delete", "query"]
    )
    retry_delay_seconds: Optional[int] = rest_field(
        name="retryDelaySeconds", visibility=["read", "create", "update", "delete", "query"]
    )
    max_fallbacks: Optional[int] = rest_field(
        name="maxFallbacks", visibility=["read", "create", "update", "delete", "query"]
    )
    push_back_to_pending: Optional[bool] = rest_field(
        name="pushBackToPending", visibility=["read", "create", "update", "delete", "query"]
    )
    circuit_breaker_retry_after: Optional[int] = rest_field(
        name="circuitBreakerRetryAfter", visibility=["read", "create", "update", "delete", "query"]
    )

    @overload
    def __init__(
        self,
        *,
        state: str,
        failures: Optional[int] = None,
        open: Optional[bool] = None,
        paused: Optional[bool] = None,
        retry_after: Optional[int] = None,
        source: Optional[str] = None,
        detail: Optional[str] = None,
        error: Optional[str] = None,
        max_retries_per_model: Optional[int] = None,
        retry_delay_seconds: Optional[int] = None,
        max_fallbacks: Optional[int] = None,
        push_back_to_pending: Optional[bool] = None,
        circuit_breaker_retry_after: Optional[int] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class BreakerTripRequest(_Model):
    """BreakerTripRequest.

    :ivar error: Required.
    :vartype error: str
    :ivar detail:
    :vartype detail: str
    :ivar source:
    :vartype source: str
    :ivar retry_after:
    :vartype retry_after: int
    """

    error: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    detail: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    source: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    retry_after: Optional[int] = rest_field(
        name="retryAfter", visibility=["read", "create", "update", "delete", "query"]
    )

    @overload
    def __init__(
        self,
        *,
        error: str,
        detail: Optional[str] = None,
        source: Optional[str] = None,
        retry_after: Optional[int] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class CompareResponse(_Model):
    """CompareResponse.

    :ivar differences: Required.
    :vartype differences: list[dict[str, any]]
    :ivar equal: Required.
    :vartype equal: bool
    """

    differences: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    equal: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        differences: list[dict[str, Any]],
        equal: bool,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ConsistencyCheckResponse(_Model):
    """ConsistencyCheckResponse.

    :ivar consistent: Required.
    :vartype consistent: bool
    :ivar issues: Required.
    :vartype issues: list[str]
    """

    consistent: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    issues: list[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        consistent: bool,
        issues: list[str],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class DeleteReceiptsResponse(_Model):
    """Delete receipts response.

    :ivar deleted: Required.
    :vartype deleted: int
    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar types: Required.
    :vartype types: list[str]
    """

    deleted: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    types: list[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        deleted: int,
        plan_id: str,
        types: list[str],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class DeltaApplyRequest(_Model):
    """DeltaApplyRequest."""


class DeltaResponse(_Model):
    """DeltaResponse.

    :ivar applied: Required.
    :vartype applied: bool
    :ivar delta:
    :vartype delta: dict[str, any]
    """

    applied: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    delta: Optional[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        applied: bool,
        delta: Optional[dict[str, Any]] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class GraphResponse(_Model):
    """State graph with pagination.

    :ivar nodes: Required.
    :vartype nodes: list[dict[str, any]]
    :ivar edges: Required.
    :vartype edges: list[dict[str, any]]
    :ivar total_edges: Required.
    :vartype total_edges: int
    :ivar cursor: Required.
    :vartype cursor: str
    :ivar limit: Required.
    :vartype limit: int
    """

    nodes: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    edges: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    total_edges: int = rest_field(name="totalEdges", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    cursor: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    limit: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        total_edges: int,
        cursor: str,
        limit: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class IdentityListResponse(_Model):
    """IdentityListResponse.

    :ivar identities: Required.
    :vartype identities: list[~org.nexus.conduitkernel.models.IdentityResponse]
    """

    identities: list["_models.IdentityResponse"] = rest_field(
        visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""

    @overload
    def __init__(
        self,
        *,
        identities: list["_models.IdentityResponse"],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class IdentityResolutionResponse(_Model):
    """Identity resolution response.

    :ivar id: Required.
    :vartype id: str
    :ivar aliases: Required.
    :vartype aliases: list[str]
    :ivar label:
    :vartype label: str
    :ivar edges_outgoing: Required.
    :vartype edges_outgoing: list[dict[str, any]]
    :ivar edges_incoming: Required.
    :vartype edges_incoming: list[dict[str, any]]
    """

    id: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    aliases: list[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    label: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    edges_outgoing: list[dict[str, Any]] = rest_field(
        name="edgesOutgoing", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""
    edges_incoming: list[dict[str, Any]] = rest_field(
        name="edgesIncoming", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""

    @overload
    def __init__(
        self,
        *,
        id: str,  # pylint: disable=redefined-builtin
        aliases: list[str],
        edges_outgoing: list[dict[str, Any]],
        edges_incoming: list[dict[str, Any]],
        label: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class IdentityResponse(_Model):
    """A work-request identity record.

    :ivar identity_id: Required.
    :vartype identity_id: str
    :ivar label: Human-facing identity label (name/email/role).
    :vartype label: str
    :ivar attributes: Opaque identity attributes.
    :vartype attributes: dict[str, any]
    :ivar aliases: Aliases for this identity.
    :vartype aliases: list[str]
    :ivar edges_outgoing: Outgoing graph edges.
    :vartype edges_outgoing: list[dict[str, any]]
    :ivar edges_incoming: Incoming graph edges.
    :vartype edges_incoming: list[dict[str, any]]
    """

    identity_id: str = rest_field(name="identityId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    label: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Human-facing identity label (name/email/role)."""
    attributes: Optional[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Opaque identity attributes."""
    aliases: Optional[list[str]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Aliases for this identity."""
    edges_outgoing: Optional[list[dict[str, Any]]] = rest_field(
        visibility=["read", "create", "update", "delete", "query"]
    )
    """Outgoing graph edges."""
    edges_incoming: Optional[list[dict[str, Any]]] = rest_field(
        visibility=["read", "create", "update", "delete", "query"]
    )
    """Incoming graph edges."""

    @overload
    def __init__(
        self,
        *,
        identity_id: str,
        label: Optional[str] = None,
        attributes: Optional[dict[str, Any]] = None,
        aliases: Optional[list[str]] = None,
        edges_outgoing: Optional[list[dict[str, Any]]] = None,
        edges_incoming: Optional[list[dict[str, Any]]] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class IdentityUpdateResponse(_Model):
    """IdentityUpdateResponse.

    :ivar identity_id: Required.
    :vartype identity_id: str
    :ivar updated: Required.
    :vartype updated: bool
    """

    identity_id: str = rest_field(name="identityId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    updated: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        identity_id: str,
        updated: bool,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class LatestReceiptTypeResponse(_Model):
    """Latest receipt type for a plan.

    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar latest_type:
    :vartype latest_type: str
    """

    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    latest_type: Optional[str] = rest_field(
        name="latestType", visibility=["read", "create", "update", "delete", "query"]
    )

    @overload
    def __init__(
        self,
        *,
        plan_id: str,
        latest_type: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class LineageResponse(_Model):
    """Lineage events.

    :ivar events: Required.
    :vartype events: list[dict[str, any]]
    :ivar count: Required.
    :vartype count: int
    """

    events: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    count: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        events: list[dict[str, Any]],
        count: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class LivenessResponse(_Model):
    """Liveness probe response.

    :ivar status: Required. Default value is "alive".
    :vartype status: str
    """

    status: Literal["alive"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required. Default value is \"alive\"."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.status: Literal["alive"] = "alive"


class MetricsResponse(_Model):
    """MetricsResponse.

    :ivar metrics: Required.
    :vartype metrics: str
    """

    metrics: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        metrics: str,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PlanDetailResponse(_Model):
    """PlanDetailResponse.

    :ivar plan_num: Required.
    :vartype plan_num: str
    :ivar identity_id: Required.
    :vartype identity_id: str
    :ivar aliases: Required.
    :vartype aliases: list[str]
    :ivar label:
    :vartype label: str
    :ivar receipt_count: Required.
    :vartype receipt_count: int
    :ivar current_wrp_state: Required.
    :vartype current_wrp_state: str
    :ivar valid_transitions: Required.
    :vartype valid_transitions: list[str]
    :ivar receipts: Required.
    :vartype receipts: list[~org.nexus.conduitkernel.models.PlanReceiptItem]
    :ivar edges_outgoing: Required.
    :vartype edges_outgoing: list[dict[str, any]]
    :ivar edges_incoming: Required.
    :vartype edges_incoming: list[dict[str, any]]
    """

    plan_num: str = rest_field(name="planNum", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    identity_id: str = rest_field(name="identityId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    aliases: list[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    label: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    receipt_count: int = rest_field(name="receiptCount", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    current_wrp_state: str = rest_field(
        name="currentWrpState", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""
    valid_transitions: list[str] = rest_field(
        name="validTransitions", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""
    receipts: list["_models.PlanReceiptItem"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    edges_outgoing: list[dict[str, Any]] = rest_field(
        name="edgesOutgoing", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""
    edges_incoming: list[dict[str, Any]] = rest_field(
        name="edgesIncoming", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""

    @overload
    def __init__(
        self,
        *,
        plan_num: str,
        identity_id: str,
        aliases: list[str],
        receipt_count: int,
        current_wrp_state: str,
        valid_transitions: list[str],
        receipts: list["_models.PlanReceiptItem"],
        edges_outgoing: list[dict[str, Any]],
        edges_incoming: list[dict[str, Any]],
        label: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PlanRawReceiptsResponse(_Model):
    """Raw receipt rows for a plan.

    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar count: Required.
    :vartype count: int
    :ivar receipts: Required.
    :vartype receipts: list[dict[str, any]]
    """

    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    count: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    receipts: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        plan_id: str,
        count: int,
        receipts: list[dict[str, Any]],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PlanReceiptItem(_Model):
    """Plan detail with WRP state machine position.

    :ivar id:
    :vartype id: str
    :ivar type:
    :vartype type: str
    :ivar agent_role:
    :vartype agent_role: str
    :ivar created_at:
    :vartype created_at: str
    :ivar summary:
    :vartype summary: str
    :ivar ticket_id:
    :vartype ticket_id: str
    """

    id: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    type: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    agent_role: Optional[str] = rest_field(name="agentRole", visibility=["read", "create", "update", "delete", "query"])
    created_at: Optional[str] = rest_field(name="createdAt", visibility=["read", "create", "update", "delete", "query"])
    summary: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    ticket_id: Optional[str] = rest_field(name="ticketId", visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        id: Optional[str] = None,  # pylint: disable=redefined-builtin
        type: Optional[str] = None,
        agent_role: Optional[str] = None,
        created_at: Optional[str] = None,
        summary: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PlanReceiptsResponse(_Model):
    """Formatted receipts list for a plan.

    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar count: Required.
    :vartype count: int
    :ivar receipts: Required.
    :vartype receipts: list[~org.nexus.conduitkernel.models.ReceiptResponse]
    """

    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    count: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    receipts: list["_models.ReceiptResponse"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        plan_id: str,
        count: int,
        receipts: list["_models.ReceiptResponse"],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReadinessResponse(_Model):
    """Readiness probe response.

    :ivar status: Required. Is either a Literal["ready"] type or a Literal["unready"] type.
    :vartype status: str or str
    :ivar kernel_version:
    :vartype kernel_version: int
    """

    status: Literal["ready", "unready"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required. Is either a Literal[\"ready\"] type or a Literal[\"unready\"] type."""
    kernel_version: Optional[int] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        status: Literal["ready", "unready"],
        kernel_version: Optional[int] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReceiptByIdResponse(_Model):
    """Single receipt by ID.

    :ivar id: Required.
    :vartype id: str
    :ivar receipt: Required.
    :vartype receipt: dict[str, any]
    """

    id: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    receipt: dict[str, Any] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        id: str,  # pylint: disable=redefined-builtin
        receipt: dict[str, Any],
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReceiptInsertRequest(_Model):
    """Receipt insert request (C1 single persistence path).

    :ivar id: Required.
    :vartype id: str
    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar type: Required.
    :vartype type: str
    :ivar agent_role: Required.
    :vartype agent_role: str
    :ivar session_id:
    :vartype session_id: str
    :ivar ticket_id:
    :vartype ticket_id: str
    :ivar artifact_path:
    :vartype artifact_path: str
    :ivar summary:
    :vartype summary: str
    :ivar metadata_json:
    :vartype metadata_json: str
    :ivar tokens_used:
    :vartype tokens_used: int
    :ivar created_at: Required.
    :vartype created_at: str
    :ivar producer_id: C1 gate 1 (Lilac): declaring-producer identity for TS front-door channel.
    :vartype producer_id: str
    :ivar source_channel:
    :vartype source_channel: str
    :ivar correlation_id:
    :vartype correlation_id: str
    """

    id: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    type: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    agent_role: str = rest_field(name="agentRole", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    session_id: Optional[str] = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    ticket_id: Optional[str] = rest_field(name="ticketId", visibility=["read", "create", "update", "delete", "query"])
    artifact_path: Optional[str] = rest_field(
        name="artifactPath", visibility=["read", "create", "update", "delete", "query"]
    )
    summary: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    metadata_json: Optional[str] = rest_field(
        name="metadataJson", visibility=["read", "create", "update", "delete", "query"]
    )
    tokens_used: Optional[int] = rest_field(
        name="tokensUsed", visibility=["read", "create", "update", "delete", "query"]
    )
    created_at: str = rest_field(name="createdAt", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    producer_id: Optional[str] = rest_field(
        name="producerId", visibility=["read", "create", "update", "delete", "query"]
    )
    """C1 gate 1 (Lilac): declaring-producer identity for TS front-door channel."""
    source_channel: Optional[str] = rest_field(
        name="sourceChannel", visibility=["read", "create", "update", "delete", "query"]
    )
    correlation_id: Optional[str] = rest_field(
        name="correlationId", visibility=["read", "create", "update", "delete", "query"]
    )

    @overload
    def __init__(
        self,
        *,
        id: str,  # pylint: disable=redefined-builtin
        plan_id: str,
        type: str,
        agent_role: str,
        created_at: str,
        session_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        artifact_path: Optional[str] = None,
        summary: Optional[str] = None,
        metadata_json: Optional[str] = None,
        tokens_used: Optional[int] = None,
        producer_id: Optional[str] = None,
        source_channel: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReceiptInsertResponse(_Model):
    """Receipt insert response.

    :ivar ok: Required.
    :vartype ok: bool
    :ivar id: Required.
    :vartype id: str
    :ivar plan_id: Required.
    :vartype plan_id: str
    """

    ok: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    id: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        ok: bool,
        id: str,  # pylint: disable=redefined-builtin
        plan_id: str,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReceiptResponse(_Model):
    """Receipt record (unified view from nebula.receipts_unified).

    :ivar receipt_id: Required.
    :vartype receipt_id: str
    :ivar plan_id: Required.
    :vartype plan_id: str
    :ivar type:
    :vartype type: str
    :ivar agent_role:
    :vartype agent_role: str
    :ivar session_id:
    :vartype session_id: str
    :ivar artifact_path:
    :vartype artifact_path: str
    :ivar summary:
    :vartype summary: str
    :ivar metadata:
    :vartype metadata: dict[str, any]
    :ivar created_at:
    :vartype created_at: str
    """

    receipt_id: str = rest_field(name="receiptId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    plan_id: str = rest_field(name="planId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    type: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    agent_role: Optional[str] = rest_field(name="agentRole", visibility=["read", "create", "update", "delete", "query"])
    session_id: Optional[str] = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    artifact_path: Optional[str] = rest_field(
        name="artifactPath", visibility=["read", "create", "update", "delete", "query"]
    )
    summary: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    metadata: Optional[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    created_at: Optional[str] = rest_field(name="createdAt", visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        receipt_id: str,
        plan_id: str,
        type: Optional[str] = None,
        agent_role: Optional[str] = None,
        session_id: Optional[str] = None,
        artifact_path: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReceiptsByPlanResponse(_Model):
    """Receipts by plan number.

    :ivar plan_num: Required.
    :vartype plan_num: str
    :ivar receipts: Required.
    :vartype receipts: list[dict[str, any]]
    :ivar count: Required.
    :vartype count: int
    """

    plan_num: str = rest_field(name="planNum", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    receipts: list[dict[str, Any]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    count: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        plan_num: str,
        receipts: list[dict[str, Any]],
        count: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class ReplayResponse(_Model):
    """ReplayResponse.

    :ivar replayed: Required.
    :vartype replayed: int
    :ivar events:
    :vartype events: list[dict[str, any]]
    """

    replayed: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    events: Optional[list[dict[str, Any]]] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        replayed: int,
        events: Optional[list[dict[str, Any]]] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class RootResponse(_Model):
    """Root service info.

    :ivar service: Required. Default value is "WRP Kernel Runtime".
    :vartype service: str
    :ivar version: Required.
    :vartype version: str
    :ivar docs: Required.
    :vartype docs: str
    """

    service: Literal["WRP Kernel Runtime"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required. Default value is \"WRP Kernel Runtime\"."""
    version: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    docs: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        version: str,
        docs: str,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.service: Literal["WRP Kernel Runtime"] = "WRP Kernel Runtime"


class SessionCostUpdateRequest(_Model):
    """SessionCostUpdateRequest.

    :ivar cost_usd: Required.
    :vartype cost_usd: float
    """

    cost_usd: float = rest_field(name="costUsd", visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        cost_usd: float,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionCostUpdateResponse(_Model):
    """SessionCostUpdateResponse.

    :ivar updated: Required.
    :vartype updated: bool
    :ivar session_id: Required.
    :vartype session_id: str
    :ivar cost_usd: Required.
    :vartype cost_usd: float
    """

    updated: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    session_id: str = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    cost_usd: float = rest_field(name="costUsd", visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        updated: bool,
        session_id: str,
        cost_usd: float,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionHeartbeatRequest(_Model):
    """SessionHeartbeatRequest.

    :ivar role:
    :vartype role: str
    :ivar state:
    :vartype state: str
    :ivar detail:
    :vartype detail: str
    :ivar pid:
    :vartype pid: int
    """

    role: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    state: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    detail: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    pid: Optional[int] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        role: Optional[str] = None,
        state: Optional[str] = None,
        detail: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionHeartbeatResponse(_Model):
    """SessionHeartbeatResponse.

    :ivar updated: Required.
    :vartype updated: bool
    :ivar session_id: Required.
    :vartype session_id: str
    :ivar timestamp: Required.
    :vartype timestamp: str
    """

    updated: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    session_id: str = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    timestamp: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        updated: bool,
        session_id: str,
        timestamp: str,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionKillResult(_Model):
    """SessionKillResult.

    :ivar killed: Required.
    :vartype killed: bool
    :ivar session_id: Required.
    :vartype session_id: str
    :ivar pids: Required.
    :vartype pids: list[int]
    :ivar errors:
    :vartype errors: list[str]
    :ivar timestamp: Required.
    :vartype timestamp: str
    """

    killed: bool = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    session_id: str = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    pids: list[int] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    errors: Optional[list[str]] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    timestamp: str = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        killed: bool,
        session_id: str,
        pids: list[int],
        timestamp: str,
        errors: Optional[list[str]] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionListResponse(_Model):
    """SessionListResponse.

    :ivar sessions: Required.
    :vartype sessions: list[~org.nexus.conduitkernel.models.SessionResponse]
    :ivar count: Required.
    :vartype count: int
    """

    sessions: list["_models.SessionResponse"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    count: int = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        sessions: list["_models.SessionResponse"],
        count: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SessionResponse(_Model):
    """SessionResponse.

    :ivar session_id: Required.
    :vartype session_id: str
    :ivar status:
    :vartype status: str
    :ivar running:
    :vartype running: bool
    :ivar cost:
    :vartype cost: float
    :ivar created_at:
    :vartype created_at: str
    :ivar last_activity:
    :vartype last_activity: str
    :ivar last_heartbeat_at:
    :vartype last_heartbeat_at: str
    :ivar pid:
    :vartype pid: int
    :ivar is_running:
    :vartype is_running: bool
    :ivar role:
    :vartype role: str
    :ivar state:
    :vartype state: str
    :ivar detail:
    :vartype detail: str
    """

    session_id: str = rest_field(name="sessionId", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    status: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    running: Optional[bool] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    cost: Optional[float] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    created_at: Optional[str] = rest_field(name="createdAt", visibility=["read", "create", "update", "delete", "query"])
    last_activity: Optional[str] = rest_field(
        name="lastActivity", visibility=["read", "create", "update", "delete", "query"]
    )
    last_heartbeat_at: Optional[str] = rest_field(
        name="lastHeartbeatAt", visibility=["read", "create", "update", "delete", "query"]
    )
    pid: Optional[int] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    is_running: Optional[bool] = rest_field(
        name="isRunning", visibility=["read", "create", "update", "delete", "query"]
    )
    role: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    state: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    detail: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        session_id: str,
        status: Optional[str] = None,
        running: Optional[bool] = None,
        cost: Optional[float] = None,
        created_at: Optional[str] = None,
        last_activity: Optional[str] = None,
        last_heartbeat_at: Optional[str] = None,
        pid: Optional[int] = None,
        is_running: Optional[bool] = None,
        role: Optional[str] = None,
        state: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class StateHealthResponse(_Model):
    """State health check.

    :ivar status: Required. Default value is "ok".
    :vartype status: str
    :ivar kernel_version: Required.
    :vartype kernel_version: int
    """

    status: Literal["ok"] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    """Required. Default value is \"ok\"."""
    kernel_version: int = rest_field(name="kernelVersion", visibility=["read", "create", "update", "delete", "query"])
    """Required."""

    @overload
    def __init__(
        self,
        *,
        kernel_version: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.status: Literal["ok"] = "ok"


class StateSummaryResponse(_Model):
    """StateSummaryResponse.

    :ivar state:
    :vartype state: str
    :ivar revision:
    :vartype revision: str
    """

    state: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])
    revision: Optional[str] = rest_field(visibility=["read", "create", "update", "delete", "query"])

    @overload
    def __init__(
        self,
        *,
        state: Optional[str] = None,
        revision: Optional[str] = None,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SystemInfoResponse(_Model):
    """System info summary.

    :ivar kernel_version: Required.
    :vartype kernel_version: int
    :ivar delta_count: Required.
    :vartype delta_count: int
    :ivar plan_count: Required.
    :vartype plan_count: int
    :ivar receipt_count: Required.
    :vartype receipt_count: int
    :ivar identity_count: Required.
    :vartype identity_count: int
    :ivar graph_edge_count: Required.
    :vartype graph_edge_count: int
    :ivar lineage_event_count: Required.
    :vartype lineage_event_count: int
    """

    kernel_version: int = rest_field(name="kernelVersion", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    delta_count: int = rest_field(name="deltaCount", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    plan_count: int = rest_field(name="planCount", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    receipt_count: int = rest_field(name="receiptCount", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    identity_count: int = rest_field(name="identityCount", visibility=["read", "create", "update", "delete", "query"])
    """Required."""
    graph_edge_count: int = rest_field(
        name="graphEdgeCount", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""
    lineage_event_count: int = rest_field(
        name="lineageEventCount", visibility=["read", "create", "update", "delete", "query"]
    )
    """Required."""

    @overload
    def __init__(
        self,
        *,
        kernel_version: int,
        delta_count: int,
        plan_count: int,
        receipt_count: int,
        identity_count: int,
        graph_edge_count: int,
        lineage_event_count: int,
    ) -> None: ...

    @overload
    def __init__(self, mapping: Mapping[str, Any]) -> None:
        """
        :param mapping: raw JSON to initialize the model.
        :type mapping: Mapping[str, Any]
        """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
