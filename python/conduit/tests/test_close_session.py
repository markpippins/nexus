"""First-close-wins session terminal outcomes.

Re-derived from the preserved record 80c6979a. The original fake matched an
older shape (`SELECT exit_code FROM sessions`); the adapter now reads
`(end_iso, exit_code)` and logs a conflicting late close at ERROR. The
contract under test is unchanged: the first close wins, and a later differing
exit code is reported without overwriting the stored outcome.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db_adapter import DBAdapter  # noqa: E402


class _Cursor:
    def __init__(self, rowcount=0, row=None):
        self.rowcount = rowcount
        self._row = row

    def fetchone(self):
        return self._row


class _SessionStore:
    """In-memory connection fake honoring close_session's SQL contract."""

    def __init__(self):
        self.session = {
            "id": "session-1",
            "is_running": 1,
            "end_iso": None,
            "exit_code": None,
            "last_activity": None,
        }
        self.followup_reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split()).lower()

        if normalized.startswith("update sessions"):
            self.assert_first_close_guard(normalized)
            end_iso, exit_code, last_activity, session_id = params
            if (session_id == self.session["id"]
                    and self.session["is_running"] == 1
                    and self.session["end_iso"] is None):
                self.session.update(
                    end_iso=end_iso,
                    is_running=0,
                    exit_code=exit_code,
                    last_activity=last_activity,
                )
                return _Cursor(rowcount=1)
            return _Cursor(rowcount=0)

        if normalized.startswith("select end_iso, exit_code from sessions"):
            self.followup_reads += 1
            if params[0] == self.session["id"]:
                return _Cursor(row=(self.session["end_iso"], self.session["exit_code"]))
            return _Cursor()

        raise AssertionError(f"Unexpected SQL: {sql}")

    @staticmethod
    def assert_first_close_guard(normalized_sql):
        if "where id=%s and is_running=1 and end_iso is null" not in normalized_sql:
            raise AssertionError(
                f"Session close is missing its first-close guard: {normalized_sql}"
            )

    def commit(self):
        pass


class CloseSessionTest(unittest.TestCase):
    def setUp(self):
        self.db = DBAdapter.__new__(DBAdapter)
        self.store = _SessionStore()
        self.connection_patch = patch.object(
            self.db, "_get_connection", return_value=self.store
        )
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)

    def test_duplicate_same_code_close_is_idempotent(self):
        self.assertTrue(self.db.close_session("session-1", 0))
        first_end_iso = self.store.session["end_iso"]

        self.assertFalse(self.db.close_session("session-1", 0))

        self.assertEqual(self.store.session["exit_code"], 0)
        self.assertEqual(self.store.session["end_iso"], first_end_iso)
        self.assertEqual(self.store.session["is_running"], 0)
        self.assertEqual(self.store.followup_reads, 1)

    def test_conflicting_close_preserves_first_terminal_result(self):
        self.assertTrue(self.db.close_session("session-1", 0))
        first_end_iso = self.store.session["end_iso"]

        with self.assertLogs("conduit.db_adapter", level="ERROR") as logs:
            self.assertFalse(self.db.close_session("session-1", 137))

        self.assertEqual(self.store.session["exit_code"], 0)
        self.assertEqual(self.store.session["end_iso"], first_end_iso)
        self.assertEqual(self.store.session["is_running"], 0)
        joined = " ".join(logs.output)
        self.assertIn("conflicting late session close ignored", joined)
        self.assertIn("stored_exit=0", joined)
        self.assertIn("incoming_exit=137", joined)

    def test_unknown_session_close_returns_false_without_conflict_log(self):
        # assertLogs asserts the presence of a log, so absence needs a spy:
        # a close for an unknown id must not be reported as a conflict.
        with patch("db_adapter._log") as log:
            self.assertFalse(self.db.close_session("session-unknown", 0))

        log.error.assert_not_called()
        self.assertEqual(self.store.followup_reads, 1)


if __name__ == "__main__":
    unittest.main()
