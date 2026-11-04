"""Decision-context manifests and doctrine-snapshot validation.

The decision context records stable identities, versions and the exact source
read-set — never source content. Resolution/PEB/SOL remain authoritative;
keychains only records the context.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .models import parse_datetime, to_iso

__all__ = [
    "DOCTRINE_HASH_RE",
    "binding_decision_event",
    "build_decision_context_manifest",
    "doctrine_snapshot",
    "observation_window",
]

DOCTRINE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BINDING_DECISION_CLASS = "deny_contract_promotion"
BINDING_DECISION_KIND_PREFIX = "peb.deny_contract_promotion."


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _field(
    trigger: Mapping[str, Any],
    name: str,
    read_set: Mapping[str, Any],
    payload: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> Any:
    return _first(trigger.get(name), meta.get(name), read_set.get(name), payload.get(name))


def binding_decision_event(
    trigger: Mapping[str, Any],
    read_set: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> bool:
    """A ratified ``deny_contract_promotion`` negative outcome is decision evidence.

    Such an event receives a checkpoint carrying its exact read-set so a rewind
    can explain the refusal/stale/rollback boundary; other negative outcomes
    stay archive-only.
    """

    read_set = read_set or _as_mapping(trigger.get("read_set"))
    payload = payload or _as_mapping(trigger.get("payload"))
    decision_class = (
        trigger.get("decision_class")
        or read_set.get("decision_class")
        or payload.get("decision_class")
    )
    kind = str(trigger.get("kind") or "")
    return decision_class == BINDING_DECISION_CLASS and kind.startswith(
        BINDING_DECISION_KIND_PREFIX
    )


def observation_window(trigger: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve the bounded observation window for a binding decision event."""

    read_set = _as_mapping(trigger.get("read_set"))
    payload = _as_mapping(trigger.get("payload"))
    meta = _as_mapping(trigger.get("meta"))

    nested_candidate: Mapping[str, Any] | None = None
    for candidate in (
        trigger.get("observation_window"),
        meta.get("observation_window"),
        read_set.get("observation_window"),
        payload.get("observation_window"),
    ):
        if isinstance(candidate, Mapping):
            nested_candidate = candidate
            break

    if nested_candidate is not None:
        nested: Mapping[str, Any] = nested_candidate
    else:
        nested = {
            "activation_ref": trigger.get("activation_ref"),
            "activated_at": trigger.get("activated_at"),
            "window_start": trigger.get("window_start"),
            "window_end": trigger.get("window_end"),
            "observed_at": trigger.get("observed_at"),
        }

    has_bounds = nested.get("window_start") is not None or nested.get("window_end") is not None
    if nested_candidate is None and not has_bounds:
        return None

    activation_ref = _first(
        nested.get("activation_ref"),
        trigger.get("activation_ref"),
        meta.get("activation_ref"),
        read_set.get("activation_ref"),
        payload.get("activation_ref"),
    )
    activated_at = _first(
        nested.get("activated_at"),
        trigger.get("activated_at"),
        meta.get("activated_at"),
        read_set.get("activated_at"),
        payload.get("activated_at"),
    )
    window_start = _first(
        nested.get("window_start"),
        nested.get("starts_at"),
        nested.get("start_at"),
        trigger.get("window_start"),
    )
    window_end = _first(
        nested.get("window_end"),
        nested.get("ends_at"),
        nested.get("end_at"),
        trigger.get("window_end"),
    )
    observed_at = _first(
        nested.get("observed_at"),
        trigger.get("observed_at"),
        trigger.get("effective_at"),
        trigger.get("recorded_at"),
    )
    if not activation_ref or not activated_at or not window_start or not window_end:
        raise ValueError(
            "bounded observation window requires activation_ref, activated_at, "
            "window_start, and window_end"
        )

    try:
        activated = parse_datetime(str(activated_at))
        start = parse_datetime(str(window_start))
        end = parse_datetime(str(window_end))
    except (TypeError, ValueError) as exc:
        raise ValueError("bounded observation window has invalid bounds") from exc
    if activated > start or start > end:
        raise ValueError("bounded observation window has invalid bounds")

    observed: datetime | None = None
    if observed_at:
        try:
            observed = parse_datetime(str(observed_at))
        except (TypeError, ValueError) as exc:
            raise ValueError("bounded observation window has invalid observed_at") from exc

    in_window: bool | None = None
    if observed is not None:
        in_window = start <= observed <= end

    if observed is None:
        phase = "post_activation"
    elif observed < activated:
        phase = "pre_activation"
    elif in_window is False:
        phase = "outside_window"
    else:
        phase = "post_activation"

    return {
        "activation_ref": str(activation_ref),
        "activated_at": str(activated_at),
        "window_start": str(window_start),
        "window_end": str(window_end),
        "observed_at": str(observed_at) if observed_at else None,
        "in_window": in_window,
        "phase": phase,
    }


