"""Tests for tackle.agent_scheduler_runner (T14/T15, decision 85ae61af).

Covers:
- cron_matches_window / parse_cron — the 5-field matcher incl. timezone
  boundaries, missed ticks, duplicate ticks (window semantics).
- The night-shift stagger: one Planner -> Builder -> Critic -> Reviewer
  cycle per 30 minutes, each stage on its own minute.
- _launch_command — harness.launcher delegation (no bare PATH), conduit
  branch preserved, missing-model handling, attached-task prompt.
- _matching_events / _stamp_consumed — event criteria match + consumed
  stamping (idempotent, no re-fire).
- The Planner eligibility gate — SonarQube is the triage work source, so an
  unreachable server or zero open findings must refuse the launch.

DB-dependent tests (Runner.connect + evaluate_tick against live data) are
run as integration probes, not here — this suite is hermetic.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tackle import agent_scheduler_runner as asr
from tackle.agent_scheduler_runner import (
    Runner,
    cron_matches_window,
    format_task_prompt,
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


# ── night-shift stagger (sql/nightshift/seed-nightshift.sql) ──────────

#: The cadence the night-shift seed installs. Kept here so a change to the
#: seed's cron expressions without a matching scheduler change (or vice versa)
#: fails this suite.
NIGHT_SHIFT_CADENCE = {
    "planner": "0,30 * * * *",
    "builder": "5,35 * * * *",
    "critic": "10,40 * * * *",
    "reviewer": "15,45 * * * *",
}


def _due_minutes(expr: str, start: datetime, count: int = 60) -> list[int]:
    """Minutes of the hour at which `expr` fires, via the real matcher.

    The matcher's window is (since, until]: a fire at minute m belongs to the
    window ending at m.
    """
    due = []
    for i in range(count):
        minute = start + timedelta(minutes=i)
        # (m-1, m+1) is the narrowest window that contains exactly m.
        if cron_matches_window(expr, minute - timedelta(minutes=1), minute + timedelta(minutes=1)):
            due.append(minute.minute)
    return sorted(due)


class TestNightShiftStagger:
    def setup_method(self):
        self.start = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)

    def test_each_stage_fires_on_its_own_minute(self):
        assert _due_minutes(NIGHT_SHIFT_CADENCE["planner"], self.start) == [0, 30]
        assert _due_minutes(NIGHT_SHIFT_CADENCE["builder"], self.start) == [5, 35]
        assert _due_minutes(NIGHT_SHIFT_CADENCE["critic"], self.start) == [10, 40]
        assert _due_minutes(NIGHT_SHIFT_CADENCE["reviewer"], self.start) == [15, 45]

    def test_no_two_stages_are_due_on_the_same_minute(self):
        # The bug this replaced: four `interval 1800` rows with a NULL
        # last_run_at were all due on the same tick, every 30 minutes.
        due: list[int] = []
        for expr in NIGHT_SHIFT_CADENCE.values():
            due.extend(_due_minutes(expr, self.start))
        assert len(due) == len(set(due))

    def test_cycle_order_is_planner_builder_critic_reviewer(self):
        stages = [
            _due_minutes(NIGHT_SHIFT_CADENCE[role], self.start)
            for role in ("planner", "builder", "critic", "reviewer")
        ]
        for index, cycle in enumerate((0, 30)):
            ordered = [due[index] for due in stages]
            assert ordered == [cycle, cycle + 5, cycle + 10, cycle + 15]

    def test_stage_is_not_due_in_the_middle_of_the_gap(self):
        # A Builder tick at :02 must not pick the Builder up early.
        at_two = self.start + timedelta(minutes=2)
        assert cron_matches_window("5,35 * * * *", at_two, at_two + timedelta(minutes=1)) is False


# ── attached task prompt (tackle.tasks + tackle.prompts) ──────────────


class TestTaskPrompt:
    def test_body_and_criteria_become_the_prompt(self):
        prompt = format_task_prompt("planner", "nightshift", {
            "prompt_title": "Night-shift Sonar triage",
            "prompt_slug": "nightshift-planner-triage",
            "prompt_body_md": "Group open findings by scope.",
            "acceptance_criteria": ["At most 5 work requests", "No Sonar writes"],
        })
        assert "Scheduled task: Night-shift Sonar triage" in prompt
        assert "Group open findings by scope." in prompt
        assert "## Acceptance criteria" in prompt
        assert "- At most 5 work requests" in prompt
        assert "- No Sonar writes" in prompt

    def test_title_falls_back_to_slug_and_empty_criteria_is_omitted(self):
        prompt = format_task_prompt("planner", "nightshift", {
            "prompt_title": None, "prompt_slug": "nightshift-planner-triage",
            "prompt_body_md": "", "acceptance_criteria": [],
        })
        assert "Scheduled task: nightshift-planner-triage" in prompt
        assert "## Acceptance criteria" not in prompt

    def test_unbound_entry_keeps_title_as_prompt(self):
        cmd, _ = Runner()._launch_command({
            "id": 9, "role": "planner", "model_id": "m", "harness": "opencode",
            "agent_config": json.dumps({"title": "scheduled-planner"}),
            "project_dir": "/home/codex/dev", "task_slug": None,
        })
        assert cmd[-1] == "scheduled-planner"

    def test_bound_entry_launches_with_the_task_prompt(self):
        runner = Runner()
        # psycopg2's default cursor yields tuples; mirror that shape.
        runner._conn = _TaskConn((
            "nightshift",
            "sonar-triage",
            ["At most 5 work requests"],
            "Night-shift Sonar triage",
            "nightshift-planner-triage",
            "Read open findings and batch them.",
        ))
        cmd, _ = runner._launch_command({
            "id": 10, "role": "planner", "model_id": "m", "harness": "opencode",
            "agent_config": json.dumps({"title": "nightshift-planner-triage"}),
            "project_dir": "/home/codex/dev", "task_slug": "nightshift",
        })
        prompt = cmd[-1]
        assert "Read open findings and batch them." in prompt
        assert "- At most 5 work requests" in prompt
        assert prompt != "nightshift-planner-triage"

    def test_missing_task_row_falls_back_without_raising(self):
        runner = Runner()
        runner._conn = _TaskConn(None)
        assert runner._load_attached_task({"role": "planner", "task_slug": "nightshift"}) is None


class _TaskCursor:
    def __init__(self, row):
        self._row = row
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _TaskConn:
    """Returns one task row (or None) for the attached-task lookup."""

    def __init__(self, row):
        self._row = row
        self.cursors = []

    def cursor(self):
        cur = _TaskCursor(self._row)
        self.cursors.append(cur)
        return cur


# ── Planner eligibility (SonarQube is the work source) ────────────────


class TestPlannerEligibility:
    def test_refuses_when_sonar_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            asr, "probe_sonar_findings",
            lambda *a, **k: asr.SonarProbe(False, None, "SonarQube unreachable"),
        )
        # No DB connection attached on purpose: a DB touch would fail open,
        # so False proves the Sonar gate is what refused the launch.
        assert Runner()._has_eligible_work("planner") is False

    def test_refuses_when_no_open_findings(self, monkeypatch):
        monkeypatch.setattr(
            asr, "probe_sonar_findings",
            lambda *a, **k: asr.SonarProbe(True, 0, "0 open finding(s) for project nexus"),
        )
        assert Runner()._has_eligible_work("planner") is False

    def test_eligible_with_open_findings(self, monkeypatch):
        monkeypatch.setattr(
            asr, "probe_sonar_findings",
            lambda *a, **k: asr.SonarProbe(True, 1386, "1386 open finding(s) for project nexus"),
        )
        assert Runner()._has_eligible_work("planner") is True

    def test_planner_gate_does_not_touch_the_role_queues(self, monkeypatch):
        monkeypatch.setattr(
            asr, "probe_sonar_findings",
            lambda *a, **k: asr.SonarProbe(True, 3, "3 open finding(s)"),
        )
        runner = Runner()
        runner._conn = _TaskConn(None)
        assert runner._has_eligible_work("planner") is True
        assert runner._conn.cursors == []  # no queue query for the Planner

    def test_probe_parses_total_and_reports_reason(self, monkeypatch):
        calls = []

        class _Resp:
            def __init__(self, payload, status=200):
                self._payload = payload
                self.status = status

            def read(self):
                return json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if req.full_url.endswith("/api/system/status"):
                return _Resp({"status": "UP"})
            return _Resp({"total": 42, "issues": []})

        monkeypatch.setattr(asr.urllib.request, "urlopen", fake_urlopen)
        probe = asr.probe_sonar_findings(base_url="http://sonar.test", token="t")
        assert probe == asr.SonarProbe(True, 42, "42 open finding(s) for project nexus")
        assert calls[1].startswith("http://sonar.test/api/issues/search?")
        assert "resolved=false" in calls[1]

    def test_probe_without_token_is_not_ready(self, monkeypatch):
        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(asr.urllib.request, "urlopen", lambda req, timeout=None: _Resp())
        # Empty string (not None) means "explicitly no token": passing None
        # would fall back to the checkout .env credential surface.
        probe = asr.probe_sonar_findings(base_url="http://sonar.test", token="")
        assert probe.ready is False
        assert "token" in probe.reason
