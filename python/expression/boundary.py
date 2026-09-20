"""Explicit compatibility and storage boundaries for Expression.

Expression is a derived, regenerable query surface over immutable transcript
assets. It may consume existing harvest/semantics/KG observations, but it does
not create a competing authority. Resolution remains canonical for identity,
lineage, dispositions, and governed evaluation joins.
"""

from __future__ import annotations

from typing import Any

COMPATIBILITY_MODES = {"consume", "converge", "supersede"}
STORAGE_LAYERS = {"source", "staging", "canonical", "projection"}


def expression_boundary() -> dict[str, Any]:
    """Return the versioned boundary declaration used by review bundles."""
    return {
        "boundary_revision": "expression-boundary-v0.1",
        "source_of_truth": "immutable_transcript_assets",
        "expression_role": "derived_query_surface",
        "observation_storage": "staging",
        "observation_regenerable": True,
        "canonical_identity_store": "resolution",
        "canonical_lineage_store": "resolution",
        "canonical_disposition_store": "resolution",
        "evaluation_authority": "solscript_resolution",
        "graph_role": "projection",
        "harvest_compatibility": "converge",
        "authority_status": "non_authoritative",
    }


def validate_boundary(bundle: dict[str, Any]) -> list[str]:
    """Return boundary violations instead of silently accepting drift."""
    errors: list[str] = []
    boundary = bundle.get("boundary", {})
    if boundary.get("observation_storage") not in STORAGE_LAYERS:
        errors.append("invalid observation storage layer")
    if boundary.get("observation_storage") != "staging":
        errors.append("Expression observations must remain staging material")
    if boundary.get("observation_regenerable") is not True:
        errors.append("Expression observations must be regenerable")
    if boundary.get("canonical_identity_store") != "resolution":
        errors.append("Resolution must remain canonical for identity")
    if boundary.get("canonical_lineage_store") != "resolution":
        errors.append("Resolution must remain canonical for lineage")
    if boundary.get("canonical_disposition_store") != "resolution":
        errors.append("Resolution must remain canonical for disposition")
    if boundary.get("authority_status") != "non_authoritative":
        errors.append("Expression cannot claim authority")
    mode = boundary.get("harvest_compatibility")
    if mode not in COMPATIBILITY_MODES:
        errors.append("invalid harvest compatibility mode")
    if mode == "supersede" and boundary.get("supersession_record") is None:
        errors.append("supersession requires an explicit compatibility record")
    return errors


def assert_boundary(bundle: dict[str, Any]) -> None:
    errors = validate_boundary(bundle)
    if errors:
        raise ValueError("invalid Expression boundary: " + "; ".join(errors))
