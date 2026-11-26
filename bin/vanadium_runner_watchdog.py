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

Exit codes: 0 runner online and LAN reachable; 1 either fault (or the runner is
            missing from the API); 2 environment error (the runners API could not
            be read — state and alerts untouched). A LAN probe that cannot
            connect is a VERDICT, not an environment error: the refusal to
            answer is the thing being watched.
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

LAN_UP = "up"
LAN_DOWN = "down"

ALERT_DOWN = "down"
ALERT_RECOVERED = "recovered"
ALERT_LAN_DOWN = "lan-down"
ALERT_LAN_RECOVERED = "lan-recovered"


@dataclass(frozen=True)
class Decision:
    status: str
    alert: Optional[str]          # None | ALERT_DOWN | ALERT_RECOVERED | ALERT_LAN_*
    message: str
    lan: Optional[str] = None     # LAN_UP | LAN_DOWN | None (not probed)

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
        "Diagnose over the one path that still works: a job on the runner itself. No "
        "diagnostic workflow is in the repo yet, so dispatch a throwaway "
        "`workflow_dispatch` job on `[self-hosted, vanadium]` that prints `ip -br a`, "
        "`ip r`, `nmcli con show --active`, `uptime -s`, `journalctl -b -1`, "
        "`vcgencmd get_throttled` (docs/runbook-vanadium-ci-runner-recovery.md §1.1).\n"
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


def fetch_runners(repo: str = REPO) -> Sequence[Dict[str, Any]]:
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runners"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    return json.loads(out).get("runners", [])


def notify(message: str, *, webhook_url: Optional[str] = None,
           alert_cmd: Optional[str] = None, dry_run: bool = False) -> str:
    """Deliver one alert; returns the channel used (or 'dry-run')."""
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
         "--title", "ALERT: vanadium CI runner offline",
         "--body", message],
        check=True, timeout=60,
    )
    return "assembly"


def check(*, repo: str = REPO, state_path: str = STATE_PATH, now: Optional[int] = None,
          fetch: Callable[[str], Sequence[Dict[str, Any]]] = fetch_runners,
          webhook_url: Optional[str] = None, alert_cmd: Optional[str] = None,
          dry_run: bool = False,
          lan_probe: Optional[Callable[[], str]] = None,
          lan_target: str = f"{LAN_HOST}:{LAN_PORT}") -> Tuple[Decision, str]:
    now = int(time.time()) if now is None else now
    status = classify(fetch(repo))
    lan = lan_probe() if lan_probe is not None else None
    prev = load_state(state_path)
    decision = decide(prev, status, now, lan=lan, lan_target=lan_target)

    channel = ""
    if decision.alert:
        channel = notify(decision.message, webhook_url=webhook_url,
                         alert_cmd=alert_cmd, dry_run=dry_run)
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
        save_state(state_path, {"status": status, "since": since,
                                "checked_at": now,
                                "lan": recorded_lan, "lan_since": lan_since,
                                "last_alert": decision.alert or (prev or {}).get("last_alert")})
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
        decision, channel = check(
            repo=args.repo, state_path=args.state, fetch=fetch,
            webhook_url=args.webhook_url, alert_cmd=args.alert_cmd, dry_run=args.dry_run,
            lan_probe=probe, lan_target=target)
    except Exception as exc:  # noqa: BLE001 — a probe failure must not look like an outage
        print(f"ERROR: cannot read the runners API: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"status": decision.status, "lan": decision.lan,
                          "alert": decision.alert, "channel": channel,
                          "message": decision.message}, indent=2))
    else:
        lan_note = f" lan={decision.lan}" if decision.lan else ""
        print(f"vanadium runner: {decision.status}{lan_note}"
              + (f" — alert={decision.alert} via {channel}" if decision.alert else ""))
        if decision.message:
            print(decision.message)
    return 0 if decision.ok else 1


if __name__ == "__main__":
    sys.exit(main())
