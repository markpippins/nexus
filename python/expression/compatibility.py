"""Read-only compatibility adapters for existing Nexus source surfaces.

Expression consumes source-owned records as regenerable staging material. This
module never writes, resolves canonical identity, or promotes authority. It
preserves source identity first, uses aliases only for proposed links, and
surfaces ambiguity, hash drift, missing identity, and supersession explicitly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .boundary import expression_boundary

SUPPORTED_KINDS = {"harvest", "harvest_candidate", "source_observation", "cross_reference"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _kind(record: dict[str, Any]) -> str:
    explicit = record.get("source_kind") or record.get("kind")
    if explicit in SUPPORTED_KINDS:
        return str(explicit)
    schema = str(record.get("source_schema") or "")
    if schema.endswith("harvest_candidates"):
        return "harvest_candidate"
    if schema.endswith("source_observation"):
        return "source_observation"
    if schema.endswith("cross_references"):
        return "cross_reference"
    if record.get("candidate_id") or (record.get("harvest_id") and record.get("title")):
        return "harvest_candidate"
    if record.get("source_observation_id") or record.get("raw_location") or record.get("revision_id"):
        return "source_observation"
    return "harvest"


def _source_id(record: dict[str, Any], kind: str | None = None) -> str | None:
    kind = kind or _kind(record)
    keys = {
        "harvest_candidate": ("candidate_id", "id"),
        "source_observation": ("source_observation_id", "observation_id", "id"),
        "cross_reference": ("cross_reference_id", "id"),
        "harvest": ("harvest_id", "id"),
    }[kind]
    for key in keys:
        value = record.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def _source_schema(record: dict[str, Any], kind: str) -> str:
    return str(record.get("source_schema") or {
        "harvest": "nebula.harvests",
        "harvest_candidate": "nebula.harvest_candidates",
        "source_observation": "semantics.source_observation",
        "cross_reference": "nebula.cross_references",
    }[kind])


def _content(record: dict[str, Any]) -> str:
    for key in ("content", "source_text", "intent_description", "raw_location", "title"):
        if record.get(key) is not None:
            return str(record[key])
    return ""


def _content_hash(record: dict[str, Any], content: str) -> str:
    supplied = record.get("content_hash") or record.get("source_hash")
    return str(supplied) if supplied else _digest(content)


def _source_revision(record: dict[str, Any]) -> str:
    return str(record.get("source_revision") or record.get("revision_id") or record.get("revision") or "unversioned")


def _identity(kind: str, source_id: str) -> str:
    return f"{kind}:{source_id}"


def _related_identity(record: dict[str, Any], field: str, kind: str) -> str | None:
    value = record.get(field)
    return _identity(kind, str(value)) if value is not None and str(value) else None


def adapt_observation(record: dict[str, Any]) -> dict[str, Any]:
    """Adapt one source record while retaining source-of-record identity."""
    kind = _kind(record)
    source_id = _source_id(record, kind)
    if not source_id:
        raise ValueError("source record has no stable source identity")
    content = _content(record)
    content_hash = _content_hash(record, content)
    source_identity = _identity(kind, source_id)
    provenance = {
        "source_kind": kind,
        "source_id": source_id,
        "source_identity": source_identity,
        "source_schema": _source_schema(record, kind),
        "raw_location": record.get("raw_location"),
        "revision_id": record.get("revision_id"),
        "source_revision": _source_revision(record),
        "harvest_id": record.get("harvest_id"),
        "content_hash": content_hash,
    }
    supersedes = (
        _related_identity(record, "supersedes_id", kind)
        or _related_identity(record, "supersedes", kind)
        or _related_identity(record, "superseded_source_id", kind)
    )
    return {
        "observation_id": "compat:" + _digest(provenance)[:32],
        "kind": "compatibility_observation",
        "value": content,
        "source": provenance,
        "source_identity": source_identity,
        "source_content_hash": content_hash,
        "source_revision": _source_revision(record),
        "source_status": record.get("status"),
        "disposition": "unreviewed",
        "authority_status": "non_authoritative",
        "supersedes_source_identity": supersedes,
        "boundary": expression_boundary(),
    }


def _identity_link_candidates(observations: list[dict[str, Any]], identity_material: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_source: dict[str, list[str]] = {}
    by_alias: dict[str, list[str]] = {}
    for item in identity_material:
        identity_id = item.get("identity_id") or item.get("canonical_id") or item.get("id")
        if not identity_id:
            continue
        identity_id = str(identity_id)
        source_identity = item.get("source_identity")
        if not source_identity and item.get("source_kind") and item.get("source_id"):
            source_identity = _identity(str(item["source_kind"]), str(item["source_id"]))
        if source_identity:
            by_source.setdefault(str(source_identity), []).append(identity_id)
        for alias in item.get("aliases", []) or []:
            by_alias.setdefault(str(alias).strip().lower(), []).append(identity_id)

    links: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for observation in observations:
        stable = sorted(set(by_source.get(observation["source_identity"], [])))
        if len(stable) == 1:
            links.append({"mention_id": observation["observation_id"], "candidate_id": stable[0], "method": "stable_source_id", "status": "proposed", "evidence": [observation["source_identity"]]})
            continue
        aliases = sorted(set(by_alias.get(observation["value"].strip().lower(), [])))
        if len(aliases) == 1:
            links.append({"mention_id": observation["observation_id"], "candidate_id": aliases[0], "method": "registered_alias", "status": "proposed", "evidence": [observation["value"]]})
        elif len(aliases) > 1 or len(stable) > 1:
            links.append({"mention_id": observation["observation_id"], "candidate_id": None, "method": "identity_collision", "status": "ambiguous", "evidence": aliases or stable})
        else:
            unresolved.append({"mention_id": observation["observation_id"], "source_identity": observation["source_identity"], "status": "unresolved", "reason": "no stable identity or unique registered alias"})
    return links, unresolved


def adapt_records(records: Iterable[dict[str, Any]], *, identity_material: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Build staging material and preserve all identity/lineage uncertainty."""
    by_identity: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []
    for record in records:
        try:
            adapted = adapt_observation(record)
        except ValueError as exc:
            invalid_records.append({"status": "unresolved", "reason": str(exc), "record": record})
            continue
        identity = adapted["source_identity"]
        prior = by_identity.get(identity)
        if prior is None:
            by_identity[identity] = adapted
        elif prior["source_content_hash"] != adapted["source_content_hash"]:
            conflicts.append({"source_identity": identity, "observation_ids": [prior["observation_id"], adapted["observation_id"]], "hashes": [prior["source_content_hash"], adapted["source_content_hash"]], "status": "conflict", "reason": "same source identity has multiple content hashes"})

    observations = sorted(by_identity.values(), key=lambda item: item["observation_id"])
    links, unresolved = _identity_link_candidates(observations, identity_material)
    supersession_links: list[dict[str, Any]] = []
    known = set(by_identity)
    for observation in observations:
        older = observation.get("supersedes_source_identity")
        if older:
            supersession_links.append({"newer_source_identity": observation["source_identity"], "older_source_identity": older, "status": "declared" if older in known else "dangling", "evidence_observation_id": observation["observation_id"]})

    return {
        "contract_revision": "expression-v0.1",
        "adapter_revision": "expression-compat-v0.2",
        "observations": observations,
        "identity_candidates": links,
        "unresolved_identities": unresolved + invalid_records,
        "conflicts": sorted(conflicts, key=lambda item: item["source_identity"]),
        "supersession_links": sorted(supersession_links, key=lambda item: item["newer_source_identity"]),
        "authority_status": "non_authoritative",
        "boundary": expression_boundary(),
    }
