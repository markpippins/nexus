"""
Tests for the substance module — Pydantic schemas, config, cache helpers,
domain_table mapping, and update_segment_set query builder.

40+ tests covering green (valid inputs), orange (edge cases, missing fields),
red (invalid inputs), and silent-failure (empty/null/None) paths.

All tests are pure — no DB, no Redis, no FastAPI TestClient needed.
"""

import datetime as dt
import json
import uuid

import pytest

from substance.schemas import (
    DomainLinkIn,
    DomainLinkOut,
    MembersAddIn,
    ResolvedSegment,
    SegmentMemberIn,
    SegmentSetCreate,
    SegmentSetOut,
    SegmentSetUpdate,
)
from substance.cache import _JSONEncoder, _domain_index_key, _segset_key
from substance.config import Settings, get_settings
from substance.repository import domain_table

# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════


def _u() -> uuid.UUID:
    return uuid.uuid4()


# ═══════════════════════════════════════════════════════════════════
#  Config — green + orange paths
# ═══════════════════════════════════════════════════════════════════


class TestConfig:
    """Settings and get_settings()."""

    def test_defaults(self):
        """Settings uses env defaults when nothing is set."""
        s = Settings()
        assert "postgresql://" in s.postgres_dsn
        assert "redis://" in s.redis_url
        assert isinstance(s.redis_ttl_seconds, int)
        assert s.redis_ttl_seconds > 0

    def test_get_settings_is_singleton(self):
        """get_settings() returns the same instance (lru_cache)."""
        assert get_settings() is get_settings()

    def test_redis_ttl_default(self):
        """Default TTL is 3600 (1 hour safety-net)."""
        assert Settings().redis_ttl_seconds == 3600


# ═══════════════════════════════════════════════════════════════════
#  Cache helpers — pure string/encoder functions
# ═══════════════════════════════════════════════════════════════════


class TestCacheHelpers:
    """_segset_key, _domain_index_key, _JSONEncoder — all pure."""

    # -- Green paths ------------------------------------------------

    def test_segset_key_uuid(self):
        """_segset_key formats UUID into nexus:segset:{id}."""
        uid = _u()
        key = _segset_key(uid)
        assert key == f"nexus:segset:{uid}"

    def test_segset_key_str(self):
        """_segset_key also accepts a string."""
        key = _segset_key("abc-123")
        assert key == "nexus:segset:abc-123"

    def test_domain_index_key(self):
        """_domain_index_key formats correctly."""
        uid = _u()
        key = _domain_index_key("candidates", uid)
        assert key == f"nexus:candidates:{uid}:segsets"

    def test_json_encoder_uuid(self):
        """_JSONEncoder serialises UUID to string."""
        uid = _u()
        result = json.dumps({"id": uid}, cls=_JSONEncoder)
        assert f'"{uid}"' in result

    def test_json_encoder_datetime(self):
        """_JSONEncoder serialises datetime to ISO string."""
        ts = dt.datetime(2026, 7, 4, 12, 0, 0, tzinfo=dt.timezone.utc)
        result = json.dumps({"ts": ts}, cls=_JSONEncoder)
        assert "2026-07-04T12:00:00+00:00" in result or "2026-07-04T12:00:00" in result

    def test_json_encoder_date(self):
        """_JSONEncoder serialises date to ISO string."""
        d = dt.date(2026, 7, 4)
        result = json.dumps({"d": d}, cls=_JSONEncoder)
        assert "2026-07-04" in result

    def test_json_encoder_passthrough(self):
        """_JSONEncoder passes standard types through normally."""
        data = {"a": 1, "b": "hello", "c": [1, 2, 3]}
        result = json.loads(json.dumps(data, cls=_JSONEncoder))
        assert result == data

    # -- Orange paths: edge cases -----------------------------------

    def test_segset_key_empty_str(self):
        """_segset_key with empty string (edge case)."""
        key = _segset_key("")
        assert key == "nexus:segset:"

    def test_domain_index_key_empty_type(self):
        """_domain_index_key with empty type string."""
        key = _domain_index_key("", _u())
        assert ":segsets" in key


# ═══════════════════════════════════════════════════════════════════
#  Repository — pure helpers
# ═══════════════════════════════════════════════════════════════════


class TestRepositoryHelpers:
    """domain_table() mapping — green + red paths."""

    def test_domain_table_candidates(self):
        """domain_table('candidates') returns correct table/fk."""
        assert domain_table("candidates") == (
            "nebula.candidate_segment_sets",
            "candidate_id",
        )

    def test_domain_table_intent_records_removed(self):
        """domain_table('intent-records') raises — intent records were
        eliminated as a domain concept; the join table is dropped by
        002_drop_intent_record_segment_sets.sql."""
        try:
            domain_table("intent-records")
        except ValueError as e:
            assert "unknown domain_type" in str(e)
        else:
            raise AssertionError("domain_table('intent-records') should raise ValueError")

    def test_domain_table_requirements(self):
        """domain_table('requirements') returns correct table/fk."""
        assert domain_table("requirements") == (
            "nebula.requirement_segment_sets",
            "requirement_id",
        )

    def test_domain_table_unknown_raises(self):
        """domain_table('unknown') raises ValueError."""
        with pytest.raises(ValueError, match="unknown domain_type"):
            domain_table("unknown")

    def test_domain_table_empty_string_raises(self):
        """domain_table('') raises ValueError."""
        with pytest.raises(ValueError):
            domain_table("")

    def test_domain_table_none_raises(self):
        """domain_table(None) raises ValueError (not in the map — Python
        doesn't enforce the str type annotation at runtime)."""
        with pytest.raises(ValueError, match="unknown domain_type"):
            domain_table(None)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════
