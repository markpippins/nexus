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
    
    # Valid package (fingerprint assigned canonically — G4)
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
        fingerprint="",
    )
    pkg.fingerprint = _package_fingerprint(pkg)
    
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


# --- G4 regression tests (review record bc724f6b, finding 6) ---


def _g4_filter(**overrides) -> ConceptPackageFilter:
    defaults = dict(
        concept_scope=["deployment"],
        member_kinds=["reference"],
        tag_filters={"deployment": ["production"]},
    )
    defaults.update(overrides)
    return ConceptPackageFilter(**defaults)


def _g4_bundle() -> dict:
    return {
        "observations": [
            {
                "observation_id": "obs-1",
                "kind": "reference",
                "value": "PR #123",
                "tags": {"deployment": ["production"]},
                "metadata": {"env": "prod"},
                "concept_scope": ["deployment"],
            },
            {
                "observation_id": "obs-2",
                "kind": "version",
                "value": "v2.3.4",
                "tags": {"deployment": ["staging"]},
                "metadata": {"env": "staging"},
                "concept_scope": ["deployment"],
            },
            {
                "observation_id": "obs-3",
                "kind": "reference",
                "value": "PR #456",
            },
        ],
        "candidate_links": [
            {"link_id": "link-1", "source_observation_ids": ["obs-1"]},
            {"link_id": "link-2", "source_observation_ids": ["obs-2"]},
            {"link_id": "link-3", "source_observation_ids": ["obs-1", "obs-2"]},
        ],
        "proposition_candidates": [
            {"proposition_id": "prop-1", "source_observation_ids": ["obs-1"]},
            {"proposition_id": "prop-2", "source_observation_ids": ["obs-2"]},
        ],
    }


def test_g4_created_package_validates_clean():
    """Review finding 6 reproduction: create then validate passes."""
    pkg = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(),
        expression_bundle=_g4_bundle(),
    )
    assert pkg.fingerprint == _package_fingerprint(pkg)
    assert validate_concept_package(pkg) == []


def test_g4_bundle_tamper_breaks_fingerprint():
    pkg = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(),
        expression_bundle=_g4_bundle(),
    )
    pkg.expression_bundle["observations"][0]["value"] = "tampered"
    errors = validate_concept_package(pkg)
    assert any("fingerprint mismatch" in e.lower() for e in errors)


def test_g4_filter_tamper_breaks_fingerprint():
    pkg = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(),
        expression_bundle=_g4_bundle(),
    )
    pkg.filter_spec.member_kinds = ["version"]
    errors = validate_concept_package(pkg)
    assert any("fingerprint mismatch" in e.lower() for e in errors)


def test_g4_fingerprint_order_insensitive():
    a = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(member_kinds=["reference", "version"]),
        expression_bundle=_g4_bundle(),
    )
    b = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(member_kinds=["version", "reference"]),
        expression_bundle=_g4_bundle(),
    )
    assert a.fingerprint == b.fingerprint


def test_g4_digest_is_sha256():
    import hashlib
    from expression.concept_package import _digest

    assert _digest("x") == hashlib.sha256(b"x").hexdigest()


def test_g4_export_import_round_trip_validates_clean():
    pkg = create_concept_package(
        package_name="g4-pkg",
        package_version="1.0.0",
        filter_spec=_g4_filter(),
        expression_bundle=_g4_bundle(),
    )
    imported = import_concept_package(export_concept_package(pkg))
    assert imported.fingerprint == pkg.fingerprint
    assert validate_concept_package(imported) == []


def test_g4_member_kinds_scope_and_tag_filters_apply():
    filtered = filter_expression_bundle(_g4_bundle(), _g4_filter())
    obs_ids = [o["observation_id"] for o in filtered["observations"]]
    # obs-1: reference + deployment scope + production tag -> kept
    # obs-2: version kind -> dropped by member_kinds
    # obs-3: reference but no scope/tag -> dropped fail-closed
    assert obs_ids == ["obs-1"]


