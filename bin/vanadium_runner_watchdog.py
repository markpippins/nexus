#!/usr/bin/env python3
"""vanadium_runner_watchdog.py — alert when the vanadium CI runner (or its LAN path) fails.

Two probes, because this host has now failed in both directions:

1. **Is the runner on duty?** `GET /repos/{owner}/{repo}/actions/runners`.
   2026-11-24: userspace wedged, the runner stopped heartbeating, and the outage
   was found from a queued `sonar` check hours later.
2. **Is it reachable on the LAN?** One TCP connect to the host's LAN address.
   2026-11-25/26: the runner stayed online and kept scanning while
   `192.168.1.209` was dead to ping, ARP and SSH. A runner-status probe alone
   stays silent through exactly that — CI needs only `localhost:9000` on the box
   and the GitHub API, so every check was green while the nightly off-machine
   backup and the live SQL sync failed for two nights.

The probe is a one-shot run meant for a systemd timer. It compares each verdict
with the last one it recorded and alerts only on a *transition*, so a timer
firing every two minutes does not page anyone twice for the same outage.

Quiet windows: `--maintenance-window HH:MM-HH:MM[,HH:MM-HH:MM...]` (env
`NEXUS_WATCHDOG_MAINTENANCE_WINDOW`, host local time, default none) holds the
delivery of any alert raised inside the window. A scheduled reboot takes the
runner offline for a minute or two and would otherwise page twice every night —
once going down, once coming back. Nothing is discarded: a down alert raised
inside a window is recorded as pending in the state file and delivered once the
window closes if the runner is still down, so a real outage that begins inside
the window is still reported. Held recovery alerts are simply dropped.

What it watches about *itself*: a probe that cannot read the API is a blind
watchdog, not a quiet one. Probe failures are counted in the state file and, at
`--probe-alert-after` consecutive failures (default 3, ~6 minutes on the
two-minute timer), one watchdog-health alert is posted; a later successful probe
announces that sight is restored. A probe failure still never claims the runner
is down, and still exits 2.

Alert channels (first one configured wins):
  --webhook-url URL / env NEXUS_ALERT_WEBHOOK   POST {"text": "..."}
  --alert-cmd CMD                               shell command, message on stdin
  default                                       Assembly issues-and-open-questions
                                                via bin/post-change-log.sh

Usage:
  vanadium_runner_watchdog.py --check              # probe both, alert on transitions
  vanadium_runner_watchdog.py --check --dry-run    # print the decision, write nothing
  vanadium_runner_watchdog.py --check --json       # machine-readable summary
  vanadium_runner_watchdog.py --check --no-lan-probe   # runner status only (old behaviour)
  vanadium_runner_watchdog.py --check --maintenance-window 02:20-02:50
                                                   # hold alerts through a reboot

Exit codes: 0 runner online and LAN reachable; 1 either fault (or the runner is
            missing from the API); 2 environment error (the runners API could not
            be read). A LAN probe that cannot connect is a VERDICT, not an
            environment error: the refusal to answer is the thing being watched.
            Exit 2 never writes a runner status and never claims an outage — but
            it does accumulate the probe-failure count and, at the threshold,
            delivers the watchdog-health alert.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

REPO = os.environ.get("NEXUS_REPO", "markpippins/nexus")
RUNNER = "vanadium"
STATE_PATH = os.environ.get("NEXUS_WATCHDOG_STATE", "/var/tmp/vanadium-runner-watchdog.json")
MAINTENANCE_WINDOW = os.environ.get("NEXUS_WATCHDOG_MAINTENANCE_WINDOW", "")
PROBE_ALERT_AFTER = int(os.environ.get("NEXUS_WATCHDOG_PROBE_ALERT_AFTER", "3"))

# The LAN identity the backups, the SQL sync and `ssh` all depend on. Probed by
# address rather than by name: the DNS name survived the whole 2026-11-25
# outage (the router kept serving `vanadium.attlocal.net -> 192.168.1.209` with
# nothing behind it), so resolving is precisely what does NOT tell you anything.
LAN_HOST = os.environ.get("NEXUS_WATCHDOG_LAN_HOST", "192.168.1.209")
LAN_PORT = os.environ.get("NEXUS_WATCHDOG_LAN_PORT", "22")
LAN_TIMEOUT = os.environ.get("NEXUS_WATCHDOG_LAN_TIMEOUT", "3")

ONLINE = "online"
OFFLINE = "offline"
MISSING = "missing"
PROBE_ERROR = "probe-error"

LAN_UP = "up"
LAN_DOWN = "down"

ALERT_DOWN = "down"
ALERT_RECOVERED = "recovered"
ALERT_LAN_DOWN = "lan-down"
ALERT_LAN_RECOVERED = "lan-recovered"
ALERT_PROBE = "probe-error"
ALERT_PROBE_RECOVERED = "probe-recovered"

# One title per verdict. The forum fallback used to hardcode "runner offline"
# for every alert, which mislabels the exact case this probe exists for: the
# host heartbeating and running scans while its LAN address is dead. An
# operator scanning titles would read "offline" about a runner that is up.
# The blindness verdicts are named for what they are too: a watchdog that
# cannot read the runners API has not learned anything about the runner.
DEFAULT_ALERT_TITLE = "ALERT: vanadium CI runner offline"
ALERT_TITLES = {
    ALERT_DOWN: DEFAULT_ALERT_TITLE,
    ALERT_RECOVERED: "RECOVERED: vanadium CI runner is online again",
    ALERT_LAN_DOWN: "ALERT: vanadium runner is online but dead on the LAN",
    ALERT_LAN_RECOVERED: "RECOVERED: vanadium's LAN path is back",
    ALERT_PROBE: "ALERT: vanadium watchdog cannot read the runners API",
    ALERT_PROBE_RECOVERED: "RECOVERED: vanadium watchdog can read the runners API again",
}


def _alert_title(alert: Optional[str]) -> str:
    """The forum thread title for one alert kind (default: the offline title)."""
    return ALERT_TITLES.get(alert or "", DEFAULT_ALERT_TITLE)


@dataclass(frozen=True)
class Decision:
    status: str
    alert: Optional[str]          # None | ALERT_DOWN | ALERT_RECOVERED | ALERT_LAN_* | ALERT_PROBE*
    message: str
    lan: Optional[str] = None     # LAN_UP | LAN_DOWN | None (not probed)
    error: Optional[str] = None   # set only when the probe itself failed
    probe_failures: int = 0       # consecutive failed probes observed so far

    @property
    def ok(self) -> bool:
        """Healthy means both probes agree: on duty *and* reachable."""
        return self.status == ONLINE and self.lan != LAN_DOWN


def classify(runners: Sequence[Dict[str, Any]], name: str = RUNNER) -> str:
    """Online only when the runner row exists and says so; otherwise alert-worthy."""
    for r in runners or ():
        if str(r.get("name")) == name:
            return ONLINE if str(r.get("status")).lower() == ONLINE else OFFLINE
    return MISSING


def probe_lan(host: str = LAN_HOST, port: int = 22, timeout: float = 3.0) -> str:
    """One TCP connect to the host's LAN address: LAN_UP or LAN_DOWN.

    A refusal is LAN_DOWN just as a timeout is — the address answered but the
    service the backups and the sync need is not there, and both are "cannot
    use this host". The reason is deliberately not distinguished here: the
    host-side dump (`ss -ltnp`, `ip -br a`, `ip neigh`) does that once the alert
    has fired, and a probe that cannot tell the difference must say so rather
    than guess.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return LAN_UP
    except (OSError, ValueError):
        return LAN_DOWN


