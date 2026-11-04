"""Keychains contextual projection — Python port (contextual-core slice).

Port of ``moleculer/nexus-broker/services/keychain-snapshot.service.ts``.

The keychains store is a one-way PG -> Mongo projection: the canonical
substrate is the resolution schema and the keychain is the contextual instance
layer ("what the next turn knows"). Mongo is never read back as operational
state (D-2026-08-14-003).
"""

from __future__ import annotations

from .api import (
    agent_records_snapshot,
    agent_records_status,
    agent_records_transitions,
    reconcile_committed_checkpoints,
    status,
)
from .checkpoints import (
    base_interval,
    compute_delta,
    decide_is_base,
    index_from_legacy_arrays,
    index_from_manifests,
    instance_manifest,
    manifests_for,
    merge_delta,
    plan_storage,
)
from .decision_context import (
    binding_decision_event,
    build_decision_context_manifest,
    doctrine_snapshot,
    observation_window,
)
from .models import (
    AgentRecord,
    AssetRef,
    CheckpointDelta,
    InstanceManifest,
    KeychainEntry,
    parse_datetime,
    to_iso,
)
from .projection import DEFAULT_RESERVATION_TTL, project_agent_records
from .store import (
    AGENT_RECORDS_KEY,
    DATABASE_NAME,
    KeychainStore,
    MemoryKeychainStore,
    ReservationConflict,
)
from .supersession import (
    extract_amendment_token,
    extract_amendment_version,
    extract_supersession_tag,
    resolve_keychain_entries,
)
from .trigger import normalize_trigger_event, outbox_row_to_trigger, parse_legacy_trigger

__version__ = "0.1.0"

__all__ = [
    "AGENT_RECORDS_KEY",
    "AgentRecord",
    "AssetRef",
    "CheckpointDelta",
    "DATABASE_NAME",
    "DEFAULT_RESERVATION_TTL",
    "InstanceManifest",
    "KeychainEntry",
    "KeychainStore",
    "MemoryKeychainStore",
    "ReservationConflict",
    "agent_records_snapshot",
    "agent_records_status",
    "agent_records_transitions",
    "base_interval",
    "binding_decision_event",
    "build_decision_context_manifest",
    "compute_delta",
    "decide_is_base",
    "doctrine_snapshot",
    "extract_amendment_token",
    "extract_amendment_version",
    "extract_supersession_tag",
    "index_from_legacy_arrays",
    "index_from_manifests",
    "instance_manifest",
    "manifests_for",
    "merge_delta",
    "normalize_trigger_event",
    "observation_window",
    "outbox_row_to_trigger",
    "parse_datetime",
    "parse_legacy_trigger",
    "plan_storage",
    "project_agent_records",
    "reconcile_committed_checkpoints",
    "resolve_keychain_entries",
    "status",
    "to_iso",
]
