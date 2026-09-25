"""Regression tests for the Conduit restart-builder dispatch path."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main  # noqa: E402


class TestRestartBuilderTimestamp(unittest.TestCase):
    """restart-builder must pass a PostgreSQL-safe timestamp to ticket creation."""

    def test_restart_builder_uses_single_rfc3339_utc_designator(self):
        db = MagicMock()
        db.is_circuit_breaker_tripped.return_value = False
        db.get_active_session.return_value = None
        db.get_plan_by_id.return_value = {
            "id": "plan-wb4",
            "title": "W-B4 dispatch test",
            "goal": "exercise restart-builder",
        }
        registry = MagicMock()

        with patch.object(main, "get_model", return_value=MagicMock()), patch.object(
            main, "_dispatch_one"
        ) as dispatch_one:
            main.dispatch_single_plan("plan-wb4", db, registry, force=True)

        db.create_ticket_if_missing.assert_called_once()
        args = db.create_ticket_if_missing.call_args.args
        self.assertEqual(args[:3], ("plan-wb4", "builder", "restart-v078"))

        created_at = args[3]
        self.assertTrue(created_at.endswith("Z"))
        self.assertFalse(created_at.endswith("+00:00Z"))
        self.assertNotIn("+00:00Z", created_at)

        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        self.assertEqual(parsed.tzinfo, timezone.utc)
        dispatch_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()
