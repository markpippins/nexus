from keychains import (
    InstanceManifest,
    base_interval,
    compute_delta,
    decide_is_base,
    index_from_legacy_arrays,
    index_from_manifests,
    merge_delta,
    plan_storage,
)


def manifest(instance_id, record_id, *, record_type="report", version=1, role="engineer"):
    return InstanceManifest(
        instance_id=instance_id,
        record_type=record_type,
        record_id=record_id,
        version=version,
        role=role,
        supersession_type="none",
        asset_id=None,
    )


def test_index_from_manifests_is_instance_to_record():
    index = index_from_manifests([manifest("a", "r1"), manifest("b", "r2")])

    assert index == {"a": "r1", "b": "r2"}


def test_index_from_legacy_arrays_strips_entry_prefixes():
    index = index_from_legacy_arrays(
        ["amendment:05d0fe54", "explicit:abcdef12", "single:1234", "raw-key"],
        ["r1", "r2", "r3", "r4"],
    )

    assert index == {"05d0fe54": "r1", "abcdef12": "r2", "1234": "r3", "raw-key": "r4"}


def test_index_from_legacy_arrays_refuses_mismatched_lengths():
    assert index_from_legacy_arrays(["a", "b"], ["r1"]) is None
    assert index_from_legacy_arrays(None, ["r1"]) is None
    assert index_from_legacy_arrays("notalist", ["r1"]) is None


def test_compute_delta_splits_added_updated_and_removed():
    prev = {"a": "r1", "b": "r2", "gone": "r9"}
    manifests = [manifest("a", "r1"), manifest("b", "r2-new"), manifest("c", "r3")]

    delta = compute_delta(prev, manifests)

    assert [m.instance_id for m in delta.added] == ["c"]
    assert [m.instance_id for m in delta.updated] == ["b"]
    assert list(delta.removed) == ["gone"]
    assert delta.counts() == {"added": 1, "updated": 1, "removed": 1}


def test_compute_delta_with_no_prior_state_is_all_added():
    delta = compute_delta({}, [manifest("a", "r1")])

    assert [m.instance_id for m in delta.added] == ["a"]
    assert delta.updated == ()
    assert delta.removed == ()


def test_merge_delta_applies_to_a_copy_and_rehomes_changed_types():
    state = {"report": [{"instance_id": "a", "record_id": "r1"}], "decision": []}
    delta = compute_delta(
        {"a": "r1", "b": "r2"},
        [manifest("a", "r1"), manifest("b", "r3", record_type="decision")],
    )

    merged = merge_delta(state, delta)

    assert state == {"report": [{"instance_id": "a", "record_id": "r1"}], "decision": []}
    assert merged["report"] == [{"instance_id": "a", "record_id": "r1"}]
    assert [item["instance_id"] for item in merged["decision"]] == ["b"]
    assert delta.counts() == {"added": 0, "updated": 1, "removed": 0}


def test_merge_delta_normalizes_a_rehomed_update_without_duplicates():
    state = {"report": [{"instance_id": "a", "record_id": "r1"}]}
    delta = compute_delta({"a": "r1"}, [manifest("a", "r2", record_type="decision")])

    merged = merge_delta(state, delta)

    assert merged["report"] == []
    assert [item["record_id"] for item in merged["decision"]] == ["r2"]


def test_base_interval_defaults_and_rejects_bad_values():
    assert base_interval({}) == 50
    assert base_interval({"KEYCHAIN_BASE_INTERVAL": "10"}) == 10
    assert base_interval({"KEYCHAIN_BASE_INTERVAL": "0"}) == 50
    assert base_interval({"KEYCHAIN_BASE_INTERVAL": "nope"}) == 50


def test_decide_is_base_on_unknown_prior_state_and_on_interval():
    assert decide_is_base(False, version=3, interval=50) is True
    assert decide_is_base(True, version=100, interval=50) is True
    assert decide_is_base(True, version=101, interval=50) is False


def test_plan_storage_degrades_to_base_when_prior_index_is_foreign():
    # >100 instances on both sides with no overlap: a scheme change, not prior state.
    manifests = [manifest(f"new-{i}", f"r{i}") for i in range(120)]
    prev = {f"old-{i}": f"p{i}" for i in range(120)}

    is_base, delta = plan_storage(manifests, prev, version=7, interval=50)

    assert is_base is True
    assert delta.added == ()


def test_plan_storage_keeps_delta_when_overlap_is_healthy():
    manifests = [manifest(f"i-{index}", f"r{index}") for index in range(120)]
    prev = {f"i-{index}": f"r{index}" for index in range(110)}

    is_base, delta = plan_storage(manifests, prev, version=7, interval=50)

    assert is_base is False
    assert len(delta.added) == 10
    assert delta.updated == ()


def test_plan_storage_small_populations_are_not_judged_on_overlap():
    manifests = [manifest("only-one", "r1")]
    prev = {"different": "r0"}

    is_base, delta = plan_storage(manifests, prev, version=2, interval=50)

    assert is_base is False
    assert delta.removed == ("different",)
