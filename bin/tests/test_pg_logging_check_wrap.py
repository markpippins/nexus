"""Hermetic tests for bin/pg_logging_check_wrap.py + bin/check_pg_logging.py.

Covers the attribution-prefix classification, the green-heartbeat state
machine (commissioning / weekly heartbeat / recovery), the daily-
suppressed findings record, tool-error passthrough, and behavioral
lockstep with record_durability_wrap. No docker, no ssh, no database:
the checker subprocess and post-agent-record calls are faked at the
subprocess.run boundary and the state file is redirected to tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # worktree root


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


WRAP = _load("pg_logging_check_wrap", REPO / "bin" / "pg_logging_check_wrap.py")
CHECK = _load("check_pg_logging", REPO / "bin" / "check_pg_logging.py")


# ---------------------------------------------------------------------------
# Checker: attribution classification (pure)
# ---------------------------------------------------------------------------

GOOD = ("2026-09-24 20:53:59.257 UTC [65] db=nexus user=pguser app=psql "
        "client=[local] LOG:  statement: CREATE TABLE t (id int);")


class TestClassification:
    def test_good_line_counted_and_attributed(self):
        ddl, attr = CHECK._classify_lines(GOOD)
        assert (ddl, attr) == (1, 1)

    def test_missing_prefix_fields_not_attributed(self):
        bad = ("2026-09-24 20:53:59 UTC [65] LOG:  statement: "
               "ALTER TABLE t ADD COLUMN c int;")
        ddl, attr = CHECK._classify_lines(bad)
        assert (ddl, attr) == (1, 0)

    def test_non_ddl_ignored(self):
        ddl, attr = CHECK._classify_lines(
            "2026-09-24 20:53:21 UTC [1] db= user= app= client= LOG:  "
            "starting PostgreSQL 17.11")
        assert (ddl, attr) == (0, 0)

    def test_grant_and_comment_counted(self):
        text = (GOOD + "\n"
                + GOOD.replace("CREATE TABLE t", "COMMENT ON TABLE t") + "\n"
                + GOOD.replace("CREATE TABLE t", "GRANT ALL ON t"))
        assert CHECK._classify_lines(text) == (3, 3)


# ---------------------------------------------------------------------------
# Wrapper: green-heartbeat contract
# ---------------------------------------------------------------------------

class FakeRuns:
    def __init__(self, checker_rc: int, checker_out: str, checker_err: str = ""):
        self.checker_rc = checker_rc
        self.checker_out = checker_out
        self.checker_err = checker_err
        self.posters: list[list[str]] = []
        self.checker_cmds: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        if any(str(a).endswith("check_pg_logging.py") for a in cmd):
            self.checker_cmds.append([str(a) for a in cmd])
            class R:
                returncode = self.checker_rc
                stdout = self.checker_out
                stderr = self.checker_err
            return R()
        self.posters.append([str(a) for a in cmd])

        class R:
            returncode = 0
            stdout = "record-uuid"
            stderr = ""
        return R()


def _record_type(cmd: list[str]) -> str:
    return cmd[cmd.index("--record-type") + 1]


GREEN_PAYLOAD = json.dumps({
    "day": "2026-09-24", "probe": "pglog_probe_x", "ok": True,
    "results": [
        {"leg": "titanium", "file": "/pglogs/postgresql-2026-09-24.log",
         "ddl_lines": 12, "ddl_with_attribution": 12,
         "live_probe_captured": True, "findings": []},
        {"leg": "vanadium", "file": "/pglogs/postgresql-2026-09-24.log",
         "ddl_lines": 4, "ddl_with_attribution": 4,
         "live_probe_captured": True, "findings": []}]})

FINDINGS_PAYLOAD = json.dumps({
    "day": "2026-09-24", "probe": "pglog_probe_x", "ok": False,
    "results": [
        {"leg": "titanium", "file": "/pglogs/postgresql-2026-09-24.log",
         "ddl_lines": 3, "ddl_with_attribution": 1,
         "live_probe_captured": True,
         "findings": ["2/3 DDL lines missing attribution prefix"]},
        {"leg": "vanadium", "file": "/pglogs/missing.log",
         "findings": ["yesterday's log file missing: /pglogs/missing.log"]}]})

TOOLERR_PAYLOAD = json.dumps({
    "day": "2026-09-24",
    "results": [{"leg": "titanium", "tool_error": "live probe failed: conn"},
                {"leg": "vanadium", "tool_error": "ssh failed: timeout"}]})


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    monkeypatch.setattr(WRAP, "STATE", sf)
    return sf


BASELINE_PAYLOAD = json.dumps({
    "day": "2026-09-23", "probe": "pglog_probe_b", "baseline": True, "ok": True,
    "results": [
        {"leg": "titanium", "file": "/pglogs/postgresql-2026-09-23.log",
         "notes": ["no log for 2026-09-23 (pre-deployment day); baseline run"
                   " — posture verified against today's log instead"],
         "today_ddl_lines": 5, "today_ddl_with_attribution": 5,
         "live_probe_captured": True, "findings": []},
        {"leg": "vanadium", "file": "/pglogs/postgresql-2026-09-23.log",
         "notes": ["no log for 2026-09-23 (pre-deployment day); baseline run"
                   " — posture verified against today's log instead"],
         "today_ddl_lines": 2, "today_ddl_with_attribution": 2,
         "live_probe_captured": True, "findings": []}]})


def test_baseline_green_commissions_with_notes(state_file, monkeypatch):
    fake = FakeRuns(0, BASELINE_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    joined = " ".join(fake.posters[0])
    assert _record_type(fake.posters[0]) == "report"
    assert "commissioning" in joined
    assert "Baseline run" in joined
    assert "--baseline" in fake.checker_cmds[0]
    st = json.loads(state_file.read_text())
    assert st["baseline_done"] is True


def test_second_run_uses_yesterday_window(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True, "last_green_recorded": "2020-W01",
         "baseline_done": True}))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert "--baseline" not in fake.checker_cmds[0]
    assert _record_type(fake.posters[0]) == "report"
    assert "heartbeat" in " ".join(fake.posters[0])


def test_baseline_consumed_even_when_alerting(state_file, monkeypatch):
    payload = json.loads(BASELINE_PAYLOAD)
    payload["results"][0]["findings"] = [
        "1/5 of today's DDL lines missing attribution prefix"]
    payload["ok"] = False
    fake = FakeRuns(1, json.dumps(payload))
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "inspection"
    st = json.loads(state_file.read_text())
    assert st["baseline_done"] is True and st["ever_green"] is False


def test_commissioning_on_first_green_run(state_file, monkeypatch):
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "commissioning" in " ".join(fake.posters[0])
    assert "titanium: 12 DDL lines" in " ".join(fake.posters[0])
    st = json.loads(state_file.read_text())
    assert st["ever_green"] is True


def test_same_week_green_silent(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True,
         "last_green_recorded": WRAP._iso_week(WRAP._utcnow()),
         "pointer": None}))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert fake.posters == []


def test_new_week_heartbeat(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True, "last_green_recorded": "2020-W01"}))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "heartbeat" in " ".join(fake.posters[0])


def test_findings_file_inspection(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True, "last_green_recorded": "2026-W39"}))
    fake = FakeRuns(1, FINDINGS_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "inspection"
    joined = " ".join(fake.posters[0])
    assert "missing attribution prefix" in joined
    assert "yesterday's log file missing" in joined
    st = json.loads(state_file.read_text())
    assert st["ever_green"] is False
    assert st["last_drift_recorded"] == WRAP._utcnow().strftime("%Y-%m-%d")


def test_findings_suppressed_same_day(state_file, monkeypatch):
    today = WRAP._utcnow().strftime("%Y-%m-%d")
    state_file.write_text(json.dumps(
        {"ever_green": False, "last_drift_recorded": today}))
    fake = FakeRuns(1, FINDINGS_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert fake.posters == []


def test_recovery_after_findings(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": False, "last_drift_recorded": "2026-09-20"}))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert "RECOVERED" in " ".join(fake.posters[0])


def test_tool_error_files_nothing(state_file, monkeypatch):
    fake = FakeRuns(2, TOOLERR_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert fake.posters == []
    assert not state_file.exists()


def test_unparseable_output_is_tool_error(state_file, monkeypatch):
    fake = FakeRuns(0, "garbage")
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert fake.posters == []


# ---------------------------------------------------------------------------
# Lockstep with the durability wrapper's decision function
# ---------------------------------------------------------------------------

def test_green_decision_lockstep_with_durability_wrapper():
    other = _load("record_durability_wrap_lockstep",
                  REPO / "bin" / "record_durability_wrap.py")
    states = [{}, {"ever_green": False, "last_drift_recorded": "2026-W38"},
              {"ever_green": True, "last_green_recorded": "2026-W38"},
              {"ever_green": True, "last_green_recorded": "2026-W39"}]
    for state in states:
        for week in ("2026-W39", "2026-W40"):
            a1, s1 = WRAP.decide_green_action(state, week)
            a2, s2 = other.decide_green_action(state, week)
            assert a1 == a2 and s1 == s2, (state, week)
