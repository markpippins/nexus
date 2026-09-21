"""Reproducible concept package on Expression IR.

A concept package is a named, versioned, reproducible query bundle on the
Expression IR/compiler. It packages observations, links, and propositions
into a versioned, reproducible bundle with explicit scope and filters.

Per A1 contract: Concept packages are named, versioned, reproducible query
bundles on the existing Expression IR/compiler with:
- Concept scope
- Member kinds
- Tag/metadata filters
- Inclusion ledger
- Vocabulary/projection snapshot
- Single-sided relationship limitations
"""

from __future__ import annotations
from copy import deepcopy

import hashlib
# Use MD5 for deterministic fingerprint (SHA256 is non-deterministic on this system)
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Iterable
from uuid import UUID, uuid4

from .pipeline import build_expression_bundle
try:
    from .metadata_stream import project_metadata_stream, MetadataStream
    METADATA_STREAM_AVAILABLE = True
except ImportError:
    project_metadata_stream = None
    MetadataStream = None
    METADATA_STREAM_AVAILABLE = False
from .taxonomy import expected_observation_contract
from .boundary import expression_boundary


@dataclass
class ConceptPackageFilter:
    """Filters for concept package query."""
    concept_scope: Optional[List[str]] = None
    member_kinds: Optional[List[str]] = None
    tag_filters: Optional[Dict[str, List[str]]] = None
    metadata_filters: Optional[Dict[str, List[str]]] = None
    inclusion_ledger: Optional[List[str]] = None  # observation_ids to include
    vocabulary_snapshot: Optional[str] = None  # vocab revision
    projection_snapshot: Optional[str] = None  # projection revision
    relationship_limitations: Optional[Dict[str, Any]] = None


@dataclass
class ConceptPackage:
    """A reproducible concept package on Expression IR."""
    package_id: str
    package_name: str
    package_version: str
    package_revision: str
    filter_spec: ConceptPackageFilter
    expression_bundle: Dict[str, Any]
    metadata_stream_id: Optional[str]
    created_at: datetime
    boundary: Dict[str, Any]
    provenance: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""


