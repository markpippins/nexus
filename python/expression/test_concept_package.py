"""Tests for concept package on Expression IR."""

from __future__ import annotations

import json
from datetime import datetime

from expression.concept_package import (
    ConceptPackage,
    ConceptPackageFilter,
    create_concept_package,
    filter_expression_bundle,
    export_concept_package,
    import_concept_package,
    validate_concept_package,
    _package_fingerprint,
)


def test_create_concept_package_from_transcript():
    """Test creating a concept package from a transcript."""
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123 to production"},
            {"role": "assistant", "content": "PR #123 deployed successfully at v2.3.4"},
        ],
    }
    
    filter_spec = ConceptPackageFilter(
        concept_scope=["deployment"],
        member_kinds=["reference", "version"],
        tag_filters={"deployment": ["production"]},
    )
    
    pkg = create_concept_package(
        package_name="deployment-package",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={
            "transcript_id": "test-transcript-001",
            "turns": [
                {"role": "user", "content": "Deploy PR #123 to production"},
                {"role": "assistant", "content": "PR #123 deployed successfully at v2.3.4"},
            ],
        },
    )
    
    assert pkg.package_id
    assert pkg.package_name == "deployment-package"
    assert pkg.package_version == "1.0.0"
    assert pkg.package_revision
    assert pkg.filter_spec.concept_scope == ["deployment"]
    assert pkg.filter_spec.member_kinds == ["reference", "version"]
    assert pkg.expression_bundle is not None
    assert "observations" in pkg.expression_bundle
    assert pkg.fingerprint
    assert len(pkg.fingerprint) == 32


def test_create_concept_package_from_bundle():
    """Test creating a concept package from a pre-built expression bundle."""
    bundle = {
        "contract_revision": "expression-v0.1",
        "source_fingerprint": "abc123",
        "transcript_id": "test-transcript",
        "segments": [],
        "observations": [
            {"observation_id": "obs-1", "kind": "reference", "value": "PR #123"},
        ],
        "candidate_links": [],
        "proposition_candidates": [],
        "authority_status": "non_authoritative",
        "boundary": {},
    }
    
    filter_spec = ConceptPackageFilter(
        concept_scope=["deployment"],
        member_kinds=["reference"],
    )
    
    pkg = create_concept_package(
        package_name="deployment-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        expression_bundle=bundle,
    )
    
    assert pkg.package_name == "deployment-pkg"
    assert pkg.expression_bundle == bundle
    assert pkg.fingerprint


def test_concept_package_filtering():
    """Test filtering expression bundle by package filters."""
    from expression.concept_package import filter_expression_bundle, ConceptPackageFilter
    
    bundle = {
        "observations": [
            {"kind": "reference", "value": "PR #123"},
            {"kind": "version", "value": "v2.3.4"},
            {"kind": "speech_act", "value": "deployed"},
        ],
        "candidate_links": [],
        "proposition_candidates": [],
    }
    
    filter_spec = ConceptPackageFilter(
        member_kinds=["reference"],
    )
    
    filtered = filter_expression_bundle(bundle, filter_spec)
    
    assert len(filtered["observations"]) == 1
    assert filtered["observations"][0]["kind"] == "reference"


def test_concept_package_fingerprint():
    """Test concept package fingerprint is deterministic."""
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123"},
        ],
    }
    
    filter_spec = ConceptPackageFilter(
        concept_scope=["deployment"],
        member_kinds=["reference"],
    )
    
    pkg1 = create_concept_package(
        package_name="test-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
    )
    
    pkg2 = create_concept_package(
        package_name="test-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
    )
    
    assert pkg1.fingerprint == pkg2.fingerprint


def test_concept_package_export_import():
    """Test export/import round-trip."""
    from expression.concept_package import (
        create_concept_package,
        export_concept_package,
        import_concept_package,
        ConceptPackageFilter,
    )
    
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123"},
        ],
    }
    
    filter_spec = ConceptPackageFilter(
        concept_scope=["test"],
        member_kinds=["reference"],
    )
    
    pkg = create_concept_package(
        package_name="test-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
    )
    
    exported = export_concept_package(pkg)
    imported = import_concept_package(exported)
    
    assert imported.package_id == pkg.package_id
    assert imported.package_name == pkg.package_name
    assert imported.package_version == pkg.package_version
    assert imported.filter_spec.concept_scope == pkg.filter_spec.concept_scope
    assert imported.expression_bundle == pkg.expression_bundle
    assert imported.fingerprint == pkg.fingerprint


def test_concept_package_validation():
    """Test concept package validation."""
    from expression.concept_package import validate_concept_package, ConceptPackage, ConceptPackageFilter
    from datetime import datetime
    
    # Valid package
    pkg = ConceptPackage(
        package_id="test-pkg",
        package_name="test",
        package_version="1.0.0",
        package_revision="2024-01-01T00:00:00Z",
        filter_spec=ConceptPackageFilter(),
        expression_bundle={"observations": [], "candidate_links": [], "proposition_candidates": []},
        metadata_stream_id=None,
        created_at=datetime.utcnow(),
        boundary={},
        fingerprint="45920f16df44db9779bc6382b9a64a55",
    )
    
    from expression.concept_package import validate_concept_package
    errors = validate_concept_package(pkg)
    assert errors == []
    
    # Invalid - missing name
    pkg2 = ConceptPackage(
        package_id="test",
        package_name="",
        package_version="1.0.0",
        package_revision="now",
        filter_spec=ConceptPackageFilter(),
        expression_bundle={},
        metadata_stream_id=None,
        created_at=datetime.utcnow(),
        boundary={},
        fingerprint="abc",
    )
    errors = validate_concept_package(pkg2)
    assert len(errors) > 0
    assert any("name" in e.lower() for e in errors)
    
    # Invalid - missing version
    pkg3 = ConceptPackage(
        package_id="test",
        package_name="test",
        package_version="",
        package_revision="now",
        filter_spec=ConceptPackageFilter(),
        expression_bundle={},
        metadata_stream_id=None,
        created_at=datetime.utcnow(),
        boundary={},
        fingerprint="abc",
    )
    errors = validate_concept_package(pkg3)
    assert any("version" in e.lower() for e in errors)
    
    # Invalid - missing expression bundle
    pkg4 = ConceptPackage(
        package_id="test",
        package_name="test",
        package_version="1.0.0",
        package_revision="now",
        filter_spec=ConceptPackageFilter(),
        expression_bundle={},
        metadata_stream_id=None,
        created_at=datetime.utcnow(),
        boundary={},
        fingerprint="abc",
    )
    errors = validate_concept_package(pkg4)
    assert any("expression bundle" in e.lower() for e in errors)


def test_concept_package_fingerprint_deterministic():
    """Test that package fingerprint is deterministic."""
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123"},
        ],
    }
    
    filter_spec = ConceptPackageFilter(
        concept_scope=["deployment"],
        member_kinds=["reference"],
    )
    
    pkg1 = create_concept_package(
        package_name="test-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
    )
    
    pkg2 = create_concept_package(
        package_name="test-pkg",
        package_version="1.0.0",
        filter_spec=filter_spec,
        transcript={"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
    )
    
    assert pkg1.fingerprint == pkg2.fingerprint
    assert len(pkg1.fingerprint) == 32
