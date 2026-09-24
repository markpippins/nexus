"""Hermetic tests for bin/record_durability_wrap.py + bin/check_record_durability.py.

Exercises the green-heartbeat state machine (commissioning / weekly
heartbeat / recovery), the daily-suppressed findings record, tool-error
passthrough, and the pure classification logic (path-like / under-50B /
exemptions). No database, no network: the checker and post-agent-record
subprocess calls are faked at the subprocess.run boundary, and the state
file is redirected to a tmp_path. The lockstep test loads the SDK
wrapper and requires identical decision outputs across the state matrix
(same behavioral contract as test_sdk_drift_stamp_wrap.py, PR #540).
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


WRAP = _load(
    "record_durability_wrap",
    REPO / "bin" / "record_durability_wrap.py")
CHECK = _load(
    "check_record_durability",
    REPO / "bin" / "check_record_durability.py")


# ---------------------------------------------------------------------------
# Pure checker classification
# ---------------------------------------------------------------------------

class TestHollowReasons:
    def test_short_path_gets_both_reasons(self):
        reasons = CHECK.hollow_reasons("/tmp/wp3_close.md")
        assert any("path" in r for r in reasons)
        assert any("under 50 bytes" in r for r in reasons)

    def test_long_document_clean(self):
        doc = ("# Plan close-out\n\n" + "The migration V198 landed and the "
               "planner unblock was filed with evidence. " * 3)
        assert CHECK.hollow_reasons(doc) == []

    def test_dev_stdin_flagged_as_path(self):
        reasons = CHECK.hollow_reasons("/dev/stdin")
        assert any("path" in r for r in reasons)

    def test_bare_filename_flagged(self):
        reasons = CHECK.hollow_reasons("audit_report.md")
        assert any("path" in r for r in reasons)

    def test_short_legit_prose_flagged_as_short_only(self):
        reasons = CHECK.hollow_reasons("Deploy finished cleanly.")
        assert reasons == [f"content under 50 bytes ({len('Deploy finished cleanly.')})"]

    def test_dev_null_flagged(self):
        reasons = CHECK.hollow_reasons("/dev/null")
        assert any("path" in r for r in reasons)

    def test_relative_dot_path_flagged(self):
        assert any("path" in r for r in CHECK.hollow_reasons("./notes.md"))

    def test_home_relative_flagged(self):
        assert any("path" in r for r in CHECK.hollow_reasons("~/nexus/audit/x.md"))


class TestExemptions:
    def test_ci_ephemeral_role_exempt(self):
        assert CHECK.is_exempt("wr-conf-016-alpha", None) is True

    def test_hollow_content_audit_exempts(self):
        assert CHECK.is_exempt("DBA", {"hollow_content_audit": "2026-09-24"}) is True

    def test_hollow_content_repair_exempts(self):
        assert CHECK.is_exempt("DBA", {"hollow_content_repair": "2026-09-24"}) is True

    def test_record_durability_review_exempts(self):
        assert CHECK.is_exempt("reviewer", {"record_durability_review": "x"}) is True

    def test_unaudited_role_not_exempt(self):
        assert CHECK.is_exempt("engineer", {}) is False

    def test_none_metadata_not_exempt(self):
        assert CHECK.is_exempt("engineer", None) is False


# ---------------------------------------------------------------------------
# Wrapper: green-heartbeat contract
# ---------------------------------------------------------------------------

class FakeRuns:
    """Scriptable subprocess.run replacement for the wrapper module."""

    def __init__(self, checker_rc: int, checker_out: str, checker_err: str = ""):
        self.checker_rc = checker_rc
        self.checker_out = checker_out
        self.checker_err = checker_err
        self.posters: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        if any(str(a).endswith("check_record_durability.py") for a in cmd):
            class R:
                returncode = self.checker_rc
                stdout = self.checker_out
                stderr = self.checker_err
            return R()
        self.posters.append([str(a) for a in cmd])

        class R:
            returncode = 0
            stdout = "record-uuid-1234"
            stderr = ""
        return R()


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    # Patch the module attribute directly (SDK suite convention): binding
    # at import means an env override set in the fixture would be inert,
    # and tests would silently read/write the REAL state file.
    sf = tmp_path / "state.json"
    monkeypatch.setattr(WRAP, "STATE", sf)
    return sf


GREEN_PAYLOAD = json.dumps({"since": "2026-09-24T00:00:00+00:00",
                            "pointer": "2026-09-25T06:30:00+00:00",
                            "count": 0, "findings": []})
FINDINGS_PAYLOAD = json.dumps({
    "since": "2026-09-24T00:00:00+00:00",
    "pointer": "2026-09-25T06:30:00+00:00",
    "count": 1,
    "findings": [{
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "role": "engineer",
        "record_type": "report",
        "title": "some update",
        "created_at": "2026-09-25T01:00:00+00:00",
        "content_preview": "/tmp/x.md",
        "reasons": ["content is a file path, not a document"],
    }]})


def _record_type(cmd: list[str]) -> str:
    """Exact --record-type value from a faked poster invocation."""
    return cmd[cmd.index("--record-type") + 1]


def test_baseline_with_residue_commissions_clean(state_file, monkeypatch):
    """First run over history with residue: fence it, commission — do NOT
    file a findings record for pre-deployment records."""
    baseline_payload = json.dumps({
        "baseline_pointer": "2026-09-24T20:19:39+00:00",
        "findings": [{"id": "bbbbbbbb-0000-0000-0000-000000000000",
                      "role": "engineer", "record_type": "report",
                      "title": "old", "created_at": "2026-08-25",
                      "content_preview": "-", "reasons": ["short"]}]})
    fake = FakeRuns(1, baseline_payload)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "commissioning" in " ".join(fake.posters[0])
    st = json.loads(state_file.read_text())
    assert st["pointer"] == "2026-09-24T20:19:39+00:00"
    assert st["ever_green"] is True


def test_commissioning_on_first_green_run(state_file, monkeypatch, capsys):
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "commissioning" in " ".join(fake.posters[0])
    st = json.loads(state_file.read_text())
    assert st["ever_green"] is True
    assert st["pointer"] == "2026-09-25T06:30:00+00:00"


def test_same_week_green_files_nothing(state_file, monkeypatch):
    st = {"ever_green": True,
          "last_green_recorded": WRAP._iso_week(WRAP._utcnow()),
          "pointer": "2026-09-24T00:00:00+00:00"}
    state_file.write_text(json.dumps(st))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert fake.posters == []


def test_new_week_green_files_heartbeat(state_file, monkeypatch):
    st = {"ever_green": True, "last_green_recorded": "2020-W01",
          "pointer": "2026-09-24T00:00:00+00:00"}
    state_file.write_text(json.dumps(st))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "heartbeat" in " ".join(fake.posters[0])


def test_findings_file_inspection_record(state_file, monkeypatch, capsys):
    st = {"ever_green": True, "last_green_recorded": "2026-W39",
          "pointer": "2026-09-24T00:00:00+00:00"}
    state_file.write_text(json.dumps(st))
    fake = FakeRuns(1, FINDINGS_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "inspection"
    joined = " ".join(fake.posters[0])
    assert "aaaaaaaa" in joined  # the finding id is in the body
    st = json.loads(state_file.read_text())
    assert st["ever_green"] is False
    assert st["last_drift_recorded"] == WRAP._utcnow().strftime("%Y-%m-%d")
    assert st["pointer"] == "2026-09-25T06:30:00+00:00"


def test_findings_suppressed_second_run_same_day(state_file, monkeypatch):
    today = WRAP._utcnow().strftime("%Y-%m-%d")
    st = {"ever_green": False, "last_drift_recorded": today,
          "pointer": "2026-09-24T00:00:00+00:00"}
    state_file.write_text(json.dumps(st))
    fake = FakeRuns(1, FINDINGS_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert fake.posters == []
    st = json.loads(state_file.read_text())
    assert st["pointer"] == "2026-09-25T06:30:00+00:00"


def test_recovery_after_findings(state_file, monkeypatch):
    st = {"ever_green": False, "last_drift_recorded": "2026-09-20",
          "pointer": "2026-09-24T00:00:00+00:00"}
    state_file.write_text(json.dumps(st))
    fake = FakeRuns(0, GREEN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 0
    assert len(fake.posters) == 1
    assert _record_type(fake.posters[0]) == "report"
    assert "RECOVERED" in " ".join(fake.posters[0])


def test_tool_error_files_nothing(state_file, monkeypatch, capsys):
    fake = FakeRuns(2, "", "connection refused")
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 2
    assert fake.posters == []
    assert not state_file.exists() or "ever_green" not in json.loads(
        state_file.read_text())


def test_unparseable_green_output_is_tool_error(state_file, monkeypatch):
    fake = FakeRuns(0, "not json at all")
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    rc = WRAP.main()
    assert rc == 2
    assert fake.posters == []


def test_poster_failure_exit_2(state_file, monkeypatch):
    class FailAfter:
        def __init__(self):
            self.calls = 0

        def __call__(self, cmd, **kwargs):
            self.calls += 1
            if any(str(a).endswith("check_record_durability.py") for a in cmd):
                class R:
                    returncode = 0
                    stdout = GREEN_PAYLOAD
                    stderr = ""
                return R()

            class R:
                returncode = 1
                stdout = ""
                stderr = "nebula down"
            return R()

    monkeypatch.setattr(WRAP.subprocess, "run", FailAfter())
    rc = WRAP.main()
    assert rc == 2


# ---------------------------------------------------------------------------
# Behavioral lockstep with the SDK wrapper (the #540 contract)
# ---------------------------------------------------------------------------

def test_green_decision_lockstep_with_sdk_wrapper():
    sdk = _load(
        "sdk_drift_stamp_wrap_lockstep",
        REPO / "bin" / "sdk_drift_stamp_wrap.py")
    states = [
        {},
        {"ever_green": False, "last_drift_recorded": "2026-W38"},
        {"ever_green": True, "last_green_recorded": "2026-W38"},
        {"ever_green": True, "last_green_recorded": "2026-W39"},
        {"ever_green": True, "last_green_recorded": "2026-W39",
         "last_drift_recorded": "2026-09-20"},
    ]
    for state in states:
        for week in ("2026-W38", "2026-W39", "2026-W40"):
            a_mine, s_mine = WRAP.decide_green_action(state, week)
            a_sdk, s_sdk = sdk.decide_green_action(state, week)
            assert a_mine == a_sdk, (state, week)
            assert s_mine == s_sdk, (state, week)
