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
from contextvars import ContextVar
from copy import deepcopy

import hashlib
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
    snapshot_pin: Optional["SnapshotPin"] = None


@dataclass
class SnapshotPin:
    """Immutable vocabulary/projection snapshot capture (Aspect G5).

    Captured from the projected tag bundle a package was built against.
    The filter_spec snapshot strings are derived from this pin, and both
    travel in the package fingerprint so drift is tamper-evident.
    """
    vocabulary_revision: Optional[str] = None
    vocabulary_fingerprint: Optional[str] = None
    projection_revision: Optional[str] = None
    projection_fingerprint: Optional[str] = None
    captured_at: Optional[datetime] = None


def _digest(value: str) -> str:
    """Deterministic SHA-256 digest (hex); callers truncate to 32 chars.

    G4: replaces the previous MD5 digest, whose comment claimed SHA-256 was
    non-deterministic on this system — hashlib is deterministic everywhere.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: Any) -> Any:
    """Recursively order-normalize lists/dicts for fingerprinting."""
    if isinstance(value, list):
        return sorted(
            (_normalized(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in sorted(value.items())}
    return value


_SNAPSHOT_PIN_SEPARATOR = ":"


def _pin_string(revision: Optional[str], fingerprint: Optional[str]) -> Optional[str]:
    """Render a snapshot pin as '<revision>:<fingerprint16>' (G5)."""
    if revision is None or fingerprint is None:
        return None
    return f"{revision}{_SNAPSHOT_PIN_SEPARATOR}{fingerprint[:16]}"


def capture_snapshot_context(
    tag_bundle: dict[str, Any],
    vocabulary_content: Optional[Any] = None,
) -> SnapshotPin:
    """Capture the immutable vocabulary/projection identity for a package (G5).

    The projection identity derives from the projected tag bundle the package
    was built against (contract/adapter revisions, observations, conflicts);
    the vocabulary identity derives from the governed vocabulary revision the
    bundle references. Pass ``vocabulary_content`` to fingerprint the actual
    vocabulary payload so content-level drift is caught even when the
    revision string is unchanged.
    """
    if not isinstance(tag_bundle, dict):
        raise ValueError("tag_bundle must be a projected tag bundle dict")

    projection_revision = tag_bundle.get("adapter_revision")
    if not projection_revision:
        raise ValueError("tag_bundle is missing adapter_revision; cannot pin projection")

    projection_payload = {
        "contract_revision": tag_bundle.get("contract_revision"),
        "adapter_revision": projection_revision,
        "observations": tag_bundle.get("observations", []),
        "conflicts": tag_bundle.get("conflicts", []),
        "authority_status": tag_bundle.get("authority_status"),
    }
    projection_fingerprint = _digest(json.dumps(projection_payload, sort_keys=True))[:32]

    vocabulary_revision = tag_bundle.get("governed_tag_vocabulary_revision")
    if vocabulary_content is not None:
        vocabulary_fingerprint = _digest(json.dumps(vocabulary_content, sort_keys=True))[:32]
    elif vocabulary_revision:
        vocabulary_fingerprint = _digest(str(vocabulary_revision))[:32]
    else:
        raise ValueError(
            "tag_bundle carries no governed_tag_vocabulary_revision; cannot pin vocabulary"
        )

    return SnapshotPin(
        vocabulary_revision=str(vocabulary_revision),
        vocabulary_fingerprint=vocabulary_fingerprint,
        projection_revision=str(projection_revision),
        projection_fingerprint=projection_fingerprint,
        captured_at=datetime.utcnow(),
    )


def _package_fingerprint(pkg: "ConceptPackage") -> str:
    """Canonical package fingerprint over package content.

    G4: this is the ONLY fingerprint computation. create_concept_package
    assigns via this same function, so a created package validates by
    construction, and any change to the filter spec, expression bundle, or
    metadata stream binding breaks the fingerprint (tamper-evident).

    Excludes identity and timestamps (package_id, package_revision,
    created_at, provenance); order-normalizes filter values so list
    ordering cannot change the fingerprint.
    """
    content = json.dumps({
        "filter_spec": {
            "concept_scope": _normalized(pkg.filter_spec.concept_scope),
            "member_kinds": _normalized(pkg.filter_spec.member_kinds),
            "tag_filters": _normalized(pkg.filter_spec.tag_filters),
            "metadata_filters": _normalized(pkg.filter_spec.metadata_filters),
            "inclusion_ledger": _normalized(pkg.filter_spec.inclusion_ledger),
            "vocabulary_snapshot": _normalized(pkg.filter_spec.vocabulary_snapshot),
            "projection_snapshot": _normalized(pkg.filter_spec.projection_snapshot),
            "vocabulary_snapshot": pkg.filter_spec.vocabulary_snapshot,
            "projection_snapshot": pkg.filter_spec.projection_snapshot,
            "relationship_limitations": pkg.filter_spec.relationship_limitations,
        },
        "snapshot_pin": {
            "vocabulary_revision": pkg.snapshot_pin.vocabulary_revision,
            "vocabulary_fingerprint": pkg.snapshot_pin.vocabulary_fingerprint,
            "projection_revision": pkg.snapshot_pin.projection_revision,
            "projection_fingerprint": pkg.snapshot_pin.projection_fingerprint,
        } if pkg.snapshot_pin is not None else None,
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
    tag_bundle: Optional[dict[str, Any]] = None,
    vocabulary_content: Optional[Any] = None,
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
    
    # G5: snapshot pinning. Pins are captured from the tag bundle the
    # package was built against; declaring pins without the bundle would
    # leave them unenforceable strings, so that fails closed.
    snapshot_pin: Optional[SnapshotPin] = None
    if tag_bundle is not None:
        snapshot_pin = capture_snapshot_context(tag_bundle, vocabulary_content=vocabulary_content)
        filter_spec.vocabulary_snapshot = _pin_string(
            snapshot_pin.vocabulary_revision, snapshot_pin.vocabulary_fingerprint
        )
        filter_spec.projection_snapshot = _pin_string(
            snapshot_pin.projection_revision, snapshot_pin.projection_fingerprint
        )
    elif filter_spec.vocabulary_snapshot or filter_spec.projection_snapshot:
        raise ValueError(
            "snapshot pins require the tag bundle they were captured from; "
            "pass tag_bundle=... so the pins can be enforced"
        )

    # G4: assign the canonical content fingerprint via the same
    # _package_fingerprint used by validate_concept_package, so a created
    # package validates by construction.
    pkg = ConceptPackage(
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
        fingerprint="",
        snapshot_pin=snapshot_pin,
    )
    pkg.fingerprint = _package_fingerprint(pkg)
    return pkg


def filter_expression_bundle(
    bundle: dict[str, Any],
    filter_spec: ConceptPackageFilter,
) -> dict[str, Any]:
    """Apply package filters to an expression bundle.

    G4: link/proposition filters consult the set of surviving observations
    so members referencing dropped observations are dropped too.
    """
    filtered = deepcopy(bundle)
    kept_ids = {
        obs.get("observation_id")
        for obs in filtered.get("observations", [])
        if _matches_filters(obs, filter_spec)
    }
    _kept_observation_ids.set(kept_ids)
    
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
    
    _kept_observation_ids.set(None)
    return filtered


_kept_observation_ids: ContextVar = ContextVar("kept_observation_ids", default=None)


def _matches_filters(observation: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    """Check if an observation matches the package filters (G4 semantics).

    Every specified filter must match (conjunction). Missing data fails
    closed: a scope-tagged package drops observations without a concept
    scope; metadata filters drop observations without the metadata key.
    """
    if filter_spec.member_kinds:
        if observation.get("kind") not in filter_spec.member_kinds:
            return False

    if filter_spec.concept_scope:
        scope_values = observation.get("concept_scope") or []
        if not set(filter_spec.concept_scope) & set(scope_values):
            return False

    if filter_spec.tag_filters:
        tags = observation.get("tags") or {}
        if isinstance(tags, list):
            mapped: dict[str, list[str]] = {}
            for tag in tags:
                key, sep, val = str(tag).partition(":")
                if sep:
                    mapped.setdefault(key, []).append(val)
                else:
                    mapped.setdefault(str(tag), []).append(str(tag))
            tags = mapped
        elif isinstance(tags, dict):
            tags = {k: v if isinstance(v, list) else [v] for k, v in tags.items()}
        for key, allowed in filter_spec.tag_filters.items():
            values = tags.get(key, [])
            if not set(allowed) & set(str(v) for v in values):
                return False

    if filter_spec.metadata_filters:
        metadata = observation.get("metadata") or {}
        if not isinstance(metadata, dict):
            return False
        for key, allowed in filter_spec.metadata_filters.items():
            if key not in metadata:
                return False
            value = metadata[key]
            value_list = value if isinstance(value, list) else [value]
            if not set(str(v) for v in value_list) & set(str(v) for v in allowed):
                return False

    if filter_spec.inclusion_ledger:
        if observation.get("observation_id") not in filter_spec.inclusion_ledger:
            return False

    return True


def _item_matches_tag_or_metadata_filters(
    item: dict[str, Any], filter_spec: ConceptPackageFilter
) -> bool:
    """Apply tag/metadata filters to links and propositions directly.

    Derived members are constrained by their own tag/metadata fields only
    when they actually carry them; otherwise membership is governed by the
    referential integrity of their source observations.
    """
    tag_spec = ConceptPackageFilter(
        tag_filters=filter_spec.tag_filters if item.get("tags") else None,
        metadata_filters=filter_spec.metadata_filters if item.get("metadata") else None,
    )
    return _matches_filters(item, tag_spec)


def _link_matches_filters(link: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    """Links survive only if their source observations all survive
    (referential integrity) and they match any tag/metadata filters."""
    kept = _kept_observation_ids.get()
    if kept is not None and set(link.get("source_observation_ids", [])) - kept:
        return False
    return _item_matches_tag_or_metadata_filters(link, filter_spec)


def _proposition_matches_filters(prop: dict[str, Any], filter_spec: ConceptPackageFilter) -> bool:
    """Propositions survive only if their source observations all survive
    (referential integrity) and they match any tag/metadata filters."""
    kept = _kept_observation_ids.get()
    if kept is not None and set(prop.get("source_observation_ids", [])) - kept:
        return False
    return _item_matches_tag_or_metadata_filters(prop, filter_spec)


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
        "snapshot_pin": {
            "vocabulary_revision": pkg.snapshot_pin.vocabulary_revision,
            "vocabulary_fingerprint": pkg.snapshot_pin.vocabulary_fingerprint,
            "projection_revision": pkg.snapshot_pin.projection_revision,
            "projection_fingerprint": pkg.snapshot_pin.projection_fingerprint,
            "captured_at": pkg.snapshot_pin.captured_at.isoformat() + "Z"
                if isinstance(pkg.snapshot_pin.captured_at, datetime) else pkg.snapshot_pin.captured_at,
        } if pkg.snapshot_pin is not None else None,
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
    
    pin_data = data.get("snapshot_pin")
    snapshot_pin = SnapshotPin(
        vocabulary_revision=pin_data.get("vocabulary_revision"),
        vocabulary_fingerprint=pin_data.get("vocabulary_fingerprint"),
        projection_revision=pin_data.get("projection_revision"),
        projection_fingerprint=pin_data.get("projection_fingerprint"),
        captured_at=(
            datetime.fromisoformat(pin_data["captured_at"].replace("Z", "+00:00"))
            if pin_data.get("captured_at") else None
        ),
    ) if pin_data else None

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
        snapshot_pin=snapshot_pin,
    )
    return pkg


def _verify_snapshot_pins(pkg: "ConceptPackage") -> list[str]:
    """Verify the package's pins against its captured snapshot context (G5)."""
    errors: list[str] = []
    pin = pkg.snapshot_pin
    if pin is None:
        if pkg.filter_spec.vocabulary_snapshot or pkg.filter_spec.projection_snapshot:
            errors.append(
                "snapshot pins present without captured snapshot context; "
                "recreate the package with tag_bundle=..."
            )
        return errors

    expected_vocab = _pin_string(pin.vocabulary_revision, pin.vocabulary_fingerprint)
    expected_proj = _pin_string(pin.projection_revision, pin.projection_fingerprint)
    if expected_vocab is not None and pkg.filter_spec.vocabulary_snapshot != expected_vocab:
        errors.append(
            "vocabulary snapshot pin does not match captured snapshot context"
        )
    if expected_proj is not None and pkg.filter_spec.projection_snapshot != expected_proj:
        errors.append(
            "projection snapshot pin does not match captured snapshot context"
        )
    return errors


