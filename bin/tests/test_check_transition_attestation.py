"""Hermetic tests for bin/check_transition_attestation.py.

Exercises the pure logic — classification, verdict, status rollup — with
in-memory inputs. No database, no network: the psycopg2 surface is only
touched behind main(), which is not imported here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # worktree root
_spec = importlib.util.spec_from_file_location(
    "check_transition_attestation", REPO / "bin" / "check_transition_attestation.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

import pytest  # noqa: E402

_wb3_spec = importlib.util.spec_from_file_location(
    "wb3_sweep_log_for_waivers", REPO / "bin" / "wb3_sweep_log.py")
wb3 = importlib.util.module_from_spec(_wb3_spec)
_wb3_spec.loader.exec_module(wb3)


class TestClassify:
    def test_empty_is_all_clean(self):
        assert mod.classify({}) == {"unattested": [], "attested": []}

    def test_zero_events_is_unattested(self):
        assert mod.classify({"t1": 0})["unattested"] == ["t1"]

    def test_any_event_attests(self):
        assert mod.classify({"t1": 1, "t2": 11})["attested"] == ["t1", "t2"]
        assert mod.classify({"t1": 1, "t2": 11})["unattested"] == []

    def test_mixed_split_deterministic_order(self):
        out = mod.classify({"zb": 0, "za": 0, "ok1": 3, "ok2": 1})
        assert out["unattested"] == ["za", "zb"]
        assert out["attested"] == ["ok1", "ok2"]


class TestVerdict:
    def test_clean_when_no_unattested(self):
        assert mod.verdict_from_counts(0, 9999) == "CLEAN"

    def test_drift_on_any_recent_unattested(self):
        assert mod.verdict_from_counts(1, 0) == "DRIFT"

    def test_historical_mass_never_fails(self):
        # The whole point of the cutoff: June-era silence must not fail.
        assert mod.verdict_from_counts(0, 363) == "CLEAN"


class TestWaivers:
    """Ruled narrow waiver (architect ruling 6b42dd3f): enumerated rows
    attested by closure evidence instead of events; never a broad
    suppression surface."""

    COHORT = "wb3_pre_fix_expired_generation"
    COHORT_RULING = "ea38297d-1f4e-4cc5-8cda-ef8273702bf8"
    WINDOW_FROM = datetime.fromisoformat("2026-06-18T00:00:00+00:00")
    WINDOW_TO = datetime.fromisoformat("2026-07-27T00:00:00+00:00")
    # sha256 of the sorted 89 ticket ids (newline-joined). Pins exact cohort
    # membership: adding, dropping, or swapping a row fails even when counts
    # agree, which is what "enumerated, never broad" means in practice.
    IDS_SHA256 = "5e2de948a5c71c0fc5d3c19db44de4091291d01ded10dfe7e323bd9aa546a50a"

    def _registry(self):
        return json.loads(mod.WAIVER_FILE.read_text())

    def _cohort_rows(self, data):
        return [w for w in data["waivers"] if w.get("cohort") == self.COHORT]

    def _write(self, tmp_path, data):
        path = tmp_path / "waivers.json"
        path.write_text(json.dumps(data))
        return path

    def test_load_real_waiver_file(self):
        # The shipped file must parse, carry the ruling reference, and contain
        # exactly the ruled rows — no more (narrow by ruling), no less (a
        # silently-empty list would be a hole).
        #
        # Two rulings are in force: 6b42dd3f (the two enrolled gen-1 rows) and
        # ea38297d (the W-B3 cohort `wb3_pre_fix_expired_generation`, 89
        # enumerated pre-PR#560 expired generations). The cohort is admissible
        # ONLY because ea38297d rules it explicitly; a third party adding rows
        # without a ruling still fails this test.
        data = self._registry()
        assert data["ruling"]["decision_record_id"] == "6b42dd3f-ae62-4abf-8cce-fdc01c41032e"
        assert data["ruling"]["reason_code"] == "pre_event_emission_fix"
        cohort = data["cohorts"][self.COHORT]
        assert cohort["ruling_ref"] == self.COHORT_RULING
        assert cohort["count"] == 89
        assert cohort["expires_at_from"] == "2026-06-18T00:00:00Z"
        assert cohort["expires_at_to"] == "2026-07-27T00:00:00Z"
        cohort_rows = self._cohort_rows(data)
        assert len(cohort_rows) == 89
        ids = [w["ticket_id"] for w in cohort_rows]
        assert len(ids) == len(set(ids)) == 89
        assert set(ids) == set(cohort["ticket_ids"])
        assert hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest() == self.IDS_SHA256
        assert {w["waiver_ref"] for w in cohort_rows} == {self.COHORT_RULING}
        for w in cohort_rows:
            expires_at = datetime.fromisoformat(w["expires_at"].replace("Z", "+00:00"))
            assert self.WINDOW_FROM <= expires_at < self.WINDOW_TO
        legacy = {w["ticket_id"] for w in data["waivers"] if not w.get("cohort")}
        assert legacy == {
            "ticket-8261654-builder-caea50cc-5bc9-4405-b0c1-8abb5de79ad1",
            "ticket-8261654-reviewer-1790105437937",
        }
        ids = {w["ticket_id"] for w in data["waivers"]}
        assert len(ids) == 91
        loaded = mod.load_waiver_ids(mod.WAIVER_FILE)
        assert set(loaded) == ids
        assert loaded["ticket-8261654-reviewer-1790105437937"]["closure_evidence"]

    def test_cohort_refuses_post_cutoff_row(self, tmp_path):
        # A row with an enumerated id but an expiry after PR #560's fix must
        # not ride the cohort: the ruling admits only the closed historical set.
        data = self._registry()
        self._cohort_rows(data)[0]["expires_at"] = "2026-08-01T00:00:00+00:00"
        with pytest.raises(ValueError, match="window"):
            wb3.load_waivers(self._write(tmp_path, data))

    def test_cohort_refuses_unenumerated_row(self, tmp_path):
        # Swapping a row for an id outside the ruling's enumeration is broader
        # than the ruling and must be refused, not silently waived.
        data = self._registry()
        self._cohort_rows(data)[0]["ticket_id"] = "ticket-not-enumerated"
        with pytest.raises(ValueError, match="enumerated"):
            wb3.load_waivers(self._write(tmp_path, data))

    def test_missing_waiver_file_is_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "WAIVER_FILE", tmp_path / "nope.json")
        assert mod.main(["--dsn", ""]) == 2  # fail before any DB access

    def test_malformed_waiver_file_is_fatal(self, tmp_path, monkeypatch):
        bad = tmp_path / "waivers.json"
        bad.write_text('{"waivers": "not-a-list"}')
        monkeypatch.setattr(mod, "WAIVER_FILE", bad)
        assert mod.main(["--dsn", ""]) == 2

    def test_waiver_entry_without_ticket_id_is_fatal(self, tmp_path):
        bad = tmp_path / "waivers.json"
        bad.write_text(json.dumps({"waivers": [{"role": "builder"}]}))
        with pytest.raises(ValueError):
            mod.load_waiver_ids(bad)

    def test_duplicate_waiver_is_fatal(self, tmp_path):
        bad = tmp_path / "waivers.json"
        tid = "ticket-x"
        bad.write_text(json.dumps({"waivers": [{"ticket_id": tid},
                                                {"ticket_id": tid}]}))
        with pytest.raises(ValueError):
            mod.load_waiver_ids(bad)

    def test_apply_waivers_honored_vs_contradicted(self):
        waived = {"w1", "w2"}
        honored, contradicted = mod.apply_waivers(
            {"w1": 0, "w2": 3, "t9": 0}, waived)
        assert honored == {"w1"}
        assert contradicted == {"w2"}  # grew an event after the ruling

    def test_apply_waivers_absent_id_ignored(self):
        honored, contradicted = mod.apply_waivers({"t9": 0}, {"gone"})
        assert honored == set()
        assert contradicted == set()

    def test_apply_waivers_empty_is_noop(self):
        assert mod.apply_waivers({"t1": 0, "t2": 2}, set()) == (set(), set())


class TestConstants:
    def test_terminal_statuses_cover_the_family(self):
        for s in ("cancelled", "superseded", "abandoned", "expired",
                  "failed", "completed"):
            assert s in mod.TERMINAL_STATUSES

    def test_cutover_is_walkthrough_date(self):
        assert mod.DEFAULT_CUTOVER.startswith("2026-09-22")


class TestMainFailLoud:
    def test_missing_dsn_exits_2(self):
        # No DSN anywhere: must fail loudly (exit 2), never report empty/CLEAN.
        assert mod.main(["--dsn", ""]) == 2

    def test_bad_dsn_exits_2_not_1(self):
        # A connection failure is FATAL (2), not "drift found" (1) and not
        # "clean" (0) — the ae18bba7 lesson encoded as an exit contract.
        assert mod.main(["--dsn", "postgresql://nobody@localhost:1/void"]) == 2
