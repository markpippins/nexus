"""Hermetic tests for bin/check_transition_attestation.py.

Exercises the pure logic — classification, verdict, status rollup — with
in-memory inputs. No database, no network: the psycopg2 surface is only
touched behind main(), which is not imported here.
"""
from __future__ import annotations

import importlib.util
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
