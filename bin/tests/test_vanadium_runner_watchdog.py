"""Hermetic tests for bin/vanadium_runner_watchdog.py.

The watchdog's whole value is that it stays quiet while the runner is healthy
and says something once when it is not, so these tests pin the transition
logic, the state file, the "probe failure is not an outage" rule, and — since
2026-11-26 — the LAN-path probe, which is the half that would have caught the
outage where the runner stayed online and its address went dead.

    python3 -m pytest bin/tests/test_vanadium_runner_watchdog.py -q
"""
from __future__ import annotations

import json
import socket
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


# ── LAN probe ─────────────────────────────────────────────────────────────

def test_probe_lan_is_up_when_a_listener_answers():
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert wd.probe_lan("127.0.0.1", port, timeout=2) == wd.LAN_UP
    finally:
        srv.close()


def test_probe_lan_is_down_when_nothing_answers():
    # bind then release the port: the connect is refused, not accepted
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert wd.probe_lan("127.0.0.1", port, timeout=1) == wd.LAN_DOWN


def test_probe_lan_calls_a_name_that_does_not_resolve_down():
    assert wd.probe_lan("vanadium.invalid", 22, timeout=2) == wd.LAN_DOWN


def test_lan_break_while_the_runner_stays_online_pages():
    prev = {"status": wd.ONLINE, "since": 1_000, "checked_at": 1_500, "lan": wd.LAN_UP}
    d = wd.decide(prev, wd.ONLINE, now=1_620, lan=wd.LAN_DOWN)
    assert d.alert == wd.ALERT_LAN_DOWN
    assert d.lan == wd.LAN_DOWN
    assert "DEAD on the LAN" in d.message
    assert "vanadium-diagnostic" in d.message
    assert "pg-backup-to-vanadium" in d.message
    assert "2m" in d.message          # last good check 120 s before this one


def test_a_dead_lan_is_not_reported_twice():
    prev = {"status": wd.ONLINE, "since": 1_000, "checked_at": 1_500,
            "lan": wd.LAN_DOWN, "lan_since": 1_560}
    for now in (1_620, 1_740, 99_999):
        d = wd.decide(prev, wd.ONLINE, now=now, lan=wd.LAN_DOWN)
        assert d.alert is None and d.message == ""


def test_a_healthy_pair_stays_silent():
    prev = {"status": wd.ONLINE, "since": 1_000, "len": wd.LAN_UP,
            "checked_at": 1_500, "lan": wd.LAN_UP}
    assert wd.decide(prev, wd.ONLINE, now=1_620, lan=wd.LAN_UP).alert is None


def test_lan_recovery_alerts_once():
    prev = {"status": wd.ONLINE, "since": 1_000, "lan": wd.LAN_DOWN, "lan_since": 1_600}
    d = wd.decide(prev, wd.ONLINE, now=1_600 + 90 * 60, lan=wd.LAN_UP)
    assert d.alert == wd.ALERT_LAN_RECOVERED
    assert "answers TCP again after 1h30m" in d.message
    assert "rerun" in d.message


def test_a_missing_lan_verdict_is_not_a_fault():
    prev = {"status": wd.ONLINE, "since": 1_000, "lan": wd.LAN_UP, "checked_at": 1_500}
    d = wd.decide(prev, wd.ONLINE, now=1_620, lan=None)
    assert d.alert is None and d.ok


def test_a_fully_dead_host_is_one_page_not_two():
    prev = {"status": wd.ONLINE, "since": 1_000, "checked_at": 1_500, "lan": wd.LAN_UP}
    d = wd.decide(prev, wd.OFFLINE, now=1_620, lan=wd.LAN_DOWN)
    assert d.alert == wd.ALERT_DOWN
    assert "OFFLINE" in d.message
    assert "DEAD on the LAN" not in d.message


def test_a_return_without_its_lan_identity_escalates():
    prev = {"status": wd.OFFLINE, "since": 1_620, "checked_at": 7_000,
            "lan": wd.LAN_DOWN, "lan_since": 1_620}
    d = wd.decide(prev, wd.ONLINE, now=7_400, lan=wd.LAN_DOWN)
    assert d.alert == wd.ALERT_LAN_DOWN
    assert "back online after" in d.message
    assert "without its LAN identity" in d.message
    assert "recorded down at" in d.message


def test_a_return_with_its_lan_identity_is_the_plain_recovery():
    prev = {"status": wd.OFFLINE, "since": 1_000, "lan": wd.LAN_DOWN, "lan_since": 1_000}
    d = wd.decide(prev, wd.ONLINE, now=1_000 + 3_600, lan=wd.LAN_UP)
    assert d.alert == wd.ALERT_RECOVERED
    assert "answers too" in d.message


