"""Hermetic tests for bin/check_transition_attestation.py.

Exercises the pure logic — classification, verdict, status rollup — with
in-memory inputs. No database, no network: the psycopg2 surface is only
touched behind main(), which is not imported here.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # worktree root
_spec = importlib.util.spec_from_file_location(
    "check_transition_attestation", REPO / "bin" / "check_transition_attestation.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

import pytest  # noqa: E402


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

    def test_load_real_waiver_file(self):
        # The shipped file must parse, carry the ruling reference, and
        # contain exactly the two ruled gen-1 rows — no more (narrow by
        # ruling), no less (a silently-empty list would be a hole).
        data = json.loads(mod.WAIVER_FILE.read_text())
        assert data["ruling"]["decision_record_id"].startswith("6b42dd3f")
        assert data["ruling"]["reason_code"] == "pre_event_emission_fix"
        ids = {w["ticket_id"] for w in data["waivers"]}
        assert ids == {
            "ticket-8261654-builder-caea50cc-5bc9-4405-b0c1-8abb5de79ad1",
            "ticket-8261654-reviewer-1790105437937",
        }
        loaded = mod.load_waiver_ids(mod.WAIVER_FILE)
        assert set(loaded) == ids
        assert loaded["ticket-8261654-reviewer-1790105437937"]["closure_evidence"]

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
