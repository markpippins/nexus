"""Tests for content-addressed doctrine snapshot provenance."""

import json
import pathlib

from types import SimpleNamespace

import pytest

from peb_kernel.doctrine import (
    SNAPSHOT_SCHEMA_VERSION,
    DoctrineSnapshot,
    build_doctrine_snapshot,
)
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


# ---------------------------------------------------------------------------
# Decision 28 cross-language pin (architect 85c6d979).
#
# The fixture is generated from THIS module (the canonical implementation) and read
# by BOTH this suite and the TypeScript suite
# (moleculer/nexus-broker/tests/doctrine-snapshot.test.js). That shared read is the
# anti-drift mechanism: a change to either implementation fails a vector in the other
# language, rather than silently splitting the content-address space.
# ---------------------------------------------------------------------------

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "bin"
    / "fixtures"
    / "doctrine-snapshot-vectors.json"
)


def _load_fixture() -> dict:
    assert _FIXTURE.exists(), f"Decision 28 fixture missing: {_FIXTURE}"
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_present_and_version_pinned():
    fixture = _load_fixture()
    assert len(fixture["vectors"]) >= 3, "Decision 28 requires at least 3 distinct vectors"
    assert fixture["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION


@pytest.mark.parametrize("vector", _load_fixture()["vectors"], ids=lambda v: v["name"])
def test_cross_language_vector_matches(vector):
    """The TS port must reproduce every vector here byte-for-byte."""
    snap = build_doctrine_snapshot(
        system_prompt=vector["input"]["system_prompt"],
        bootstrap=vector["input"]["bootstrap"],
        active_procedure_cards=vector["input"]["active_procedure_cards"],
    )
    expected = vector["expected"]
    assert snap.snapshot_id == expected["snapshot_id"], (
        f"snapshot_id drift on '{vector['name']}' — python and TS disagree"
    )
    assert snap.system_prompt_hash == expected["system_prompt_hash"]
    assert snap.bootstrap_hash == expected["bootstrap_hash"]
    assert list(snap.active_procedure_cards) == expected["active_procedure_cards"]
    assert snap.schema_version == SNAPSHOT_SCHEMA_VERSION


def test_fixture_encodes_order_independence_and_dedupe():
    by_name = {v["name"]: v for v in _load_fixture()["vectors"]}
    assert (
        by_name["card-reorder"]["expected"]["snapshot_id"]
        == by_name["three-cards"]["expected"]["snapshot_id"]
    ), "reordering cards must not change the address"
    assert (
        by_name["duplicate-cards"]["expected"]["snapshot_id"]
        == by_name["three-cards"]["expected"]["snapshot_id"]
    ), "duplicate cards must be deduped, not counted twice"


def test_canonical_serialization_is_alphabetical():
    """Guard the trap Decision 28 names: sort_keys=True, NOT the field-declaration order.

    Decision 28's prose lists schema_version first, but the canonical recipe hashes
    ALPHABETICAL bytes. A port that followed the prose would diverge here.
    """
    import json as _json

    from peb_kernel.doctrine import _canonical_json

    rendered = _canonical_json(
        {
            "system_prompt_hash": "sha256:" + "a" * 64,
            "bootstrap_hash": "sha256:" + "b" * 64,
            "schema_version": 1,
            "active_procedure_cards": [],
        }
    )
    order = [k for k in ("active_procedure_cards", "bootstrap_hash", "schema_version", "system_prompt_hash")]
    positions = [rendered.index(k) for k in order]
    assert positions == sorted(positions), f"keys must serialize alphabetically, got {rendered}"
    assert " " not in rendered, "compact separators: no whitespace"


def test_non_ascii_is_not_escaped():
    from peb_kernel.doctrine import _canonical_json

    assert _canonical_json("建築") == '"建築"', "ensure_ascii=False must keep non-ASCII literal"