def test_g4_links_and_props_drop_dangling_sources():
    filtered = filter_expression_bundle(_g4_bundle(), _g4_filter())
    link_ids = [l["link_id"] for l in filtered["candidate_links"]]
    # link-1 (obs-1 kept) survives; link-2 and link-3 reference dropped obs-2
    assert link_ids == ["link-1"]
    prop_ids = [p["proposition_id"] for p in filtered["proposition_candidates"]]
    assert prop_ids == ["prop-1"]


def test_g4_inclusion_ledger_restricts_observations():
    ledger_only = filter_expression_bundle(
        _g4_bundle(), ConceptPackageFilter(inclusion_ledger=["obs-1", "obs-3"])
    )
    assert [o["observation_id"] for o in ledger_only["observations"]] == ["obs-1", "obs-3"]

    combined = filter_expression_bundle(
        _g4_bundle(),
        ConceptPackageFilter(concept_scope=["deployment"], inclusion_ledger=["obs-1", "obs-3"]),
    )
    # obs-3 has no concept_scope -> dropped fail-closed even though ledgered
    assert [o["observation_id"] for o in combined["observations"]] == ["obs-1"]


def test_g4_metadata_filters_fail_closed():
    prod = filter_expression_bundle(
        _g4_bundle(), ConceptPackageFilter(metadata_filters={"env": ["prod"]})
    )
    assert [o["observation_id"] for o in prod["observations"]] == ["obs-1"]

    missing_key = filter_expression_bundle(
        _g4_bundle(), ConceptPackageFilter(metadata_filters={"nope": ["x"]})
    )
    assert missing_key["observations"] == []


def test_g4_tag_filter_accepts_list_form():
    bundle = _g4_bundle()
    bundle["observations"][0]["tags"] = ["deployment:production"]
    filtered = filter_expression_bundle(
        bundle, ConceptPackageFilter(tag_filters={"deployment": ["production"]})
    )
    # obs-1 has the namespaced tag; obs-3 has no tags -> dropped fail-closed
    assert [o["observation_id"] for o in filtered["observations"]] == ["obs-1"]


# --- G5 regression tests (review record bc724f6b, finding 7) ---

import pytest

from expression.concept_package import SnapshotPin, capture_snapshot_context, verify_snapshots


def _g5_tag_bundle(**overrides) -> dict:
    bundle = {
        "contract_revision": "expression-v0.1",
        "adapter_revision": "tag-adapter-v2",
        "observations": [
            {
                "observation_id": "obs-1",
                "tag_namespace": "expr",
                "key": "deployment",
                "normalized_value": "production",
                "authority_status": "projected",
            },
        ],
        "conflicts": [],
        "authority_status": "non_authoritative",
        "governed_tag_vocabulary_revision": "vocab-2026-09-22",
    }
    bundle.update(overrides)
    return bundle


def test_g5_capture_snapshot_context():
    pin = capture_snapshot_context(_g5_tag_bundle())
    assert pin.vocabulary_revision == "vocab-2026-09-22"
    assert pin.projection_revision == "tag-adapter-v2"
    assert pin.vocabulary_fingerprint
    assert pin.projection_fingerprint
    assert pin.captured_at is not None


def test_g5_capture_fails_without_vocabulary_revision():
    bundle = _g5_tag_bundle()
    del bundle["governed_tag_vocabulary_revision"]
    with pytest.raises(ValueError, match="cannot pin vocabulary"):
        capture_snapshot_context(bundle)


def test_g5_capture_fails_without_adapter_revision():
    bundle = _g5_tag_bundle()
    del bundle["adapter_revision"]
    with pytest.raises(ValueError, match="cannot pin projection"):
        capture_snapshot_context(bundle)


def test_g5_pinned_package_validates_clean():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(concept_scope=["deployment"], member_kinds=["reference"]),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
    )
    pin = pkg.snapshot_pin
    assert pin is not None
    assert pkg.filter_spec.vocabulary_snapshot == (
        f"vocab-2026-09-22:{pin.vocabulary_fingerprint[:16]}"
    )
    assert pkg.filter_spec.projection_snapshot == (
        f"tag-adapter-v2:{pin.projection_fingerprint[:16]}"
    )
    assert validate_concept_package(pkg) == []


