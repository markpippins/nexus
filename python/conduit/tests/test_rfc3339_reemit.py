"""Regression tests for the RFC3339 parse-then-re-emit path (WO-1 task 3).

The defect (work order d6398876): ``create_ticket_if_missing`` recomputed
``expires_at`` from the caller-supplied ``created_at`` with
``datetime.fromisoformat(created_at.replace("Z", ""))``.  When
``created_at`` arrived in the ``+00:00`` form (what
``datetime.now(timezone.utc).isoformat()`` natively produces), the
``.replace("Z", "")`` was a no-op, ``fromisoformat`` returned an *aware*
datetime, and the final ``.isoformat() + "Z"`` re-emitted the malformed
``+00:00Z`` suffix — the same bug class previously fixed on the
restart-builder dispatch path (test_restart_builder_timestamp.py).

The fix routes the re-emit through ``parse_rfc3339_utc`` — a single
format owner that accepts both UTC spellings and rejects naive input —
so ``+00:00``-form input can never reproduce ``+00:00Z``.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db_adapter import (  # noqa: E402
    DEFAULT_TICKET_TTL_HOURS,
    DBAdapter,
    _ConnectionProxy,
    _get_schema,
    parse_rfc3339_utc,
)


def _plus00_form(dt: datetime) -> str:
    """The +00:00 spelling (aware isoformat, no Z mangling)."""
    return dt.astimezone(timezone.utc).isoformat()


def _z_form(dt: datetime) -> str:
    """The Z spelling (what _utc_now_rfc3339() emits on main.py)."""
    return _plus00_form(dt).replace("+00:00", "Z")


class _CapturingConn:
    """Mock PG connection capturing INSERT params through a real proxy."""

    def __init__(self) -> None:
        self.captured: list[tuple[str, tuple]] = []
        self.mock_cursor = MagicMock()
        self.mock_cursor.rowcount = 1  # INSERT reported as applied
        self.mock_cursor.fetchone.return_value = None
        self.mock_cursor.fetchall.return_value = []
        self.mock_cursor.description = None

        def capture_execute(sql, params=None):
            self.captured.append((sql, params or ()))
            return self.mock_cursor

        self.mock_cursor.execute = MagicMock(side_effect=capture_execute)
        self.mock_conn = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cursor

    def proxy(self) -> _ConnectionProxy:
        return _ConnectionProxy(self.mock_conn, schema=_get_schema())


def _make_adapter() -> DBAdapter:
    db = DBAdapter.__new__(DBAdapter)
    db.db_path = "mock-dsn"
    db._schema = _get_schema()
    return db


def _last_insert_params(capturing: _CapturingConn) -> tuple:
    inserts = [(s, p) for s, p in capturing.captured if "INSERT INTO tickets" in s]
    assert inserts, f"no tickets INSERT captured: {capturing.captured}"
    return inserts[-1][1]


class TestParseRfc3339Utc(unittest.TestCase):
    def test_accepts_z_form(self):
        dt = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
        parsed = parse_rfc3339_utc(_z_form(dt))
        self.assertEqual(parsed, dt)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_accepts_plus00_form(self):
        dt = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
        parsed = parse_rfc3339_utc(_plus00_form(dt))
        self.assertEqual(parsed, dt)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            parse_rfc3339_utc("2026-09-29T12:00:00")

    def test_rejects_empty_and_non_string(self):
        for bad in ("", "   ", None, 12345):
            with self.assertRaises(ValueError):
                parse_rfc3339_utc(bad)

    def test_normalizes_offset_to_utc(self):
        # +02:00 offset normalizes to the same UTC instant.
        parsed = parse_rfc3339_utc("2026-09-29T14:00:00+02:00")
        self.assertEqual(parsed.hour, 12)
        self.assertEqual(parsed.tzinfo, timezone.utc)


class TestCreateTicketExpiresAtReemit(unittest.TestCase):
    """The +00:00-form defect: expires_at must never come out as +00:00Z."""

    def test_plus00_form_created_at_does_not_produce_plus00z(self):
        capturing = _CapturingConn()
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        created_at = _plus00_form(now_dt)  # e.g. 2026-09-29T12:00:00+00:00

        with patch.object(
            DBAdapter, "_get_connection", return_value=_cm(capturing.proxy())
        ):
            ticket_id = _make_adapter().create_ticket_if_missing(
                "plan-x", "builder", "restart-v078", created_at
            )

        self.assertIsNotNone(ticket_id)
        params = _last_insert_params(capturing)
        # (id, plan_id, role, created_by_receipt, created_at, objective,
        #  completion_criteria, owner, parent_ticket_id, spawn_reason,
        #  last_activity, expires_at, replacement_of)
        self.assertEqual(params[4], created_at)
        expires_at = params[11]
        self.assertFalse(
            expires_at.endswith("+00:00Z"),
            f"malformed re-emit reproduced: {expires_at!r}",
        )
        self.assertNotIn("+00:00Z", expires_at)
        self.assertTrue(expires_at.endswith("Z"))
        # Round-trips and equals created_at + TTL.
        parsed = datetime.fromisoformat(expires_at[:-1] + "+00:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed, now_dt + timedelta(hours=DEFAULT_TICKET_TTL_HOURS))

    def test_z_form_created_at_still_produces_z_form(self):
        capturing = _CapturingConn()
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        created_at = _z_form(now_dt)

        with patch.object(
            DBAdapter, "_get_connection", return_value=_cm(capturing.proxy())
        ):
            ticket_id = _make_adapter().create_ticket_if_missing(
                "plan-y", "builder", "restart-v078", created_at
            )

        self.assertIsNotNone(ticket_id)
        expires_at = _last_insert_params(capturing)[11]
        self.assertFalse("+00:00Z" in expires_at)
        self.assertTrue(expires_at.endswith("Z"))
        parsed = datetime.fromisoformat(expires_at[:-1] + "+00:00")
        self.assertEqual(parsed, now_dt + timedelta(hours=DEFAULT_TICKET_TTL_HOURS))

    def test_naive_created_at_is_rejected_loudly(self):
        # Naive input previously sailed through; now it must raise rather
        # than silently produce an ambiguous expiry.
        capturing = _CapturingConn()
        with patch.object(
            DBAdapter, "_get_connection", return_value=_cm(capturing.proxy())
        ):
            with self.assertRaises(ValueError):
                _make_adapter().create_ticket_if_missing(
                    "plan-z", "builder", "restart-v078", "2026-09-29T12:00:00"
                )
        # Nothing reached the tickets table (proxy's SET search_path aside).
        self.assertFalse(
            any("INSERT INTO tickets" in sql for sql, _ in capturing.captured),
            f"tickets INSERT ran despite naive input: {capturing.captured}",
        )


def _cm(value):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=value)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


if __name__ == "__main__":
    unittest.main()