def verify_snapshots(
    pkg: "ConceptPackage",
    tag_bundle: Optional[dict[str, Any]] = None,
    vocabulary_content: Optional[Any] = None,
) -> list[str]:
    """Re-verify a package's snapshot pins (Aspect G5).

    Without a tag bundle: verifies pin-shape integrity (pins must match the
    captured snapshot context). With a tag bundle: re-captures the context
    and reports drift when the live projection/vocabulary identity no longer
    matches the pinned one.
    """
    errors = _verify_snapshot_pins(pkg)
    if errors:
        return errors

    if tag_bundle is None:
        return errors

    pin = pkg.snapshot_pin
    if pin is None:
        return ["package has no snapshot pin to verify against the tag bundle"]

    live = capture_snapshot_context(tag_bundle, vocabulary_content=vocabulary_content)
    if live.vocabulary_fingerprint != pin.vocabulary_fingerprint:
        errors.append(
            "snapshot drift: live vocabulary fingerprint "
            f"{live.vocabulary_fingerprint} != pinned {pin.vocabulary_fingerprint}"
        )
    if live.projection_fingerprint != pin.projection_fingerprint:
        errors.append(
            "snapshot drift: live projection fingerprint "
            f"{live.projection_fingerprint} != pinned {pin.projection_fingerprint}"
        )
    return errors


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

    # G5: verify snapshot pins against the captured snapshot context
    errors.extend(_verify_snapshot_pins(pkg))
    
    # Validate expression bundle structure
    bundle = pkg.expression_bundle
    for key in ["observations", "candidate_links", "proposition_candidates"]:
        if key not in bundle:
            errors.append(f"Expression bundle missing {key}")
    
    return errors
