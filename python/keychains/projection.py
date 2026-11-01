"""The agent-records contextual projection.

Reads ``nebula.agent_records``, resolves supersession chains, and writes one
keychain entry per logical instance (latest issuance only) plus a staged ->
committed -> promoted checkpoint. Mongo is never read back as operational
state (D-2026-08-14-003): this is a one-way PG -> Mongo projection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .checkpoints import base_interval as resolve_base_interval
from .checkpoints import index_from_legacy_arrays, index_from_manifests, manifests_for
from .checkpoints import plan_storage
from .decision_context import binding_decision_event, build_decision_context_manifest
from .decision_context import doctrine_snapshot, observation_window
from .models import AgentRecord, AssetRef, KeychainEntry, to_iso
from .store import KeychainStore, ReservationConflict
from .supersession import resolve_keychain_entries
from .trigger import normalize_trigger_event, parse_legacy_trigger

__all__ = [
    "DEFAULT_RESERVATION_TTL",
    "project_agent_records",
]

DEFAULT_RESERVATION_TTL = timedelta(minutes=5)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _drift_findings(
    entries: Mapping[str, KeychainEntry],
    prev_instance_ids: set[str],
    prev_record_ids: set[str],
    version: int,
    now: str,
) -> list[dict[str, Any]]:
    """Drift between this checkpoint and the previous one.

    Comparison is on ``instance_id`` (unprefixed), which is what the prior
    index stores. The TypeScript service compares ``entries.keys()`` — the
    prefixed map keys (``single:<uuid>``, ``amendment:<token>``) — against an
    index built from unprefixed instance ids, so every instance is reported as
    ``new_instance`` on every checkpoint and ``superseded`` never fires. This
    port implements the evident intent; see README "Parity divergences".
    """

    findings: list[dict[str, Any]] = []
    for entry in entries.values():
        instance_id = entry.instance_id
        if instance_id not in prev_instance_ids:
            findings.append(
                {
                    "snapshot_version": version,
                    "kind": "new_instance",
                    "instance_id": instance_id,
                    "record_type": entry.record_type,
                    "role": entry.role,
                    "description": (
                        f"New keychain instance {instance_id} "
                        f"({entry.record_type}/{entry.role})"
                    ),
                    "severity": "info",
                    "detected_at": now,
                }
            )
            continue
        if entry.record_id not in prev_record_ids:
            findings.append(
                {
                    "snapshot_version": version,
                    "kind": "superseded",
                    "instance_id": instance_id,
                    "record_type": entry.record_type,
                    "role": entry.role,
                    "new_record_id": entry.record_id,
                    "new_version": entry.version,
                    "description": (
                        f"Instance {instance_id} superseded: now v{entry.version} "
                        f"({entry.record_id})"
                    ),
                    "severity": "info",
                    "detected_at": now,
                }
            )
    return findings


def _state_vector(entries: Mapping[str, KeychainEntry]) -> dict[str, list[dict[str, Any]]]:
    """D1 (Option B): the current set of records per type — a compact manifest.

    Not a content clone. An instance carrying multiple record types (unusual)
    is listed under each type it currently represents.
    """

    vector: dict[str, list[dict[str, Any]]] = {}
    for entry in entries.values():
        vector.setdefault(entry.record_type, []).append(
            {
                "instance_id": entry.instance_id,
                "record_id": entry.record_id,
                "version": entry.version,
                "role": entry.role,
                "supersession_type": entry.supersession_type,
                "asset_id": entry.asset_id,
            }
        )
    return vector


def _prior_index(
    store: KeychainStore, prev_checkpoint: Mapping[str, Any] | None, prev_version: int
) -> dict[str, str] | None:
    """Prior instance index — the only prior state a delta needs.

    Preferred source is the bounded ``checkpoint_heads`` pointer (one small
    document). A legacy checkpoint's parallel id arrays are the fallback so the
    transition out of the historical shape is seamless. With neither, the next
    checkpoint becomes a base: a delta is never computed against unknown state.
    """

    head = store.checkpoint_head()
    if head and head.get("index") is not None and int(head.get("version") or 0) == prev_version:
        return {str(key): str(value) for key, value in dict(head["index"]).items()}
    if prev_checkpoint:
        return index_from_legacy_arrays(
            prev_checkpoint.get("instance_ids"), prev_checkpoint.get("current_record_ids")
        )
    return None


def project_agent_records(
    records: Sequence[AgentRecord],
    store: KeychainStore,
    *,
    assets: Mapping[str, AssetRef] | None = None,
    label: str | None = None,
    trigger: str | None = None,
    trigger_event: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    base_interval: int | None = None,
    reservation_ttl: timedelta | None = None,
) -> dict[str, Any]:
    """Run one contextual projection checkpoint.

    Returns the same camelCase summary the TypeScript ``agentRecordsSnapshot``
    action returns.
    """

    now_dt = now or datetime.now(tz=timezone.utc)
    now_iso = to_iso(now_dt)
    ttl = reservation_ttl or DEFAULT_RESERVATION_TTL

    # ── Normalize the trigger event ───────────────────────────────────────
    normalized: dict[str, Any] | None = None
    if trigger_event is not None:
        normalized = normalize_trigger_event(trigger_event)
    elif trigger is not None:
        normalized = parse_legacy_trigger(trigger)

    doctrine_doc: dict[str, Any] | None = None
    doctrine_id: str | None = None
    is_binding = False

    if normalized is not None:
        doctrine_doc, doctrine_id = doctrine_snapshot(normalized)
        normalized["recorded_at"] = normalized.get("recorded_at") or now_iso
        store.upsert_doctrine_snapshot(doctrine_doc or {})

        read_set = _mapping(normalized.get("read_set"))
        payload = _mapping(normalized.get("payload"))
        is_binding = binding_decision_event(normalized, read_set, payload)
        if is_binding:
            window = observation_window(normalized)
            if window:
                normalized["observation_window"] = window

        key = str(normalized["idempotency_key"])
        existing = store.get_transition(key)
        if existing and existing.get("snapshot_version"):
            return _with_doctrine(
                {
                    "ok": True,
                    "deduplicated": True,
                    "version": existing["snapshot_version"],
                    "trigger": normalized,
                    "note": "snapshot already exists for this trigger event",
                },
                doctrine_id,
            )

        # A process can fail after the checkpoint commits but before the
        # transition document is finalized. Source identity is the durable
        # recovery key: repair the transition, do not create a second
        # checkpoint on retry.
        source_namespace = normalized.get("source_namespace")
        source_event_id = normalized.get("source_event_id")
        if source_namespace and source_event_id:
            committed = store.find_committed_by_source(source_namespace, source_event_id)
            if committed:
                store.promote_active_checkpoint(committed)
                store.deliver_transition(
                    key,
                    {
                        **normalized,
                        "keychain_event_id": key,
                        "snapshot_version": committed["version"],
                        "checkpoint_id": committed.get("checkpoint_id"),
                        "checkpoint_status": "delivered",
                        "decision_context": build_decision_context_manifest(
                            normalized,
                            {
                                "checkpoint_id": committed.get("checkpoint_id"),
                                "version": committed["version"],
                            },
                        ),
                        "delivered_at": committed.get("committed_at")
                        or committed.get("created_at"),
                        "created_at": normalized["recorded_at"],
                    },
                    upsert=True,
                )
                return _with_doctrine(
                    {
                        "ok": True,
                        "deduplicated": True,
                        "version": committed["version"],
                        "trigger": normalized,
                        "note": "recovered an already committed checkpoint for this source event",
                    },
                    doctrine_id,
                )

        # Negative outcomes from the ratified deny_contract_promotion class are
        # decision evidence, not archive-only events. Other source events keep
        # the archive-only behavior unless they are committed.
        outcome = normalized.get("outcome")
        if outcome and outcome != "committed" and not is_binding:
            store.archive_negative_outcome(
                {
                    **normalized,
                    "keychain_event_id": key,
                    "created_at": normalized["recorded_at"],
                    "snapshot_version": None,
                    "checkpoint_status": "not_applicable",
                }
            )
            return _with_doctrine(
                {
                    "ok": True,
                    "archived": True,
                    "checkpoint_created": False,
                    "trigger": normalized,
                    "note": "negative outcome archived without a state-vector checkpoint",
                },
                doctrine_id,
            )

        # Reserve the event before building the projection. The sparse unique
        # index makes concurrent deliveries converge on one reservation.
        try:
            store.reserve_transition(
                {
                    **normalized,
                    "keychain_event_id": key,
                    "created_at": normalized["recorded_at"],
                    "reservation": True,
                    "reservation_at": now_dt,
                }
            )
        except ReservationConflict:
            reserved = store.get_transition(key)
            if reserved and reserved.get("snapshot_version"):
                return {
                    "ok": True,
                    "deduplicated": True,
                    "version": reserved["snapshot_version"],
                    "trigger": normalized,
                }
            if not store.takeover_stale_reservation(key, now_dt - ttl):
                return {
                    "ok": False,
                    "deduplicated": True,
                    "checkpoint_pending": True,
                    "trigger": normalized,
                    "note": "another delivery owns checkpoint construction",
                }
            # The previous owner is stale; this delivery now owns the
            # reservation and continues through normal checkpoint construction.

    # ── Prior state ───────────────────────────────────────────────────────
    prev_checkpoint = store.latest_committed_checkpoint()
    prev_version = int(prev_checkpoint.get("version") or 0) if prev_checkpoint else 0
    prev_index = _prior_index(store, prev_checkpoint, prev_version)
    prev_instance_ids = set(prev_index or {})
    prev_record_ids = set((prev_index or {}).values())

    # ── Resolve logical instances ─────────────────────────────────────────
    version = store.allocate_version(prev_version)
    checkpoint_label = label or f"ar-snapshot-v{version}"
    entries = resolve_keychain_entries(records, version, now_iso, assets)
    manifests = manifests_for(entries.values())

    # ── Base vs delta (D6) ────────────────────────────────────────────────
    interval = base_interval if base_interval is not None else resolve_base_interval()
    is_base, delta = plan_storage(manifests, prev_index, version, interval)
    base_version: int | None = None
    if not is_base:
        base_version = store.nearest_base_version(prev_version)

    current_index = index_from_manifests(manifests)
    findings = _drift_findings(entries, prev_instance_ids, prev_record_ids, version, now_iso)

    checkpoint_id = str(uuid4())
    state_vector = _state_vector(entries)
    type_breakdown: dict[str, int] = {}
    role_breakdown: dict[str, int] = {}
    for entry in entries.values():
        type_breakdown[entry.record_type] = type_breakdown.get(entry.record_type, 0) + 1
        role_breakdown[entry.role] = role_breakdown.get(entry.role, 0) + 1

    decision_context = (
        build_decision_context_manifest(
            normalized, {"checkpoint_id": checkpoint_id, "version": version}
        )
        if normalized
        else None
    )

    checkpoint: dict[str, Any] = {
        "checkpoint_id": checkpoint_id,
        "version": version,
        "label": checkpoint_label,
        "created_at": now_iso,
        "totalRecords": len(records),
        "entryCount": len(entries),
        "supersededRecords": len(records) - len(entries),
        "driftCount": len(findings),
        "typeBreakdown": type_breakdown,
        "roleBreakdown": role_breakdown,
        "storage": "base" if is_base else "delta",
        "decision_context": decision_context,
        "trigger": trigger,
        "trigger_event": normalized,
        "checkpoint_status": "staged",
        "prevVersion": prev_version,
    }
    if is_base:
        checkpoint["state_vector"] = state_vector
    else:
        checkpoint["base_version"] = base_version
        checkpoint["delta"] = delta.to_doc()
        checkpoint["delta_counts"] = delta.counts()
    if doctrine_id:
        checkpoint["doctrine_snapshot_id"] = doctrine_id
    if normalized and normalized.get("source_namespace") and normalized.get("source_event_id"):
        checkpoint["source_namespace"] = normalized["source_namespace"]
        checkpoint["source_event_id"] = normalized["source_event_id"]

    # ── Stage, commit, promote ────────────────────────────────────────────
    committed = False
    try:
        store.insert_staged_checkpoint(checkpoint)
        store.commit_checkpoint(checkpoint_id, now_iso)
        committed = True
        store.promote_active_checkpoint(checkpoint)
        # Written after promotion so a failed commit never leaves a head
        # describing a checkpoint that does not exist.
        store.save_checkpoint_head(version, current_index, now_iso)
    except Exception:
        if not committed:
            store.delete_staged_checkpoint(checkpoint_id)
        raise

    if findings:
        store.add_drift_findings(
            [{**finding, "checkpoint_id": checkpoint_id} for finding in findings]
        )

    # ── Compatibility views (only while this checkpoint is active) ────────
    active = store.active_checkpoint()
    if active and active.get("checkpoint_id") == checkpoint_id:
        entry_docs = [
            {**entry.to_doc(), "checkpoint_id": checkpoint_id} for entry in entries.values()
        ]
        store.replace_entries(entry_docs)
        record_type_state = [
            {
                "record_type": record_type,
                "active_instances": instances,
                "instance_count": len(instances),
                "updated_at": now_iso,
                "snapshot_version": version,
                "checkpoint_id": checkpoint_id,
            }
            for record_type, instances in state_vector.items()
        ]
        store.replace_record_type_state(record_type_state)

    # ── Record the trigger event with the produced snapshot version ───────
    if normalized is not None:
        store.deliver_transition(
            str(normalized["idempotency_key"]),
            {
                **normalized,
                "idempotency_key": normalized["idempotency_key"],
                "snapshot_version": version,
                "checkpoint_id": checkpoint_id,
                "checkpoint_status": "delivered",
                "decision_context": decision_context,
                "delivered_at": now_iso,
                "created_at": now_iso,
            },
        )

    return _with_doctrine(
        {
            "ok": True,
            "version": version,
            "label": checkpoint_label,
            "totalRecords": len(records),
            "entries": len(entries),
            "supersededRecords": len(records) - len(entries),
            "driftFindings": len(findings),
            "typeBreakdown": type_breakdown,
            "roleBreakdown": role_breakdown,
            "recordTypeCount": len(state_vector),
            "storage": "base" if is_base else "delta",
            "base_version": base_version,
            "delta_counts": delta.counts(),
            "trigger": normalized,
            "decision_context": decision_context,
            "deduplicated": False,
            "prevVersion": prev_version,
        },
        doctrine_id,
    )


def _with_doctrine(result: dict[str, Any], doctrine_id: str | None) -> dict[str, Any]:
    if doctrine_id:
        result["doctrine_snapshot_id"] = doctrine_id
    return result
