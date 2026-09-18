#!/usr/bin/env python3
"""helium_core_probe.py — recurring health probe for the helium nexus-core stack.

Why this exists (2026-09-18, Engineer II per operator request): helium runs the
nexus-core data-infra tier (nexus-core Spring Boot :8092, helium-mongo,
helium-redis, helium-nats containers, ollama :11434 for embeddings) and nothing
watched it from titanium. The 2026-09-17 rebuild (host-key rotation, status
report record 0ebeed36) showed nobody would have noticed an outage.

Checks (all read-only):
  1. Actuator   : GET http://<host>:8092/actuator/health — overall status plus
                  db and diskSpace components; disk free converted to GB and
                  compared against --min-disk-free-gb (warn threshold).
  2. Containers : ONE ssh BatchMode round-trip, `docker ps --format ...` on the
                  host; every expected container must be `running`.
  3. Ollama     : GET /api/version + /api/tags — service up and the embed model
                  list non-empty (warn when empty; the embed pipeline defaults
                  to helium ollama).

Alerting: transitions only. The probe keeps per-check status in a state file
and posts ONE agent record per run in which something changed (strongest tag
wins: type:incident > type:recovery > type:warning), routed to:sysadmin —
no alert storms on the 5-minute cadence. The first run records a baseline:
no alert when healthy, immediate alert when already failing.

Exit codes: 0 = ok/warn, 1 = fail (journal-visible), 2 = probe contract broken
(bad arguments or unexpected crash).

Run:
  python3 -m pytest bin/tests/test_helium_core_probe.py -v
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_HOST = "helium"
DEFAULT_TIMEOUT = 5.0
DEFAULT_MIN_DISK_FREE_GB = 2.0
DEFAULT_CONTAINERS = ["nexus-core", "helium-mongo", "helium-redis", "helium-nats"]
DEFAULT_OLLAMA_PORT = 11434
DEFAULT_ACTUATOR_PORT = 8092
DEFAULT_STATE_FILE = Path.home() / ".cache" / "helium-core-probe" / "state.json"
NEBULA_RECORDS_URL = "http://localhost:3101/api/agent-records"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

# ok -> fail (incident) and fail -> ok (recovery) are the alerting edges.
TRANSITIONS = {
    (STATUS_OK, STATUS_FAIL): "type:incident",
    (STATUS_WARN, STATUS_FAIL): "type:incident",
    (STATUS_FAIL, STATUS_OK): "type:recovery",
    (STATUS_FAIL, STATUS_WARN): "type:recovery",
    (STATUS_OK, STATUS_WARN): "type:warning",
    (STATUS_WARN, STATUS_OK): None,  # healing toward ok — no alert needed
}

TAG_SEVERITY = ["type:warning", "type:recovery", "type:incident"]


# --------------------------------------------------------------------------
# fetchers (thin, injectable)
# --------------------------------------------------------------------------

def fetch_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_docker_states(ssh_host: str, timeout: float) -> dict:
    """One SSH round-trip returning {container_name: state} for running containers."""
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        ssh_host,
        "docker ps --format '{{.Names}}\\t{{.State}}'",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    states = {}
    for line in out.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2:
            states[parts[0]] = parts[1]
    return states


# --------------------------------------------------------------------------
# checks (pure logic; fetchers injected so tests pass stubs)
# --------------------------------------------------------------------------

def check_actuator(fetch, url, timeout, min_disk_free_gb):
    try:
        body = fetch(url, timeout)
    except Exception as exc:
        return {"name": "actuator", "status": STATUS_FAIL,
                "detail": f"unreachable: {exc}"}
    overall = str(body.get("status", "")).upper()
    components = body.get("components", {}) or {}
    if overall != "UP":
        return {"name": "actuator", "status": STATUS_FAIL,
                "detail": f"overall={overall or 'missing'}"}
    problems = []
    for name in ("db", "diskSpace"):
        comp = components.get(name) or {}
        if str(comp.get("status", "")).upper() != "UP":
            problems.append(f"{name}={comp.get('status', 'missing')}")
    disk_gb = None
    disk_details = (components.get("diskSpace") or {}).get("details") or {}
    free_bytes = disk_details.get("free")
    if isinstance(free_bytes, (int, float)) and free_bytes >= 0:
        disk_gb = free_bytes / 1024 ** 3
        if disk_gb < min_disk_free_gb:
            problems.append(f"disk_free={disk_gb:.2f}GB<{min_disk_free_gb}GB")
    if problems:
        return {"name": "actuator", "status": STATUS_WARN,
                "detail": "UP with: " + ", ".join(problems)}
    detail = "UP"
    if disk_gb is not None:
        detail += f" (disk_free={disk_gb:.1f}GB)"
    return {"name": "actuator", "status": STATUS_OK, "detail": detail}


def check_containers(fetch_states, ssh_host, timeout, expected):
    try:
        states = fetch_states(ssh_host, timeout)
    except Exception as exc:
        return [{"name": f"container:{c}", "status": STATUS_FAIL,
                 "detail": f"ssh/docker probe failed: {exc}"} for c in expected]
    results = []
    for name in expected:
        state = states.get(name)
        if state == "running":
            results.append({"name": f"container:{name}", "status": STATUS_OK,
                            "detail": "running"})
        elif state is None:
            results.append({"name": f"container:{name}", "status": STATUS_FAIL,
                            "detail": "not listed in docker ps"})
        else:
            results.append({"name": f"container:{name}", "status": STATUS_FAIL,
                            "detail": f"state={state}"})
    return results


def check_ollama(fetch, base_url, timeout):
    try:
        version = fetch(f"{base_url}/api/version", timeout)
    except Exception as exc:
        return {"name": "ollama", "status": STATUS_FAIL,
                "detail": f"unreachable: {exc}"}
    ver = version.get("version", "?")
    try:
        tags = fetch(f"{base_url}/api/tags", timeout)
        models = [m.get("name", "?") for m in (tags.get("models") or [])]
    except Exception:
        models = []
    if not models:
        return {"name": "ollama", "status": STATUS_WARN,
                "detail": f"version={ver} but no models loaded"}
    return {"name": "ollama", "status": STATUS_OK,
            "detail": f"version={ver} models={','.join(models)}"}


def aggregate(results):
    statuses = [r["status"] for r in results]
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_WARN in statuses:
        return STATUS_WARN
    return STATUS_OK


# --------------------------------------------------------------------------
# state + transitions
# --------------------------------------------------------------------------

def load_state(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def transitions_for(previous, current):
    """Yield (name, from, to, alert_tag) for changed checks with an alert edge."""
    prev = {c["name"]: c["status"] for c in (previous or {}).get("checks", [])}
    for check in current:
        old = prev.get(check["name"])
        if old is None or old == check["status"]:
            continue
        alert = TRANSITIONS.get((old, check["status"]))
        if alert is not None:
            yield check["name"], old, check["status"], alert


def strongest_tag(tags):
    best = None
    for tag in tags:
        if best is None or TAG_SEVERITY.index(tag) > TAG_SEVERITY.index(best):
            best = tag
    return best


# --------------------------------------------------------------------------
# alerting
# --------------------------------------------------------------------------

def render_alert(changed, overall, detail_by_check):
    lines = [f"helium nexus-core probe: {overall.upper()}"]
    for name, old, new, tag in changed:
        lines.append(
            f"- {name}: {old} -> {new} ({tag}) — {detail_by_check.get(name, '')}")
    return "\n".join(lines)


def post_alert(body_text, alert_tag, changed_names, overall, dry_run):
    if dry_run:
        print(f"[dry-run] would post {alert_tag} alert:\n{body_text}",
              file=sys.stderr)  # stderr: stdout carries the JSON result contract
        return True
    record = {
        "recordType": "report",
        "role": "engineer",
        "title": f"[{alert_tag}] helium nexus-core probe: {overall} — "
                 f"{', '.join(changed_names)}",
        "content": body_text,
        "tags": ["to:sysadmin", alert_tag, "helium", "nexus-core", "probe"],
        "level": 1,
        "visibilityScope": "all",
    }
    try:
        req = urllib.request.Request(
            NEBULA_RECORDS_URL, data=json.dumps(record).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as exc:
        print(f"warning: alert post failed ({exc}); alert lost", file=sys.stderr)
        return False


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="helium nexus-core health probe")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--min-disk-free-gb", type=float, default=DEFAULT_MIN_DISK_FREE_GB)
    p.add_argument("--containers", default=",".join(DEFAULT_CONTAINERS))
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--dry-run", action="store_true",
                   help="print alerts instead of posting records")
    p.add_argument("--no-alert", action="store_true",
                   help="skip alert posting AND state persistence")
    return p


def run_probe(args, fetch=None, fetch_states=None, poster=None):
    fetch = fetch or fetch_json
    fetch_states = fetch_states or fetch_docker_states
    poster = poster or post_alert

    actuator_url = f"http://{args.host}:{DEFAULT_ACTUATOR_PORT}/actuator/health"
    ollama_url = f"http://{args.host}:{DEFAULT_OLLAMA_PORT}"

    results = [check_actuator(fetch, actuator_url, args.timeout, args.min_disk_free_gb)]
    results += check_containers(fetch_states, args.host, args.timeout,
                                [c.strip() for c in args.containers.split(",") if c.strip()])
    results.append(check_ollama(fetch, ollama_url, args.timeout))

    overall = aggregate(results)
    previous = load_state(args.state_file)
    changed = list(transitions_for(previous, results))
    if previous is None and overall == STATUS_FAIL:
        # First run against an already-broken stack must alert, or a dead
        # target would sit silent until its first state change.
        changed = [(r["name"], "baseline", STATUS_FAIL, "type:incident")
                   for r in results if r["status"] == STATUS_FAIL]

    alerts_posted = None
    if changed and not args.no_alert:
        tags = [t for _n, _o, _s, t in changed]
        tag = strongest_tag(tags)
        detail_by_check = {c["name"]: c["detail"] for c in results}
        body = render_alert(changed, overall, detail_by_check)
        names = [n for n, _o, _s, _t in changed]
        alerts_posted = poster(body, tag, names, overall, args.dry_run)

    if not args.no_alert:
        save_state(args.state_file, {"checks": results, "overall": overall})

    print(json.dumps({
        "overall": overall,
        "checks": results,
        "transitions": [{"check": n, "from": o, "to": s, "tag": t}
                        for n, o, s, t in changed],
        "alert_tag": (strongest_tag([t for _n, _o, _s, t in changed])
                      if changed else None),
        "alerts_posted": alerts_posted,
        "first_run": previous is None,
    }, indent=2))

    return EXIT_OK if overall in (STATUS_OK, STATUS_WARN) else EXIT_FAIL


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run_probe(args)
    except SystemExit:
        raise
    except Exception as exc:  # contract-level failure
        print(f"probe contract failure: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
