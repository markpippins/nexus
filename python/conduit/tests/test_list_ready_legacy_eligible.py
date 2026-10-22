"""Regression tests for _list_ready() legacy-eligibility (planner cd206980).

W-A 8261653 was reconciled every minute but never consumed: its only
execution request (business_key legacy-plan-8261653, created by
resolve_request_for_receipt() tier 2 for a REAL plan) carries
intent_type='legacy', and the D-T19-2(b) guard excluded every such row
even when its source_plan_id was in the eligible set (live open ticket).

Eligibility is scoped by source_plan_id IN (eligible plans). The
intent_type predicate is gone; these tests pin that contract without a
live DB by capturing the SQL _list_ready issues.
"""

import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution_worker import _list_ready  # noqa: E402


class FakeCursor:
    def __init__(self, rows, conn):
        self._rows = rows
        self._conn = conn

    def dict_fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return FakeCursor(self._rows, self)


def make_db(rows):
    """DBAdapter stand-in: _get_connection() yields a recording fake."""
    db = MagicMock()
    conn = FakeConn(rows)

    @contextmanager
    def _cm():
        yield conn

    db._get_connection.side_effect = _cm
    db._fake_conn = conn
    return db


LEGACY_ROW = {
    "id": "8d3258a3-61d3-469a-bc5a-dfcd02d1ca94",
    "business_key": "legacy-plan-8261653",
    "status": "READY",
    "intent_type": "legacy",
    "source_plan_id": "8261653",
}

TASK_ROW = {
    "id": "aaaaaaaa-0000-0000-0000-000000000001",
    "business_key": "task-8261653",
    "status": "READY",
    "intent_type": "task",
    "source_plan_id": "8261653",
}


class TestListReadyLegacyEligible(unittest.TestCase):
    """A legacy bridge row for an eligible plan must be consumable."""

    def test_legacy_row_for_eligible_plan_is_not_excluded(self):
        db = make_db([LEGACY_ROW, TASK_ROW])
        rows = _list_ready(db, include_legacy=False, eligible_plan_ids={"8261653"})
        sql, params = db._fake_conn.statements[0]
        # The regression: old SQL carried
        #   AND intent_type IS DISTINCT FROM 'legacy'
        # which skipped the W-A request despite its live ticket.
        self.assertNotIn("intent_type", sql)
        self.assertEqual([r["id"] for r in rows],
                         [LEGACY_ROW["id"], TASK_ROW["id"]])

    def test_scoping_still_by_eligible_source_plan_id(self):
        db = make_db([LEGACY_ROW])
        _list_ready(db, include_legacy=False, eligible_plan_ids={"8261653"})
        sql, params = db._fake_conn.statements[0]
        self.assertIn("source_plan_id IN", sql)
        self.assertEqual(tuple(params), ("8261653",))

    def test_empty_eligible_set_touches_no_db(self):
        db = make_db([LEGACY_ROW])
        rows = _list_ready(db, include_legacy=False, eligible_plan_ids=set())
        self.assertEqual(rows, [])
        self.assertEqual(db._fake_conn.statements, [])

    def test_include_legacy_uses_status_path(self):
        db = make_db([LEGACY_ROW])
        db.get_requests_by_status.return_value = [LEGACY_ROW]
        rows = _list_ready(db, include_legacy=True, eligible_plan_ids=set())
        db.get_requests_by_status.assert_called_once_with("READY", limit=100)
        self.assertEqual(rows, [LEGACY_ROW])


if __name__ == "__main__":
    unittest.main()
