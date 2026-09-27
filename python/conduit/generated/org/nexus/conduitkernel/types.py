# coding=utf-8

from typing_extensions import Required, TypedDict


class BreakerFailureRecoveryConfig(TypedDict, total=False):
    """BreakerFailureRecoveryConfig.

    :ivar maxRetriesPerModel:
    :vartype maxRetriesPerModel: int
    :ivar retryDelaySeconds:
    :vartype retryDelaySeconds: int
    :ivar maxFallbacks:
    :vartype maxFallbacks: int
    :ivar pushBackToPending:
    :vartype pushBackToPending: bool
    :ivar circuitBreakerRetryAfter:
    :vartype circuitBreakerRetryAfter: int
    """

    maxRetriesPerModel: int
    retryDelaySeconds: int
    maxFallbacks: int
    pushBackToPending: bool
    circuitBreakerRetryAfter: int


class BreakerTripRequest(TypedDict, total=False):
    """BreakerTripRequest.

    :ivar error: Required.
    :vartype error: str
    :ivar detail:
    :vartype detail: str
    :ivar source:
    :vartype source: str
    :ivar retryAfter:
    :vartype retryAfter: int
    """

    error: Required[str]
    """Required."""
    detail: str
    source: str
    retryAfter: int


class DeltaApplyRequest(TypedDict, total=False):
    """DeltaApplyRequest."""


class ReceiptInsertRequest(TypedDict, total=False):
    """Receipt insert request (C1 single persistence path).

    :ivar id: Required.
    :vartype id: str
    :ivar planId: Required.
    :vartype planId: str
    :ivar type: Required.
    :vartype type: str
    :ivar agentRole: Required.
    :vartype agentRole: str
    :ivar sessionId:
    :vartype sessionId: str
    :ivar ticketId:
    :vartype ticketId: str
    :ivar artifactPath:
    :vartype artifactPath: str
    :ivar summary:
    :vartype summary: str
    :ivar metadataJson:
    :vartype metadataJson: str
    :ivar tokensUsed:
    :vartype tokensUsed: int
    :ivar createdAt: Required.
    :vartype createdAt: str
    :ivar producerId: C1 gate 1 (Lilac): declaring-producer identity for TS front-door channel.
    :vartype producerId: str
    :ivar sourceChannel:
    :vartype sourceChannel: str
    :ivar correlationId:
    :vartype correlationId: str
    """

    id: Required[str]
    """Required."""
    planId: Required[str]
    """Required."""
    type: Required[str]
    """Required."""
    agentRole: Required[str]
    """Required."""
    sessionId: str
    ticketId: str
    artifactPath: str
    summary: str
    metadataJson: str
    tokensUsed: int
    createdAt: Required[str]
    """Required."""
    producerId: str
    """C1 gate 1 (Lilac): declaring-producer identity for TS front-door channel."""
    sourceChannel: str
    correlationId: str


class SessionCostUpdateRequest(TypedDict, total=False):
    """SessionCostUpdateRequest.

    :ivar costUsd: Required.
    :vartype costUsd: float
    """

    costUsd: Required[float]
    """Required."""


class SessionHeartbeatRequest(TypedDict, total=False):
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

    role: str
    state: str
    detail: str
    pid: int
