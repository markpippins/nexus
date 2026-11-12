#!/usr/bin/env python3
"""Focused tests for the inert, candidate-scoped Track A evaluator."""
import json
import subprocess
import unittest

import candidate_track_a as track_a


class TestClassifyCandidate(unittest.TestCase):
    def row(self, **overrides):
        result = {
            "id": "11111111-1111-4111-8111-111111111111",
            "title": "candidate",
            "compilation_readiness": "0.70",
            "status": None,
            "asset_id_present": True,
            "system_id_present": True,
            "mapping_evidence": {"status": "resolved", "value": True},
        }
        result.update(overrides)
        return result

    def test_eligible_at_threshold_with_resolved_true_bridge(self):
        result = track_a.classify_candidate(self.row(), "ignored")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "eligible")

    def test_readiness_below_threshold_is_excluded(self):
        result = track_a.classify_candidate(
            self.row(compilation_readiness="0.699"), "ignored")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "readiness_below_threshold_or_missing")

    def test_missing_asset_or_mapping_fails_closed(self):
        cases = [
            ({"asset_id_present": False}, "asset_id_missing"),
            ({"system_id_present": False}, "system_id_missing"),
            ({"mapping_evidence": None}, "system_mapped_unresolved_or_false"),
            ({"mapping_evidence": {"status": "unavailable", "value": None}},
             "system_mapped_unresolved_or_false"),
            ({"mapping_evidence": {"status": "resolved", "value": False}},
             "system_mapped_unresolved_or_false"),
            ({"mapping_evidence": {"status": "resolved", "value": "true"}},
             "system_mapped_unresolved_or_false"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                result = track_a.classify_candidate(self.row(**changes), "ignored")
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], reason)

    def test_non_pending_status_is_excluded(self):
        for status in ("promoted", "discarded", "rejected", "superseded", "struck", "approved"):
            with self.subTest(status=status):
                result = track_a.classify_candidate(self.row(status=status), "ignored")
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], "terminal_status")

    def test_missing_candidate_is_excluded(self):
        result = track_a.classify_candidate(None, "11111111-1111-4111-8111-111111111111")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "candidate_not_found")


class TestReadOnlyQuery(unittest.TestCase):
    def test_query_is_candidate_scoped_and_read_only(self):
        query = track_a.candidate_query("11111111-1111-4111-8111-111111111111").upper()
        self.assertIn("BEGIN READ ONLY", query)
        self.assertIn("COMMIT", query)
        self.assertIn("WHERE ID = '11111111-1111-4111-8111-111111111111'::UUID", query)
        self.assertIn("LIMIT 1", query)
        self.assertIn("READ_SHRAPNEL_STATE_MEMBER", query)
        self.assertIn("AND C.STATUS IS NULL", query)
        self.assertNotIn("UPDATE NEBULA.HARVEST_CANDIDATES", query)
        self.assertNotIn("INSERT INTO", query)
        self.assertNotIn("DELETE FROM", query)

    def test_evaluate_uses_one_candidate_and_parses_output(self):
        candidate_id = "11111111-1111-4111-8111-111111111111"
        row = {
            "id": candidate_id,
            "title": "candidate",
            "compilation_readiness": 0.70,
            "status": None,
            "asset_id_present": True,
            "system_id_present": True,
            "mapping_evidence": {"status": "resolved", "value": True},
        }
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0, json.dumps(row), "")

        result = track_a.evaluate(candidate_id, runner=runner)
        self.assertTrue(result["eligible"])
        self.assertIn(f"'{candidate_id}'::uuid", seen["command"][-1])
        self.assertEqual(seen["kwargs"]["timeout"], 15)
        self.assertIn("BEGIN READ ONLY", seen["command"][-1])

    def test_invalid_uuid_is_rejected_before_database_call(self):
        called = False

        def runner(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("database must not be called")

        with self.assertRaisesRegex(ValueError, "must be a UUID"):
            track_a.evaluate("not-a-uuid", runner=runner)
        self.assertFalse(called)

    def test_query_failure_is_not_a_pass(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            track_a.evaluate("11111111-1111-4111-8111-111111111111", runner=runner)


if __name__ == "__main__":
    unittest.main(verbosity=2)