def load_state(path: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(path: str, state: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def _fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _iso(when: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


def _fmt_minute(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_hhmm(value: str, spec: str) -> int:
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"maintenance window {spec!r}: {value!r} is not HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"maintenance window {spec!r}: {value!r} is not a time of day")
    return hour * 60 + minute


def parse_window_spec(spec: Optional[str]) -> Tuple[Tuple[int, int], ...]:
    """Parse ``HH:MM-HH:MM[,HH:MM-HH:MM...]`` into (start, end) minutes of day.

    An empty spec means no quiet windows. A malformed one raises rather than
    being ignored: a typo must not quietly disable the window it was written to
    create, because the symptom would be the pages it exists to prevent.
    """
    windows: list[Tuple[int, int]] = []
    for chunk in (spec or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        start_text, sep, end_text = chunk.partition("-")
        if not sep:
            raise ValueError(f"maintenance window {chunk!r} is not HH:MM-HH:MM")
        windows.append((_parse_hhmm(start_text, chunk), _parse_hhmm(end_text, chunk)))
    return tuple(windows)


def active_window(windows: Sequence[Tuple[int, int]], now: int) -> Optional[str]:
    """Return the window containing ``now`` as ``HH:MM-HH:MM``, else None.

    Windows are read in the host's local time: the probe runs from a systemd
    timer on the box whose reboot schedule the window covers, so the operator
    writes the window in the same clock they write the schedule. A window whose
    start is later than its end crosses midnight; a zero-length one matches
    nothing.
    """
    if not windows:
        return None
    local = time.localtime(now)
    minute = local.tm_hour * 60 + local.tm_min
    for start, end in windows:
        if start == end:
            continue
        inside = (start <= minute < end) if start < end else (minute >= start or minute < end)
        if inside:
            return f"{_fmt_minute(start)}-{_fmt_minute(end)}"
    return None


def _deferred_note(window: Optional[str], since: Optional[int], now: int) -> str:
    """Say why a down alert is arriving late — it was held, not lost."""
    if not window:
        return ""
    down_for = f" for {_fmt(now - int(since))}" if since else ""
    return (
        f"This alert was held during the maintenance window {window} so a scheduled "
        f"reboot could not page anyone; the runner is still down{down_for}, so it is "
        f"being sent now.\n\n"
    )


def decide(prev: Optional[Dict[str, Any]], status: str, now: int, *,
           lan: Optional[str] = None,
           lan_target: str = f"{LAN_HOST}:{LAN_PORT}") -> Decision:
    """Alert only on a transition, never on a repeat observation.

    Two independent verdicts, one message per run: if the host is down and the
    LAN with it, that is one outage, not two pages.
    """
    prev = {} if prev is None else prev
    prev_status = prev.get("status")
    since = int(prev.get("since") or now)
    prev_lan = prev.get("lan")
    lan_since = int(prev.get("lan_since") or now)

    runner_alert: Optional[str] = None
    if prev_status is None:
        # First observation: say something only if it is already bad.
        if status != ONLINE:
            runner_alert = ALERT_DOWN
    elif prev_status != status:
        runner_alert = ALERT_RECOVERED if status == ONLINE else ALERT_DOWN

    # A LAN verdict is its own alert only while the runner is heartbeating:
    # when the host is offline the runner alert already covers it, and the LAN
    # state is recorded silently so that a return *without* its LAN identity
    # still escalates — the 2026-11-25 shape, twice.
    lan_alert: Optional[str] = None
    if lan is not None and status == ONLINE:
        # Alert on the LAN transitioning down, and also when the runner comes
        # BACK while the LAN is still down: a host returning without its LAN
        # identity is the recurrence this probe exists for.
        if lan == LAN_DOWN and (prev_lan != LAN_DOWN or runner_alert == ALERT_RECOVERED):
            lan_alert = ALERT_LAN_DOWN
        elif lan == LAN_UP and prev_lan == LAN_DOWN:
            lan_alert = ALERT_LAN_RECOVERED

    if lan_alert == ALERT_LAN_DOWN:
        message = _lan_down_message(_lan_lead(lan_target, prev_lan, prev, lan_since, now))
        if runner_alert == ALERT_RECOVERED:
            message = (_recovered_text(now - since, prev_status)
                       + "\n\n**It came back without its LAN identity.**\n\n" + message)
        return Decision(status, ALERT_LAN_DOWN, message, lan=lan)

    if runner_alert == ALERT_DOWN:
        return Decision(status, ALERT_DOWN, _down_message(status, since, now), lan=lan)

    if runner_alert == ALERT_RECOVERED:
        message = _recovered_text(now - since, prev_status)
        if lan == LAN_UP:
            message += f" The LAN path ({lan_target}) answers too."
        return Decision(status, ALERT_RECOVERED, message, lan=lan)

    if lan_alert == ALERT_LAN_RECOVERED:
        return Decision(status, ALERT_LAN_RECOVERED,
                        _lan_recovered_message(lan_target, lan_since, now), lan=lan)

    return Decision(status, None, "", lan=lan)


def _down_message(status: str, since: Optional[int] = None, now: Optional[int] = None) -> str:
    was = ""
    if since:
        dur = _fmt((now or since) - since)
        was = f" It had been online for {dur} (since {_iso(since)})."
    if status == MISSING:
        head = ("The `vanadium` runner is not present in the repository's Actions runner "
                "list at all — renamed, removed, or re-registered under another name.")
    else:
        head = "The `vanadium` CI runner is OFFLINE."
    return (
        f"{head}{was}\n\n"
        "Every PR now sits at N-1/N with the required `sonar` check queued, and no PR can "
        "merge until it returns.\n\n"
        "Diagnose and recover: docs/runbook-vanadium-ci-runner-recovery.md §1-§3 "
        "(measured fingerprint; the host can answer ping and TCP while sshd never sends a "
        "banner, which needs a physical power cycle).\n"
        "Do not relabel `titanium` as `vanadium`: the preflight then skips the scan and the "
        "gate goes green without scanning."
    )


def _recovered_text(seconds: int, prev_status: Optional[str]) -> str:
    return (f"vanadium CI runner is back online after {_fmt(seconds)} "
            f"(was {prev_status}). Queued sonar jobs should start on their own; if they "
            f"expired, rerun them with `gh run rerun <run-id> --failed`.")


def _lan_lead(target: str, prev_lan: Optional[str], prev: Dict[str, Any],
              lan_since: int, now: int) -> str:
    """When it broke, as precisely as a two-minute poll can say it."""
    if prev_lan == LAN_UP:
        last_good = int(prev.get("checked_at") or 0)
        if last_good:
            return (f"{target} answered a TCP connection at {_iso(last_good)} and has not "
                    f"answered since ({_fmt(now - last_good)} ago).")
    if prev_lan == LAN_DOWN:
        return (f"{target} has not answered since it was recorded down at "
                f"{_iso(lan_since)} ({_fmt(now - lan_since)} ago).")
    return f"{target} did not answer on the first check that probed it."


def _lan_down_message(lead: str) -> str:
    return (
        f"vanadium is heartbeating to GitHub but DEAD on the LAN. {lead}\n\n"
        "This is the 2026-11-25/26 signature, and the reason a runner-status-only watchdog "
        "stayed quiet through it: the host reaches github.com, so the runners API says "
        "`online` and every push still runs `vanadium-sonar` — a job that needs only "
        "`localhost:9000` on the box itself. Behind that green check, everything that needs "
        "its LAN address is broken:\n"
        "  - `pg-backup-to-vanadium` — FAIL(preflight), nothing dumped\n"
        "  - `sync-vanadium` — `No route to host` on 5432\n"
        "  - `vanadium-ci-backup` — target unreachable\n\n"
        "Do not read the green `sonar` check as evidence the host is healthy.\n\n"
        "Diagnose over the one path that still works: a job on the runner itself. "
        "Dispatch `.github/workflows/vanadium-diagnostic.yml` from the default branch "
        "(Actions → Run workflow; `[self-hosted, vanadium]`, read-only) and read its "
        "verdict block — `ip -br a`, `ip r`, `ip neigh`, `nmcli con show --active`, "
        "`ip route get`, `uptime -s`, `journalctl -b -1`, `vcgencmd get_throttled` "
        "(docs/runbook-vanadium-ci-runner-recovery.md §1.1).\n"
        "Recovery: docs/runbook-vanadium-ci-runner-recovery.md §1-§3. A reboot is not "
        "obviously the fix — this host has already come back twice without its LAN address, "
        "and a Pi 5 has no out-of-band reset if the next boot does not return."
    )


def _lan_recovered_message(target: str, since: int, now: int) -> str:
    return (
        f"vanadium's LAN path is back: {target} answers TCP again after "
        f"{_fmt(now - since)} (it had been recorded down since {_iso(since)}).\n\n"
        "The nightly off-machine copy and the live SQL sync were failing while it was gone — "
        "rerun them, and check `pgsql/pg-backup-to-vanadium.log` and `pgsql/sync-vanadium.log` "
        "for the gap."
    )


def _probe_message(failures: int, error: str, repo: str, prev: Optional[Dict[str, Any]],
                   threshold: int) -> str:
    last_known = (prev or {}).get("status")
    known = (f"The runner's last known status is unchanged (`{last_known}`) — this is not a "
             f"runner-outage report, it is the watchdog reporting that it cannot see."
             if last_known else
             "No runner status has ever been confirmed by this watchdog.")
    return (
        f"The `vanadium` runner watchdog has failed to read the runners API "
        f"{failures} times in a row (alert threshold {threshold}). It is BLIND: no runner "
        f"status is being checked, so a real outage would go unreported.\n\n"
        f"Last error: `{error}`\n\n"
        f"Most likely cause when that error is HTTP 403 \"Resource not accessible by "
        f"personal access token\": the gh credential cannot read the runners list. "
        f"`GET /repos/{repo}/actions/runners` is an *administrative* endpoint — a "
        f"fine-grained PAT needs **Administration: Read-only** scoped to `{repo}` "
        f"(Actions: Read is not enough, and core scopes are not accepted).\n\n"
        f"Check from the same account:\n"
        f"  gh api repos/{repo}/actions/runners\n"
        f"  python3 bin/vanadium_runner_watchdog.py --check --dry-run\n\n"
        f"{known}\n\n"
        f"Required credential and recovery steps: "
        f"docs/runbook-vanadium-ci-runner-recovery.md §9."
    )


def _probe_recovered_message(failures: int, status: str) -> str:
    return (
        f"The `vanadium` runner watchdog can read the runners API again after {failures} "
        f"consecutive failed probes. Runner is currently `{status}`. Probe health restored; "
        f"normal transition alerting resumes."
    )


def fetch_runners(repo: str = REPO) -> Sequence[Dict[str, Any]]:
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runners"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    return json.loads(out).get("runners", [])


def notify(message: str, *, webhook_url: Optional[str] = None,
           alert_cmd: Optional[str] = None, title: str = DEFAULT_ALERT_TITLE,
           dry_run: bool = False) -> str:
    """Deliver one alert; returns the channel used (or 'dry-run'):

    ``title`` names the forum thread and is otherwise unused — the webhook and
    alert-command channels carry the message alone.
    """
    if dry_run:
        print(f"[dry-run] would alert via "
              f"{'webhook' if webhook_url else (alert_cmd or 'assembly')}:\n{message}")
        return "dry-run"
    if webhook_url:
        import urllib.request
        req = urllib.request.Request(
            webhook_url, data=json.dumps({"text": message}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        return "webhook"
    if alert_cmd:
        subprocess.run(alert_cmd, shell=True, input=message, text=True, check=True, timeout=60)
        return "alert-cmd"
    script = Path(__file__).resolve().parent / "post-change-log.sh"
    subprocess.run(
        [str(script), "--forum", "issues-and-open-questions",
         "--title", title,
         "--body", message],
        check=True, timeout=60,
    )
    return "assembly"


def _probe_failure(prev: Optional[Dict[str, Any]], exc: Exception, *, now: int,
                   state_path: str, repo: str, probe_alert_after: int,
                   webhook_url: Optional[str] = None, alert_cmd: Optional[str] = None,
                   dry_run: bool = False) -> Tuple[Decision, str]:
    """A blind probe: count it, and say so once the blindness is not transient.

    Never writes a runner status: an unreachable API must not look like an
    outage, and must not clear a real one either.
    """
    error = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()[:400]
    failures = int((prev or {}).get("probe_failures") or 0) + 1
    threshold = max(1, int(probe_alert_after))
    already_alerted = (prev or {}).get("last_alert") == ALERT_PROBE

    alert: Optional[str] = None
    message = ""
    if failures >= threshold and not already_alerted:
        alert = ALERT_PROBE
        message = _probe_message(failures, error, repo, prev, threshold)

    channel = ""
    if alert:
        channel = notify(message, webhook_url=webhook_url, alert_cmd=alert_cmd, dry_run=dry_run)

    if not dry_run:
        state = dict(prev or {})
        state.update({
            "probe_failures": failures,
            "last_probe_error": error,
            "checked_at": now,
            "last_alert": alert or state.get("last_alert"),
        })
        save_state(state_path, state)

    return Decision(PROBE_ERROR, alert, message, error=error, probe_failures=failures), channel


def check(*, repo: str = REPO, state_path: str = STATE_PATH, now: Optional[int] = None,
          fetch: Callable[[str], Sequence[Dict[str, Any]]] = fetch_runners,
          webhook_url: Optional[str] = None, alert_cmd: Optional[str] = None,
          dry_run: bool = False,
          lan_probe: Optional[Callable[[], str]] = None,
          lan_target: str = f"{LAN_HOST}:{LAN_PORT}",
          maintenance: Sequence[Tuple[int, int]] = (),
          probe_alert_after: int = PROBE_ALERT_AFTER) -> Tuple[Decision, str]:
    now = int(time.time()) if now is None else now
    prev = load_state(state_path)

    try:
        status = classify(fetch(repo))
    except Exception as exc:  # noqa: BLE001 — a probe failure must not look like an outage
        return _probe_failure(prev, exc, now=now, state_path=state_path, repo=repo,
                              probe_alert_after=probe_alert_after,
                              webhook_url=webhook_url, alert_cmd=alert_cmd, dry_run=dry_run)

    lan = lan_probe() if lan_probe is not None else None
    decision = decide(prev, status, now, lan=lan, lan_target=lan_target)

    # The blindness alert is cleared by the first probe that succeeds again,
    # whatever the runner turned out to be doing in the meantime.
    failures_before = int((prev or {}).get("probe_failures") or 0)
    if decision.alert is None and failures_before and \
            (prev or {}).get("last_alert") == ALERT_PROBE:
        decision = Decision(status, ALERT_PROBE_RECOVERED,
                            _probe_recovered_message(failures_before, status), lan=lan)

    window = active_window(maintenance, now)

    # A window that closed with the runner still down owes one alert.
    if decision.alert is None and window is None and status != ONLINE:
        owed = (prev or {}).get("pending_alert")
        if owed:
            decision = Decision(
                status, owed,
                _deferred_note((prev or {}).get("pending_window"),
                               (prev or {}).get("since"), now)
                + _down_message(status), lan=lan)

    held: Optional[str] = None
    # Blindness is never held: a reboot does not make the GitHub API unreadable,
    # so a probe that cannot see during a window is still the watchdog's own
    # failure to report — holding it would hide exactly that.
    if decision.alert and window and decision.alert not in (ALERT_PROBE, ALERT_PROBE_RECOVERED):
        held = decision.alert
        decision = Decision(
            status, None,
            f"[maintenance {window}] holding the {held} alert until the window closes: "
            f"{decision.message}", lan=lan)
    elif window and status != ONLINE:
        # A later in-window sample sees no *new* transition, so it says nothing
        # about the alert it is still holding. Say so, and keep the debt.
        still = (prev or {}).get("pending_alert")
        if still:
            decision = Decision(
                status, None,
                f"[maintenance {window}] still holding the {still} alert (runner is "
                f"{status}); it is sent when the window closes if the runner is still down",
                lan=lan)

    channel = ""
    if decision.alert:
        channel = notify(decision.message, webhook_url=webhook_url,
                         alert_cmd=alert_cmd,
                         title=_alert_title(decision.alert), dry_run=dry_run)
    if not dry_run:
        since = int((prev or {}).get("since") or now)
        if not prev or prev.get("status") != status:
            since = now
        # A skipped LAN probe must not erase the last recorded verdict, or the
        # next probing run would treat a still-dead LAN as a new transition.
        recorded_lan = lan if lan is not None else (prev or {}).get("lan")
        lan_since = int((prev or {}).get("lan_since") or now)
        if (prev or {}).get("lan") != recorded_lan:
            lan_since = now
        state: Dict[str, Any] = {"status": status, "since": since,
                                 "checked_at": now,
                                 "lan": recorded_lan, "lan_since": lan_since,
                                 "last_alert": decision.alert or (prev or {}).get("last_alert"),
                                 "probe_failures": 0}
        if status != ONLINE:
            # An alert raised inside the window is owed until the window closes.
            # Every later in-window tick must carry that debt forward: clearing
            # it here is what made the deferred send never fire (2026-11-26).
            owed = held or ((prev or {}).get("pending_alert") if window else None)
            if owed:
                state["pending_alert"] = owed
                state["pending_window"] = window or (prev or {}).get("pending_window")
        save_state(state_path, state)
    return decision, channel


def main(argv: Optional[Sequence[str]] = None,
         fetch: Callable[[str], Sequence[Dict[str, Any]]] = fetch_runners,
         lan_probe: Optional[Callable[[], str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Alert when the vanadium CI runner or its LAN path fails.")
    ap.add_argument("--check", action="store_true", help="probe once and alert on a transition (default)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--webhook-url", default=os.environ.get("NEXUS_ALERT_WEBHOOK"))
    ap.add_argument("--alert-cmd", default=os.environ.get("NEXUS_ALERT_CMD"))
    ap.add_argument("--lan-host", default=LAN_HOST,
                    help="host whose LAN reachability matters (default %(default)s)")
    ap.add_argument("--lan-port", type=int, default=int(LAN_PORT), help="TCP port to probe")
    ap.add_argument("--lan-timeout", type=float, default=float(LAN_TIMEOUT), help="seconds per connect")
    ap.add_argument("--no-lan-probe", action="store_true",
                    help="probe the runners API only (no LAN check; LAN state is left as recorded)")
    ap.add_argument("--maintenance-window", default=MAINTENANCE_WINDOW,
                    help="quiet window(s) 'HH:MM-HH:MM' in this host's local time, "
                         "comma-separated; alerts raised inside one are held and sent when it "
                         "closes if the runner is still down (default: none; env "
                         "NEXUS_WATCHDOG_MAINTENANCE_WINDOW)")
    ap.add_argument("--probe-alert-after", type=int, default=PROBE_ALERT_AFTER,
                    help="consecutive probe failures before the watchdog alerts that it is blind "
                         "(default %(default)s; env NEXUS_WATCHDOG_PROBE_ALERT_AFTER)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing, alert nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = f"{args.lan_host}:{args.lan_port}"
    if args.no_lan_probe:
        probe = None
    elif lan_probe is not None:
        probe = lan_probe
    else:
        probe = lambda: probe_lan(args.lan_host, args.lan_port, args.lan_timeout)  # noqa: E731

    try:
        maintenance = parse_window_spec(args.maintenance_window)
    except ValueError as exc:
        ap.error(str(exc))

    decision, channel = check(
        repo=args.repo, state_path=args.state, fetch=fetch,
        webhook_url=args.webhook_url, alert_cmd=args.alert_cmd, dry_run=args.dry_run,
        lan_probe=probe, lan_target=target, maintenance=maintenance,
        probe_alert_after=args.probe_alert_after)

    if decision.error:
        if args.json:
            print(json.dumps({"status": decision.status, "lan": decision.lan,
                              "alert": decision.alert, "channel": channel,
                              "message": decision.message,
                              "probe_failures": decision.probe_failures,
                              "error": decision.error}, indent=2))
        print(f"ERROR: cannot read the runners API: {decision.error}", file=sys.stderr)
        print(f"vanadium runner: probe-error — {decision.probe_failures} consecutive failure(s)"
              + (f", alert={decision.alert} via {channel}" if decision.alert else
                 f", alerting at {max(1, args.probe_alert_after)}"), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"status": decision.status, "lan": decision.lan,
                          "alert": decision.alert, "channel": channel,
                          "message": decision.message,
                          "probe_failures": decision.probe_failures}, indent=2))
    else:
        lan_note = f" lan={decision.lan}" if decision.lan else ""
        print(f"vanadium runner: {decision.status}{lan_note}"
              + (f" — alert={decision.alert} via {channel}" if decision.alert else ""))
        if decision.message:
            print(decision.message)
    return 0 if decision.ok else 1


if __name__ == "__main__":
    sys.exit(main())
