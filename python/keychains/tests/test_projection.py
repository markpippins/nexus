from datetime import datetime, timedelta, timezone

import pytest

from keychains import (
    AgentRecord,
    MemoryKeychainStore,
    agent_records_snapshot,
    agent_records_status,
    agent_records_transitions,
)
from keychains.api import reconcile_committed_checkpoints

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def record(record_id, *, title="plain", tags=(), offset=0, role="engineer", record_type="report"):
    return AgentRecord(
        id=record_id,
        record_type=record_type,
        role=role,
        title=title,
        content=f"content-{record_id}",
        created_at=BASE + timedelta(hours=offset),
        level=2,
        visibility_scope="all",
        tags=tuple(tags),
        model="test/model",
    )


def doctrine():
    return {
        "schema_version": 1,
        "snapshot_id": HASH_A,
        "system_prompt_hash": HASH_B,
        "bootstrap_hash": HASH_C,
        "active_procedure_cards": sorted([HASH_B, HASH_C]),
    }


A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
C = "cccccccc-0000-0000-0000-000000000003"
D = "dddddddd-0000-0000-0000-000000000004"


def test_first_projection_creates_a_base_checkpoint_and_compatibility_views():
    store = MemoryKeychainStore()
    records = [record(A, title="T 05d0fe54 — amendment v1: one"), record(B)]

    result = agent_records_snapshot(records, store, now=NOW)

    assert result["ok"] is True
    assert result["version"] == 1
    assert result["storage"] == "base"
    assert result["base_version"] is None
    assert result["totalRecords"] == 2
    assert result["entries"] == 2
    assert result["supersededRecords"] == 0
    assert result["prevVersion"] == 0
    assert result["label"] == "ar-snapshot-v1"
    assert result["delta_counts"] == {"added": 0, "updated": 0, "removed": 0}
    assert result["typeBreakdown"] == {"report": 2}
    assert result["roleBreakdown"] == {"engineer": 2}
    assert result["recordTypeCount"] == 1

    checkpoint = store.checkpoints[0]
    assert checkpoint["checkpoint_status"] == "committed"
    assert checkpoint["committed_at"] == "2026-11-01T12:00:00.000Z"
    assert checkpoint["state_vector"]["report"][0]["instance_id"] == "05d0fe54"

    # Compatibility views are refreshed only while this checkpoint is active.
    assert store.active["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert len(store.entries) == 2
    assert store.record_type_state[0]["instance_count"] == 2

    # The bounded prior-state pointer advances with the checkpoint.
    assert store.head["version"] == 1
    assert store.head["index"] == {"05d0fe54": A, B: B}


def test_second_projection_is_a_delta_against_the_head_index():
    store = MemoryKeychainStore()
    agent_records_snapshot(
        [record(A, title="T 05d0fe54 — amendment v1: one"), record(B), record(C)], store, now=NOW
    )
    result = agent_records_snapshot(
        [
            record(A, title="T 05d0fe54 — amendment v1: one"),
            record(A.replace("000001", "000009"), title="T 05d0fe54 — amendment v2: two", offset=1),
            record(B),
            record(D, offset=2),
        ],
        store,
        now=NOW + timedelta(minutes=5),
    )

    assert result["version"] == 2
    assert result["storage"] == "delta"
    assert result["base_version"] == 1
    assert result["delta_counts"] == {"added": 1, "updated": 1, "removed": 1}
    assert result["entries"] == 3
    assert result["supersededRecords"] == 1
    assert result["driftFindings"] == 2

    delta = store.checkpoints[1]["delta"]
    assert [item["instance_id"] for item in delta["added"]] == [D]
    assert [item["instance_id"] for item in delta["updated"]] == ["05d0fe54"]
    assert delta["removed"] == [C]

    second_checkpoint = store.checkpoints[1]["checkpoint_id"]
    kinds = sorted(
        finding["kind"]
        for finding in store.drift_findings
        if finding["checkpoint_id"] == second_checkpoint
    )
    assert kinds == ["new_instance", "superseded"]


def test_every_nth_checkpoint_is_a_base():
    store = MemoryKeychainStore()

    storages = []
    for index in range(3):
        result = agent_records_snapshot(
            [record(A), record(f"{index}0000000-0000-0000-0000-00000000000{index}")],
            store,
            now=NOW,
            base_interval=2,
        )
        storages.append(result["storage"])

    assert storages == ["base", "base", "delta"]


def test_trigger_event_deduplicates_on_idempotency_key():
    store = MemoryKeychainStore()
    event = {
        "kind": "resolution.transition.committed",
        "source_namespace": "sol-api",
        "source_event_id": "evt-1",
        "outcome": "committed",
    }

    first = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)
    second = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["version"] == first["version"]
    assert second["note"] == "snapshot already exists for this trigger event"
    assert len(store.checkpoints) == 1


