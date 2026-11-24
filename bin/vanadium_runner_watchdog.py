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
  vanadium_runner_watchdog.py --check              # probe + alert on transition
  vanadium_runner_watchdog.py --check --dry-run    # print the decision, write nothing
  vanadium_runner_watchdog.py --check --json       # machine-readable summary

Exit codes: 0 runner online; 1 runner offline or missing from the API;
            2 environment error (probe failed). Exit 2 never writes a runner
            status and never claims an outage — but it does accumulate the
            probe-failure count and, at the threshold, delivers the
            watchdog-health alert.
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
PROBE_ALERT_AFTER = int(os.environ.get("NEXUS_WATCHDOG_PROBE_ALERT_AFTER", "3"))

ONLINE = "online"
OFFLINE = "offline"
MISSING = "missing"
PROBE_ERROR = "probe-error"

ALERT_DOWN = "down"
ALERT_RECOVERED = "recovered"
ALERT_PROBE = "probe-error"
ALERT_PROBE_RECOVERED = "probe-recovered"


@dataclass(frozen=True)
class Decision:
    status: str
    alert: Optional[str]          # None | ALERT_DOWN | ALERT_RECOVERED | ALERT_PROBE | ...
    message: str
    error: Optional[str] = None   # set only when the probe itself failed
    probe_failures: int = 0       # consecutive failed probes observed so far


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
         "--title", "ALERT: vanadium runner watchdog cannot reach the GitHub API",
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
          probe_alert_after: int = PROBE_ALERT_AFTER) -> Tuple[Decision, str]:
    now = int(time.time()) if now is None else now
    prev = load_state(state_path)

    try:
        status = classify(fetch(repo))
    except Exception as exc:  # noqa: BLE001 — a probe failure must not look like an outage
        return _probe_failure(prev, exc, now=now, state_path=state_path, repo=repo,
                              probe_alert_after=probe_alert_after,
                              webhook_url=webhook_url, alert_cmd=alert_cmd, dry_run=dry_run)

    decision = decide(prev, status, now)
    failures_before = int((prev or {}).get("probe_failures") or 0)
    if decision.alert is None and failures_before and \
            (prev or {}).get("last_alert") == ALERT_PROBE:
        decision = Decision(status, ALERT_PROBE_RECOVERED,
                            _probe_recovered_message(failures_before, status))

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
                                "last_alert": decision.alert or (prev or {}).get("last_alert"),
                                "probe_failures": 0})
    return decision, channel


def main(argv: Optional[Sequence[str]] = None,
         fetch: Callable[[str], Sequence[Dict[str, Any]]] = fetch_runners) -> int:
    ap = argparse.ArgumentParser(description="Alert when the vanadium CI runner goes offline.")
    ap.add_argument("--check", action="store_true", help="probe once and alert on a transition (default)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--webhook-url", default=os.environ.get("NEXUS_ALERT_WEBHOOK"))
    ap.add_argument("--alert-cmd", default=os.environ.get("NEXUS_ALERT_CMD"))
    ap.add_argument("--probe-alert-after", type=int, default=PROBE_ALERT_AFTER,
                    help="consecutive probe failures before the watchdog alerts that it is blind "
                         "(default %(default)s; env NEXUS_WATCHDOG_PROBE_ALERT_AFTER)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing, alert nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    decision, channel = check(
        repo=args.repo, state_path=args.state, fetch=fetch,
        webhook_url=args.webhook_url, alert_cmd=args.alert_cmd, dry_run=args.dry_run,
        probe_alert_after=args.probe_alert_after)

    if decision.error:
        if args.json:
            print(json.dumps({"status": decision.status, "alert": decision.alert,
                              "channel": channel, "message": decision.message,
                              "probe_failures": decision.probe_failures,
                              "error": decision.error}, indent=2))
        print(f"ERROR: cannot read the runners API: {decision.error}", file=sys.stderr)
        print(f"vanadium runner: probe-error — {decision.probe_failures} consecutive failure(s)"
              + (f", alert={decision.alert} via {channel}" if decision.alert else
                 f", alerting at {max(1, args.probe_alert_after)}"), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"status": decision.status, "alert": decision.alert,
                          "channel": channel, "message": decision.message,
                          "probe_failures": decision.probe_failures}, indent=2))
    else:
        print(f"vanadium runner: {decision.status}"
              + (f" — alert={decision.alert} via {channel}" if decision.alert else ""))
        if decision.message:
            print(decision.message)
    return 0 if decision.status == ONLINE else 1


if __name__ == "__main__":
    sys.exit(main())
