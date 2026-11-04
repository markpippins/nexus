"""Supersession resolution: logical record instances from agent records.

Groups records by logical instance identity, then keeps the latest issuance
per instance. Three grouping mechanisms, in the same precedence order as the
TypeScript service:

1. Amendment chains — records sharing the hex token in
   ``"<context> <token> — amendment v<N>: ..."`` titles.
2. Explicit supersession tags — ``supersedes:<id-prefix>`` walked forward to
   the chain tip, plus ``superseded-by:<id-prefix>`` (recognised, not walked).
3. Everything else is a single-issuance instance.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from .models import AgentRecord, AssetRef, KeychainEntry

__all__ = [
    "ENTRY_KEY_PREFIXES",
    "amendments_only",
    "extract_amendment_token",
    "extract_amendment_version",
    "extract_supersession_tag",
    "resolve_keychain_entries",
]

ENTRY_KEY_PREFIXES = ("amendment", "explicit", "single")

_AMENDMENT_TOKEN_RE = re.compile(r"([0-9a-f]{6,})\s*[—–-]\s*amendment\s+v(\d+)", re.IGNORECASE)
_AMENDMENT_VERSION_RE = re.compile(r"amendment\s+v(\d+)", re.IGNORECASE)
_FORWARD_TAG_RE = re.compile(
    r"^supersedes(?::|-(?:partially|incident|c5)[^:]*)?:([0-9a-f]{8,})", re.IGNORECASE
)
_BACKWARD_TAG_RE = re.compile(r"^superseded-by:([0-9a-f]{8,})", re.IGNORECASE)


def extract_supersession_tag(tags: Sequence[str]) -> tuple[str, str] | None:
    """Return ``(points_to, direction)`` for a supersession tag, if any.

    ``direction`` is ``"forward"`` when this record supersedes an earlier one,
    ``"backward"`` when this record reports that a later one supersedes it.
    """

    for tag in tags or ():
        lower = (tag or "").lower()
        forward = _FORWARD_TAG_RE.match(lower)
        if forward:
            return forward.group(1), "forward"
        backward = _BACKWARD_TAG_RE.match(lower)
        if backward:
            return backward.group(1), "backward"
    return None


def extract_amendment_token(title: str) -> str | None:
    """Extract the logical instance token from an amendment title."""

    match = _AMENDMENT_TOKEN_RE.search(title or "")
    return match.group(1) if match else None


def extract_amendment_version(title: str) -> int:
    """Extract the amendment version (0 when the title is not an amendment)."""

    match = _AMENDMENT_VERSION_RE.search(title or "")
    return int(match.group(1)) if match else 0


def amendments_only(records: Iterable[AgentRecord]) -> list[AgentRecord]:
    """Filter to records that carry an amendment token (used by tests/tooling)."""

    return [record for record in records if extract_amendment_token(record.title)]


def _id_prefix(record_id: str) -> str:
    return record_id.replace("-", "")[:8]


def resolve_keychain_entries(
    records: Sequence[AgentRecord],
    version: int,
    now: str,
    assets_by_record_id: Mapping[str, AssetRef] | None = None,
) -> dict[str, KeychainEntry]:
    """Resolve logical record instances, returning ``entry key -> entry``.

    Entry keys carry the historical prefixes (``amendment:``, ``explicit:``,
    ``single:``) while ``instance_id`` is unprefixed; the projection strips the
    prefix when deriving a prior index from legacy parallel arrays.
    """

    assets = assets_by_record_id or {}

    by_id: dict[str, AgentRecord] = {}
    by_id_prefix: dict[str, AgentRecord] = {}
    for record in records:
        by_id[record.id] = record
        prefix = _id_prefix(record.id)
        by_id_prefix.setdefault(prefix, record)
        by_id_prefix[record.id] = record

    # ── Group 1: amendment chains ─────────────────────────────────────────
    amendment_groups: dict[str, list[AgentRecord]] = {}
    amendment_record_ids: set[str] = set()
    for record in records:
        token = extract_amendment_token(record.title)
        if token:
            amendment_groups.setdefault(token, []).append(record)
            amendment_record_ids.add(record.id)

    # ── Group 2: explicit supersession chains ─────────────────────────────
    supersedes_map: dict[str, str] = {}  # newer id -> older id it supersedes
    superseded_by_map: dict[str, str] = {}  # older id -> newer id
    explicit_ids: set[str] = set()
    for record in records:
        parsed = extract_supersession_tag(record.tags)
        if not parsed:
            continue
        points_to, direction = parsed
        if direction != "forward":
            continue
        older = by_id_prefix.get(points_to)
        if older:
            supersedes_map[record.id] = older.id
            superseded_by_map[older.id] = record.id
            explicit_ids.add(record.id)
            explicit_ids.add(older.id)

    explicit_chains: dict[str, list[str]] = {}  # tip id -> [oldest..newest]
    processed: set[str] = set()
    for record in records:
        if record.id not in explicit_ids or record.id in processed:
            continue
        # A tip supersedes someone and is not itself superseded.
        is_tip = record.id in supersedes_map and record.id not in superseded_by_map
        if not is_tip:
            continue
        chain: list[str] = []
        current: str | None = record.id
        while current and current not in processed:
            chain.insert(0, current)
            processed.add(current)
            current = supersedes_map.get(current)
        if chain:
            explicit_chains[record.id] = chain

    entries: dict[str, KeychainEntry] = {}

    # ── Entry 1: amendment chains ─────────────────────────────────────────
    for token, chain_records in amendment_groups.items():
        ordered = sorted(chain_records, key=lambda r: r.created_at)
        latest = ordered[-1]
        chain_ids = [r.id for r in ordered]
        previous_id = ordered[-2].id if len(ordered) > 1 else None
        asset = assets.get(latest.id)
        entries[f"amendment:{token}"] = KeychainEntry(
            instance_id=token,
            record_type=latest.record_type,
            role=latest.role,
            asset_id=asset.canonical_asset_id if asset else None,
            asset_kind=asset.asset_kind if asset else None,
            record_id=latest.id,
            version=extract_amendment_version(latest.title) or len(ordered),
            title=latest.title,
            content=latest.content,
            tags=tuple(latest.tags or ()),
            level=latest.level,
            visibility_scope=latest.visibility_scope,
            model=latest.model,
            record_created_at=_iso(latest.created_at),
            issued_by=latest.role,
            issued_at=_iso(latest.created_at),
            supersedes=previous_id,
            supersession_type="amendment",
            chain_length=len(ordered),
            chain_ids=tuple(chain_ids),
            snapshot_version=version,
            projected_at=now,
        )

    # ── Entry 2: explicit supersession chains ─────────────────────────────
    for tip_id, chain_ids in explicit_chains.items():
        if any(member in amendment_record_ids for member in chain_ids):
            continue
        oldest = by_id[chain_ids[0]]
        latest = by_id[chain_ids[-1]]
        previous = by_id[chain_ids[-2]] if len(chain_ids) > 1 else None
        instance_key = oldest.id[:8]
        asset = assets.get(latest.id)
        entries[f"explicit:{instance_key}"] = KeychainEntry(
            instance_id=instance_key,
            record_type=latest.record_type,
            role=latest.role,
            asset_id=asset.canonical_asset_id if asset else None,
            asset_kind=asset.asset_kind if asset else None,
            record_id=latest.id,
            version=len(chain_ids),
            title=latest.title,
            content=latest.content,
            tags=tuple(latest.tags or ()),
            level=latest.level,
            visibility_scope=latest.visibility_scope,
            model=latest.model,
            record_created_at=_iso(latest.created_at),
            issued_by=latest.role,
            issued_at=_iso(latest.created_at),
            supersedes=previous.id if previous else None,
            supersession_type="explicit",
            chain_length=len(chain_ids),
            chain_ids=tuple(chain_ids),
            snapshot_version=version,
            projected_at=now,
        )

    # ── Entry 3: single-issuance records ──────────────────────────────────
    for record in records:
        if record.id in amendment_record_ids or record.id in explicit_ids:
            continue
        asset = assets.get(record.id)
        entries[f"single:{record.id}"] = KeychainEntry(
            instance_id=record.id,
            record_type=record.record_type,
            role=record.role,
            asset_id=asset.canonical_asset_id if asset else None,
            asset_kind=asset.asset_kind if asset else None,
            record_id=record.id,
            version=1,
            title=record.title,
            content=record.content,
            tags=tuple(record.tags or ()),
            level=record.level,
            visibility_scope=record.visibility_scope,
            model=record.model,
            record_created_at=_iso(record.created_at),
            issued_by=record.role,
            issued_at=_iso(record.created_at),
            supersedes=None,
            supersession_type="none",
            chain_length=1,
            chain_ids=(record.id,),
            snapshot_version=version,
            projected_at=now,
        )

    return entries


def _iso(value: datetime) -> str:
    from .models import to_iso

    return to_iso(value)
