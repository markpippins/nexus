"""Canonical Aspects v0.1 contract and fingerprint helpers (Aspect G6).

Aspects is the governed binding layer: it owns the governed tag vocabulary
and the bindings from Expression's projected tags into that vocabulary.
This module is the conformance boundary — it publishes the language-neutral
contract manifest, fingerprints it, and refuses authority drift on the
projected side (projected tags stay non-authoritative until a binding is
approved through the G2 lifecycle machine).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .binding_port import ALLOWED_TRANSITIONS, BINDING_STATUSES

ASPECTS_CONTRACT_REVISION = "aspects-v0.1"
ASPECTS_CONTRACT_FINGERPRINT_VERSION = 1

VOCABULARY_TABLE = "aspects.governed_tag_vocabulary"
BINDING_TABLE = "aspects.tag_binding"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def contract_manifest() -> dict[str, Any]:
    """Return the language-neutral Aspects contract manifest."""
    return {
        "contract_revision": ASPECTS_CONTRACT_REVISION,
        "fingerprint_version": ASPECTS_CONTRACT_FINGERPRINT_VERSION,
        "binding_owner": "aspects",
        "binding_statuses": sorted(BINDING_STATUSES),
        "binding_lifecycle": {
            status: sorted(nexts)
            for status, nexts in ALLOWED_TRANSITIONS.items()
        },
        "vocabulary_table": VOCABULARY_TABLE,
        "binding_table": BINDING_TABLE,
        "projected_tag_authority_status": "projected",
        "authority_boundary": {
            "aspects_owns": ["governed_tag_vocabulary", "tag_binding"],
            "expression_authority_status": "non_authoritative",
            "projected_tags_pre_populated_governed_tag_id": None,
            "binding_approval_required_for_governed_identity": True,
        },
    }


def contract_fingerprint() -> str:
    """Fingerprint the semantic manifest, not source formatting or file paths."""
    return _digest(contract_manifest())


def validate_projected_tag_input(observation: dict[str, Any]) -> list[str]:
    """Conformance check for a projected tag entering the binding layer.

    Mirrors the ProjectedTagObservationInput TypeSpec model: projected tags
    must arrive non-authoritative ('projected') with no pre-populated
    governed tag id — governed identity is only granted by an approved
    binding (review finding 1's escalation defect, enforced here at the
    governed boundary).
    """
    errors: list[str] = []
    for field in (
        "tag_observation_id",
        "source_identity",
        "source_revision",
        "tag_namespace",
        "key",
        "normalized_value",
        "basis",
    ):
        if not observation.get(field):
            errors.append(f"projected tag missing required field {field!r}")

    if observation.get("status") != "observed":
        errors.append("projected tag status must be 'observed'")

    authority = observation.get("authority_status")
    if authority != "projected":
        errors.append(
            f"projected tag authority_status must be 'projected', got {authority!r}"
        )

    if observation.get("governed_tag_id") is not None:
        errors.append(
            "projected tag must not carry a pre-populated governed_tag_id; "
            "governed identity is granted only by an approved binding"
        )

    if observation.get("kind") not in ("source_tag", "projected_metadata"):
        errors.append(
            f"projected tag kind must be source_tag|projected_metadata, "
            f"got {observation.get('kind')!r}"
        )
    return errors
