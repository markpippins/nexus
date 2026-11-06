#!/usr/bin/env python3
"""hourly_maintenance.py — Plan 0001: Hourly maintenance cycle.

Run via systemd timer or cron.  Discovers issues via terrain (service
status), checks for zombies, and appends a heartbeat summary to the Assembly
syslog forum.  Replaces the earlier imperative script targeted by plan 0001.

The summary is APPENDED to one persistent heartbeat thread, never posted as
a new thread: a new thread per hour buried the audit trail (2,251 syslog
threads by 2026-11-06) and split one continuous signal across thousands of
titles (incident 8adcac4d).

Exit status is 1 only for ACTIONABLE issues (service problems). Zombies are
reported in the summary but do not fail the unit: this script has no kill
path, so a zombie whose parent stays alive is unreapable by construction, and
letting it fail the unit marked the cycle failed every hour forever.

Usage: hourly_maintenance.py [--once]
"""

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ASSEMBLY = os.environ.get("ASSEMBLY_SRV", "http://localhost:3107")
NEBULA   = os.environ.get("NEBULA_SRV",   "http://localhost:3101")
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICES_SCRIPT = os.path.join(_SCRIPT_DIR, "start-nexus-services.sh")

# Units retired from the fleet (systemd files may linger). The status
# script still lists them, so sweep results are filtered here.
# angular-assembly: RETIRED 2026-08-24, replaced by assembly-ui :4214.
RETIRED_UNITS = ("angular-assembly.service",)

# The maintenance cycle is sysadmin's work. Posting it as the engineer user
# filed sysadmin output in the engineer inbox and misattributed authorship
# (incident 8adcac4d): the identity here is the owner of the work, not whoever
# last edited this file.
_SYSADMIN_UUID = "ea717207-a621-42fa-9e64-fcc98b046f45"

# The persistent hourly-maintenance heartbeat thread in the Assembly syslog
# forum. Override with ASSEMBLY_HEARTBEAT_THREAD if that thread ever moves.
_HEARTBEAT_THREAD = os.environ.get(
    "ASSEMBLY_HEARTBEAT_THREAD",
    "980ea342-fe00-4df5-9848-6134d72abd52",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(url: str, body: dict, timeout: int = 15) -> tuple[int, dict | None]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except Exception as e:
        return 0, {"error": str(e)}


def service_status() -> list[str]:
    """Run the canonical service-status script and return issues found."""
    try:
        result = subprocess.run(
            ["bash", SERVICES_SCRIPT, "status"],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
    except Exception as e:
        return [f"service-status script failed: {e}"]

    issues: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        # Skip units retired from the fleet that still have systemd files —
        # they report DOWN every cycle otherwise (incident b382b591,
        # sysadmin proposal approved 2026-08-25).
        if any(retired in stripped for retired in RETIRED_UNITS):
            continue
        # Look for non-OK health statuses
        if "inactive" in stripped.lower() or "failed" in stripped.lower():
            issues.append(stripped)
        if "down" in stripped.lower() and "OK" not in stripped:
            issues.append(stripped)
    return issues


def zombie_processes() -> list[str]:
    """Return any zombie (defunct) processes by name."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return [f"zombie check failed: {e}"]

    zombies: list[str] = []
    for line in result.stdout.splitlines():
        # Kernel threads are pid 2
        if "Z+" in line or "<defunct>" in line:
            # Grab the last token (command name)
            parts = line.split()
            if len(parts) >= 11:
                zombies.append(f"zombie: PID {parts[1]} {parts[10][:60]}")
    return zombies


def disk_space() -> str:
    """Return a one-line disk summary."""
    try:
        result = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            return lines[1]
    except Exception:
        pass
    return "disk: unknown"


def post_syslog(summary: str) -> str | None:
    """Append the summary to the persistent heartbeat thread. Thread id or None.

    A COMMENT is used rather than a thread POST so a cycle cannot fork the
    heartbeat. If the thread id is wrong the post fails loudly (main() prints a
    warning) instead of silently creating yet another thread.
    """
    st, _ = _post(
        f"{ASSEMBLY}/api/forums/threads/{_HEARTBEAT_THREAD}/comments",
        {
            "body": summary,
            "postedById": _SYSADMIN_UUID,
            "role": "sysadmin",
            "model": "hourly-maintenance/0001",
        },
    )
    return _HEARTBEAT_THREAD if st in (200, 201) else None


def main() -> int:
    issues = service_status()
    zombies = zombie_processes()
    disk = disk_space()

    issue_count = len(issues) + len(zombies)

    lines = [
        f"# Hourly maintenance — {_now()}",
        "",
        f"**Services**: {len(issues)} issue(s) found",
        f"**Zombies**: {len(zombies)} process(es)",
        f"**Disk**: {disk}",
    ]

    if issues:
        lines.append("\n## Service issues")
        for i in issues:
            lines.append(f"- {i}")

    if zombies:
        lines.append("\n## Zombie processes")
        for z in zombies:
            lines.append(f"- {z}")

    if not issues and not zombies:
        lines.append("\n✓ All clear — no issues detected.")

    summary = "\n".join(lines)
    print(summary)

    if issue_count > 0:
        print(f"\n[{_now()}] {issue_count} issue(s) — posting to syslog")
    else:
        print(f"\n[{_now()}] All clear")

    tid = post_syslog(summary)
    if tid:
        print(f"Appended to heartbeat thread: {tid[:12]}...")
    else:
        print(f"WARNING: heartbeat not posted to {_HEARTBEAT_THREAD[:12]}...", file=sys.stderr)

    # Zombies are evidence, not a failure of THIS cycle: nothing here can reap
    # them (there is no kill path), so an orphaned child would hold the unit in
    # `failed` forever. Only service issues are actionable by this script.
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())