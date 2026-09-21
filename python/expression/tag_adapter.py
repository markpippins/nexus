"""Source-tag and metadata projection adapter for Expression.

This adapter does not create governed SOL tags. It produces projected,
source-owned observations that can be queried by a concept package and later
reviewed for governed binding.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable, Optional

from .boundary import expression_boundary

# Optional binding port integration
try:
    from aspects.binding_port import AspectsBindingPort, GovernedTag, TagBinding
    BINDING_PORT_AVAILABLE = True
except ImportError:
    BINDING_PORT_AVAILABLE = False
    AspectsBindingPort = None
    GovernedTag = None
    TagBinding = None

_TAG_RE = re.compile(r"[^a-z0-9._:-]+")


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_identity(record: dict[str, Any]) -> str | None:
    if record.get("source_identity"):
        return str(record["source_identity"])
    source_kind = record.get("source_kind") or record.get("kind") or "source"
    source_id = (
        record.get("source_record_id")
        or record.get("agent_record_id")
        or record.get("source_observation_id")
        or record.get("candidate_id")
        or record.get("id")
    )
    return f"{source_kind}:{source_id}" if source_id else None


def _normalize_tag(value: Any) -> str:
    return _TAG_RE.sub("-", str(value).strip().lower()).strip("-")


def _raw_tags(record: dict[str, Any]) -> list[tuple[str, Any]]:
    values = record.get("tags", [])
    if isinstance(values, dict):
        return [(str(key), value) for key, value in values.items()]
    if isinstance(values, (list, tuple, set)):
        return [("tag", value) for value in values]
    if values:
        return [("tag", values)]
    return []


def _metadata_items(record: dict[str, Any]) -> list[tuple[str, Any]]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return []
    return [(str(key), value) for key, value in sorted(metadata.items())]


def adapt_tag_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Project one source record's tags and metadata without governing them."""
    source_identity = _source_identity(record)
    if not source_identity:
        raise ValueError("tag source record has no stable source identity")
    source_revision = str(record.get("source_revision") or record.get("revision_id") or "unversioned")
    namespace = str(record.get("tag_namespace") or record.get("source_kind") or record.get("kind") or "source")
    observations: list[dict[str, Any]] = []

    for key, raw_value in [*_raw_tags(record), *_metadata_items(record)]:
        value = raw_value if isinstance(raw_value, (str, int, float, bool)) else raw_value
        normalized = _normalize_tag(value)
        if not normalized:
            continue
        kind = "source_tag" if key == "tag" else "projected_metadata"
        observation_basis = {
            "source_identity": source_identity,
            "source_revision": source_revision,
            "namespace": namespace,
            "key": key,
            "raw_value": value,
        }
        observations.append({
            "tag_observation_id": "tagobs:" + _digest(observation_basis)[:32],
            "source_identity": source_identity,
            "source_revision": source_revision,
            "namespace": namespace,
            "key": key,
            "raw_value": value,
            "normalized_value": normalized,
            "kind": kind,
            "status": "observed",
            "authority_status": "projected",
            "governed_tag_id": None,
            "basis": "source_record_tags" if kind == "source_tag" else "source_record_metadata",
            "provenance": {
                "source_identity": source_identity,
                "source_revision": source_revision,
                "source_schema": record.get("source_schema"),
                "source_content_hash": record.get("content_hash") or record.get("source_hash"),
            },
            "boundary": expression_boundary(),
        })
    return sorted(observations, key=lambda item: item["tag_observation_id"])


