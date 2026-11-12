#!/usr/bin/env python3
"""Hermetic tests for the WorkRequest disposition verifier.

No database: every test drives the pure artifact/live validators with the real
ratified JSON (deep-copied) and constructed live rows. The live read is a
SELECT-only contract; the SQL constants are pinned against mutating keywords.

The negative controls are the point: the verifier must fail on a changed
verdict distribution, a missing MERGE target, a non-PROMOTE target, a live
status drift, a vanished 8261668 gap, and any mutating SQL.

Dual-runnable: pytest or python3 bin/tests/test_workrequest_disposition_verifier.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1]
REPO = BIN.parent
MODULE_PATH = BIN / "verify-workrequest-disposition.py"
ARTIFACT = REPO / "docs" / "workrequest-disposition-amendment.json"

_spec = importlib.util.spec_from_file_location("verify_workrequest_disposition", MODULE_PATH)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _doc() -> dict:
    return copy.deepcopy(json.loads(ARTIFACT.read_text()))


def _rows(doc: dict) -> list[dict]:
    return [
        {
            "row_id": r["row_id"],
            "business_status": r["business_status"],
            "legacy_id": r.get("legacy_id", ""),
            "plan_number": r.get("plan_number", ""),
            "plan_status": r.get("plan_status", ""),
            "plan_id": "",
        }
        for r in doc["rows"]
    ]


class ArtifactValidationTest(unittest.TestCase):
    def test_real_ratified_artifact_is_valid(self):
        self.assertEqual([], mod.validate_artifact(_doc()))

    def test_status_must_be_ratified(self):
        doc = _doc()
        doc["status"] = "proposed_for_ratification"
        self.assertTrue(any("status" in e for e in mod.validate_artifact(doc)))

    def test_row_count_is_pinned_to_46(self):
        doc = _doc()
        doc["rows"].pop()
        self.assertTrue(any("row count" in e for e in mod.validate_artifact(doc)))

    def test_duplicate_row_id_is_rejected(self):
        doc = _doc()
        doc["rows"][1]["row_id"] = doc["rows"][0]["row_id"]
        errors = mod.validate_artifact(doc)
        self.assertTrue(any("duplicate row ids" in e for e in errors), errors)

    def test_verdict_distribution_is_pinned(self):
        doc = _doc()
        # Flip one RETIRE to PROMOTE: 13/22 must fail.
        row = next(r for r in doc["rows"] if r["verdict"] == "RETIRE")
        row["verdict"] = "PROMOTE"
        self.assertTrue(any("verdict counts" in e for e in mod.validate_artifact(doc)))

    def test_merge_requires_target(self):
        doc = _doc()
        row = next(r for r in doc["rows"] if r["verdict"] == "MERGE")
        row["target"] = None
        self.assertTrue(any("MERGE requires" in e for e in mod.validate_artifact(doc)))

    def test_non_merge_must_have_null_target(self):
        doc = _doc()
        row = next(r for r in doc["rows"] if r["verdict"] == "RETIRE")
        row["target"] = doc["rows"][0]["row_id"]
        self.assertTrue(any("must have a null target" in e for e in mod.validate_artifact(doc)))

    def test_merge_target_must_be_present_and_promote(self):
        doc = _doc()
        row = next(r for r in doc["rows"] if r["verdict"] == "MERGE")
        row["target"] = "00000000-0000-4000-8000-000000000000"
        self.assertTrue(any("is not in the ledger" in e for e in mod.validate_artifact(doc)))

        doc = _doc()
        row = next(r for r in doc["rows"] if r["verdict"] == "MERGE")
        target = next(r for r in doc["rows"] if r["row_id"] == row["target"])
        target["verdict"] = "RETIRE"
        self.assertTrue(any("expected PROMOTE" in e for e in mod.validate_artifact(doc)))

    def test_rationale_and_ledger_ref_are_required(self):
        doc = _doc()
        doc["rows"][0]["rationale"] = "  "
        doc["rows"][1]["ledger_ref"] = ""
        errors = mod.validate_artifact(doc)
        self.assertTrue(any("rationale is empty" in e for e in errors), errors)
        self.assertTrue(any("ledger_ref is empty" in e for e in errors), errors)

    def test_plan_status_distribution_is_pinned(self):
        doc = _doc()
        row = next(r for r in doc["rows"] if r["plan_status"] == "pending")
        row["plan_status"] = "completed"
        self.assertTrue(any("plan status counts" in e for e in mod.validate_artifact(doc)))

    def test_live_queue_row_must_project_pending_8261650(self):
        doc = _doc()
        row = next(r for r in doc["rows"] if r["row_id"].startswith("6217db7f"))
        row["plan_status"] = "completed"
        errors = mod.validate_artifact(doc)
        self.assertTrue(any("6217db7f" in e for e in errors), errors)

    def test_architecture_rules_are_pinned(self):
        doc = _doc()
        doc["architecture_rules"]["business_status_mutation"] = True
        self.assertTrue(any("architecture_rules.business_status_mutation" in e for e in mod.validate_artifact(doc)))


class LiveValidationTest(unittest.TestCase):
    def test_real_snapshot_matches_live_shape(self):
        doc = _doc()
        errors, warnings = mod.validate_live(doc, _rows(doc), ["8261668"])
        self.assertEqual([], errors)
        self.assertTrue(any("8261668" in w for w in warnings), warnings)
        self.assertTrue(any("plan_id is still 0/46" in w for w in warnings), warnings)

    def test_status_drift_fails(self):
        doc = _doc()
        rows = _rows(doc)
        rows[0]["business_status"] = "APPROVED"
        errors, _ = mod.validate_live(doc, rows, ["8261668"])
        self.assertTrue(any("business_status" in e for e in errors), errors)

    def test_missing_and_extra_rows_fail(self):
        doc = _doc()
        rows = _rows(doc)
        missing = rows.pop(0)
        errors, _ = mod.validate_live(doc, rows, ["8261668"])
        self.assertTrue(any("missing 1 row" in e for e in errors), errors)
        self.assertTrue(any(missing["row_id"][:8] in e for e in errors), errors)

        rows = _rows(doc)
        rows.append({**rows[0], "row_id": "00000000-0000-4000-8000-000000000000"})
        errors, _ = mod.validate_live(doc, rows, ["8261668"])
        self.assertTrue(any("not in the artifact" in e for e in errors), errors)

    def test_plan_status_drift_fails(self):
        doc = _doc()
        rows = _rows(doc)
        row = next(r for r in rows if r["plan_number"] == "8261650")
        row["plan_status"] = "completed"
        errors, _ = mod.validate_live(doc, rows, ["8261668"])
        self.assertTrue(any("plan_status" in e for e in errors), errors)

    def test_missing_8261668_gap_is_an_error(self):
        doc = _doc()
        errors, warnings = mod.validate_live(doc, _rows(doc), [])
        self.assertTrue(any("8261668 gap disappeared" in e for e in errors), errors)
        self.assertFalse(any("8261668" in w for w in warnings), warnings)

    def test_plan_id_backfill_is_a_warning_not_a_failure(self):
        doc = _doc()
        rows = _rows(doc)
        rows[0]["plan_id"] = "8261653"
        errors, warnings = mod.validate_live(doc, rows, ["8261668"])
        self.assertEqual([], errors)
        self.assertTrue(any("populated on 1/46" in w for w in warnings), warnings)


class ReadOnlyContractTest(unittest.TestCase):
    def test_sql_is_select_only(self):
        sql = mod.SQL_LIVE + mod.SQL_PENDING_WITHOUT_WR
        self.assertIsNone(
            re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT)\b", sql, re.I),
            sql,
        )

    def test_session_is_set_read_only_in_source(self):
        source = MODULE_PATH.read_text()
        self.assertIn("set_session(readonly=True", source)

    def test_summary_reports_ratified_counts(self):
        doc = _doc()
        summary = mod.summarize(doc, _rows(doc))
        self.assertEqual("ratified", summary["artifact_status"])
        self.assertEqual(46, summary["total"])
        self.assertEqual(mod.EXPECTED_COUNTS, summary["verdicts"])
        self.assertEqual(mod.EXPECTED_STATUS, summary["business_status"])


if __name__ == "__main__":
    unittest.main()
