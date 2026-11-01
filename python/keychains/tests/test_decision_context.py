import pytest

from keychains import (
    binding_decision_event,
    build_decision_context_manifest,
    doctrine_snapshot,
    observation_window,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def doctrine(**overrides):
    snapshot = {
        "schema_version": 1,
        "snapshot_id": HASH_A,
        "system_prompt_hash": HASH_B,
        "bootstrap_hash": HASH_C,
        "active_procedure_cards": sorted([HASH_A, HASH_B]),
    }
    snapshot.update(overrides)
    return snapshot


def test_binding_decision_requires_class_and_kind_prefix():
    event = {
        "kind": "peb.deny_contract_promotion.rejected",
        "read_set": {"decision_class": "deny_contract_promotion"},
    }

    assert binding_decision_event(event) is True
    assert binding_decision_event({**event, "kind": "resolution.transition.committed"}) is False
    assert binding_decision_event({**event, "read_set": {"decision_class": "other"}}) is False


def test_observation_window_is_absent_without_a_nested_window_or_bounds():
    assert observation_window({"kind": "k"}) is None


def test_observation_window_resolves_phase_post_activation():
    event = {
        "kind": "peb.deny_contract_promotion.rejected",
        "observation_window": {
            "activation_ref": "act-1",
            "activated_at": "2026-10-01T00:00:00Z",
            "window_start": "2026-10-01T01:00:00Z",
            "window_end": "2026-10-01T02:00:00Z",
            "observed_at": "2026-10-01T01:30:00Z",
        },
    }

    window = observation_window(event)

    assert window == {
        "activation_ref": "act-1",
        "activated_at": "2026-10-01T00:00:00Z",
        "window_start": "2026-10-01T01:00:00Z",
        "window_end": "2026-10-01T02:00:00Z",
        "observed_at": "2026-10-01T01:30:00Z",
        "in_window": True,
        "phase": "post_activation",
    }


def test_observation_window_phases_for_pre_activation_and_outside_window():
    base = {
        "activation_ref": "act-1",
        "activated_at": "2026-10-01T01:00:00Z",
        "window_start": "2026-10-01T01:00:00Z",
        "window_end": "2026-10-01T02:00:00Z",
    }

    early = observation_window({**base, "observed_at": "2026-10-01T00:30:00Z"})
    late = observation_window({**base, "observed_at": "2026-10-01T03:00:00Z"})

    assert early["phase"] == "pre_activation"
    assert early["in_window"] is False
    assert late["phase"] == "outside_window"
    assert late["in_window"] is False


def test_observation_window_without_observed_at_is_post_activation_and_unbounded():
    window = observation_window(
        {
            "activation_ref": "act-1",
            "activated_at": "2026-10-01T00:00:00Z",
            "window_start": "2026-10-01T00:00:00Z",
            "window_end": "2026-10-01T01:00:00Z",
        }
    )

    assert window["observed_at"] is None
    assert window["in_window"] is None
    assert window["phase"] == "post_activation"


@pytest.mark.parametrize(
    "overrides",
    [
        {"activation_ref": None},
        {"activated_at": None},
        {"window_start": None},
    ],
)
def test_observation_window_requires_every_identity_field(overrides):
    event = {
        "kind": "peb.deny_contract_promotion.rejected",
        **{
            "activation_ref": "act-1",
            "activated_at": "2026-10-01T00:00:00Z",
            "window_start": "2026-10-01T00:00:00Z",
            "window_end": "2026-10-01T01:00:00Z",
        },
        **overrides,
    }

    with pytest.raises(ValueError, match="requires activation_ref"):
        observation_window(event)


def test_observation_window_rejects_inverted_bounds():
    event = {
        "kind": "k",
        "activation_ref": "act-1",
        "activated_at": "2026-10-01T02:00:00Z",
        "window_start": "2026-10-01T01:00:00Z",
        "window_end": "2026-10-01T03:00:00Z",
    }

    with pytest.raises(ValueError, match="invalid bounds"):
        observation_window(event)


def test_doctrine_snapshot_accepts_a_well_formed_snapshot():
    snapshot, snapshot_id = doctrine_snapshot({"doctrine_snapshot": doctrine()})

    assert snapshot is not None
    assert snapshot_id == HASH_A


def test_doctrine_snapshot_rejects_unsorted_or_duplicate_cards():
    cards = [HASH_B, HASH_A, HASH_B]
    with pytest.raises(ValueError, match="sorted and unique"):
        doctrine_snapshot({"doctrine_snapshot": doctrine(active_procedure_cards=cards)})


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"snapshot_id": "not-a-hash"},
        {"system_prompt_hash": "sha256:short"},
        {"active_procedure_cards": ["nope"]},
        {"active_procedure_cards": "notalist"},
    ],
)
def test_doctrine_snapshot_rejects_malformed_snapshots(overrides):
    with pytest.raises(ValueError, match="malformed"):
        doctrine_snapshot({"doctrine_snapshot": doctrine(**overrides)})