def test_g5_pins_enter_fingerprint():
    unpinned = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(concept_scope=["deployment"]),
        expression_bundle=_g4_bundle(),
    )
    pinned = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(concept_scope=["deployment"]),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
    )
    assert unpinned.snapshot_pin is None
    assert pinned.snapshot_pin is not None
    assert unpinned.fingerprint != pinned.fingerprint

    pinned_other_vocab = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(concept_scope=["deployment"]),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(governed_tag_vocabulary_revision="vocab-older"),
    )
    assert pinned.fingerprint != pinned_other_vocab.fingerprint


def test_g5_pinning_without_tag_bundle_fails_closed():
    with pytest.raises(ValueError, match="require the tag bundle"):
        create_concept_package(
            package_name="g5-pkg",
            package_version="1.0.0",
            filter_spec=ConceptPackageFilter(vocabulary_snapshot="vocab-2026-09-22:abcdef"),
            expression_bundle=_g4_bundle(),
        )


def test_g5_pin_strings_without_context_fail_validation():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(concept_scope=["deployment"]),
        expression_bundle=_g4_bundle(),
    )
    pkg.filter_spec.vocabulary_snapshot = "vocab-2026-09-22:0123456789abcdef"
    # fingerprint must be recomputed to isolate the pin-context failure
    pkg.fingerprint = _package_fingerprint(pkg)
    errors = validate_concept_package(pkg)
    assert any("without captured snapshot context" in e for e in errors)


def test_g5_verify_snapshots_detects_vocabulary_drift():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
        vocabulary_content={"tags": ["deployment", "environment"]},
    )
    assert verify_snapshots(pkg) == []
    drifted = verify_snapshots(
        pkg,
        tag_bundle=_g5_tag_bundle(),
        vocabulary_content={"tags": ["deployment"]},
    )
    assert any("vocabulary fingerprint" in e for e in drifted)


def test_g5_verify_snapshots_detects_projection_drift():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
    )
    drifted_bundle = _g5_tag_bundle(
        observations=[
            {
                "observation_id": "obs-1",
                "tag_namespace": "expr",
                "key": "deployment",
                "normalized_value": "staging",
                "authority_status": "projected",
            },
        ],
    )
    drifted = verify_snapshots(pkg, tag_bundle=drifted_bundle)
    assert any("projection fingerprint" in e for e in drifted)


def test_g5_verify_snapshots_without_bundle_clean_for_pinned():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
    )
    assert verify_snapshots(pkg) == []


def test_g5_export_import_round_trips_pins():
    pkg = create_concept_package(
        package_name="g5-pkg",
        package_version="1.0.0",
        filter_spec=ConceptPackageFilter(),
        expression_bundle=_g4_bundle(),
        tag_bundle=_g5_tag_bundle(),
    )
    imported = import_concept_package(export_concept_package(pkg))
    assert imported.snapshot_pin is not None
    assert imported.snapshot_pin.vocabulary_revision == pkg.snapshot_pin.vocabulary_revision
    assert imported.snapshot_pin.vocabulary_fingerprint == pkg.snapshot_pin.vocabulary_fingerprint
    assert imported.snapshot_pin.projection_fingerprint == pkg.snapshot_pin.projection_fingerprint
    assert imported.fingerprint == pkg.fingerprint
    assert validate_concept_package(imported) == []


def test_g5_legacy_unpinned_package_still_validates():
    pkg = ConceptPackage(
        package_id="legacy",
        package_name="legacy",
        package_version="1.0.0",
        package_revision="2024-01-01T00:00:00Z",
        filter_spec=ConceptPackageFilter(),
        expression_bundle={"observations": [], "candidate_links": [], "proposition_candidates": []},
        metadata_stream_id=None,
        created_at=datetime.utcnow(),
        boundary={},
        fingerprint="",
    )
    pkg.fingerprint = _package_fingerprint(pkg)
    assert validate_concept_package(pkg) == []
