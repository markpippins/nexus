"""MCP tool surface for lilac-srv.

conduit-mcp is a transport, not an owner: the tools below carry names,
argument contracts and delegation, and nothing else. Each handler calls one
``LilacClient`` method — there is no SQL, no queue, no retry loop that could
create a second write path (AC5).

The adapter is constructed with a client, never with a database handle, so a
tool invocation cannot reach the store by any route the service did not
classify.

STAGED-INERT (AC6): ``tools_for_client(None)`` returns an empty list. When no
Lilac client is configured, conduit-mcp advertises no Lilac tools at all —
rather than advertising tools that would 503 on first call.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

TOOL_PREFIX = "lilac_"

#: tool name → (client method, required arguments, description)
TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "lilac_issue_receipt": {
        "method": "issue_receipt",
        "required": ["producer_id", "kind", "source_system", "source_receipt_id", "payload"],
        "description": "Issue a canonical Resolution receipt (kind-discriminated, idempotent)",
    },
    "lilac_issue_ticket": {
        "method": "issue_ticket",
        "required": ["workflow_ref", "role", "position"],
        "description": "Issue a workflow ticket (idempotent on workflow_ref/role/position/generation)",
    },
    "lilac_record_transition": {
        "method": "record_transition",
        "required": ["ticket_id", "input_receipt_id", "from_status", "to_status"],
        "description": "Record a ticket status transition with the receipt that drove it",
    },
    "lilac_record_fanout": {
        "method": "record_fanout",
        "required": ["input_receipt_id", "kind", "produced"],
        "description": "Record a receipt-to-ticket fan-out on the single ledger",
    },
    "lilac_ticket_timeline": {
        "method": "ticket_timeline",
        "required": ["ticket_id"],
        "description": "Read a ticket and its ordered transitions, each with its receipt",
    },
    "lilac_parity": {
        "method": "parity_diagnostics",
        "required": [],
        "description": "Read recorded parity evidence and provenance counts per surface",
    },
}


class LilacToolError(ValueError):
    """Raised for a malformed tool invocation, before any request is sent."""


def _handler_for(client: Any, name: str, spec: Mapping[str, Any]) -> Callable[..., Any]:
    method = getattr(client, spec["method"])

    def handler(arguments: Mapping[str, Any]) -> Any:
        missing = [key for key in spec["required"] if key not in arguments]
        if missing:
            raise LilacToolError(f"{name} requires {', '.join(missing)}")
        allowed = {k: v for k, v in arguments.items() if k != "provenance"}
        return method(**allowed)

    return handler


def tools_for_client(client: Optional[Any]) -> List[Dict[str, Any]]:
    """Tool definitions for the MCP registry.

    With no client configured the list is EMPTY: staging means the tools do not
    exist, not that they exist and fail.
    """
    if client is None:
        return []
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": {
                "type": "object",
                "required": list(spec["required"]),
                "properties": {key: {"type": "string"} for key in spec["required"]},
            },
        }
        for name, spec in TOOL_SPECS.items()
    ]


def dispatch(client: Any, name: str, arguments: Mapping[str, Any]) -> Any:
    """Invoke one tool by name. Unknown names are refused, never guessed."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise LilacToolError(f"unknown lilac tool: {name}")
    return _handler_for(client, name, spec)(arguments)
