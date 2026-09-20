"""Read-only adapter for existing harvest/semantics observations.

The adapter deliberately accepts records from both existing pipelines without
claiming that either record is canonical for Expression. It emits a stable
staging envelope that retains the original source identity and provenance.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .boundary import expression_boundary

SUPPORTED_KINDS = {"harvest", "harvest_candidate", "source_observation"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_id(record: dict[str, Any], kind: str | None = None) -> str | None:
    if kind == "harvest_candidate":
        keys = ("candidate_id", "id", "harvest_id")
    elif kind == "source_observation":
        keys = ("source_observation_id", "observation_id", "id")
    else:
        keys = ("harvest_id", "id", "source_observation_id")
    for key in keys:
        value = record.get(key)
        if value:
            return str(value)
    return None


def _kind(record: dict[str, Any]) -> str:
    explicit = record.get("source_kind") or record.get("kind") or record.get("asset_kind")
    if explicit in SUPPORTED_KINDS:
        return str(explicit)
    if record.get("harvest_id") and record.get("title"):
        return "harvest_candidate"
    if record.get("raw_location") or record.get("revision_id"):
        return "source_observation"
    return "harvest"


def adapt_observation(record: dict[str, Any]) -> dict[str, Any]:
    """Adapt one existing record without writing or resolving its identity."""
    kind = _kind(record)
    source_id = _source_id(record, kind)
    if not source_id:
        raise ValueError("source record has no stable source identity")

    content = record.get("content")
    if content is None:
        content = record.get("source_text")
    if content is None:
        content = record.get("intent_description")
    if content is None:
        content = record.get("raw_location") or record.get("title") or ""
    content = str(content)
    content_hash = str(record.get("content_hash") or record.get("source_hash") or _digest(content))
    provenance = {
        "source_kind": kind,
        "source_id": source_id,
        "source_schema": record.get("source_schema") or (
            "semantics.source_observation" if kind == "source_observation" else "nebula.harvests"
        ),
        "raw_location": record.get("raw_location"),
        "revision_id": record.get("revision_id"),
        "harvest_id": record.get("harvest_id"),
        "content_hash": content_hash,
    }
    observation_id = "compat:" + _digest(provenance)[:32]
    return {
        "observation_id": observation_id,
        "kind": "compatibility_observation",
        "value": content,
        "source": provenance,
        "source_identity": f"{kind}:{source_id}",
        "source_content_hash": content_hash,
        "disposition": "unreviewed",
        "authority_status": "non_authoritative",
        "boundary": expression_boundary(),
    }


def adapt_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic staging bundle from existing records.

    Duplicate source identities with the same content hash collapse to one
    observation. A source identity appearing with conflicting hashes is kept
    as a conflict observation, never silently overwritten.
    """
    by_identity: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for record in records:
        adapted = adapt_observation(record)
        identity = adapted["source_identity"]
        prior = by_identity.get(identity)
        if prior is None:
            by_identity[identity] = adapted
        elif prior["source_content_hash"] != adapted["source_content_hash"]:
            conflicts.append({
                "source_identity": identity,
                "observation_ids": [prior["observation_id"], adapted["observation_id"]],
                "status": "conflict",
                "reason": "same source identity has multiple content hashes",
            })
        # Same identity and hash is an idempotent duplicate; retain first record.

    observations = sorted(by_identity.values(), key=lambda item: item["observation_id"])
    return {
        "contract_revision": "expression-v0.1",
        "adapter_revision": "expression-compat-v0.1",
        "observations": observations,
        "conflicts": sorted(conflicts, key=lambda item: item["source_identity"]),
        "authority_status": "non_authoritative",
        "boundary": expression_boundary(),
    }
