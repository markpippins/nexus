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


def _boom(_repo):
    raise RuntimeError("gh: Resource not accessible by personal access token (HTTP 403)")


def test_probe_failure_does_not_look_like_an_outage(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    assert wd.main(["--check", "--state", state], fetch=_boom) == 2
    saved = json.loads(Path(state).read_text())   # bookkeeping only, no runner claim
    assert saved["probe_failures"] == 1
    assert saved.get("status") is None
    assert saved.get("last_alert") is None
    assert "cannot read the runners API" in capsys.readouterr().err


# ── watchdog health: a blind probe must not be a silent probe ─────────────

def test_one_probe_failure_is_counted_but_silent(tmp_path):
    """A single failed probe is noise: it must not page and must not claim anything."""
    p = tmp_path / "s.json"
    d, ch = wd.check(state_path=str(p), fetch=_boom, now=1_000, alert_cmd="false")
    assert d.status == wd.PROBE_ERROR and d.alert is None and ch == ""
    saved = json.loads(p.read_text())
    assert saved["probe_failures"] == 1
    assert "status" not in saved
    assert saved.get("last_alert") != wd.ALERT_DOWN


def test_probe_failures_alert_once_at_the_threshold(tmp_path):
    """Three consecutive failures page once; the fourth does not page again."""
    p = tmp_path / "s.json"
    log = tmp_path / "alerts.txt"
    seen = []
    for now in (1_000, 1_120, 1_240, 1_360):
        d, _ = wd.check(state_path=str(p), fetch=_boom, now=now, alert_cmd=f"cat >> {log}")
        seen.append(d.alert)
    assert seen == [None, None, wd.ALERT_PROBE, None]
    text = log.read_text()
    assert text.count("BLIND") == 1
    assert "Administration: Read-only" in text
    assert "actions/runners" in text
    assert json.loads(p.read_text())["probe_failures"] == 4


def test_probe_recovery_is_announced_and_resets_the_counter(tmp_path):
    p = tmp_path / "s.json"
    log = tmp_path / "alerts.txt"
    for now in (1_000, 1_120, 1_240):
        wd.check(state_path=str(p), fetch=_boom, now=now, alert_cmd=f"cat >> {log}")
    d, ch = wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=1_360,
                     alert_cmd=f"cat >> {log}")
    assert d.alert == wd.ALERT_PROBE_RECOVERED and ch == "alert-cmd"
    assert "after 3 consecutive failed probes" in d.message
    saved = json.loads(p.read_text())
    assert saved["probe_failures"] == 0
    assert saved["status"] == wd.ONLINE


def test_probe_failure_over_a_known_outage_keeps_the_outage_and_does_not_rep_page(tmp_path):
    """Blindness over a known-offline runner keeps `offline` and does not re-page `down`."""
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"status": wd.OFFLINE, "since": 1_000, "last_alert": wd.ALERT_DOWN}))
    d, _ = wd.check(state_path=str(p), fetch=_boom, now=1_120, alert_cmd="false")
    assert d.alert is None
    saved = json.loads(p.read_text())
    assert saved["status"] == wd.OFFLINE and saved["since"] == 1_000
    assert saved["probe_failures"] == 1


def test_threshold_is_configurable(tmp_path):
    p = tmp_path / "s.json"
    d, _ = wd.check(state_path=str(p), fetch=_boom, now=1_000,
                    alert_cmd=f"cat >> {tmp_path}/a.txt", probe_alert_after=1)
    assert d.alert == wd.ALERT_PROBE


def test_cli_reports_the_failure_count_and_keeps_exit_2(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    assert wd.main(["--check", "--state", state], fetch=_boom) == 2
    err = capsys.readouterr().err
    assert "cannot read the runners API" in err
    assert "1 consecutive failure(s)" in err
    assert "alerting at 3" in err


def test_dry_run_never_writes_probe_state(tmp_path):
    p = tmp_path / "s.json"
    wd.check(state_path=str(p), fetch=_boom, now=1_000, dry_run=True)
    assert not p.exists()
