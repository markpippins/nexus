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
import time
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
    # The alert must point at the workflow that actually ships, not at a throwaway.
    assert ".github/workflows/vanadium-diagnostic.yml" in d.message
    assert "throwaway" not in d.message
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


# ── alert titles ──────────────────────────────────────────────────────────
# The forum fallback is the hands-free channel, and its thread title used to be
# one hardcoded "runner offline" for all four verdicts. That mislabels the
# case this probe was built for — an online runner with a dead LAN address —
# so the title follows the verdict.

def test_every_verdict_gets_its_own_title():
    assert wd._alert_title(wd.ALERT_DOWN) == wd.DEFAULT_ALERT_TITLE
    titles = {a: wd._alert_title(a) for a in
              (wd.ALERT_DOWN, wd.ALERT_RECOVERED, wd.ALERT_LAN_DOWN,
               wd.ALERT_LAN_RECOVERED)}
    assert len(set(titles.values())) == 4, titles


def test_lan_down_title_never_claims_the_runner_is_offline():
    title = wd._alert_title(wd.ALERT_LAN_DOWN)
    assert "LAN" in title
    assert "online" in title
    assert "offline" not in title.lower()


def test_an_unknown_verdict_falls_back_to_the_offline_title():
    assert wd._alert_title(None) == wd.DEFAULT_ALERT_TITLE
    assert wd._alert_title("something-new") == wd.DEFAULT_ALERT_TITLE


def test_the_forum_post_carries_the_verdict_title(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return None

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    channel = wd.notify("vanadium is heartbeating to GitHub but DEAD on the LAN.",
                        title=wd._alert_title(wd.ALERT_LAN_DOWN))
    assert channel == "assembly"
    argv = seen["argv"]
    title = argv[argv.index("--title") + 1]
    assert title == wd.ALERT_TITLES[wd.ALERT_LAN_DOWN]
    assert "offline" not in title.lower()


# ── maintenance windows ───────────────────────────────────────────────────
#
# A scheduled reboot takes the runner down for a minute or two. Without a quiet
# window the two-minute timer sees the drop and the return and pages twice; with
# one it stays silent, but a real outage that happens to start inside the window
# must still be reported once the window closes.

def _at_local(monkeypatch, hour, minute):
    """Freeze what the watchdog reads as local time, TZ-independently."""
    stamp = time.struct_time((2026, 1, 1, hour, minute, 0, 3, 1, 0))
    monkeypatch.setattr(wd.time, "localtime", lambda _ts: stamp)


def _window_around(ts, before=10, after=10):
    """A window spec that covers ts's local minute, +/- the given minutes."""
    m = wd.time.localtime(ts).tm_hour * 60 + wd.time.localtime(ts).tm_min
    start, end = (m - before) % 1440, (m + after) % 1440
    return wd.parse_window_spec(f"{wd._fmt_minute(start)}-{wd._fmt_minute(end)}")


def test_parse_window_spec_empty_means_no_window():
    assert wd.parse_window_spec(None) == ()
    assert wd.parse_window_spec("") == ()
    assert wd.parse_window_spec("   ") == ()


def test_parse_window_spec_reads_one_or_more_windows():
    assert wd.parse_window_spec("02:20-02:50") == ((140, 170),)
    assert wd.parse_window_spec("02:20-02:50, 03:10-03:20") == ((140, 170), (190, 200))
    assert wd.parse_window_spec("02:20-02:50;03:10-03:20") == ((140, 170), (190, 200))


def test_parse_window_spec_rejects_malformed_specs():
    """A typo must not silently disable the quiet window."""
    for bad in ("02:20", "2:2:0", "25:00-26:00", "02:70-03:00", "two-twenty", "02:20-"):
        with pytest.raises(ValueError):
            wd.parse_window_spec(bad)


def test_active_window_reports_the_window_it_matched(monkeypatch):
    _at_local(monkeypatch, 2, 35)
    assert wd.active_window(wd.parse_window_spec("02:20-02:50"), 0) == "02:20-02:50"
    assert wd.active_window(wd.parse_window_spec("02:20-02:50,03:00-04:00"), 0) == "02:20-02:50"
    # the end is exclusive, and a zero-length window matches nothing
    assert wd.active_window(wd.parse_window_spec("02:20-02:35"), 0) is None
    assert wd.active_window(wd.parse_window_spec("02:35-02:35"), 0) is None
    assert wd.active_window(wd.parse_window_spec("02:36-02:50"), 0) is None


def test_active_window_supports_a_window_crossing_midnight(monkeypatch):
    spec = wd.parse_window_spec("23:00-01:00")
    for hour, minute, inside in (
        (23, 0, True), (23, 59, True), (0, 30, True),
        (1, 0, False), (22, 59, False), (1, 1, False),
    ):
        _at_local(monkeypatch, hour, minute)
        assert bool(wd.active_window(spec, 1_700_000_000)) is inside, (hour, minute)


def test_no_window_means_the_old_behaviour(monkeypatch):
    _at_local(monkeypatch, 2, 35)
    assert wd.active_window((), 1_700_000_000) is None


def test_a_drop_inside_the_window_is_held_then_sent_when_it_closes(tmp_path):
    p = tmp_path / "state.json"
    alerts = tmp_path / "alerts.txt"
    cmd = f"cat >> {alerts}"
    down = lambda _r: [OFFLINE_ROW]        # noqa: E731

    # tick 1: healthy, before the window
    d0, _ = wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=1_000,
                     alert_cmd=cmd)
    assert d0.alert is None

    # tick 2: goes down inside the window -> held, nothing delivered
    window = _window_around(1_060)
    d1, ch1 = wd.check(state_path=str(p), fetch=down, now=1_060,
                       maintenance=window, alert_cmd=cmd)
    assert d1.alert is None and ch1 == ""
    assert "holding the down alert" in d1.message
    assert not alerts.exists(), "a held alert must not be delivered"
    saved = json.loads(p.read_text())
    assert saved["status"] == wd.OFFLINE
    assert saved["pending_alert"] == wd.ALERT_DOWN

    # tick 3: window closed, still down -> exactly one alert, saying it was held
    d2, ch2 = wd.check(state_path=str(p), fetch=down, now=1_060 + 3_600,
                       maintenance=window, alert_cmd=cmd)
    assert d2.alert == wd.ALERT_DOWN and ch2 == "alert-cmd"
    assert "held during the maintenance window" in d2.message
    assert "still down" in d2.message
    assert "OFFLINE" in alerts.read_text()

    # tick 4: quiet again, and nothing owed any more
    d3, _ = wd.check(state_path=str(p), fetch=down, now=1_060 + 3_660,
                     maintenance=window, alert_cmd=cmd)
    assert d3.alert is None
    assert "pending_alert" not in json.loads(p.read_text())
    assert alerts.read_text().count("OFFLINE") == 1