def test_first_observation_with_a_dead_lan_pages():
    d = wd.decide(None, wd.ONLINE, now=1_000, lan=wd.LAN_DOWN)
    assert d.alert == wd.ALERT_LAN_DOWN
    assert "first check that probed it" in d.message


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


def test_check_records_the_lan_verdict_and_times_the_fault(tmp_path):
    p = tmp_path / "state.json"
    healthy = lambda _r: [ONLINE_ROW]                 # noqa: E731
    up = lambda: wd.LAN_UP                            # noqa: E731
    down = lambda: wd.LAN_DOWN                        # noqa: E731

    wd.check(state_path=str(p), fetch=healthy, now=1_000, lan_probe=up, alert_cmd="true")
    saved = json.loads(p.read_text())
    assert saved["lan"] == wd.LAN_UP and saved["lan_since"] == 1_000

    d, ch = wd.check(state_path=str(p), fetch=healthy, now=1_620, lan_probe=down,
                     alert_cmd=f"cat > {tmp_path}/lan.txt")
    assert d.alert == wd.ALERT_LAN_DOWN and ch == "alert-cmd"
    assert "DEAD on the LAN" in (tmp_path / "lan.txt").read_text()
    saved = json.loads(p.read_text())
    assert saved["lan"] == wd.LAN_DOWN and saved["lan_since"] == 1_620
    assert saved["since"] == 1_000          # the runner never dropped

    d2, _ = wd.check(state_path=str(p), fetch=healthy, now=1_740, lan_probe=down,
                     alert_cmd="true")
    assert d2.alert is None


def test_a_skipped_lan_probe_keeps_the_recorded_verdict(tmp_path):
    p = tmp_path / "state.json"
    wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=1_000,
             lan_probe=lambda: wd.LAN_DOWN, alert_cmd="true")
    wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=1_060, alert_cmd="true")
    saved = json.loads(p.read_text())
    assert saved["lan"] == wd.LAN_DOWN and saved["lan_since"] == 1_000


def test_dry_run_neither_writes_state_nor_alerts(tmp_path, capsys):
    p = tmp_path / "state.json"
    d, ch = wd.check(state_path=str(p), fetch=lambda _r: [OFFLINE_ROW], now=1_000, dry_run=True)
    assert d.alert == wd.ALERT_DOWN and ch == "dry-run"
    assert not p.exists()
    assert "[dry-run]" in capsys.readouterr().out


# ── CLI ───────────────────────────────────────────────────────────────────

def test_cli_exit_codes(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    up = lambda: wd.LAN_UP                # noqa: E731
    ok = lambda _r: [ONLINE_ROW]          # noqa: E731
    bad = lambda _r: [OFFLINE_ROW]        # noqa: E731
    assert wd.main(["--check", "--state", state, "--dry-run"], fetch=ok, lan_probe=up) == 0
    assert wd.main(["--check", "--state", state, "--dry-run"], fetch=bad, lan_probe=up) == 1
    assert wd.main(["--check", "--state", state, "--dry-run", "--json"],
                   fetch=bad, lan_probe=up) == 1
    assert wd.main(["--check", "--state", state],
                   fetch=lambda _r: (_ for _ in ()).throw(RuntimeError("api down"))) == 2
    out = capsys.readouterr().out
    assert '"alert": "down"' in out or '"alert": "down"' in out


def test_cli_fails_when_only_the_lan_is_dead(tmp_path):
    state = str(tmp_path / "s.json")
    rc = wd.main(["--check", "--state", state, "--dry-run"],
                 fetch=lambda _r: [ONLINE_ROW], lan_probe=lambda: wd.LAN_DOWN)
    assert rc == 1


def test_cli_lan_pins_are_reported_in_json(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    wd.main(["--check", "--state", state, "--dry-run", "--json",
             "--lan-host", "10.0.0.9", "--lan-port", "2222"],
            fetch=lambda _r: [ONLINE_ROW], lan_probe=lambda: wd.LAN_DOWN)
    assert '"lan": "down"' in capsys.readouterr().out


def test_cli_no_lan_probe_restores_runner_only_behaviour(tmp_path):
    state = str(tmp_path / "s.json")
    rc = wd.main(["--check", "--state", state, "--dry-run", "--no-lan-probe"],
                 fetch=lambda _r: [ONLINE_ROW], lan_probe=lambda: wd.LAN_DOWN)
    assert rc == 0


def test_probe_failure_does_not_look_like_an_outage(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    assert wd.main(["--check", "--state", state],
                   fetch=lambda _r: (_ for _ in ()).throw(RuntimeError("api down"))) == 2
    assert not Path(state).exists()
    assert "cannot read the runners API" in capsys.readouterr().err
