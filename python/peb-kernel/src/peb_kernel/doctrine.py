"""Content-addressed doctrine snapshots for governed execution provenance.

A snapshot describes the doctrine inputs that were in force for an execution:
the system prompt, bootstrap, and the full content hashes of the active
procedure cards. Session identity, wall-clock time, and execution identity are
intentionally excluded so identical doctrine sets share one address.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SNAPSHOT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_hash(value: Any) -> str:
    """Hash content, accepting an already-normalized sha256 reference."""
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value
    raw = value if isinstance(value, str) else _canonical_json(value)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a sha256:<64 lowercase hex> reference")
    return value


def _normalize_cards(cards: Sequence[Any]) -> tuple[str, ...]:
    if isinstance(cards, (str, bytes, Mapping)):
        raise TypeError("active_procedure_cards must be a sequence of card contents")
    normalized = {_content_hash(card) for card in cards}
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class DoctrineSnapshot:
    """Immutable, content-addressed doctrine manifest."""

    schema_version: int
    snapshot_id: str
    system_prompt_hash: str
    bootstrap_hash: str
    active_procedure_cards: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "system_prompt_hash": self.system_prompt_hash,
            "bootstrap_hash": self.bootstrap_hash,
            "active_procedure_cards": list(self.active_procedure_cards),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DoctrineSnapshot":
        if not isinstance(raw, Mapping):
            raise ValueError("doctrine snapshot must be an object")
        if raw.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported doctrine snapshot schema version")
        snapshot_id = _require_hash(raw.get("snapshot_id"), "doctrine_snapshot_id")
        system_prompt_hash = _require_hash(raw.get("system_prompt_hash"), "system_prompt_hash")
        bootstrap_hash = _require_hash(raw.get("bootstrap_hash"), "bootstrap_hash")
        cards = raw.get("active_procedure_cards")
        if not isinstance(cards, list):
            raise ValueError("active_procedure_cards must be an array")
        normalized_cards = tuple(_require_hash(card, "active_procedure_cards[]") for card in cards)
        if len(set(normalized_cards)) != len(normalized_cards):
            raise ValueError("active_procedure_cards must not contain duplicates")
        if tuple(sorted(normalized_cards)) != normalized_cards:
            raise ValueError("active_procedure_cards must be sorted")
        expected = build_doctrine_snapshot(
            system_prompt=system_prompt_hash,
            bootstrap=bootstrap_hash,
            active_procedure_cards=normalized_cards,
        )
        if expected.snapshot_id != snapshot_id:
            raise ValueError("doctrine snapshot content address mismatch")
        return cls(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            system_prompt_hash=system_prompt_hash,
            bootstrap_hash=bootstrap_hash,
            active_procedure_cards=normalized_cards,
        )


def build_doctrine_snapshot(
    *,
    system_prompt: str,
    bootstrap: str,
    active_procedure_cards: Sequence[Any],
) -> DoctrineSnapshot:
    """Build a deterministic snapshot from the three doctrine inputs.

    ``active_procedure_cards`` accepts full card strings or JSON-compatible
    card mappings. Each card is reduced to a full-content hash before being
    sorted and deduplicated; callers should pass card contents, not only
    mutable slugs, when they want edits to change the snapshot address.
    """
    if not isinstance(system_prompt, str) or not isinstance(bootstrap, str):
        raise TypeError("system_prompt and bootstrap must be strings")
    system_prompt_hash = _content_hash(system_prompt)
    bootstrap_hash = _content_hash(bootstrap)
    cards = _normalize_cards(active_procedure_cards)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "system_prompt_hash": system_prompt_hash,
        "bootstrap_hash": bootstrap_hash,
        "active_procedure_cards": list(cards),
    }
    return DoctrineSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=_content_hash(payload),
        system_prompt_hash=system_prompt_hash,
        bootstrap_hash=bootstrap_hash,
        active_procedure_cards=cards,
    )


__all__ = [
    "DoctrineSnapshot",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_doctrine_snapshot",
]