def test_embedded_snapshot_id_wins_over_an_explicit_reference():
    # Parity note: the TS service resolves the id as
    # ``snapshot.snapshot_id || trigger.doctrine_snapshot_id || ...`` and then
    # compares the two, so its "reference mismatch" guard cannot fire while a
    # well-formed snapshot is embedded. The port preserves that ordering rather
    # than silently changing the contract; the dead guard is reported upstream.
    snapshot, snapshot_id = doctrine_snapshot(
        {"doctrine_snapshot": doctrine(), "doctrine_snapshot_id": HASH_C}
    )

    assert snapshot is not None
    assert snapshot_id == HASH_A


def test_bare_doctrine_reference_identifies_the_snapshot():
    snapshot, snapshot_id = doctrine_snapshot({"read_set": {"doctrine_snapshot_id": HASH_C}})

    assert snapshot is None
    assert snapshot_id == HASH_C


def test_doctrine_snapshot_reads_nested_candidates():
    _, snapshot_id = doctrine_snapshot(
        {"meta": {"doctrine_snapshot": doctrine()}, "payload": {"doctrine_snapshot_id": HASH_A}}
    )

    assert snapshot_id == HASH_A


def test_decision_context_manifest_carries_identity_but_not_content():
    event = {
        "kind": "peb.deny_contract_promotion.rejected",
        "source_namespace": "peb",
        "source_event_id": "evt-7",
        "outcome": "denied",
        "decision_class": "deny_contract_promotion",
        "decision_id": "dec-1",
        "proposition_id": "prop-1",
        "subject_id": "subj-1",
        "work_item_id": "wr-1",
        "disposition": "refused",
        "binding_owner": "architect",
        "authority_level": "L4",
        "authorization_ref": "auth-1",
        "activation_ref": "act-1",
        "activated_at": "2026-10-01T00:00:00Z",
        "grant_id": "grant-9",
        "evaluator_id": "eval-1",
        "evaluator_version": 3,
        "contract_id": "contract-1",
        "contract_version": "v2",
        "law_id": "law-1",
        "law_version": 1,
        "bridge_id": "bridge-1",
        "bridge_version": 2,
        "read_set": {"digest": "abc", "activation_ref": "act-1"},
        "doctrine_snapshot": doctrine(),
        "observation_window": {
            "activation_ref": "act-1",
            "activated_at": "2026-10-01T00:00:00Z",
            "window_start": "2026-10-01T01:00:00Z",
            "window_end": "2026-10-01T02:00:00Z",
            "observed_at": "2026-10-01T01:30:00Z",
        },
    }

    manifest = build_decision_context_manifest(
        event, {"checkpoint_id": "cp-1", "version": 12}
    )

    assert manifest["schema_version"] == 1
    assert manifest["checkpoint_class"] == "decision"
    assert manifest["decision_class"] == "deny_contract_promotion"
    assert manifest["binding_decision_owner"] == {
        "role": "architect",
        "authority_level": "L4",
        "authorization_ref": "auth-1",
    }
    assert manifest["activation"]["grant_id"] == "grant-9"
    assert manifest["decision"]["evidence_ids"] is None
    assert manifest["rollback"]["reference"] is None
    assert manifest["evaluator"] == {"id": "eval-1", "version": 3}
    assert manifest["contract"] == {"id": "contract-1", "version": "v2"}
    assert manifest["source_read_set"] == {"digest": "abc", "activation_ref": "act-1"}
    assert manifest["read_set_manifest"] == {"digest": "abc"}
    assert manifest["doctrine_snapshot_id"] == HASH_A
    assert manifest["observation_window"]["phase"] == "post_activation"
    assert manifest["checkpoint_reference"] == {"checkpoint_id": "cp-1", "version": 12}
    # The read-set content itself is never copied into the manifest.
    assert "payload" not in manifest
