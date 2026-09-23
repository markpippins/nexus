"""Conformance tests for the Aspects contract (Aspect G6).

The Aspects governed layer must be an independently implementable service:
this suite pins the contract manifest/fingerprint, the G2 lifecycle
machine, the authority boundary, and the .tsp manifest parity, per review
record bc724f6b finding 2.
"""

from __future__ import annotations

import json
import pathlib

from aspects.binding_port import ALLOWED_TRANSITIONS, BINDING_STATUSES
from aspects.contract import (
    ASPECTS_CONTRACT_FINGERPRINT_VERSION,
    ASPECTS_CONTRACT_REVISION,
    BINDING_TABLE,
    VOCABULARY_TABLE,
    contract_fingerprint,
    contract_manifest,
    validate_projected_tag_input,
)


def _projected_tag() -> dict:
    return {
        "tag_observation_id": "tag-obs-1",
        "source_identity": "transcript:t1",
        "source_revision": "rev-1",
        "tag_namespace": "expr",
        "key": "deployment",
        "raw_value": "production",
        "normalized_value": "production",
        "kind": "source_tag",
        "status": "observed",
        "authority_status": "projected",
        "basis": "explicit text match",
    }


def test_contract_revision_and_fingerprint_version():
    assert ASPECTS_CONTRACT_REVISION == "aspects-v0.1"
    assert ASPECTS_CONTRACT_FINGERPRINT_VERSION == 1


def test_contract_fingerprint_is_deterministic_and_content_bound():
    assert contract_fingerprint() == contract_fingerprint()
    assert len(contract_fingerprint()) == 64

    drifted = contract_manifest()
    drifted["binding_owner"] = "expression"
    drifted_json = json.dumps(drifted, sort_keys=True, separators=(",", ":"))
    import hashlib

    assert hashlib.sha256(drifted_json.encode()).hexdigest() != contract_fingerprint()


def test_manifest_pins_the_g2_lifecycle():
    manifest = contract_manifest()
    assert manifest["binding_statuses"] == sorted(BINDING_STATUSES)
    assert manifest["binding_lifecycle"] == {
        status: sorted(nexts) for status, nexts in ALLOWED_TRANSITIONS.items()
    }
    # Terminal state stays terminal; approval is the only path out of proposed
    # to governed identity.
    assert manifest["binding_lifecycle"]["expired"] == []
    assert set(manifest["binding_lifecycle"]["proposed"]) >= {"approved", "rejected"}


def test_manifest_declares_authority_boundary():
    manifest = contract_manifest()
    assert manifest["binding_owner"] == "aspects"
    assert manifest["projected_tag_authority_status"] == "projected"
    assert manifest["vocabulary_table"] == "aspects.governed_tag_vocabulary"
    assert manifest["binding_table"] == "aspects.tag_binding"
    assert manifest["authority_boundary"]["expression_authority_status"] == "non_authoritative"
    assert (
        manifest["authority_boundary"]["binding_approval_required_for_governed_identity"]
        is True
    )


def test_committed_manifest_matches_implementation():
    path = (
        pathlib.Path(__file__).parents[2]
        / "typespec"
        / "v1"
        / "aspects"
        / "contract-manifest.json"
    )
    committed = json.loads(path.read_text())
    expected = contract_manifest()
    expected["contract_fingerprint"] = contract_fingerprint()
    assert committed == expected
    assert committed["contract_fingerprint"] == contract_fingerprint()


def test_typespec_contract_file_exists_with_matching_names():
    tsp = (
        pathlib.Path(__file__).parents[2]
        / "typespec"
        / "v1"
        / "aspects"
        / "main.tsp"
    ).read_text()
    # The .tsp is the shape witness for the Python contract: the lifecycle
    # statuses and the two tables must appear by name.
    for status in BINDING_STATUSES:
        assert f'"{status}"' in tsp
    assert "governed_tag_vocabulary" in tsp
    assert "tag_binding" in tsp
    assert "model TagBinding" in tsp
    assert "model GovernedTagVocabularyEntry" in tsp


def test_projected_tag_input_conforms():
    assert validate_projected_tag_input(_projected_tag()) == []


def test_projected_tag_missing_fields_fail():
    tag = _projected_tag()
    del tag["normalized_value"]
    errors = validate_projected_tag_input(tag)
    assert any("normalized_value" in e for e in errors)


def test_projected_tag_authority_drift_is_rejected():
    tag = _projected_tag()
    tag["authority_status"] = "governed"
    errors = validate_projected_tag_input(tag)
    assert any("must be 'projected'" in e for e in errors)


def test_projected_tag_pre_populated_governed_id_is_rejected():
    tag = _projected_tag()
    tag["governed_tag_id"] = "some-governed-uuid"
    errors = validate_projected_tag_input(tag)
    assert any("governed_tag_id" in e for e in errors)


def test_projected_tag_wrong_kind_is_rejected():
    tag = _projected_tag()
    tag["kind"] = "governed_aspect"
    errors = validate_projected_tag_input(tag)
    assert any("kind" in e for e in errors)