def adapt_tag_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic projected-tag bundle and preserve conflicts."""
    observations: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for record in records:
        for observation in adapt_tag_record(record):
            identity = (
                observation["source_identity"],
                observation["source_revision"],
                observation["namespace"],
                observation["key"],
                observation["normalized_value"],
            )
            key = "|".join(map(str, identity))
            prior = observations.get(key)
            if prior is None:
                observations[key] = observation
            elif prior["raw_value"] != observation["raw_value"]:
                conflicts.append({
                    "source_identity": observation["source_identity"],
                    "source_revision": observation["source_revision"],
                    "namespace": observation["namespace"],
                    "key": observation["key"],
                    "observation_ids": [prior["tag_observation_id"], observation["tag_observation_id"]],
                    "status": "conflict",
                    "reason": "same projected tag identity has different raw values",
                })

    return {
        "contract_revision": "expression-v0.1",
        "adapter_revision": "expression-tags-v0.1",
        "observations": sorted(observations.values(), key=lambda item: item["tag_observation_id"]),
        "conflicts": sorted(conflicts, key=lambda item: item["source_identity"]),
        "authority_status": "non_authoritative",
        "governed_tag_vocabulary_revision": None,
        "boundary": expression_boundary(),
    }


def attach_projected_tags(
    compatibility_bundle: dict[str, Any], tag_bundle: dict[str, Any]
) -> dict[str, Any]:
    """Attach projected tags by stable source identity, never by display name."""
    result = deepcopy(compatibility_bundle)
    source_ids = {item.get("source_identity") for item in result.get("observations", [])}
    attached: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for tag in tag_bundle.get("observations", []):
        if tag.get("source_identity") in source_ids:
            attached.append(tag)
        else:
            unresolved.append({
                "tag_observation_id": tag.get("tag_observation_id"),
                "source_identity": tag.get("source_identity"),
                "status": "unresolved",
                "reason": "no matching compatibility observation identity",
            })
    result["projected_tags"] = sorted(attached, key=lambda item: item["tag_observation_id"])
    result["tag_conflicts"] = tag_bundle.get("conflicts", [])
    result["unresolved_tag_observations"] = unresolved
    return result


# ── Governed Tag Binding (A3) ───────────────────────────────────────────

async def bind_projected_tags_to_governed(
    tag_bundle: dict[str, Any],
    binding_port,
    status: str = "proposed",
    bound_by: Optional[str] = None
) -> dict[str, Any]:
    """Bind Expression projected tags to governed vocabulary.
    
    Matches projected tags to governed vocabulary by normalized_name and
    creates tag bindings in the Aspects binding port.
    
    Args:
        tag_bundle: Output from adapt_tag_records() or attach_projected_tags()
        binding_port: AspectsBindingPort instance
        status: Binding status (proposed, approved, rejected)
        bound_by: Who/what created the binding
    
    Returns:
        Updated tag_bundle with governed_tag_id populated and binding metadata
    """
    if not BINDING_PORT_AVAILABLE:
        raise RuntimeError("Binding port not available - aspects package not installed")
    
    updated_bundle = deepcopy(tag_bundle)
    observations = updated_bundle.get("observations", [])
    
    # Get all active governed tags for matching
    governed_tags = await binding_port.list_governed_tags(active_only=True)
    governed_by_normalized = {tag.normalized_name: tag for tag in governed_tags}
    
    for obs in observations:
        normalized = obs.get("normalized_value")
        if not normalized:
            continue
        
        # Match by normalized value
        governed_tag = governed_by_normalized.get(normalized)
        if governed_tag:
            obs["governed_tag_id"] = str(governed_tag.id)
            obs["governed_tag_name"] = governed_tag.name
            obs["governed_tag_member_kind"] = governed_tag.member_kind
            obs["authority_status"] = "governed"
            
            # Create binding in Aspects
            try:
                await binding_port.create_binding(
                    governed_tag_id=governed_tag.id,
                    source_identity=obs["source_identity"],
                    source_revision=obs["source_revision"],
                    namespace=obs["namespace"],
                    tag_key=obs["key"],
                    normalized_value=obs["normalized_value"],
                    expression_observation_id=obs.get("tag_observation_id"),
                    status="proposed",
                    bound_by=bound_by or "auto-binding"
                )
                obs["binding_status"] = "proposed"
            except Exception as e:
                obs["binding_error"] = str(e)
                obs["binding_status"] = "error"
        else:
            obs["governed_tag_id"] = None
            obs["binding_status"] = "unmatched"
    
    # Update bundle metadata
    updated_bundle["governed_tag_vocabulary_revision"] = "v1"
    updated_bundle["authority_status"] = "mixed"  # some governed, some not
    return updated_bundle


def attach_projected_tags(
    compatibility_bundle: dict[str, Any], tag_bundle: dict[str, Any]
) -> dict[str, Any]:
    """Attach projected tags by stable source identity, never by display name."""
    result = deepcopy(compatibility_bundle)
    source_ids = {item.get("source_identity") for item in result.get("observations", [])}
    attached: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for tag in tag_bundle.get("observations", []):
        if tag.get("source_identity") in source_ids:
            attached.append(tag)
        else:
            unresolved.append({
                "tag_observation_id": tag.get("tag_observation_id"),
                "source_identity": tag.get("source_identity"),
                "status": "unresolved",
                "reason": "no matching compatibility observation identity",
            })
    result["projected_tags"] = sorted(attached, key=lambda item: item["tag_observation_id"])
    result["tag_conflicts"] = tag_bundle.get("conflicts", [])
    result["unresolved_tag_observations"] = unresolved
    return result
