"""Canonical Expression v0.1 contract and fingerprint helpers.

The extraction pipeline uses convenient internal dictionaries. This module is
the E1 boundary: it normalizes those dictionaries into one stable wire shape,
rejects authority drift, and fingerprints the semantic contract independently
of runtime implementation language.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .boundary import assert_boundary
from .taxonomy import EXPLICIT_KINDS, expected_observation_contract

CONTRACT_REVISION = "expression-v0.1"
CONTRACT_FINGERPRINT_VERSION = 1
ACTIVE_CANDIDATE_LINK_STATUSES = {"proposed", "ambiguous"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def contract_manifest() -> dict[str, Any]:
    """Return the language-neutral semantic contract manifest."""
    return {
        "contract_revision": CONTRACT_REVISION,
        "fingerprint_version": CONTRACT_FINGERPRINT_VERSION,
        "authority_status": "non_authoritative",
        "observation_storage": "staging",
        "observation_regenerable": True,
        "active_observation_kinds": sorted(EXPLICIT_KINDS),
        "active_candidate_link_statuses": sorted(ACTIVE_CANDIDATE_LINK_STATUSES),
        "candidate_predicate_status": "unresolved",
        "aspects_boundary": {
            "projected_tag_authority_status": "projected",
            "governed_tag_id": None,
            "binding_owner": "aspects",
        },
        "canonical_owners": {
            "identity": "resolution",
            "lineage": "resolution",
            "disposition": "resolution",
            "evaluation": "solscript_resolution",
        },
    }


def contract_fingerprint() -> str:
    """Fingerprint the semantic manifest, not source formatting or file paths."""
    return _digest(contract_manifest())


def _source_ref(source: dict[str, Any]) -> dict[str, Any]:
    required = ("segment_id", "transcript_id", "text_hash", "start_turn", "end_turn")
    missing = [field for field in required if field not in source]
    if missing:
        raise ValueError(f"source reference missing fields: {missing}")
    return {field: source[field] for field in required}


def _normalize_observation(observation: dict[str, Any]) -> dict[str, Any]:
    errors = expected_observation_contract(observation)
    if errors:
        raise ValueError("invalid Expression observation: " + "; ".join(errors))
    result = {
        "observation_id": observation["observation_id"],
        "kind": observation["kind"],
        "value": observation["value"],
        "source": _source_ref(observation["source"]),
        "extractor_revision": observation["extractor_revision"],
        "input_fingerprint": observation["input_fingerprint"],
        "disposition": observation["disposition"],
        "authority_status": observation["authority_status"],
    }
    for field in ("reference_kind", "verb"):
        if field in observation:
            result[field] = observation[field]
    return result


def _normalize_link(link: dict[str, Any]) -> dict[str, Any]:
    status = link.get("status")
    if status not in ACTIVE_CANDIDATE_LINK_STATUSES:
        raise ValueError(
            f"candidate link status {status!r} is not active in {CONTRACT_REVISION}; "
            "confirmation belongs to a governed Resolution/Aspects path"
        )
    return {
        "mention_id": link["mention_id"],
        "candidate_id": link["candidate_id"],
        "method": link["method"],
        "status": status,
        "evidence": list(link.get("evidence", [])),
    }


def _normalize_proposition(proposition: dict[str, Any]) -> dict[str, Any]:
    if proposition.get("status") != "candidate":
        raise ValueError("Expression v0.1 only carries candidate propositions")
    if proposition.get("predicate") != "mentions_decision_language":
        raise ValueError("Expression v0.1 predicate token is not in the active taxonomy")
    if proposition.get("governed_relation_id"):
        raise ValueError("Expression cannot claim a governed relation")
    return {
        "proposition_id": proposition["proposition_id"],
        "subject_ref": proposition["subject_ref"],
        "predicate": proposition["predicate"],
        "predicate_status": "unresolved",
        "object_ref": proposition.get("object_ref"),
        "source_observation_ids": list(proposition.get("source_observation_ids", [])),
        "modality": proposition["modality"],
        "status": "candidate",
        "required_read_set": proposition.get("required_read_set"),
    }


def canonicalize_tag_bundle(tag_bundle: dict[str, Any]) -> dict[str, Any]:
    """Normalize projected tags for the TypeSpec/Aspects boundary.

    The Python/Aspects adapter calls the field ``namespace``; TypeSpec uses
    ``tag_namespace`` because ``namespace`` is reserved in this context. The
    translation is explicit and remains non-authoritative.
    """
    if tag_bundle.get("authority_status") != "non_authoritative":
        raise ValueError("Expression tag projections must remain non_authoritative")
    observations = []
    for observation in tag_bundle.get("observations", []):
        if observation.get("authority_status") != "projected":
            raise ValueError("projected tag authority drift")
        if observation.get("governed_tag_id") is not None:
            raise ValueError("Expression cannot emit governed Aspects tag ids")
        observations.append({
            "tag_observation_id": observation["tag_observation_id"],
            "source_identity": observation["source_identity"],
            "source_revision": observation["source_revision"],
            "tag_namespace": observation["namespace"],
            "key": observation["key"],
            "raw_value": observation["raw_value"],
            "normalized_value": observation["normalized_value"],
            "kind": observation["kind"],
            "status": "observed",
            "authority_status": "projected",
            "governed_tag_id": None,
            "basis": observation["basis"],
            "provenance": observation.get("provenance", {}),
        })
    return {
        "contract_revision": tag_bundle.get("contract_revision", CONTRACT_REVISION),
        "adapter_revision": tag_bundle["adapter_revision"],
        "observations": sorted(observations, key=lambda item: item["tag_observation_id"]),
        "conflicts": tag_bundle.get("conflicts", []),
        "authority_status": "non_authoritative",
        "governed_tag_vocabulary_revision": None,
    }


def canonicalize_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Normalize a pipeline bundle to the E1 contract shape.

    This function is pure. It neither resolves identities nor contacts Aspects,
    Resolution, SOLScript, a database, or a graph service.
    """
    if bundle.get("contract_revision") != CONTRACT_REVISION:
        raise ValueError("unsupported Expression contract revision")
    if bundle.get("authority_status") != "non_authoritative":
        raise ValueError("Expression bundle must remain non_authoritative")
    assert_boundary(bundle)
    return {
        "contract_revision": CONTRACT_REVISION,
        "contract_fingerprint": contract_fingerprint(),
        "source_fingerprint": bundle["source_fingerprint"],
        "transcript_id": bundle["transcript_id"],
        "segments": [
            {
                "segment_id": segment["segment_id"],
                "transcript_id": segment["transcript_id"],
                "ordinal": segment["ordinal"],
                "role": segment.get("role"),
                "text": segment["text"],
                "text_hash": segment["text_hash"],
                "start_turn": segment["start_turn"],
                "end_turn": segment["end_turn"],
                "turn_count": segment["turn_count"],
                "boundary_reason": segment["boundary_reason"],
            }
            for segment in bundle.get("segments", [])
        ],
        "observations": [
            _normalize_observation(observation)
            for observation in bundle.get("observations", [])
        ],
        "candidate_links": [
            _normalize_link(link) for link in bundle.get("candidate_links", [])
        ],
        "proposition_candidates": [
            _normalize_proposition(proposition)
            for proposition in bundle.get("proposition_candidates", [])
        ],
        "authority_status": "non_authoritative",
        "boundary": bundle["boundary"],
    }