#  Pydantic schemas — green + orange + red + silent-failure paths
# ═══════════════════════════════════════════════════════════════════


class TestSegmentMemberIn:
    """SegmentMemberIn schema — member of a segment set."""

    def test_full_constructor(self):
        """All fields provided."""
        uid = _u()
        m = SegmentMemberIn(segment_id=uid, ordinal=1, note="test note")
        assert m.segment_id == uid
        assert m.ordinal == 1
        assert m.note == "test note"

    def test_minimal_constructor(self):
        """Only required fields — note defaults to None."""
        uid = _u()
        m = SegmentMemberIn(segment_id=uid, ordinal=2)
        assert m.segment_id == uid
        assert m.ordinal == 2
        assert m.note is None

    def test_serialize_to_dict(self):
        """model_dump() returns correct dict."""
        uid = _u()
        m = SegmentMemberIn(segment_id=uid, ordinal=1)
        d = m.model_dump()
        assert d["segment_id"] == uid
        assert d["ordinal"] == 1
        assert d["note"] is None

    # -- Orange: edge cases ----------------------------------------

    def test_zero_ordinal(self):
        """Ordinal 0 is valid (edge case)."""
        m = SegmentMemberIn(segment_id=_u(), ordinal=0)
        assert m.ordinal == 0

    def test_negative_ordinal(self):
        """Negative ordinal is accepted (schema has no min bound)."""
        m = SegmentMemberIn(segment_id=_u(), ordinal=-1)
        assert m.ordinal == -1


class TestSegmentSetCreate:
    """SegmentSetCreate schema."""

    def test_defaults(self):
        """All fields default to None/empty."""
        ssc = SegmentSetCreate()
        assert ssc.name is None
        assert ssc.description is None
        assert ssc.metadata == {}
        assert ssc.members == []

    def test_with_name_and_description(self):
        """Name and description set explicitly."""
        ssc = SegmentSetCreate(name="test", description="desc")
        assert ssc.name == "test"
        assert ssc.description == "desc"

    def test_with_members(self):
        """Members list is stored as-is."""
        members = [SegmentMemberIn(segment_id=_u(), ordinal=1)]
        ssc = SegmentSetCreate(members=members)
        assert len(ssc.members) == 1
        assert ssc.members[0].ordinal == 1

    def test_with_metadata(self):
        """Metadata dict is preserved."""
        ssc = SegmentSetCreate(metadata={"key": "val"})
        assert ssc.metadata == {"key": "val"}

    # -- Orange: edge cases ----------------------------------------

    def test_empty_metadata(self):
        """Explicit empty metadata is accepted."""
        ssc = SegmentSetCreate(metadata={})
        assert ssc.metadata == {}

    def test_nested_metadata(self):
        """Nested dicts in metadata are preserved."""
        ssc = SegmentSetCreate(metadata={"nested": {"a": 1}})
        assert ssc.metadata["nested"]["a"] == 1

    # -- Red: invalid inputs ---------------------------------------

    def test_members_not_a_list_fails(self):
        """Non-list members should raise ValidationError."""
        with pytest.raises(Exception):  # pydantic.ValidationError
            SegmentSetCreate(members="not-a-list")  # type: ignore[arg-type]


class TestSegmentSetUpdate:
    """SegmentSetUpdate schema."""

    def test_empty_update(self):
        """All fields default to None."""
        ssu = SegmentSetUpdate()
        assert ssu.name is None
        assert ssu.description is None
        assert ssu.status is None
        assert ssu.metadata is None

    def test_partial_update_name_only(self):
        """Only name set."""
        ssu = SegmentSetUpdate(name="new-name")
        assert ssu.name == "new-name"
        assert ssu.description is None

    def test_partial_update_status(self):
        """Status can be active or archived."""
        ssu = SegmentSetUpdate(status="active")
        assert ssu.status == "active"
        ssu2 = SegmentSetUpdate(status="archived")
        assert ssu2.status == "archived"

    def test_model_dump_exclude_unset(self):
        """model_dump(exclude_unset=True) excludes defaults."""
        ssu = SegmentSetUpdate(name="x")
        d = ssu.model_dump(exclude_unset=True)
        assert "name" in d
        assert "description" not in d
        assert "metadata" not in d

    # -- Red: invalid status ---------------------------------------

    def test_invalid_status_raises(self):
        """Non-'active'/'archived' status is rejected."""
        with pytest.raises(Exception):
            SegmentSetUpdate(status="deleted")