def test_repeated_in_window_samples_keep_the_owed_alert(tmp_path):
    """Regression: the second in-window tick saw no new transition and dropped it.

    The two-minute timer fires many times inside a window. Only the first of
    those ticks sees a transition; the rest must carry the held alert forward,
    or the debt is forgotten and the deferred send at window close never fires.
    """
    p = tmp_path / "state.json"
    alerts = tmp_path / "alerts.txt"
    cmd = f"cat >> {alerts}"
    down = lambda _r: [OFFLINE_ROW]        # noqa: E731

    wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=1_000, alert_cmd=cmd)

    window = _window_around(1_060, 30, 30)     # wide enough for several samples
    for sample, now in enumerate((1_060, 1_120, 1_180, 1_240, 1_300), start=1):
        d, ch = wd.check(state_path=str(p), fetch=down, now=now,
                         maintenance=window, alert_cmd=cmd)
        assert d.alert is None and ch == "", f"sample {sample} delivered early"
        assert "holding the down alert" in d.message, f"sample {sample}: {d.message!r}"
        saved = json.loads(p.read_text())
        assert saved["pending_alert"] == wd.ALERT_DOWN, f"sample {sample} forgot the debt"
        assert not alerts.exists(), f"sample {sample} delivered while in the window"

    # Window closes with the runner still down: one deferred delivery, once.
    d, ch = wd.check(state_path=str(p), fetch=down, now=1_300 + 3_600,
                     maintenance=window, alert_cmd=cmd)
    assert d.alert == wd.ALERT_DOWN and ch == "alert-cmd"
    assert "held during the maintenance window" in d.message
    assert alerts.read_text().count("OFFLINE") == 1
    assert "pending_alert" not in json.loads(p.read_text())


def test_a_recovery_inside_the_window_is_dropped_not_queued(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"status": wd.OFFLINE, "since": 1_000}))
    d, ch = wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=2_000,
                     maintenance=_window_around(2_000), alert_cmd="false")
    assert d.alert is None and ch == ""
    assert "holding the recovered alert" in d.message
    saved = json.loads(p.read_text())
    assert saved["status"] == wd.ONLINE
    assert "pending_alert" not in saved


def test_a_recovery_outside_the_window_still_alerts(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"status": wd.OFFLINE, "since": 1_000}))
    d, _ = wd.check(state_path=str(p), fetch=lambda _r: [ONLINE_ROW], now=2_000,
                    maintenance=_window_around(2_000 + 3_600), alert_cmd="true")
    assert d.alert == wd.ALERT_RECOVERED


def test_dry_run_inside_a_window_still_writes_nothing(tmp_path, capsys):
    p = tmp_path / "state.json"
    d, ch = wd.check(state_path=str(p), fetch=lambda _r: [OFFLINE_ROW], now=1_000,
                     dry_run=True, maintenance=_window_around(1_000))
    assert d.alert is None and ch == ""
    assert "holding the down alert" in d.message
    assert not p.exists()


def test_cli_window_holds_the_alert(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    now = int(time.time())
    label = wd.active_window(_window_around(now), now)
    code = wd.main(["--check", "--state", state, "--dry-run",
                    "--maintenance-window", label], fetch=lambda _r: [OFFLINE_ROW])
    # the exit code still reports the runner's status; only delivery is held
    assert code == 1
    assert "holding the down alert" in capsys.readouterr().out


def test_cli_rejects_a_malformed_window(tmp_path):
    state = str(tmp_path / "s.json")
    with pytest.raises(SystemExit) as exc:
        wd.main(["--check", "--state", state, "--dry-run", "--maintenance-window", "02:20"],
                fetch=lambda _r: [ONLINE_ROW])
    assert exc.value.code == 2
    assert not Path(state).exists()


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