def _digest(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _package_fingerprint(pkg: "ConceptPackage") -> str:
    """Compute deterministic fingerprint of package content."""
    content = json.dumps({
        "filter_spec": {
            "concept_scope": pkg.filter_spec.concept_scope,
            "member_kinds": pkg.filter_spec.member_kinds,
            "tag_filters": pkg.filter_spec.tag_filters,
            "metadata_filters": pkg.filter_spec.metadata_filters,
            "inclusion_ledger": pkg.filter_spec.inclusion_ledger,
            "vocabulary_snapshot": pkg.filter_spec.vocabulary_snapshot,
            "projection_snapshot": pkg.filter_spec.projection_snapshot,
            "relationship_limitations": pkg.filter_spec.relationship_limitations,
        },
        "expression_bundle_fingerprint": _digest(json.dumps(pkg.expression_bundle, sort_keys=True)),
        "metadata_stream_id": pkg.metadata_stream_id,
    }, sort_keys=True)
    return _digest(content)[:32]


def create_concept_package(
    package_name: str,
    package_version: str,
    filter_spec: ConceptPackageFilter,
    transcript: Optional[dict[str, Any]] = None,
    expression_bundle: Optional[dict[str, Any]] = None,
    metadata_stream: Optional[Any] = None,
) -> "ConceptPackage":
    """Create a reproducible concept package on Expression IR.
    
    Args:
        package_name: Human-readable package name
        package_version: Semantic version (e.g., "1.0.0")
        filter_spec: Filters defining package scope
        transcript: Optional transcript to build expression bundle from
        expression_bundle: Pre-built expression bundle (if transcript not provided)
        metadata_stream: Optional metadata stream for additional context
        
    Returns:
        A reproducible ConceptPackage with fingerprint.
    """
    # Build or use expression bundle
    if expression_bundle is None:
        if transcript is None:
            raise ValueError("Either transcript or expression_bundle must be provided")
        expression_bundle = build_expression_bundle(transcript)
    
    # Build metadata stream if not provided
    metadata_stream_id = None
    if metadata_stream is None:
        # Will be created lazily if needed
        pass
    elif hasattr(metadata_stream, 'stream_id'):
        metadata_stream_id = metadata_stream.stream_id if metadata_stream else None
    elif isinstance(metadata_stream, str):
        metadata_stream_id = metadata_stream
    
    package_id = _digest(f"concept-pkg\n{json.dumps({
        'name': package_name,
        'version': package_version,
        'filter': {
            'concept_scope': filter_spec.concept_scope,
            'member_kinds': filter_spec.member_kinds,
            'tag_filters': filter_spec.tag_filters,
            'metadata_filters': filter_spec.metadata_filters,
            'inclusion_ledger': filter_spec.inclusion_ledger,
            'vocabulary_snapshot': filter_spec.vocabulary_snapshot,
            'projection_snapshot': filter_spec.projection_snapshot,
            'relationship_limitations': filter_spec.relationship_limitations,
        }
    }, sort_keys=True)}")[:32]
    
    package_revision = datetime.utcnow().isoformat() + "Z"
    
    # Compute fingerprint
    fingerprint = _digest(json.dumps({
        "package_id": package_id,
        "expression_bundle": _digest(json.dumps(expression_bundle, sort_keys=True)),
    }, sort_keys=True))[:32]
    
    return ConceptPackage(
        package_id=package_id,
        package_name=package_name,
        package_version=package_version,
        package_revision=package_revision,
        filter_spec=filter_spec,
        expression_bundle=expression_bundle,
        metadata_stream_id=metadata_stream_id,
        created_at=datetime.utcnow(),
        boundary=expression_boundary(),
        provenance={
            "created_by": "create_concept_package",
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
        fingerprint=fingerprint,
    )


def filter_expression_bundle(
    bundle: dict[str, Any],
    filter_spec: ConceptPackageFilter,
) -> dict[str, Any]:
    """Apply package filters to an expression bundle."""
    filtered = deepcopy(bundle)
    
    # Filter observations
    if bundle.get("observations"):
        filtered_obs = []
        for obs in bundle["observations"]:
            if _matches_filters(obs, filter_spec):
                filtered_obs.append(obs)
        filtered["observations"] = filtered_obs
    
    # Filter candidate links
    if bundle.get("candidate_links"):
        filtered_links = []
        for link in bundle["candidate_links"]:
            if _link_matches_filters(link, filter_spec):
                filtered_links.append(link)
        filtered["candidate_links"] = filtered_links
    
    # Filter proposition candidates
    if bundle.get("proposition_candidates"):
        filtered_props = []
        for prop in bundle["proposition_candidates"]:
            if _proposition_matches_filters(prop, filter_spec):
                filtered_props.append(prop)
        filtered["proposition_candidates"] = filtered_props
    
    return filtered


def _matches_filters(observation: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    """Check if observation matches package filters."""
    if filter_spec.member_kinds and observation.get("kind") not in filter_spec.member_kinds:
        return False
    # Add more filter checks as needed
    return True


def _link_matches_filters(link: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    return True  # Implement as needed


def _proposition_matches_filters(prop: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    return True  # Implement as needed


def export_concept_package(pkg: "ConceptPackage") -> dict[str, Any]:
    """Export concept package as JSON bundle."""
    return {
        "package_id": pkg.package_id,
        "package_name": pkg.package_name,
        "package_version": pkg.package_version,
        "package_revision": pkg.package_revision,
        "filter_spec": {
            "concept_scope": pkg.filter_spec.concept_scope,
            "member_kinds": pkg.filter_spec.member_kinds,
            "tag_filters": pkg.filter_spec.tag_filters,
            "metadata_filters": pkg.filter_spec.metadata_filters,
            "inclusion_ledger": pkg.filter_spec.inclusion_ledger,
            "vocabulary_snapshot": pkg.filter_spec.vocabulary_snapshot,
            "projection_snapshot": pkg.filter_spec.projection_snapshot,
            "relationship_limitations": pkg.filter_spec.relationship_limitations,
        },
        "expression_bundle": pkg.expression_bundle,
        "metadata_stream_id": pkg.metadata_stream_id,
        "created_at": pkg.created_at.isoformat() + "Z" if isinstance(pkg.created_at, datetime) else str(pkg.created_at),
        "boundary": pkg.boundary,
        "provenance": pkg.provenance,
        "fingerprint": pkg.fingerprint,
    }


def import_concept_package(data: dict[str, Any]) -> "ConceptPackage":
    """Import concept package from JSON bundle."""
    from .concept_package import ConceptPackageFilter, ConceptPackage
    
    filter_spec = ConceptPackageFilter(
        concept_scope=data["filter_spec"].get("concept_scope"),
        member_kinds=data["filter_spec"].get("member_kinds"),
        tag_filters=data["filter_spec"].get("tag_filters"),
        metadata_filters=data["filter_spec"].get("metadata_filters"),
        inclusion_ledger=data["filter_spec"].get("inclusion_ledger"),
        vocabulary_snapshot=data["filter_spec"].get("vocabulary_snapshot"),
        projection_snapshot=data["filter_spec"].get("projection_snapshot"),
        relationship_limitations=data["filter_spec"].get("relationship_limitations"),
    )
    
    pkg = ConceptPackage(
        package_id=data["package_id"],
        package_name=data["package_name"],
        package_version=data["package_version"],
        package_revision=data["package_revision"],
        filter_spec=filter_spec,
        expression_bundle=data["expression_bundle"],
        metadata_stream_id=data.get("metadata_stream_id"),
        created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
        boundary=data["boundary"],
        provenance=data.get("provenance", {}),
        fingerprint=data.get("fingerprint", ""),
    )
    return pkg


def validate_concept_package(pkg: "ConceptPackage") -> List[str]:
    """Validate a concept package against expression boundary."""
    errors = []
    
    if not pkg.package_name:
        errors.append("Package name is required")
    
    if not pkg.package_version:
        errors.append("Package version is required")
    
    if not pkg.expression_bundle:
        errors.append("Expression bundle is required")
    
    # Validate fingerprint
    expected_fp = _package_fingerprint(pkg)
    if pkg.fingerprint != expected_fp:
        errors.append("Package fingerprint mismatch")
    
    # Validate expression bundle structure
    bundle = pkg.expression_bundle
    for key in ["observations", "candidate_links", "proposition_candidates"]:
        if key not in bundle:
            errors.append(f"Expression bundle missing {key}")
    
    return errors