def test_negative_outcome_is_archived_without_a_checkpoint():
    store = MemoryKeychainStore()
    event = {
        "kind": "resolution.transition.rejected",
        "source_namespace": "sol-api",
        "source_event_id": "evt-2",
        "outcome": "rejected",
    }

    result = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)

    assert result["archived"] is True
    assert result["checkpoint_created"] is False
    assert store.checkpoints == []
    transition = store.transitions["sol-api:evt-2"]
    assert transition["checkpoint_status"] == "not_applicable"
    assert transition["snapshot_version"] is None


def test_binding_decision_event_gets_a_checkpoint_with_a_decision_context():
    store = MemoryKeychainStore()
    event = {
        "kind": "peb.deny_contract_promotion.rejected",
        "source_namespace": "peb",
        "source_event_id": "evt-3",
        "outcome": "denied",
        "decision_class": "deny_contract_promotion",
        "decision_id": "dec-1",
        "binding_owner": "architect",
        "activation_ref": "act-1",
        "activated_at": "2026-10-01T00:00:00Z",
        "window_start": "2026-10-01T01:00:00Z",
        "window_end": "2026-10-01T02:00:00Z",
        "observed_at": "2026-10-01T01:30:00Z",
        "read_set": {"decision_class": "deny_contract_promotion"},
        "doctrine_snapshot": doctrine(),
    }

    result = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)

    assert result["ok"] is True
    assert result["doctrine_snapshot_id"] == HASH_A
    assert result["decision_context"]["observation_window"]["phase"] == "post_activation"

    checkpoint = store.checkpoints[0]
    assert checkpoint["doctrine_snapshot_id"] == HASH_A
    assert checkpoint["decision_context"]["checkpoint_reference"]["version"] == 1
    assert checkpoint["source_namespace"] == "peb"

    transition = store.transitions["peb:evt-3"]
    assert transition["checkpoint_status"] == "delivered"
    assert transition["snapshot_version"] == 1
    assert "reservation" not in transition

    listed = agent_records_transitions(store, activation_ref="act-1")
    assert listed["count"] == 1
    assert listed["items"][0]["keychain_event_id"] == "peb:evt-3"

    assert agent_records_transitions(store, observation_status="outside_window")["count"] == 0


def test_doctrine_snapshot_is_persisted_once():
    store = MemoryKeychainStore()

    agent_records_snapshot(
        [record(A)],
        store,
        now=NOW,
        trigger_event={
            "kind": "k",
            "source_namespace": "ns",
            "source_event_id": "1",
            "doctrine_snapshot": doctrine(),
        },
    )

    assert list(store.doctrine_snapshots) == [HASH_A]


