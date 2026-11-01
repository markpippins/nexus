"""Trigger-event normalisation (shared trigger contract, catalogue 332d6831).

A trigger is a successful governed state transition / decision point.
``idempotency_key`` is ``<namespace>:<source_event_id>`` so the same logical
decision point can never create two indistinguishable checkpoints.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "TRIGGER_SCHEMA_VERSION",
    "normalize_trigger_event",
    "parse_legacy_trigger",
    "outbox_row_to_trigger",
]

TRIGGER_SCHEMA_VERSION = 1


def normalize_trigger_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a source event into the shared Keychains trigger contract."""

    normalized = dict(event)
    source_namespace = normalized.get("source_namespace") or normalized.get("source") or "unknown"
    source_event_id = normalized.get("source_event_id") or normalized.get("id") or None
    key = normalized.get("idempotency_key") or f"{source_namespace}:{source_event_id or ''}"
    normalized.update(
        {
            "schema_version": normalized.get("schema_version") or TRIGGER_SCHEMA_VERSION,
            "source_namespace": source_namespace,
            "source_event_id": source_event_id,
            "event_id": normalized.get("event_id") or key,
            "id": source_event_id,
            "idempotency_key": key,
            "outcome": normalized.get("outcome") or "committed",
        }
    )
    return normalized


def parse_legacy_trigger(trigger: str) -> dict[str, Any]:
    """Parse the legacy ``sol-transition:<transitionId>:<entityId>`` string form."""

    prefix = "sol-transition:"
    if trigger.startswith(prefix):
        rest = trigger[len(prefix) :]
        colon = rest.find(":")
        transition_id = rest if colon < 0 else rest[:colon]
        entity_id = None if colon < 0 else rest[colon + 1 :]
        return {
            "kind": "resolution.transition.committed",
            "id": transition_id or None,
            "source_namespace": "sol-api",
            "source_event_id": f"{transition_id or ''}:{entity_id or ''}",
            "source": "sol-api",
            "outcome": "committed",
            "meta": {"entity_id": entity_id or None},
            "idempotency_key": f"sol-api:{transition_id or ''}:{entity_id or ''}",
        }
    return normalize_trigger_event(
        {
            "kind": "unknown",
            "id": None,
            "raw": trigger,
            "outcome": "committed",
            "idempotency_key": f"legacy:{trigger}",
        }
    )


def _passthrough_json(value: Any) -> Any:
    """Mirror the TS ``parseJsonValue`` helper: pg may hand back text or an object."""

    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def outbox_row_to_trigger(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a ``resolution.keychain_event_outbox`` row into a trigger event."""

    source_event_id = str(row["source_event_id"])
    source_namespace = str(row["source_namespace"])
    return normalize_trigger_event(
        {
            "schema_version": row.get("schema_version"),
            "source_namespace": source_namespace,
            "source_event_id": source_event_id,
            "event_id": f"{source_namespace}:{source_event_id}",
            "kind": row["event_kind"],
            "outcome": row.get("outcome"),
            "aggregate_id": row.get("aggregate_id"),
            "actor": row.get("actor"),
            "correlation_id": row.get("correlation_id"),
            "contract_id": row.get("contract_id"),
            "evaluator_id": row.get("evaluator_id"),
            "law_id": row.get("law_id"),
            "effective_at": row.get("effective_at"),
            "recorded_at": row.get("recorded_at"),
            "read_set": _passthrough_json(row.get("read_set")),
            "payload": _passthrough_json(row.get("payload")),
            "idempotency_key": f"{source_namespace}:{source_event_id}",
        }
    )
