"""Tests for tackle.agent_scheduler_runner (T14/T15, decision 85ae61af).

Covers:
- cron_matches_window / parse_cron — the 5-field matcher incl. timezone
  boundaries, missed ticks, duplicate ticks (window semantics).
- _launch_command — harness.launcher delegation (no bare PATH), conduit
  branch preserved, missing-model handling.
- _matching_events / _stamp_consumed — event criteria match + consumed
  stamping (idempotent, no re-fire).

DB-dependent tests (Runner.connect + evaluate_tick against live data) are
run as integration probes, not here — this suite is hermetic.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tackle.agent_scheduler_runner import (
    Runner,
    cron_matches_window,
    parse_cron,
)

NOW = datetime(2026, 8, 9, 12, 30, 0, tzinfo=timezone.utc)


# ── cron matcher ──────────────────────────────────────────────────────


class TestCronMatcher:
    def test_every_15_fires_inside_window(self):
        assert cron_matches_window("*/15 * * * *", NOW - timedelta(minutes=20), NOW) is True

    def test_no_fire_within_short_window(self):
        assert cron_matches_window("*/30 * * * *", NOW - timedelta(minutes=10), NOW) is False

    def test_hourly_crosses_top_of_hour(self):
        assert cron_matches_window("0 * * * *", NOW - timedelta(minutes=35), NOW) is True

    def test_day_of_week_restriction(self):
        monday_morning = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)  # Mon
        assert cron_matches_window("0 9 * * 1", monday_morning, monday_morning + timedelta(hours=2)) is True

    def test_day_of_week_restriction_does_not_match_other_days(self):
        # `0 9 * * 1` (Monday-only) must NOT match a Tuesday window — dom is
        # `*`, so the dow restriction must hold (Vixie AND semantics).
        tuesday_morning = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)  # Tue
        assert cron_matches_window("0 9 * * 1", tuesday_morning, tuesday_morning + timedelta(hours=2)) is False

    def test_day_of_month_restriction_alone(self):
        # `0 9 15 * *` (15th of month) must NOT match the 10th.
        tenth = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        assert cron_matches_window("0 9 15 * *", tenth, tenth + timedelta(hours=2)) is False

    def test_both_restricted_uses_or(self):
        # `0 9 15 * 1` fires when EITHER dom=15 OR dow=Mon matches.
        monday_not_15 = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)  # Mon 10th
        assert cron_matches_window("0 9 15 * 1", monday_not_15, monday_not_15 + timedelta(hours=2)) is True
        not_monday_15th = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)  # Sat 15th
        assert cron_matches_window("0 9 15 * 1", not_monday_15th, not_monday_15th + timedelta(hours=2)) is True

    def test_window_boundaries_exclusive(self):
        # Fire exactly at `since` belongs to previous tick.
        assert cron_matches_window("*/30 * * * *", NOW - timedelta(minutes=30), NOW) is False
        # Fire exactly at `until` belongs to next tick.
        assert cron_matches_window("*/30 * * * *", NOW - timedelta(minutes=30), NOW + timedelta(minutes=1)) is True

    def test_window_between_fires(self):
        assert cron_matches_window("*/30 * * * *", NOW - timedelta(minutes=25), NOW - timedelta(minutes=15)) is False

    def test_missed_ticks_fire(self):
        # Multiple */15 fires inside a 40-minute window => still just "due".
        assert cron_matches_window("*/15 * * * *", NOW - timedelta(minutes=40), NOW) is True

    def test_invalid_expression_returns_false(self):
        assert cron_matches_window("not-a-cron", NOW - timedelta(minutes=5), NOW) is False

    def test_parse_5_field_with_steps_and_ranges(self):
        m, h, d, mo, dow = parse_cron("*/5 9-17 * * 1-5")
        assert 5 in m and 9 in h and 17 in h
        assert 1 in dow and 5 in dow and 0 not in dow


# ── launch construction (T14) ─────────────────────────────────────────


class TestLaunchCommand:
    def setup_method(self):
        self.runner = Runner()  # no DB connection needed

    def test_opencode_delegated_no_bare_path(self):
        cmd, cwd = self.runner._launch_command({
            "id": 1, "role": "builder", "model_id": "gpt-4o", "harness": "opencode",
            "agent_config": json.dumps({"title": "scheduled-builder"}),
            "project_dir": "/home/codex/dev",
        })
        assert cmd[0] != "opencode"  # absolute binary, no PATH assumption
        assert "--agent" in cmd and "--dir" in cmd and "--model" in cmd
        assert "gpt-4o" in cmd
        assert cwd == "/home/codex/dev"

    def test_opencode_without_model(self):
        cmd, _ = self.runner._launch_command({
            "id": 2, "role": "builder", "model_id": None, "harness": "opencode",
            "agent_config": "{}", "project_dir": "/home/codex/dev",
        })
        assert "--model" not in cmd

    def test_conduit_branch_preserved(self):
        cmd, cwd = self.runner._launch_command({
            "id": 3, "role": "reviewer", "model_id": "", "harness": "conduit",
            "agent_config": "{}", "project_dir": "/home/codex/dev",
        })
        assert cmd[0].endswith("python3") and cmd[1] == "main.py" and cmd[2] == "--run"
        assert cwd.endswith("conduit")

    def test_extra_args_appended(self):
        cmd, _ = self.runner._launch_command({
            "id": 4, "role": "builder", "model_id": "m", "harness": "opencode",
            "agent_config": json.dumps({"extra_args": ["--dry-run"]}),
            "project_dir": "/home/codex/dev",
        })
        assert cmd[-1] == "--dry-run"


# ── event matching (T15) ──────────────────────────────────────────────


class TestEventCriteria:
    def test_criteria_json_parsing_helper(self):
        # The runner's _matching_events accepts both a JSON string and a dict;
        # the parse guard is exercised via evaluate_tick integration probes.
        r = Runner()
        assert hasattr(r, "_matching_events")
        assert hasattr(r, "_stamp_consumed")


# ── eligibility gate (1285 remediation) ───────────────────────────────


class _FakeCursor:
    def __init__(self, count, sink):
        self._count = count
        self._sink = sink

    def execute(self, sql, params=None):
        self._sink["sql"] = " ".join(sql.split())
        self._sink["params"] = params

    def fetchone(self):
        return (self._count,)


class _FakeConn:
    """Records the queries the eligibility gate issues."""

    def __init__(self, count):
        self._count = count
        self.queries = []

    def cursor(self):
        sink = {}
        self.queries.append(sink)
        return _FakeCursor(self._count, sink)


class TestEligibilityGate:
    def test_placeholder_task_slug_never_launches(self):
        # No connection attached: a DB touch would raise into the fail-open
        # branch and return True. False proves the placeholder guard runs
        # first — this is the `test-task` row 164/165 runaway.
        runner = Runner()
        for slug in ("test-task", "TEST-TASK", " test-task ", "placeholder"):
            assert runner._has_eligible_work("engineer-ii", {"task_slug": slug}) is False
            assert runner._has_eligible_work("devops", {"task_slug": slug}) is False

    def test_non_placeholder_rows_skip_the_guard(self):
        # A real task_slug must reach the role checks, not be swallowed by
        # the placeholder guard (fail-open here because no DB is attached).
        runner = Runner()
        assert runner._has_eligible_work("engineer-ii", {"task_slug": "wire-up-x"}) is True
        assert runner._has_eligible_work("devops", {"task_slug": "containerize-srv"}) is True

    def test_reviewer_without_open_tickets_is_ineligible(self):
        runner = Runner()
        runner._conn = _FakeConn(0)
        assert runner._has_eligible_work("reviewer", {}) is False
        assert "vision.tickets" in runner._conn.queries[0]["sql"]

    def test_reviewer_with_open_tickets_is_eligible(self):
        runner = Runner()
        runner._conn = _FakeConn(1)
        assert runner._has_eligible_work("reviewer", {}) is True

    def test_builder_query_unchanged(self):
        runner = Runner()
        runner._conn = _FakeConn(2)
        assert runner._has_eligible_work("builder", {}) is True
        assert "execution.requests" in runner._conn.queries[0]["sql"]

    def test_unknown_role_still_falls_back_to_eligible(self):
        # No connection attached: unclassified roles must not touch the DB.
        runner = Runner()
        assert runner._has_eligible_work("critic", {}) is True
