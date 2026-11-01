from datetime import datetime, timedelta, timezone

from keychains import (
    AgentRecord,
    AssetRef,
    extract_amendment_token,
    extract_amendment_version,
    extract_supersession_tag,
    resolve_keychain_entries,
)

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = "2026-11-01T00:00:00.000Z"


def record(record_id, *, title="t", tags=(), offset=0, role="engineer", record_type="report"):
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


def test_supersession_tag_forward_variants():
    assert extract_supersession_tag(["supersedes:abcd1234"]) == ("abcd1234", "forward")
    assert extract_supersession_tag(["Supersedes-partially:abcd1234"]) == ("abcd1234", "forward")
    assert extract_supersession_tag(["supersedes-c5-note:abcd1234"]) == ("abcd1234", "forward")


def test_supersession_tag_backward_and_absent():
    assert extract_supersession_tag(["superseded-by:abcd1234"]) == ("abcd1234", "backward")
    assert extract_supersession_tag(["unrelated", "to:lead-engineer"]) is None
    assert extract_supersession_tag([]) is None


def test_amendment_token_and_version():
    title = "W1.10 decision 05d0fe54 — amendment v4: revised"
    assert extract_amendment_token(title) == "05d0fe54"
    assert extract_amendment_version(title) == 4
    assert extract_amendment_token("plain title") is None
    assert extract_amendment_version("plain title") == 0


def test_amendment_chain_keeps_latest_issuance_and_provenance():
    records = [
        record("aaaaaaaa-0000-0000-0000-000000000001", title="A 05d0fe54 — amendment v1: one", offset=0),
        record("aaaaaaaa-0000-0000-0000-000000000002", title="A 05d0fe54 — amendment v2: two", offset=1),
        record("aaaaaaaa-0000-0000-0000-000000000003", title="A 05d0fe54 — amendment v3: three", offset=2),
    ]

    entries = resolve_keychain_entries(records, version=7, now=NOW)

    assert list(entries) == ["amendment:05d0fe54"]
    entry = entries["amendment:05d0fe54"]
    assert entry.instance_id == "05d0fe54"
    assert entry.record_id == "aaaaaaaa-0000-0000-0000-000000000003"
    assert entry.version == 3
    assert entry.supersession_type == "amendment"
    assert entry.chain_length == 3
    assert entry.supersedes == "aaaaaaaa-0000-0000-0000-000000000002"
    assert entry.snapshot_version == 7
    assert entry.projected_at == NOW


def test_explicit_chain_walks_to_the_tip_only_once():
    oldest = record("11111111-1111-1111-1111-111111111111", title="first", offset=0)
    middle = record(
        "22222222-2222-2222-2222-222222222222",
        title="second",
        tags=["supersedes:11111111"],
        offset=1,
    )
    tip = record(
        "33333333-3333-3333-3333-333333333333",
        title="third",
        tags=["supersedes:22222222"],
        offset=2,
    )

    entries = resolve_keychain_entries([oldest, middle, tip], version=3, now=NOW)

    assert list(entries) == ["explicit:11111111"]
    entry = entries["explicit:11111111"]
    assert entry.record_id == tip.id
    assert entry.version == 3
    assert entry.supersession_type == "explicit"
    assert entry.chain_ids == (oldest.id, middle.id, tip.id)
    assert entry.supersedes == middle.id


def test_middle_of_chain_does_not_create_a_second_instance():
    oldest = record("a1111111-1111-1111-1111-111111111111", offset=0)
    middle = record(
        "a2222222-2222-2222-2222-222222222222", tags=["supersedes:a1111111"], offset=1
    )
    tip = record("a3333333-3333-3333-3333-333333333333", tags=["supersedes:a2222222"], offset=2)

    # Deliberately out of order: prefix resolution must not depend on input order.
    entries = resolve_keychain_entries([middle, tip, oldest], version=1, now=NOW)

    assert len(entries) == 1
    assert entries["explicit:a1111111"].chain_length == 3


def test_orphan_supersedes_link_falls_back_to_single_issuance():
    orphan = record("deadbeef-0000-0000-0000-000000000000", tags=["supersedes:99999999"], offset=0)

    entries = resolve_keychain_entries([orphan], version=1, now=NOW)

    assert list(entries) == [f"single:{orphan.id}"]
    assert entries[f"single:{orphan.id}"].supersession_type == "none"


def test_backward_tag_alone_is_still_single_issuance():
    pointed_at = record("feedface-0000-0000-0000-000000000000", offset=0)
    reporter = record("feedface-0000-0000-0000-000000000001", tags=["superseded-by:feedface"], offset=1)

    entries = resolve_keychain_entries([pointed_at, reporter], version=1, now=NOW)

    assert set(entries) == {f"single:{pointed_at.id}", f"single:{reporter.id}"}


def test_explicit_chain_yields_to_an_amendment_member():
    chain_old = record("c0000000-0000-0000-0000-000000000001", title="base record", offset=0)
    chain_new = record(
        "c0000000-0000-0000-0000-000000000002",
        title="later 0badc0de — amendment v2: amended",
        tags=["supersedes:c0000000"],
        offset=1,
    )

    entries = resolve_keychain_entries([chain_old, chain_new], version=1, now=NOW)

    # Parity with the TypeScript resolver: when a member of an explicit chain is
    # already an amendment record, the whole explicit chain is dropped. The
    # older record is then represented by nothing, because entry 3 skips it as
    # a chain member. Faithful port, not a new behaviour.
    assert list(entries) == ["amendment:0badc0de"]


def test_asset_reference_attaches_to_the_folder_and_current_record():
    records = [
        record("aaaa1111-0000-0000-0000-000000000001", title="A 1234abcd — amendment v1: a", offset=0),
        record("aaaa1111-0000-0000-0000-000000000002", title="A 1234abcd — amendment v2: b", offset=1),
    ]
    assets = {
        "aaaa1111-0000-0000-0000-000000000002": AssetRef(
            canonical_asset_id="asset:nexus:nebula_agent_records:aaaa1111", asset_kind="agent_record"
        )
    }

    entry = resolve_keychain_entries(records, version=1, now=NOW, assets_by_record_id=assets)[
        "amendment:1234abcd"
    ]

    assert entry.asset_id == "asset:nexus:nebula_agent_records:aaaa1111"
    assert entry.asset_kind == "agent_record"


def test_entry_document_shape_matches_the_typescript_contract():
    single = record("bbbb2222-0000-0000-0000-000000000001", title="plain")

    doc = resolve_keychain_entries([single], version=4, now=NOW)[f"single:{single.id}"].to_doc()

    assert doc["current"]["record_id"] == single.id
    assert doc["current"]["created_at"] == "2026-09-01T00:00:00.000Z"
    assert doc["provenance"]["supersession_type"] == "none"
    assert doc["provenance"]["chain_ids"] == [single.id]
    assert doc["snapshot_version"] == 4
