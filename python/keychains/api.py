"""Action-shaped entry points.

Names mirror the Moleculer actions on ``keychain-snapshot`` so the Python
package stays a recognisable port:

======================  ==========================
Moleculer action        Python function
======================  ==========================
agentRecordsSnapshot    ``agent_records_snapshot``
agentRecordsStatus      ``agent_records_status``
agentRecordsTransitions ``agent_records_transitions``
(status)                ``status``
======================  ==========================

Deferred to a follow-up slice (see README): ``snapshot`` (legacy
observations/assessments projection), ``agentRecordsDoctrineSets``,
``agentRecordsDoctrineReconstruction``, ``agentRecordsDoctrineBackfill``,
``agentRecordsCensus`` and ``agentRecordsRewind``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .models import AgentRecord, AssetRef, to_iso
from .projection import DEFAULT_RESERVATION_TTL, project_agent_records
from .store import KeychainStore

__all__ = [
    "agent_records_snapshot",
    "agent_records_status",
    "agent_records_transitions",
    "reconcile_committed_checkpoints",
    "status",
]

MAX_TRANSITIONS = 200
DEFAULT_TRANSITIONS = 50


def agent_records_snapshot(
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
    """Project agent records into the keychain contextual layer."""

    return project_agent_records(
        records,
        store,
        assets=assets,
        label=label,
        trigger=trigger,
        trigger_event=trigger_event,
        now=now,
        base_interval=base_interval,
        reservation_ttl=reservation_ttl,
    )


def agent_records_status(store: KeychainStore) -> dict[str, Any]:
    """Current contextual-projection status for ``agent-records``."""

    active = store.active_checkpoint()
    if active and active.get("checkpoint_id"):
        latest = store.latest_committed_checkpoint()
    else:
        latest = store.latest_committed_checkpoint()

    if latest is not None and latest.get("entryCount") is not None:
        entry_count = int(latest["entryCount"])
    else:
        entry_count = store.entry_count()

    return {
        "enabled": True,
        "latestSnapshot": latest.get("version") if latest else None,
        "latestSnapshotAt": latest.get("created_at") if latest else None,
        "checkpointId": active.get("checkpoint_id") if active else None,
        "entryCount": entry_count,
        "totalRecordsProjected": latest.get("totalRecords") if latest else None,
        "supersededRecords": latest.get("supersededRecords") if latest else None,
    }


def agent_records_transitions(
    store: KeychainStore,
    *,
    limit: int = DEFAULT_TRANSITIONS,
    activation_ref: str | None = None,
    observation_status: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Recent decision-point transitions, newest first."""

    bounded = max(1, min(int(limit or DEFAULT_TRANSITIONS), MAX_TRANSITIONS))
    items = store.query_transitions(
        limit=bounded,
        activation_ref=activation_ref,
        observation_status=observation_status,
        outcome=outcome,
    )
    return {"count": len(items), "items": items}


def reconcile_committed_checkpoints(
    store: KeychainStore,
    *,
    now: datetime | None = None,
    reservation_ttl: timedelta | None = None,
) -> dict[str, Any]:
    """Repair an interrupted delivery at startup.

    Drops stale reservations and staged checkpoints, then promotes the newest
    committed checkpoint.
    """

    now_dt = now or datetime.now(tz=timezone.utc)
    store.reconcile(now_dt - (reservation_ttl or DEFAULT_RESERVATION_TTL))
    active = store.active_checkpoint()
    return {
        "ok": True,
        "reconciledAt": to_iso(now_dt),
        "activeCheckpointId": active.get("checkpoint_id") if active else None,
        "activeVersion": active.get("version") if active else None,
    }


def status(store: KeychainStore, *, outbox_poll_ms: int = 5000) -> dict[str, Any]:
    """Event-driven posture for the legacy snapshot domain.

    There is no snapshot timer (D3, keychains thread e267263c): the interval
    retired with PR #116 and snapshots are driven by decision points consumed
    from ``resolution.keychain_event_outbox``. ``lastEventAt`` is supplied by
    the caller that owns the Postgres connection.
    """

    return {
        "enabled": True,
        "mode": "event_driven",
        "outboxPollMs": outbox_poll_ms,
        "lastEventAt": None,
        "lastSnapshotAt": None,
        "latestSnapshot": None,
        "latestSnapshotAt": None,
        "chainHealth": None,
        "deepestRewindSinceBoot": 0,
    }
