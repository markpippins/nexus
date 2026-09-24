"""Tests for the Aspects binding port lifecycle (Aspect G2).

Covers the defects from implementation review record bc724f6b:
  - list_bindings row aliasing (tb.id AS binding_id vs dataclass field id)
  - unvalidated binding statuses / missing explicit lifecycle machine

All DB access goes through fake pools — no asyncpg or database required.
"""

import unittest
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from aspects.binding_port import (
    ALLOWED_TRANSITIONS,
    BINDING_STATUSES,
    AspectsBindingPort,
    TagBinding,
    _row_to_tag_binding,
    validate_binding_transition,
)


# ── row mapping ──────────────────────────────────────────────────────────

class RowMappingTests(unittest.TestCase):
    @staticmethod
    def _full_row(**overrides):
        row = {
            "id": uuid4(),
            "governed_tag_id": uuid4(),
            "governed_tag_name": "To Architect",
            "governed_normalized_name": "to-architect",
            "member_kind": "concept",
            "applies_to": "agent_record",
            "source_identity": "agent_record:r1",
            "source_revision": "rev-1",
            "namespace": "agent-record",
            "tag_key": "tag",
            "normalized_value": "to-architect",
            "expression_observation_id": "tagobs:abc",
            "status": "proposed",
            "bound_by": "engineer",
            "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "expired_at": None,
        }
        row.update(overrides)
        return row

    def test_row_with_id_maps_directly(self):
        binding_id = uuid4()
        binding = _row_to_tag_binding(self._full_row(id=binding_id))
        self.assertEqual(binding.id, binding_id)

    def test_row_with_binding_id_alias_is_remapped(self):
        """list_bindings aliases tb.id AS binding_id; the dataclass field is id."""
        binding_id = uuid4()
        row = self._full_row(binding_id=binding_id)
        del row["id"]
        binding = _row_to_tag_binding(row)
        self.assertEqual(binding.id, binding_id)
        self.assertNotIn("binding_id", vars(binding))


# ── lifecycle state machine ──────────────────────────────────────────────

class LifecycleMachineTests(unittest.TestCase):
    def test_status_vocabulary(self):
        self.assertEqual(
            set(BINDING_STATUSES), {"proposed", "approved", "rejected", "expired"}
        )

    def test_legal_transitions_pass(self):
        validate_binding_transition("proposed", "approved")
        validate_binding_transition("proposed", "rejected")
        validate_binding_transition("proposed", "expired")
        validate_binding_transition("approved", "expired")
        validate_binding_transition("rejected", "expired")

    def test_illegal_transitions_fail_closed(self):
        for current, new in [
            ("approved", "rejected"),   # a decided binding cannot be re-decided
            ("approved", "proposed"),   # no un-approving
            ("rejected", "approved"),   # no un-rejecting
            ("expired", "approved"),    # expired is terminal
            ("expired", "proposed"),
        ]:
            with self.assertRaises(ValueError):
                validate_binding_transition(current, new)

    def test_unknown_statuses_fail_closed(self):
        with self.assertRaises(ValueError):
            validate_binding_transition("governed", "approved")
        with self.assertRaises(ValueError):
            validate_binding_transition("proposed", "mixed")
        with self.assertRaises(ValueError):
            validate_binding_transition("", "approved")

    def test_expired_is_terminal_in_table(self):
        self.assertEqual(ALLOWED_TRANSITIONS["expired"], set())


# ── port behavior with a fake pool ───────────────────────────────────────

class _FakeConn:
    def __init__(self, fetchrow_results):
        self._fetchrow_results = list(fetchrow_results)
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


class PortLifecycleTests(IsolatedAsyncioTestCase):
    async def test_create_binding_rejects_expired_as_initial_status(self):
        port = AspectsBindingPort(pool=None)  # validation must precede pool use
        with self.assertRaises(ValueError):
            await port.create_binding(
                governed_tag_id=uuid4(),
                source_identity="agent_record:r1",
                source_revision="rev-1",
                namespace="agent-record",
                tag_key="tag",
                normalized_value="to-architect",
                status="expired",
            )

    async def test_create_binding_rejects_unknown_initial_status(self):
        port = AspectsBindingPort(pool=None)
        with self.assertRaises(ValueError):
            await port.create_binding(
                governed_tag_id=uuid4(),
                source_identity="agent_record:r1",
                source_revision="rev-1",
                namespace="agent-record",
                tag_key="tag",
                normalized_value="to-architect",
                status="governed",
            )

    async def test_update_binding_status_rejects_illegal_transition(self):
        conn = _FakeConn([{"status": "approved"}])  # current status from DB
        port = AspectsBindingPort(pool=_FakePool(conn))
        with self.assertRaises(ValueError):
            await port.update_binding_status(uuid4(), "rejected")
        self.assertEqual(len(conn.queries), 1)  # no UPDATE was issued

    async def test_update_binding_status_legal_transition_returns_binding(self):
        binding_id = uuid4()
        conn = _FakeConn([
            {"status": "proposed"},                      # current status read
            {                                            # UPDATE ... RETURNING
                "id": binding_id,
                "governed_tag_id": uuid4(),
                "source_identity": "agent_record:r1",
                "source_revision": "rev-1",
                "namespace": "agent-record",
                "tag_key": "tag",
                "normalized_value": "to-architect",
                "expression_observation_id": "tagobs:abc",
                "status": "expired",
                "bound_by": "engineer",
                "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
                "expired_at": datetime(2026, 9, 22, tzinfo=timezone.utc),
                "decided_by": None,
                "decided_at": None,
                "decision_note": None,
            },
            {                                            # governed tag detail read
                "name": "To Architect",
                "normalized_name": "to-architect",
                "member_kind": "concept",
                "applies_to": "agent_record",
            },
        ])
        port = AspectsBindingPort(pool=_FakePool(conn))
        binding = await port.update_binding_status(binding_id, "expired")
        self.assertIsInstance(binding, TagBinding)
        self.assertEqual(binding.id, binding_id)
        self.assertEqual(binding.governed_tag_name, "To Architect")
        self.assertEqual(binding.status, "expired")
        self.assertIsNotNone(binding.expired_at)

    async def test_update_binding_status_unknown_binding_returns_none(self):
        conn = _FakeConn([])  # status read returns nothing
        port = AspectsBindingPort(pool=_FakePool(conn))
        self.assertIsNone(await port.update_binding_status(uuid4(), "approved"))
        self.assertEqual(len(conn.queries), 1)  # read only, no update


if __name__ == "__main__":
    unittest.main()
