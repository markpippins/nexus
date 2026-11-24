"""Hermetic tests for bin/vanadium_runner_watchdog.py.

The watchdog's whole value is that it stays quiet while the runner is healthy
and says something once when it is not, so these tests pin the transition
logic, the state file, and the "probe failure is not an outage" rule.

    python3 -m pytest bin/tests/test_vanadium_runner_watchdog.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vanadium_runner_watchdog as wd  # noqa: E402

ONLINE_ROW = {"name": "vanadium", "status": "online", "busy": False}
OFFLINE_ROW = {"name": "vanadium", "status": "offline", "busy": False}
TITANIUM_ROW = {"name": "titanium", "status": "online", "busy": False}


# ── classification ────────────────────────────────────────────────────────

def test_online_only_when_the_runner_says_online():
    assert wd.classify([TITANIUM_ROW, ONLINE_ROW]) == wd.ONLINE
    assert wd.classify([TITANIUM_ROW, OFFLINE_ROW]) == wd.OFFLINE


def test_a_missing_runner_is_alarmed_not_ignored():
    assert wd.classify([TITANIUM_ROW]) == wd.MISSING
    assert wd.classify([]) == wd.MISSING
    assert "not present" in wd._down_message(wd.MISSING)


# ── transitions ───────────────────────────────────────────────────────────

def test_first_healthy_observation_is_silent():
    d = wd.decide(None, wd.ONLINE, now=1_000)
    assert d.alert is None and d.message == ""


def test_first_observation_while_down_alerts_once():
    d = wd.decide(None, wd.OFFLINE, now=1_000)
    assert d.alert == wd.ALERT_DOWN
    assert "OFFLINE" in d.message
    assert "runbook-vanadium-ci-runner-recovery" in d.message
    assert "Do not relabel" in d.message


def test_repeated_offline_observations_do_not_repeat_the_alert():
    prev = {"status": wd.OFFLINE, "since": 1_000}
    for now in (1_120, 1_240, 100_000):
        assert wd.decide(prev, wd.OFFLINE, now=now).alert is None


def test_online_to_offline_alerts_with_uptime_context():
    prev = {"status": wd.ONLINE, "since": 1_000}
    d = wd.decide(prev, wd.OFFLINE, now=1_000 + 3 * 3600 + 600)
    assert d.alert == wd.ALERT_DOWN
    assert "3h10m" in d.message


def test_recovery_alerts_with_downtime():
    prev = {"status": wd.OFFLINE, "since": 1_000}
    d = wd.decide(prev, wd.ONLINE, now=1_000 + 95 * 60 + 30)
    assert d.alert == wd.ALERT_RECOVERED
    assert "back online after 1h35m" in d.message
    assert "rerun" in d.message


def test_offline_to_missing_is_also_an_alert():
    prev = {"status": wd.OFFLINE, "since": 1_000}
    assert wd.decide(prev, wd.MISSING, now=1_100).alert == wd.ALERT_DOWN


# ── state file ────────────────────────────────────────────────────────────

def test_state_round_trips(tmp_path):
    p = tmp_path / "state.json"
    assert wd.load_state(str(p)) is None
    wd.save_state(str(p), {"status": wd.OFFLINE, "since": 7})
    assert wd.load_state(str(p)) == {"status": wd.OFFLINE, "since": 7}


def test_corrupt_state_is_treated_as_first_run(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json")
    assert wd.load_state(str(p)) is None


# ── end-to-end check() with an injected probe ─────────────────────────────

def test_check_alerts_once_then_goes_quiet(tmp_path):
    p = tmp_path / "state.json"
    sent = []

    def probe(_repo):
        return [TITANIUM_ROW, OFFLINE_ROW]

    d1, ch1 = wd.check(state_path=str(p), fetch=probe, now=1_000,
                       alert_cmd=f"cat > {tmp_path}/alert.txt")
    d2, ch2 = wd.check(state_path=str(p), fetch=probe, now=1_060,
                       alert_cmd=f"cat > {tmp_path}/alert.txt")
    assert d1.alert == wd.ALERT_DOWN and ch1 == "alert-cmd"
    assert d2.alert is None and ch2 == ""
    assert "OFFLINE" in (tmp_path / "alert.txt").read_text()
    saved = json.loads(p.read_text())
    assert saved["status"] == wd.OFFLINE and saved["since"] == 1_000

    def healthy(_repo):
        return [TITANIUM_ROW, ONLINE_ROW]

    d3, _ = wd.check(state_path=str(p), fetch=healthy, now=1_060 + 300,
                     alert_cmd="true")
    assert d3.alert == wd.ALERT_RECOVERED
    assert json.loads(p.read_text())["since"] == 1_060 + 300


def test_dry_run_neither_writes_state_nor_alerts(tmp_path, capsys):
    p = tmp_path / "state.json"
    d, ch = wd.check(state_path=str(p), fetch=lambda _r: [OFFLINE_ROW], now=1_000, dry_run=True)
    assert d.alert == wd.ALERT_DOWN and ch == "dry-run"
    assert not p.exists()
    assert "[dry-run]" in capsys.readouterr().out


# ── CLI ───────────────────────────────────────────────────────────────────

def test_cli_exit_codes(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    ok = lambda _r: [ONLINE_ROW]          # noqa: E731
    bad = lambda _r: [OFFLINE_ROW]        # noqa: E731
    assert wd.main(["--check", "--state", state, "--dry-run"], fetch=ok) == 0
    assert wd.main(["--check", "--state", state, "--dry-run"], fetch=bad) == 1
    assert wd.main(["--check", "--state", state, "--dry-run", "--json"], fetch=bad) == 1
    assert wd.main(["--check", "--state", state],
                   fetch=lambda _r: (_ for _ in ()).throw(RuntimeError("api down"))) == 2
    out = capsys.readouterr().out
    assert '"alert": "down"' in out or '"alert": "down"' in out


def test_probe_failure_does_not_look_like_an_outage(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    assert wd.main(["--check", "--state", state],
                   fetch=lambda _r: (_ for _ in ()).throw(RuntimeError("api down"))) == 2
    assert not Path(state).exists()
    assert "cannot read the runners API" in capsys.readouterr().err
