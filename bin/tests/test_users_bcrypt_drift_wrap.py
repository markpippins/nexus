"""Hermetic tests for bin/users_bcrypt_drift_wrap.py.

Exercises the green-heartbeat state machine (commissioning / weekly
heartbeat / recovery), the daily-suppressed drift record, and tool-error
passthrough. No database, no network: the checker and post-agent-record
subprocess calls are faked at the subprocess.run boundary, and the state
file is redirected to a tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location(
    "users_bcrypt_drift_wrap", REPO / "bin" / "users_bcrypt_drift_wrap.py")
wrap = importlib.util.module_from_spec(spec)
sys.modules["users_bcrypt_drift_wrap"] = wrap
spec.loader.exec_module(wrap)


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def check_ok():
    return _Result(0, "[ENFORCED] assembly.users: OK\n[ENFORCED] gateway.users: OK\n", "")


def check_drift():
    return _Result(1, "[DRIFT] assembly.users: plaintext 3/35\n", "")


def check_tool_error():
    return _Result(2, "", "TOOL ERROR: cannot reach postgresql://...")


def record_ok():
    return _Result(0, "record created: 8f2a...\n", "")


def record_fail():
    return _Result(1, "", "nebula unreachable")


class FakeRun:
    """Queue of subprocess.run results, recording the commands."""

    def __init__(self, monkeypatch, results):
        self.calls = []
        self.results = list(results)
        monkeypatch.setattr(wrap.subprocess, "run", self._fake)

    def _fake(self, cmd, **kwargs):
        self.calls.append(cmd)
        return self.results.pop(0)

    def record_cmds(self):
        return [c for c in self.calls if "post-agent-record.py" in " ".join(c)]

    @staticmethod
    def title(cmd):
        return cmd[cmd.index("-t") + 1]

    @staticmethod
    def tags(cmd):
        return cmd[cmd.index("--tags") + 1]

    @staticmethod
    def record_type(cmd):
        return cmd[cmd.index("--record-type") + 1]


@pytest.fixture(autouse=True)
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setattr(wrap, "STATE", p)
    return p


def read_state(state_path):
    return json.loads(state_path.read_text())


# ---------------------------------------------------------------- green path

def test_first_clean_run_files_commissioning_record(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_ok(), record_ok()])
    assert wrap.main() == 0
    recs = fake.record_cmds()
    assert len(recs) == 1
    title = fake.title(recs[0])
    assert "commissioning" in title and "GREEN" in title
    assert "series:users-bcrypt-drift" in fake.tags(recs[0])
    st = read_state(state_path)
    assert st["ever_green"] is True
    assert st["last_green_recorded"] == wrap._iso_week(wrap._utcnow())


def test_second_clean_run_same_week_files_nothing(monkeypatch, state_path):
    FakeRun(monkeypatch, [check_ok(), record_ok(), check_ok()])
    assert wrap.main() == 0
    assert wrap.main() == 0  # same week: decision is None, only checker ran
    st = read_state(state_path)
    assert st["ever_green"] is True


def test_first_clean_run_next_week_files_heartbeat(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_ok(), record_ok(), check_ok(), record_ok()])
    wrap.main()
    st = read_state(state_path)
    st["last_green_recorded"] = "2020-W01"
    state_path.write_text(json.dumps(st))
    assert wrap.main() == 0
    title = fake.title(fake.record_cmds()[-1])
    assert "heartbeat" in title


def test_recovery_record_after_drift(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_drift(), record_ok(), check_ok(), record_ok()])
    assert wrap.main() == 0          # drift day: inspection record
    st = read_state(state_path)
    assert st["ever_green"] is False
    assert st["last_drift_recorded"] == wrap._utcnow().strftime("%Y-%m-%d")
    assert wrap.main() == 0          # next green: recovery record
    title = fake.title(fake.record_cmds()[-1])
    assert "RECOVERED" in title
    st = read_state(state_path)
    assert st["ever_green"] is True
    assert "last_drift_recorded" not in st


def test_recovery_wording_when_historical_drift_never_green(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_ok(), record_ok()])
    state_path.write_text(json.dumps({"last_drift_recorded": "2026-09-20"}))
    assert wrap.main() == 0
    assert "RECOVERED" in fake.title(fake.record_cmds()[-1])


# ----------------------------------------------------------------- drift path

def test_drift_files_inspection_record_then_same_day_suppressed(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_drift(), record_ok(), check_drift()])
    assert wrap.main() == 0
    rec = fake.record_cmds()[-1]
    assert "series:users-bcrypt-drift" in fake.tags(rec)
    assert fake.record_type(rec) == "inspection"
    assert wrap.main() == 0  # suppressed: no second record call
    assert len(fake.record_cmds()) == 1


def test_second_drift_next_day_re_alerts(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_drift(), record_ok(), check_drift(), record_ok()])
    assert wrap.main() == 0
    st = read_state(state_path)
    st["last_drift_recorded"] = "2026-09-01"
    state_path.write_text(json.dumps(st))
    assert wrap.main() == 0
    assert len(fake.record_cmds()) == 2


def test_drift_record_failure_exits_2(monkeypatch, state_path):
    FakeRun(monkeypatch, [check_drift(), record_fail()])
    assert wrap.main() == 2


# -------------------------------------------------------------- tool errors

def test_tool_error_files_nothing_and_exits_2(monkeypatch, state_path):
    fake = FakeRun(monkeypatch, [check_tool_error()])
    assert wrap.main() == 2
    assert fake.record_cmds() == []
    assert not state_path.exists()


def test_tool_error_does_not_disturb_green_state(monkeypatch, state_path):
    FakeRun(monkeypatch, [check_ok(), record_ok(), check_tool_error(), check_ok()])
    wrap.main()
    assert wrap.main() == 2
    assert wrap.main() == 0  # still same week -> nothing new filed


# ------------------------------------------------------- decision unit tests

def test_decide_green_action_matrix():
    action, st = wrap.decide_green_action({}, "2026-W39")
    assert action == "commissioning" and st["ever_green"] is True
    action, _ = wrap.decide_green_action({"last_drift_recorded": "2026-09-20"}, "2026-W39")
    assert action == "recovery"
    action, _ = wrap.decide_green_action(
        {"ever_green": True, "last_green_recorded": "2026-W39"}, "2026-W39")
    assert action is None
    action, st = wrap.decide_green_action(
        {"ever_green": True, "last_green_recorded": "2026-W38"}, "2026-W39")
    assert action == "heartbeat" and st["last_green_recorded"] == "2026-W39"
