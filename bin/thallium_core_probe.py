#!/usr/bin/env python3
"""thallium_core_probe.py — recurring health probe for the thallium nexus-core stack.

Why this exists (2026-09-22, Engineer II per operator request): thallium runs
the JVM nexus-core (:8092, rebuilt from main per PR #454) plus the atomic-*
infra containers (nats/redis/mongodb) and the JetStream WRITE_QUEUE stream the
write-queue arc depends on. Nothing watched it from titanium. Derived from the
helium probe (same transition-alert architecture, proven over 51/53 real
transitions during helium's v6 flap), extended with two thallium-specific
check families:

Checks (all read-only):
  1. Actuator    : GET http://<host>:8092/actuator/health — overall status plus
                   db and diskSpace components; disk free compared against
                   --min-disk-free-gb (warn threshold).
  2. Routes      : GET each verified nexus-core route family. These pin the
                   surface that the 2026-09-22 rebuild restored — and the
                   /search/api NAMESPACE QUIRK: in the monolith the
                   moleculer-search surface is deliberately mounted under
                   /search/api (collision avoidance with the broker aggregate's
                   /api/health), NOT /api/search/health. A probe assuming the
                   /api/... convention would false-alarm on a healthy service.
  3. Containers  : ONE ssh BatchMode round-trip (-4, per the helium v6 flap
                   lesson), `docker ps --format ...`; every expected container
                   must be `running`.
  4. JetStream   : nats-py against <host>:4222 — the WRITE_QUEUE stream must
                   exist (its absence is the silent-intent-drop condition: the
                   producer falls back to core NATS and intents evaporate) with
                   file storage, and the write_queue_reconciler durable
                   consumer must exist without a growing backlog.

Alerting: transitions only. Per-check status lives in a state file; ONE agent
record per run in which something changed (strongest tag wins: type:incident >
type:recovery > type:warning), routed to:sysadmin via nebula :3101. First run
records a baseline: no alert when healthy, immediate alert when already
failing.

Exit codes: 0 = ok/warn, 1 = fail (journal-visible), 2 = probe contract broken.

Tests: mirror of the helium probe suite — pure logic, fetchers injectable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HOST = "thallium"
DEFAULT_TIMEOUT = 5.0
# The ssh/docker leg gets its own budget: it is a two-host round-trip.
DEFAULT_SSH_TIMEOUT = 10.0
DEFAULT_MIN_DISK_FREE_GB = 2.0
DEFAULT_CONTAINERS = ["nexus-core", "atomic-nats", "atomic-redis-dev", "atomic-mongodb"]
DEFAULT_ACTUATOR_PORT = 8092
DEFAULT_NATS_PORT = 4222
DEFAULT_STATE_FILE = Path.home() / ".cache" / "thallium-core-probe" / "state.json"
NEBULA_RECORDS_URL = "http://localhost:3101/api/agent-records"

# Route families verified live during the 2026-09-22 rebuild from main
# (PR #454). 200 = ok for every one of them.
DEFAULT_ROUTES = [
    "/api/shrapnel/health",
    "/api/meep/health",
    "/api/aegis/registries",      # aegis has no /health; registries is the data-liveness probe
    "/api/solscript/health",
    "/api/workers/execution/health",
    "/search/api/health",          # QUIRK: moleculer-search is /search/api in the monolith
]

STREAM_NAME = "WRITE_QUEUE"
CONSUMER_NAME = "write_queue_reconciler"
CONSUMER_BACKLOG_WARN = 100

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

TRANSITIONS = {
    (STATUS_OK, STATUS_FAIL): "type:incident",
    (STATUS_WARN, STATUS_FAIL): "type:incident",
    (STATUS_FAIL, STATUS_OK): "type:recovery",
    (STATUS_FAIL, STATUS_WARN): "type:recovery",
    (STATUS_OK, STATUS_WARN): "type:warning",
    (STATUS_WARN, STATUS_OK): None,
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
    """One SSH round-trip returning {container_name: state} for running containers.

    Forces IPv4 (-4): the helium probe flapped incident/recovery for a day
    when its host name resolved to a stale dead v6 path (51/53 transitions,
    2026-09-21). Pinning the family makes the leg deterministic.
    """
    cmd = [
        "ssh", "-4", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
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


def fetch_jetstream(host: str, timeout: float) -> dict:
    """nats-py probe of the WRITE_QUEUE stream + durable consumer.

    Returns {stream: {exists, storage, messages}, consumer: {exists,
    num_pending, num_ack_pending}}. Raises on connect/probe failure (the
    check functions translate that to status).
    """
    import nats  # nats-py — present on titanium system python3

    async def _run():
        nc = await nats.connect(
            f"nats://{host}:{DEFAULT_NATS_PORT}",
            connect_timeout=timeout, allow_reconnect=False)
        try:
            js = nc.jetstream()
            si = await js.stream_info(STREAM_NAME)
            try:
                ci = await js.consumer_info(STREAM_NAME, CONSUMER_NAME)
                consumer = {"exists": True,
                            "num_pending": ci.num_pending,
                            "num_ack_pending": ci.num_ack_pending}
            except Exception:
                consumer = {"exists": False}
            return {"stream": {"exists": True,
                               "storage": str(getattr(si.config, "storage", "?")),
                               "messages": si.state.messages},
                    "consumer": consumer}
        finally:
            await nc.close()

    return asyncio.run(asyncio.wait_for(_run(), timeout=timeout + 5))


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


def check_route(fetch, base_url, path, timeout):
    url = f"{base_url}{path}"
    try:
        fetch(url, timeout)
    except urllib.error.HTTPError as exc:
        return {"name": f"route:{path}", "status": STATUS_FAIL,
                "detail": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"name": f"route:{path}", "status": STATUS_FAIL,
                "detail": f"unreachable: {exc}"}
    return {"name": f"route:{path}", "status": STATUS_OK, "detail": "200"}


def check_routes(fetch, host, timeout, routes):
    base = f"http://{host}:{DEFAULT_ACTUATOR_PORT}"
    return [check_route(fetch, base, path, timeout) for path in routes]


def check_containers(fetch_states, ssh_host, timeout, expected, ssh_timeout=DEFAULT_SSH_TIMEOUT):
    try:
        states = fetch_states(ssh_host, ssh_timeout)
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


def check_jetstream(fetch_js, host, timeout):
    try:
        data = fetch_js(host, timeout)
    except ImportError as exc:
        return {"name": "jetstream", "status": STATUS_FAIL,
                "detail": f"probe dependency missing: {exc}"}
    except Exception as exc:
        return {"name": "jetstream", "status": STATUS_FAIL,
                "detail": f"nats unreachable/probe failed: {exc}"}
    stream = data.get("stream", {})
    if not stream.get("exists"):
        # The pre-provisioning failure mode: with no stream, the producer
        # silently falls back to core NATS and intents evaporate.
        return {"name": "jetstream", "status": STATUS_FAIL,
                "detail": f"{STREAM_NAME} missing — write intents would drop"}
    consumer = data.get("consumer", {})
    problems = []
    if str(stream.get("storage", "")).lower() not in ("file", "storagetype.file"):
        problems.append(f"storage={stream.get('storage')} (expected file)")
    if not consumer.get("exists"):
        problems.append(f"consumer {CONSUMER_NAME} missing (stream undrained)")
    else:
        pending = consumer.get("num_pending") or 0
        if pending > CONSUMER_BACKLOG_WARN:
            problems.append(f"backlog num_pending={pending}>{CONSUMER_BACKLOG_WARN}")
    if problems:
        return {"name": "jetstream", "status": STATUS_WARN,
                "detail": STREAM_NAME + " up with: " + "; ".join(problems)}
    return {"name": "jetstream", "status": STATUS_OK,
            "detail": (f"{STREAM_NAME} file-backed, consumer {CONSUMER_NAME} "
                       f"lag={consumer.get('num_pending', 0)}")}


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
    lines = [f"thallium nexus-core probe: {overall.upper()}"]
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
        "title": f"[{alert_tag}] thallium nexus-core probe: {overall} — "
                 f"{', '.join(changed_names)}",
        "content": body_text,
        "tags": ["to:sysadmin", alert_tag, "thallium", "nexus-core", "probe"],
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
    p = argparse.ArgumentParser(description="thallium nexus-core health probe")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--ssh-timeout", type=float, default=DEFAULT_SSH_TIMEOUT,
                   help="subprocess budget for the docker-over-ssh round-trip")
    p.add_argument("--min-disk-free-gb", type=float, default=DEFAULT_MIN_DISK_FREE_GB)
    p.add_argument("--containers", default=",".join(DEFAULT_CONTAINERS))
    p.add_argument("--routes", default=",".join(DEFAULT_ROUTES),
                   help="comma-separated :8092 paths to probe (200 expected)")
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--no-actuator", action="store_true",
                   help="skip the :8092 actuator check")
    p.add_argument("--no-routes", action="store_true",
                   help="skip the route-family checks")
    p.add_argument("--no-jetstream", action="store_true",
                   help="skip the WRITE_QUEUE stream/consumer check")
    p.add_argument("--dry-run", action="store_true",
                   help="print alerts instead of posting records")
    p.add_argument("--no-alert", action="store_true",
                   help="skip alert posting AND state persistence")
    return p


def run_probe(args, fetch=None, fetch_states=None, fetch_js=None, poster=None):
    fetch = fetch or fetch_json
    fetch_states = fetch_states or fetch_docker_states
    fetch_js = fetch_js or fetch_jetstream
    poster = poster or post_alert

    actuator_url = f"http://{args.host}:{DEFAULT_ACTUATOR_PORT}/actuator/health"

    results = []
    if not getattr(args, "no_actuator", False):
        results.append(check_actuator(fetch, actuator_url, args.timeout, args.min_disk_free_gb))
    if not getattr(args, "no_routes", False):
        routes = [r.strip() for r in args.routes.split(",") if r.strip()]
        results += check_routes(fetch, args.host, args.timeout, routes)
    results += check_containers(
        fetch_states, args.host, args.timeout,
        [c.strip() for c in args.containers.split(",") if c.strip()],
        ssh_timeout=getattr(args, "ssh_timeout", DEFAULT_SSH_TIMEOUT))
    if not getattr(args, "no_jetstream", False):
        results.append(check_jetstream(fetch_js, args.host, args.timeout))

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
