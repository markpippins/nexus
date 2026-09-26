# coding=utf-8

from typing import Any
from typing_extensions import Required, TypedDict


class PebTransactionRequest(TypedDict, total=False):
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
    :ivar idempotencyKey: Caller-provided idempotency key for safe retry. Required.
    :vartype idempotencyKey: str
    :ivar entityId: Entity identifier initiating this request. Required.
    :vartype entityId: str
    :ivar toolName: MCP facade tool name to dispatch. Required.
    :vartype toolName: str
    :ivar input: Arbitrary JSON payload for the tool. Required.
    :vartype input: Any
    """

    id: str
    """Optional caller-supplied UUID; the Python domain assigns one when omitted."""
    envelope_id: str
    """Governance envelope identity (W1.05 contract reference)."""
    evaluation_fingerprint: str
    """Evaluation fingerprint from the governance envelope (sha256:<hex>)."""
    idempotencyKey: Required[str]
    """Caller-provided idempotency key for safe retry. Required."""
    entityId: Required[str]
    """Entity identifier initiating this request. Required."""
    toolName: Required[str]
    """MCP facade tool name to dispatch. Required."""
    input: Required[Any]
    """Arbitrary JSON payload for the tool. Required."""