def test_concurrent_delivery_returns_checkpoint_pending():
    store = MemoryKeychainStore()
    event = {
        "kind": "resolution.transition.committed",
        "source_namespace": "sol-api",
        "source_event_id": "evt-4",
        "outcome": "committed",
    }

    # Another delivery already owns an unexpired reservation.
    store.transitions["sol-api:evt-4"] = {
        "keychain_event_id": "sol-api:evt-4",
        "reservation": True,
        "reservation_at": NOW,
    }

    result = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)

    assert result["ok"] is False
    assert result["checkpoint_pending"] is True
    assert store.checkpoints == []


def test_stale_reservation_is_taken_over():
    store = MemoryKeychainStore()
    event = {
        "kind": "resolution.transition.committed",
        "source_namespace": "sol-api",
        "source_event_id": "evt-5",
        "outcome": "committed",
    }
    stale_at = NOW - timedelta(minutes=30)
    store.transitions["sol-api:evt-5"] = {
        "keychain_event_id": "sol-api:evt-5",
        "reservation": True,
        "reservation_at": stale_at,
        "_takeover_at": NOW,
    }

    result = agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)

    assert result["ok"] is True
    assert result["version"] == 1
    assert store.transitions["sol-api:evt-5"]["checkpoint_status"] == "delivered"


def test_committed_checkpoint_is_recovered_for_a_retried_source_event():
    store = MemoryKeychainStore()
    event = {
        "kind": "resolution.transition.committed",
        "source_namespace": "sol-api",
        "source_event_id": "evt-6",
        "outcome": "committed",
    }
    agent_records_snapshot([record(A)], store, now=NOW, trigger_event=event)
    # The process died before the transition document was finalized.
    store.transitions.pop("sol-api:evt-6")

    result = agent_records_snapshot(
        [record(A)], store, now=NOW + timedelta(minutes=1), trigger_event=event
    )

    assert result["deduplicated"] is True
    assert result["version"] == 1
    assert result["note"] == "recovered an already committed checkpoint for this source event"
    assert len(store.checkpoints) == 1
    assert store.transitions["sol-api:evt-6"]["checkpoint_status"] == "delivered"


class _FailingCommitStore(MemoryKeychainStore):
    def commit_checkpoint(self, checkpoint_id, committed_at):
        raise RuntimeError("commit failed")


def test_a_failed_commit_leaves_no_staged_checkpoint_or_head():
    store = _FailingCommitStore()

    with pytest.raises(RuntimeError, match="commit failed"):
        agent_records_snapshot([record(A)], store, now=NOW)

    assert store.checkpoints == []
    assert store.head is None
    assert store.entries == []


def test_legacy_trigger_string_still_projects_a_checkpoint():
    store = MemoryKeychainStore()

    result = agent_records_snapshot([record(A)], store, now=NOW, trigger="sol-transition:t-1:e-1")

    assert result["ok"] is True
    assert result["trigger"]["kind"] == "resolution.transition.committed"
    assert result["trigger"]["idempotency_key"] == "sol-api:t-1:e-1"


def test_status_reports_the_active_checkpoint():
    store = MemoryKeychainStore()
    agent_records_snapshot([record(A), record(B)], store, now=NOW)

    status = agent_records_status(store)

    assert status["latestSnapshot"] == 1
    assert status["latestSnapshotAt"] == "2026-11-01T12:00:00.000Z"
    assert status["entryCount"] == 2
    assert status["totalRecordsProjected"] == 2
    assert status["supersededRecords"] == 0
    assert status["checkpointId"] == store.active["checkpoint_id"]


def test_empty_projection_still_commits_an_empty_base():
    store = MemoryKeychainStore()

    result = agent_records_snapshot([], store, now=NOW)

    assert result["version"] == 1
    assert result["entries"] == 0
    assert result["storage"] == "base"
    assert store.entries == []


def test_reconcile_promotes_the_latest_committed_checkpoint():
    store = MemoryKeychainStore()
    agent_records_snapshot([record(A)], store, now=NOW)
    store.active = None

    result = reconcile_committed_checkpoints(store, now=NOW + timedelta(minutes=10))

    assert result["ok"] is True
    assert result["activeVersion"] == 1
