"""Checkpoint storage mechanics: base + delta (D6).

Every Nth checkpoint stores a full state vector (a BASE); the rest store only
what moved (a DELTA) plus the version of the base they replay from. A delta is
never computed against unknown prior state — that case degrades to a base.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping, Sequence

from .models import CheckpointDelta, InstanceManifest, KeychainEntry

__all__ = [
    "LEGACY_KEY_RE",
    "base_interval",
    "compute_delta",
    "decide_is_base",
    "index_from_legacy_arrays",
    "index_from_manifests",
    "instance_manifest",
    "manifests_for",
    "merge_delta",
    "overlap_below_threshold",
    "plan_storage",
]

LEGACY_KEY_RE = re.compile(r"^(amendment|explicit|single):(.+)$", re.IGNORECASE)
DEFAULT_BASE_INTERVAL = 50


def instance_manifest(entry: KeychainEntry) -> InstanceManifest:
    """Project a resolved entry down to its contentless manifest."""

    return InstanceManifest(
        instance_id=entry.instance_id,
        record_type=entry.record_type,
        record_id=entry.record_id,
        version=entry.version,
        role=entry.role,
        supersession_type=entry.supersession_type,
        asset_id=entry.asset_id,
    )


def manifests_for(entries: Iterable[KeychainEntry]) -> list[InstanceManifest]:
    return [instance_manifest(entry) for entry in entries]


def index_from_manifests(manifests: Iterable[InstanceManifest]) -> dict[str, str]:
    """Flat ``instance_id -> record_id`` index — all a delta needs as prior state."""

    return {manifest.instance_id: manifest.record_id for manifest in manifests}


def index_from_legacy_arrays(
    instance_ids: Any, current_record_ids: Any
) -> dict[str, str] | None:
    """Derive an index from a legacy checkpoint's parallel id arrays.

    Both arrays were built by iterating the same map in one pass, so they are
    positionally parallel. A length mismatch means the pairing cannot be
    trusted and ``None`` is returned, which forces the next checkpoint to be a
    base rather than a delta against unknown state.

    The arrays hold the prefixed entry keys while manifests carry unprefixed
    instance ids; the prefix is stripped so the derived index is comparable.
    """

    if not isinstance(instance_ids, (list, tuple)):
        return None
    if not isinstance(current_record_ids, (list, tuple)):
        return None
    if len(instance_ids) != len(current_record_ids):
        return None
    index: dict[str, str] = {}
    for raw_id, raw_record_id in zip(instance_ids, current_record_ids):
        raw = str(raw_id)
        matched = LEGACY_KEY_RE.match(raw)
        index[matched.group(2) if matched else raw] = str(raw_record_id)
    return index


def compute_delta(
    prev_index: Mapping[str, str], manifests: Sequence[InstanceManifest]
) -> CheckpointDelta:
    """Diff the current population against a prior index."""

    added: list[InstanceManifest] = []
    updated: list[InstanceManifest] = []
    current: dict[str, str] = {}
    for manifest in manifests:
        current[manifest.instance_id] = manifest.record_id
        previous_record_id = prev_index.get(manifest.instance_id)
        if previous_record_id is None:
            added.append(manifest)
        elif previous_record_id != manifest.record_id:
            updated.append(manifest)
    removed = [instance_id for instance_id in prev_index if instance_id not in current]
    return CheckpointDelta(added=tuple(added), updated=tuple(updated), removed=tuple(removed))


def merge_delta(
    state_vector: Mapping[str, Sequence[Mapping[str, Any]]],
    delta: CheckpointDelta,
) -> dict[str, list[dict[str, Any]]]:
    """Apply a delta to a state vector, returning a new vector.

    Pure by construction so a rewind can replay without mutating the base it
    loaded. A record_type change re-homes the instance under its new type.
    """

    merged: dict[str, list[dict[str, Any]]] = {
        record_type: [dict(instance) for instance in instances or ()]
        for record_type, instances in (state_vector or {}).items()
    }

    def drop(instance_id: str) -> None:
        for record_type in list(merged):
            merged[record_type] = [
                instance for instance in merged[record_type] if instance["instance_id"] != instance_id
            ]

    for instance_id in delta.removed:
        drop(instance_id)
    for manifest in delta.updated:
        drop(manifest.instance_id)
        merged.setdefault(manifest.record_type, []).append(manifest.to_doc())
    for manifest in delta.added:
        merged.setdefault(manifest.record_type, []).append(manifest.to_doc())
    return merged


def base_interval(environ: Mapping[str, str] | None = None) -> int:
    """Every Nth checkpoint is a full base; the rest are deltas."""

    source = os.environ if environ is None else environ
    raw = source.get("KEYCHAIN_BASE_INTERVAL")
    try:
        value = int(raw) if raw is not None else DEFAULT_BASE_INTERVAL
    except (TypeError, ValueError):
        return DEFAULT_BASE_INTERVAL
    return value if value > 0 else DEFAULT_BASE_INTERVAL


def overlap_below_threshold(
    manifests: Sequence[InstanceManifest],
    prev_index: Mapping[str, str] | None,
    *,
    floor: int = 100,
    ratio: float = 0.5,
) -> bool:
    """True when a prior index is not comparable prior state.

    A prior index sharing essentially no keys with the current population is a
    scheme change or a foreign index; diffing against it would produce a
    full-rewrite delta, so the caller degrades to a base instead.
    """

    if not prev_index or len(manifests) <= floor or len(prev_index) <= floor:
        return False
    known = set(prev_index)
    overlap = sum(1 for manifest in manifests if manifest.instance_id in known)
    return (overlap / len(manifests)) < ratio


def decide_is_base(prev_state_known: bool, version: int, interval: int) -> bool:
    return (not prev_state_known) or version % interval == 0


def plan_storage(
    manifests: Sequence[InstanceManifest],
    prev_index: Mapping[str, str] | None,
    version: int,
    interval: int,
) -> tuple[bool, CheckpointDelta]:
    """Decide base-vs-delta and compute the delta when it is a delta."""

    prev_state_known = prev_index is not None
    if prev_state_known and overlap_below_threshold(manifests, prev_index):
        prev_index = None
        prev_state_known = False
    is_base = decide_is_base(prev_state_known, version, interval)
    if is_base:
        return True, CheckpointDelta()
    return False, compute_delta(prev_index or {}, manifests)
