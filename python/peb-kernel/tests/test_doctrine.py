"""Tests for content-addressed doctrine snapshot provenance."""

from types import SimpleNamespace

import pytest

from peb_kernel.doctrine import DoctrineSnapshot, build_doctrine_snapshot
from peb_kernel.keychains import PebKeychainsAdapter


def _snapshot(card_order: list[dict[str, str]]):
    return build_doctrine_snapshot(
        system_prompt="system prompt v1",
        bootstrap="bootstrap v1",
        active_procedure_cards=card_order,
    )


def test_identical_doctrine_components_share_an_address():
    cards = [
        {"slug": "review", "content": "review card v1"},
        {"slug": "build", "content": "build card v1"},
    ]
    first = _snapshot(cards)
    second = _snapshot(list(reversed(cards)))

    assert first.snapshot_id == second.snapshot_id
    assert first.active_procedure_cards == second.active_procedure_cards
    assert first.to_dict() == second.to_dict()


def test_card_content_change_changes_the_address():
    first = _snapshot([{"slug": "review", "content": "review card v1"}])
    changed = _snapshot([{"slug": "review", "content": "review card v2"}])

    assert first.snapshot_id != changed.snapshot_id


def test_normalized_snapshot_round_trip_and_tamper_rejection():
    snapshot = _snapshot([{"slug": "review", "content": "review card v1"}])
    restored = DoctrineSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot

    tampered = snapshot.to_dict()
    tampered["bootstrap_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="content address mismatch"):
        DoctrineSnapshot.from_dict(tampered)


def test_peb_adapter_propagates_snapshot_without_overloading_law_ids():
    snapshot = _snapshot([{"slug": "review", "content": "review card v1"}])
    transaction = SimpleNamespace(
        id="tx-1",
        idempotency_key="doctrine-test",
        entity_id="entity-1",
        tool_name="peb_record_decision",
        admission_result=SimpleNamespace(value="ALLOWED"),
        kernel_event_id=None,
        kernel_event_type=None,
        before_hash=None,
        after_hash=None,
        input={},
    )
    binding = {
        "decision_class": "deny_contract_promotion",
        "doctrine_ids": ["law-1"],
        "doctrine_snapshot": snapshot.to_dict(),
    }

    read_set = PebKeychainsAdapter._read_set(transaction, binding)

    assert read_set["doctrine_ids"] == ["law-1"]
    assert read_set["doctrine_snapshot_id"] == snapshot.snapshot_id
    assert read_set["doctrine_snapshot"] == snapshot.to_dict()
    payload = PebKeychainsAdapter._payload(transaction, binding, "committed")
    assert payload["doctrine_snapshot_id"] == snapshot.snapshot_id