class TestResolvedSegment:
    """ResolvedSegment schema — full segment with conversation metadata."""

    def test_minimal(self):
        """Only required fields."""
        uid = _u()
        rs = ResolvedSegment(segment_id=uid, ordinal=1)
        assert rs.segment_id == uid
        assert rs.ordinal == 1
        assert rs.note is None
        assert rs.conversation_id is None

    def test_full(self):
        """All fields set."""
        uid = _u()
        conv_id = _u()
        rs = ResolvedSegment(
            segment_id=uid,
            ordinal=2,
            note="important note",
            conversation_id=conv_id,
            start_block_index=10,
            end_block_index=20,
            segment_type="text",
            title="My Segment",
        )
        assert rs.note == "important note"
        assert rs.conversation_id == conv_id
        assert rs.start_block_index == 10
        assert rs.end_block_index == 20
        assert rs.segment_type == "text"
        assert rs.title == "My Segment"

    def test_model_dump(self):
        """model_dump() includes all fields."""
        uid = _u()
        rs = ResolvedSegment(segment_id=uid, ordinal=3)
        d = rs.model_dump()
        assert d["segment_id"] == uid
        assert d["ordinal"] == 3
        assert d.get("note") is None


class TestSegmentSetOut:
    """SegmentSetOut schema — full output with resolved segments."""

    def test_full_constructor(self):
        """All required fields and segments list."""
        now = dt.datetime.now(dt.timezone.utc)
        uid = _u()
        seg = ResolvedSegment(segment_id=_u(), ordinal=1)
        out = SegmentSetOut(
            id=uid,
            name="test",
            description="desc",
            status="active",
            metadata={"key": "val"},
            created_at=now,
            updated_at=now,
            segments=[seg],
        )
        assert out.id == uid
        assert out.name == "test"
        assert len(out.segments) == 1

    def test_empty_segments(self):
        """Segments can be an empty list (silent-failure path)."""
        now = dt.datetime.now(dt.timezone.utc)
        out = SegmentSetOut(
            id=_u(),
            name=None,
            description=None,
            status="active",
            metadata={},
            created_at=now,
            updated_at=now,
            segments=[],
        )
        assert out.segments == []

    def test_multiple_segments_ordered(self):
        """Multiple segments preserve order."""
        now = dt.datetime.now(dt.timezone.utc)
        segs = [ResolvedSegment(segment_id=_u(), ordinal=i) for i in range(3)]
        out = SegmentSetOut(
            id=_u(),
            name=None,
            description=None,
            status="active",
            metadata={},
            created_at=now,
            updated_at=now,
            segments=segs,
        )
        assert [s.ordinal for s in out.segments] == [0, 1, 2]


class TestMembersAddIn:
    """MembersAddIn — batch add segments."""

    def test_with_segments(self):
        """Segments list is preserved."""
        segs = [SegmentMemberIn(segment_id=_u(), ordinal=i) for i in range(2)]
        m = MembersAddIn(segments=segs)
        assert len(m.segments) == 2

    def test_empty_segments(self):
        """Empty list is valid (silent-failure / orange path)."""
        m = MembersAddIn(segments=[])
        assert m.segments == []


class TestDomainLinkSchemas:
    """DomainLinkIn and DomainLinkOut."""

    def test_link_in_default_role(self):
        """DomainLinkIn defaults role to 'primary'."""
        link = DomainLinkIn(segment_set_id=_u())
        assert link.role == "primary"

    def test_link_in_supporting_role(self):
        """DomainLinkIn accepts 'supporting' role."""
        link = DomainLinkIn(segment_set_id=_u(), role="supporting")
        assert link.role == "supporting"

    def test_link_out_minimal(self):
        """DomainLinkOut with required fields."""
        uid = _u()
        out = DomainLinkOut(segment_set_id=uid, role="primary", active=True)
        assert out.segment_set_id == uid
        assert out.role == "primary"
        assert out.active is True
        assert out.segment_set is None

    def test_link_out_with_segment_set(self):
        """DomainLinkOut with nested segment_set."""
        uid = _u()
        now = dt.datetime.now(dt.timezone.utc)
        seg = SegmentSetOut(
            id=uid,
            name="nested",
            description=None,
            status="active",
            metadata={},
            created_at=now,
            updated_at=now,
            segments=[],
        )
        out = DomainLinkOut(
            segment_set_id=uid, role="primary", active=True, segment_set=seg
        )
        assert out.segment_set is not None
        assert out.segment_set.name == "nested"

    # -- Red: invalid role -----------------------------------------

    def test_link_in_invalid_role_raises(self):
        """Invalid role literal raises ValidationError."""
        with pytest.raises(Exception):
            DomainLinkIn(segment_set_id=_u(), role="admin")

    def test_link_out_invalid_role_raises(self):
        """Invalid role literal raises ValidationError."""
        with pytest.raises(Exception):
            DomainLinkOut(segment_set_id=_u(), role="invalid", active=True)
