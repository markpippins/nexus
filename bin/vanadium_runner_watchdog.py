#!/usr/bin/env python3
"""vanadium_runner_watchdog.py — alert when the vanadium CI runner goes offline.

The 2026-11-24 outage was discovered from a queued `sonar` check, long after the
runner had stopped heartbeating. This is the smallest thing that catches it in
minutes: a one-shot probe meant for a systemd timer, which compares the runner's
current status with the last status it recorded and alerts only on a
*transition* — so a timer firing every two minutes does not page anyone twice
for the same outage.

What it watches: the `vanadium` entry in
`GET /repos/{owner}/{repo}/actions/runners`. Online -> nothing to say.
Anything else -> alert, with how long the runner had been online first.

Alert channels (first one configured wins):
  --webhook-url URL / env NEXUS_ALERT_WEBHOOK   POST {"text": "..."}
  --alert-cmd CMD                               shell command, message on stdin
  default                                       Assembly issues-and-open-questions
                                                via bin/post-change-log.sh

Usage:
  vanadium_runner_watchdog.py --check              # probe + alert on transition
  vanadium_runner_watchdog.py --check --dry-run    # print the decision, write nothing
  vanadium_runner_watchdog.py --check --json       # machine-readable summary

Exit codes: 0 runner online; 1 runner offline or missing from the API;
            2 environment error (probe failed — state and alerts untouched).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

REPO = os.environ.get("NEXUS_REPO", "markpippins/nexus")
RUNNER = "vanadium"
STATE_PATH = os.environ.get("NEXUS_WATCHDOG_STATE", "/var/tmp/vanadium-runner-watchdog.json")

ONLINE = "online"
OFFLINE = "offline"
MISSING = "missing"

ALERT_DOWN = "down"
ALERT_RECOVERED = "recovered"


@dataclass(frozen=True)
class Decision:
    status: str
    alert: Optional[str]          # None | ALERT_DOWN | ALERT_RECOVERED
    message: str


def classify(runners: Sequence[Dict[str, Any]], name: str = RUNNER) -> str:
    """Online only when the runner row exists and says so; otherwise alert-worthy."""
    for r in runners or ():
        if str(r.get("name")) == name:
            return ONLINE if str(r.get("status")).lower() == ONLINE else OFFLINE
    return MISSING


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


def decide(prev: Optional[Dict[str, Any]], status: str, now: int) -> Decision:
    """Alert only on a transition, never on a repeat observation."""
    prev_status = (prev or {}).get("status")
    since = int((prev or {}).get("since") or now)

    if prev_status is None:
        # First observation: say something only if it is already bad.
        if status == ONLINE:
            return Decision(status, None, "")
        return Decision(status, ALERT_DOWN, _down_message(status))

    if prev_status == status:
        return Decision(status, None, "")

    if status == ONLINE:
        return Decision(status, ALERT_RECOVERED,
                        f"vanadium CI runner is back online after {_fmt(now - since)} "
                        f"(was {prev_status}). Queued sonar jobs should start on their own; "
                        f"if they expired, rerun them with `gh run rerun <run-id> --failed`.")
    return Decision(status, ALERT_DOWN, _down_message(status, since, now))


def _down_message(status: str, since: Optional[int] = None, now: Optional[int] = None) -> str:
    was = ""
    if since:
        dur = _fmt((now or since) - since)
        was = (f" It had been online for {dur} "
               f"(since {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(since))}).")
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
          dry_run: bool = False) -> Tuple[Decision, str]:
    now = int(time.time()) if now is None else now
    status = classify(fetch(repo))
    prev = load_state(state_path)
    decision = decide(prev, status, now)

    channel = ""
    if decision.alert:
        channel = notify(decision.message, webhook_url=webhook_url,
                         alert_cmd=alert_cmd, dry_run=dry_run)
    if not dry_run:
        since = int((prev or {}).get("since") or now)
        if not prev or prev.get("status") != status:
            since = now
        save_state(state_path, {"status": status, "since": since,
                                "checked_at": now,
                                "last_alert": decision.alert or (prev or {}).get("last_alert")})
    return decision, channel


def main(argv: Optional[Sequence[str]] = None,
         fetch: Callable[[str], Sequence[Dict[str, Any]]] = fetch_runners) -> int:
    ap = argparse.ArgumentParser(description="Alert when the vanadium CI runner goes offline.")
    ap.add_argument("--check", action="store_true", help="probe once and alert on a transition (default)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--webhook-url", default=os.environ.get("NEXUS_ALERT_WEBHOOK"))
    ap.add_argument("--alert-cmd", default=os.environ.get("NEXUS_ALERT_CMD"))
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing, alert nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        decision, channel = check(
            repo=args.repo, state_path=args.state, fetch=fetch,
            webhook_url=args.webhook_url, alert_cmd=args.alert_cmd, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — a probe failure must not look like an outage
        print(f"ERROR: cannot read the runners API: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"status": decision.status, "alert": decision.alert,
                          "channel": channel, "message": decision.message}, indent=2))
    else:
        print(f"vanadium runner: {decision.status}"
              + (f" — alert={decision.alert} via {channel}" if decision.alert else ""))
        if decision.message:
            print(decision.message)
    return 0 if decision.status == ONLINE else 1


if __name__ == "__main__":
    sys.exit(main())
