"""Core value types for the keychains contextual projection.

Python port of ``moleculer/nexus-broker/services/keychain-snapshot.service.ts``
(contextual-core slice). The canonical substrate is the resolution schema; the
keychains store is a one-way PG -> Mongo projection and is never read back as
operational authority (D-2026-08-14-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "AgentRecord",
    "AssetRef",
    "CheckpointDelta",
    "InstanceManifest",
    "KeychainEntry",
    "to_iso",
    "parse_datetime",
]


def to_iso(value: datetime) -> str:
    """Render a datetime the way JS ``Date.toISOString()`` does.

    Always UTC, always millisecond precision, always trailing ``Z`` — the
    stored documents are compared against values written by the TypeScript
    service, so the format has to match exactly.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def parse_datetime(value: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AssetRef:
    """A canonical asset reference (``semantics.canonical_asset``)."""

    canonical_asset_id: str
    asset_kind: str = ""


@dataclass(frozen=True)
class AgentRecord:
    """One row from ``nebula.agent_records``."""

    id: str
    record_type: str
    role: str
    title: str
    content: str
    created_at: datetime
    level: int
    visibility_scope: str
    tags: Sequence[str] = field(default_factory=tuple)
    model: str | None = None
    source_path: str | None = None
    metadata: Any = None
    system_id: str | None = None
    subsystem_id: str | None = None
    feature_id: str | None = None
    plan_ref: str | None = None
    candidate_id: str | None = None
    requirement_id: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "AgentRecord":
        """Build from a DB row (dict with snake_case column names)."""

        tags = row.get("tags") or ()
        if isinstance(tags, str):
            tags = (tags,)
        return cls(
            id=str(row["id"]),
            record_type=row["record_type"],
            role=row["role"],
            title=row["title"] or "",
            content=row.get("content") or "",
            created_at=parse_datetime(row["created_at"]),
            level=int(row.get("level") or 0),
            visibility_scope=row.get("visibility_scope") or "",
            tags=tuple(tags),
            model=row.get("model"),
            source_path=row.get("source_path"),
            metadata=row.get("metadata"),
            system_id=row.get("system_id"),
            subsystem_id=row.get("subsystem_id"),
            feature_id=row.get("feature_id"),
            plan_ref=row.get("plan_ref"),
            candidate_id=row.get("candidate_id"),
            requirement_id=row.get("requirement_id"),
        )


@dataclass(frozen=True)
class KeychainEntry:
    """One resolved logical record instance (current issuance only)."""

    instance_id: str
    record_type: str
    role: str
    asset_id: str | None
    asset_kind: str | None
    #: ``current`` block: latest issuance of this instance.
    record_id: str
    version: int
    title: str
    content: str
    tags: Sequence[str]
    level: int
    visibility_scope: str
    model: str | None
    record_created_at: str
    #: ``provenance`` block.
    issued_by: str
    issued_at: str
    supersedes: str | None
    supersession_type: str
    chain_length: int
    chain_ids: Sequence[str]
    #: Projection metadata.
    snapshot_version: int
    projected_at: str

    def to_doc(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "record_type": self.record_type,
            "role": self.role,
            "asset_id": self.asset_id,
            "asset_kind": self.asset_kind,
            "current": {
                "record_id": self.record_id,
                "version": self.version,
                "title": self.title,
                "content": self.content,
                "tags": list(self.tags),
                "level": self.level,
                "visibility_scope": self.visibility_scope,
                "model": self.model,
                "created_at": self.record_created_at,
            },
            "provenance": {
                "issued_by": self.issued_by,
                "issued_at": self.issued_at,
                "supersedes": self.supersedes,
                "supersession_type": self.supersession_type,
                "chain_length": self.chain_length,
                "chain_ids": list(self.chain_ids),
            },
            "snapshot_version": self.snapshot_version,
            "projected_at": self.projected_at,
        }


@dataclass(frozen=True)
class InstanceManifest:
    """A contentless instance manifest — the unit of a state-vector delta."""

    instance_id: str
    record_type: str
    record_id: str
    version: int
    role: str
    supersession_type: str
    asset_id: str | None

    def to_doc(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "record_type": self.record_type,
            "record_id": self.record_id,
            "version": self.version,
            "role": self.role,
            "supersession_type": self.supersession_type,
            "asset_id": self.asset_id,
        }


@dataclass(frozen=True)
class CheckpointDelta:
    """What moved between two checkpoints (D6)."""

    added: Sequence[InstanceManifest] = ()
    updated: Sequence[InstanceManifest] = ()
    removed: Sequence[str] = ()

    def to_doc(self) -> dict[str, Any]:
        return {
            "added": [m.to_doc() for m in self.added],
            "updated": [m.to_doc() for m in self.updated],
            "removed": list(self.removed),
        }

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "removed": len(self.removed),
        }


def as_manifest_list(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize stored manifests back to plain documents."""

    return [dict(item) for item in items]