def doctrine_snapshot(trigger: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the trigger's doctrine snapshot and resolve its id.

    Returns ``(snapshot, snapshot_id)``. Malformed snapshots and reference
    mismatches raise ``ValueError`` for the same reasons the TS service throws:
    a checkpoint must never attach an unverified doctrine reference.
    """

    read_set = _as_mapping(trigger.get("read_set"))
    payload = _as_mapping(trigger.get("payload"))
    meta = _as_mapping(trigger.get("meta"))

    snapshot: dict[str, Any] | None = None
    for candidate in (
        trigger.get("doctrine_snapshot"),
        meta.get("doctrine_snapshot"),
        read_set.get("doctrine_snapshot"),
        payload.get("doctrine_snapshot"),
    ):
        if candidate is None:
            continue
        if not isinstance(candidate, Mapping):
            raise ValueError("doctrine_snapshot is malformed")
        if candidate.get("schema_version") != 1:
            raise ValueError("doctrine_snapshot is malformed")
        for key in ("snapshot_id", "system_prompt_hash", "bootstrap_hash"):
            value = candidate.get(key)
            if not isinstance(value, str) or not DOCTRINE_HASH_RE.match(value):
                raise ValueError("doctrine_snapshot is malformed")
        cards = candidate.get("active_procedure_cards")
        if not isinstance(cards, (list, tuple)):
            raise ValueError("doctrine_snapshot is malformed")
        if not all(isinstance(card, str) and DOCTRINE_HASH_RE.match(card) for card in cards):
            raise ValueError("doctrine_snapshot is malformed")
        if list(cards) != sorted(cards) or len(set(cards)) != len(cards):
            raise ValueError("doctrine_snapshot card set must be sorted and unique")
        snapshot = dict(candidate)
        snapshot["active_procedure_cards"] = list(cards)
        break

    snapshot_id = _first(
        snapshot.get("snapshot_id") if snapshot else None,
        trigger.get("doctrine_snapshot_id"),
        meta.get("doctrine_snapshot_id"),
        read_set.get("doctrine_snapshot_id"),
        payload.get("doctrine_snapshot_id"),
    )
    if snapshot and snapshot_id and snapshot["snapshot_id"] != snapshot_id:
        raise ValueError("doctrine snapshot reference mismatch")
    return snapshot, str(snapshot_id) if snapshot_id else None


def build_decision_context_manifest(
    trigger: Mapping[str, Any],
    checkpoint_reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable decision-context manifest attached to a checkpoint."""

    snapshot, snapshot_id = doctrine_snapshot(trigger)
    read_set = _as_mapping(trigger.get("read_set"))
    payload = _as_mapping(trigger.get("payload"))
    meta = _as_mapping(trigger.get("meta"))

    def field(name: str) -> Any:
        return _field(trigger, name, read_set, payload, meta)

    decision_class = _first(
        trigger.get("decision_class"),
        meta.get("decision_class"),
        read_set.get("decision_class"),
        payload.get("decision_class"),
        payload.get("class"),
    )
    authority_level = _first(
        trigger.get("authority_level"),
        meta.get("authority_level"),
        read_set.get("authority_level"),
        payload.get("authority_level"),
    )
    owner = _first(
        trigger.get("binding_owner"),
        meta.get("binding_owner"),
        read_set.get("binding_owner"),
        payload.get("binding_owner"),
    )
    authorization_ref = _first(
        trigger.get("authorization_ref"),
        meta.get("authorization_ref"),
        read_set.get("authorization_ref"),
        payload.get("authorization_ref"),
    )
    rollback_reference = _first(
        field("rollback_ref"), field("rollback_of"), field("rollback_reference")
    )

    read_set_manifest = _first(
        trigger.get("read_set_manifest"),
        meta.get("read_set_manifest"),
        read_set.get("manifest"),
    )
    if read_set_manifest is None and (
        read_set.get("manifest_id") or read_set.get("digest") or read_set.get("manifest_digest")
    ):
        read_set_manifest = {}
        if read_set.get("manifest_id"):
            read_set_manifest["manifest_id"] = read_set["manifest_id"]
        if read_set.get("digest"):
            read_set_manifest["digest"] = read_set["digest"]
        if read_set.get("manifest_digest"):
            read_set_manifest["manifest_digest"] = read_set["manifest_digest"]

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "checkpoint_class": "decision",
        "decision_class": decision_class,
        "source_namespace": trigger.get("source_namespace") or "unknown",
        "source_event_id": trigger.get("source_event_id") or None,
        "binding_decision_owner": {
            "role": owner,
            "authority_level": authority_level,
            "authorization_ref": authorization_ref,
        },
        "activation": {
            "authorization_ref": authorization_ref,
            "activation_ref": field("activation_ref"),
            "grant_id": field("grant_id"),
            "authority_ref": field("authority_ref"),
            "activated_at": field("activated_at"),
        },
        "decision": {
            "id": field("decision_id"),
            "proposition_id": field("proposition_id"),
            "subject_id": field("subject_id"),
            "work_item_id": field("work_item_id"),
            "disposition": field("disposition"),
            "outcome": _first(
                trigger.get("outcome"),
                meta.get("outcome"),
                read_set.get("outcome"),
                payload.get("outcome"),
            ),
            "evidence_ids": field("evidence_ids"),
            "replay_context": field("replay_context"),
            "as_of": field("as_of"),
            "evaluation_fingerprint": field("evaluation_fingerprint"),
            "lineage_fingerprint": field("lineage_fingerprint"),
        },
        "rollback": {
            "reference": rollback_reference,
            "status": field("rollback_status"),
            "evidence_ids": field("rollback_evidence_ids"),
        },
        "evaluator": {
            "id": _first(
                trigger.get("evaluator_id"),
                meta.get("evaluator_id"),
                read_set.get("evaluator_id"),
                payload.get("evaluator_id"),
            ),
            "version": _first(
                trigger.get("evaluator_version"),
                meta.get("evaluator_version"),
                read_set.get("evaluator_version"),
                payload.get("evaluator_version"),
            ),
        },
        "contract": {
            "id": _first(
                trigger.get("contract_id"),
                meta.get("contract_id"),
                read_set.get("contract_id"),
                payload.get("contract_id"),
            ),
            "version": _first(
                trigger.get("contract_version"),
                meta.get("contract_version"),
                read_set.get("contract_version"),
                payload.get("contract_version"),
            ),
        },
        "law": {
            "id": _first(
                trigger.get("law_id"),
                meta.get("law_id"),
                read_set.get("law_id"),
                payload.get("law_id"),
            ),
            "version": _first(
                trigger.get("law_version"),
                meta.get("law_version"),
                read_set.get("law_version"),
                payload.get("law_version"),
            ),
        },
        "bridge": {
            "id": _first(
                trigger.get("bridge_id"),
                meta.get("bridge_id"),
                read_set.get("bridge_id"),
                payload.get("bridge_id"),
            ),
            "version": _first(
                trigger.get("bridge_version"),
                meta.get("bridge_version"),
                read_set.get("bridge_version"),
                payload.get("bridge_version"),
            ),
        },
        "source_read_set": trigger.get("read_set") or {},
        "read_set_manifest": read_set_manifest,
    }

    window = observation_window(trigger)
    if snapshot_id:
        manifest["doctrine_snapshot_id"] = snapshot_id
    if window:
        manifest["observation_window"] = window
    if checkpoint_reference:
        manifest["checkpoint_reference"] = dict(checkpoint_reference)
    return manifest


def utc_now_iso() -> str:
    return to_iso(datetime.now(tz=timezone.utc))
