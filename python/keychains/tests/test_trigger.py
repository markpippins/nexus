from keychains import normalize_trigger_event, outbox_row_to_trigger, parse_legacy_trigger


def test_normalize_derives_idempotency_key_from_namespace_and_event():
    event = normalize_trigger_event({"source_namespace": "peb", "source_event_id": "evt-1", "kind": "peb.x"})

    assert event["idempotency_key"] == "peb:evt-1"
    assert event["event_id"] == "peb:evt-1"
    assert event["id"] == "evt-1"
    assert event["schema_version"] == 1
    assert event["outcome"] == "committed"


def test_normalize_falls_back_to_source_alias_and_unknown_namespace():
    event = normalize_trigger_event({"source": "sol-api", "id": "t9", "kind": "resolution.transition.committed"})

    assert event["source_namespace"] == "sol-api"
    assert event["idempotency_key"] == "sol-api:t9"

    anonymous = normalize_trigger_event({"kind": "x"})
    assert anonymous["source_namespace"] == "unknown"
    assert anonymous["idempotency_key"] == "unknown:"


def test_normalize_preserves_supplied_key_and_outcome():
    event = normalize_trigger_event(
        {"idempotency_key": "custom:1", "outcome": "denied", "kind": "k", "source_namespace": "ns", "source_event_id": "1"}
    )

    assert event["idempotency_key"] == "custom:1"
    assert event["outcome"] == "denied"


def test_parse_legacy_sol_transition():
    event = parse_legacy_trigger("sol-transition:t-42:entity-7")

    assert event["kind"] == "resolution.transition.committed"
    assert event["id"] == "t-42"
    assert event["source_namespace"] == "sol-api"
    assert event["source_event_id"] == "t-42:entity-7"
    assert event["meta"] == {"entity_id": "entity-7"}
    assert event["idempotency_key"] == "sol-api:t-42:entity-7"


def test_parse_legacy_without_entity():
    event = parse_legacy_trigger("sol-transition:t-42")

    assert event["source_event_id"] == "t-42:"
    assert event["meta"] == {"entity_id": None}


def test_parse_legacy_unknown_shape_is_named_by_the_raw_trigger():
    event = parse_legacy_trigger("something-else")

    assert event["kind"] == "unknown"
    assert event["raw"] == "something-else"
    assert event["idempotency_key"] == "legacy:something-else"


def test_outbox_row_maps_every_contract_field_and_parses_json_text():
    row = {
        "source_namespace": "resolution",
        "source_event_id": "evt-9",
        "schema_version": 2,
        "event_kind": "resolution.transition.committed",
        "outcome": "committed",
        "aggregate_id": "agg-1",
        "actor": "architect",
        "correlation_id": "corr-1",
        "contract_id": "contract-1",
        "evaluator_id": "eval-1",
        "law_id": "law-1",
        "effective_at": "2026-10-01T00:00:00Z",
        "recorded_at": "2026-10-01T00:00:01Z",
        "read_set": '{"decision_class": "deny_contract_promotion"}',
        "payload": {"k": "v"},
    }

    event = outbox_row_to_trigger(row)

    assert event["idempotency_key"] == "resolution:evt-9"
    assert event["event_id"] == "resolution:evt-9"
    assert event["read_set"] == {"decision_class": "deny_contract_promotion"}
    assert event["payload"] == {"k": "v"}
    assert event["aggregate_id"] == "agg-1"
    assert event["law_id"] == "law-1"


def test_outbox_row_leaves_unparseable_text_untouched():
    row = {"source_namespace": "ns", "source_event_id": "1", "event_kind": "k", "read_set": "not json"}

    assert outbox_row_to_trigger(row)["read_set"] == "not json"
