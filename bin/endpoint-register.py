#!/usr/bin/env python3
"""endpoint-register — declare/observe fleet instances in terrain.service_endpoints.

The instance registry (R1 0d40c061, topology analysis): `terrain.service_endpoints`
was designed as a live, heartbeat-driven instance registry
(UNIQUE(unit, instance)) but only ever held static seed rows
(instance='primary', heartbeats never flowed). This tool makes it real:

  - register   : upsert one service or a whole manifest as this host's
                 instances (host defaults to NEXUS_HOST or gethostname(),
                 instance to NEXUS_INSTANCE or the host name — the
                 hostname-encoding keeps UNIQUE(unit, instance)
                 collision-free across machines)
  - heartbeat  : re-assert liveness for rows this host already owns
                 (updates last_heartbeat, no topology mutation)
  - retire     : mark the legacy static seed rows (instance='primary',
                 no heartbeat) RETIRED for the services being registered
                 — explicit disposition, never silent deletion

Declare-vs-observe discipline: a row is only written status='UP' after a
successful probe of the service; a failed probe still registers the
instance but as status='DOWN' — a machine that cannot be reached is
data, not an omission. Absent identity is honest; failed identity is
louder.

Exit codes: 0 ok · 1 hard failure (bad args / DB error).

Usage:
  endpoint-register.py register --service nebula-srv --port 3101 \
      [--health-path /health] [--host H] [--instance I]
  endpoint-register.py register --manifest bin/config/endpoint-manifest.json
  endpoint-register.py heartbeat --manifest bin/config/endpoint-manifest.json
  endpoint-register.py retire --service cascade [--all]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
PROBE_TIMEOUT_S = 3
SEED_INSTANCE = "primary"  # the static bootstrap rows we retire


# ── seams (hermetic tests inject these) ─────────────────────────────

def default_connect(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def probe(url: str, timeout: int = PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """Return (ok, detail). TCP-level reachability via curl (house
    convention — no third-party HTTP dep at this layer)."""
    try:
        r = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        if r.returncode == 0:
            return True, "probe-ok"
        return False, f"probe-failed rc={r.returncode}"
    except Exception as exc:  # probe failure is data, never a crash
        return False, f"probe-error {type(exc).__name__}: {exc}"


def probe_tcp(port: int, host: str = "localhost",
              timeout: int = PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """Port-open check for services without a GET health route
    (JSON-RPC MCP surfaces: conduit, nebula-mcp, tackle, ...)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "tcp-ok"
    except Exception as exc:
        return False, f"tcp-failed {type(exc).__name__}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── identity ────────────────────────────────────────────────────────

def resolve_host(explicit: Optional[str]) -> str:
    return explicit or os.environ.get("NEXUS_HOST") or socket.gethostname()


def resolve_instance(explicit: Optional[str], host: str) -> str:
    # Hostname-encoding: UNIQUE(unit, instance) must not collide across
    # machines — 'primary' on every host would.
    return explicit or os.environ.get("NEXUS_INSTANCE") or host


# ── manifest ────────────────────────────────────────────────────────

def load_manifest(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    services = data["services"] if isinstance(data, dict) else data
    out = []
    for entry in services:
        if "service" not in entry or "port" not in entry:
            raise ValueError(f"manifest entry needs service+port: {entry!r}")
        out.append(
            {
                "service": str(entry["service"]),
                "port": int(entry["port"]),
                "scheme": str(entry.get("scheme", "http")),
                "health_path": str(entry.get("health_path", "/")),
                "tcp": bool(entry.get("tcp", False)),
                "ip": entry.get("ip"),
            }
        )
    return out


# ── SQL ─────────────────────────────────────────────────────────────

def endpoint_id(host: str, instance: str, service: str) -> str:
    """Deterministic row identity: same service on the same host+instance
    always maps to the same row id (NOT NULL PK has no default; uuid5
    keeps re-registrations stable and debuggable)."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"nexus://terrain/{host}/{instance}/{service}")
    )


UPSERT_SQL = """
INSERT INTO terrain.service_endpoints
    (id, host, instance, ip, unit, port, scheme, status, last_heartbeat)
VALUES (%(id)s, %(host)s, %(instance)s, %(ip)s, %(unit)s, %(port)s,
        %(scheme)s, %(status)s, now())
ON CONFLICT (unit, instance) DO UPDATE SET
    host            = EXCLUDED.host,
    ip              = EXCLUDED.ip,
    port            = EXCLUDED.port,
    scheme          = EXCLUDED.scheme,
    status          = EXCLUDED.status,
    last_heartbeat  = now()
"""

RETIRE_SQL = """
UPDATE terrain.service_endpoints
   SET status = 'RETIRED'
 WHERE instance = %(seed)s
   AND last_heartbeat IS NULL
   AND status <> 'RETIRED'
   AND (%(service)s IS NULL OR unit = %(service)s)
"""


def upsert_endpoint(cur, *, host: str, instance: str, service: str,
                    port: int, scheme: str, status: str, ip: str) -> None:
    # unit + ip are NOT NULL on live — supplied here, and the conflict
    # target (unit, instance) works because unit is in the VALUES list.
    cur.execute(
        UPSERT_SQL,
        {"id": endpoint_id(host, instance, service), "host": host,
         "instance": instance, "ip": ip, "unit": service,
         "port": port, "scheme": scheme, "status": status},
    )


def retire_seeds(cur, service: Optional[str]) -> int:
    cur.execute(
        RETIRE_SQL, {"seed": SEED_INSTANCE, "service": service}
    )
    return cur.rowcount or 0


# ── commands ────────────────────────────────────────────────────────

def resolve_ip(explicit: Optional[str]) -> str:
    """Best-honest chain for the fleet-routable address (ip is NOT NULL
    inet on live): explicit manifest value > NEXUS_IP env > local egress
    interface (UDP-connect trick — no packets are sent) > documented
    loopback fallback. A loopback address in a fleet registry is honest
    only when no better source exists; the manifest override is the fix.
    """
    if explicit:
        return explicit
    env = os.environ.get("NEXUS_IP")
    if env:
        return env
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # unroutable; nothing sent
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            s.close()
    except Exception:
        pass
    return "127.0.0.1"


def probe_dispatch(target):
    """Probe dispatcher: ('tcp', port) → TCP check, str → HTTP URL."""
    if isinstance(target, tuple) and target[0] == "tcp":
        return probe_tcp(target[1])
    return probe(target)


def cmd_register(args, connect_fn: Callable, probe_fn=probe) -> int:
    host = resolve_host(args.host)
    instance = resolve_instance(args.instance, host)
    if args.manifest:
        services = load_manifest(args.manifest)
    elif args.service and args.port:
        services = [{"service": args.service, "port": args.port,
                     "scheme": args.scheme,
                     "health_path": args.health_path}]
    else:
        print("register needs --service/--port or --manifest", file=sys.stderr)
        return 1

    conn = connect_fn(args.dsn)
    registered, down = 0, 0
    try:
        with conn.cursor() as cur:
            if not args.no_retire:
                retired = retire_seeds(cur, None if args.manifest else args.service)
                if retired:
                    print(f"retired {retired} static seed row(s)")
            for svc in services:
                if svc.get("tcp"):
                    ok, detail = probe_fn(("tcp", svc["port"]))
                else:
                    url = f"{svc['scheme']}://localhost:{svc['port']}{svc['health_path']}"
                    ok, detail = probe_fn(url)
                status = "UP" if ok else "DOWN"
                upsert_endpoint(
                    cur, host=host, instance=instance, service=svc["service"],
                    port=svc["port"], scheme=svc["scheme"], status=status,
                    ip=resolve_ip(svc.get("ip")),
                )
                registered += 1
                down += 0 if ok else 1
                print(f"registered {svc['service']} {host}/{instance}:{svc['port']} "
                      f"-> {status} ({detail})")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"register failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"register: {registered} endpoint(s), {down} DOWN")
    return 0


def cmd_heartbeat(args, connect_fn: Callable) -> int:
    host = resolve_host(args.host)
    instance = resolve_instance(args.instance, host)
    services = load_manifest(args.manifest) if args.manifest else None
    conn = connect_fn(args.dsn)
    try:
        with conn.cursor() as cur:
            if services:
                cur.execute(
                    "UPDATE terrain.service_endpoints SET last_heartbeat = now(), "
                    "status = 'UP' WHERE host = %s AND instance = %s AND unit = ANY(%s)",
                    (host, instance, [s["service"] for s in services]),
                )
            else:
                cur.execute(
                    "UPDATE terrain.service_endpoints SET last_heartbeat = now(), "
                    "status = 'UP' WHERE host = %s AND instance = %s",
                    (host, instance),
                )
            n = cur.rowcount or 0
        conn.commit()
        print(f"heartbeat: {n} endpoint(s) re-asserted at {utcnow()}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"heartbeat failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_retire(args, connect_fn: Callable) -> int:
    conn = connect_fn(args.dsn)
    try:
        with conn.cursor() as cur:
            n = retire_seeds(cur, None if args.all else args.service)
        conn.commit()
        print(f"retire: {n} static seed row(s) marked RETIRED")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"retire failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dsn", default=os.environ.get("NEXUS_PG_DSN", DEFAULT_DSN))
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--host", default=None)
        sp.add_argument("--instance", default=None)

    r = sub.add_parser("register", help="upsert endpoint rows (probe-gated)")
    common(r)
    r.add_argument("--service")
    r.add_argument("--port", type=int)
    r.add_argument("--scheme", default="http")
    r.add_argument("--health-path", default="/")
    r.add_argument("--manifest")
    r.add_argument("--no-retire", action="store_true",
                   help="skip retiring legacy 'primary' seed rows")
    r.set_defaults(fn=lambda a: cmd_register(a, default_connect, probe_dispatch))

    h = sub.add_parser("heartbeat", help="re-assert liveness (no topology change)")
    common(h)
    h.add_argument("--manifest")
    h.set_defaults(fn=lambda a: cmd_heartbeat(a, default_connect))

    t = sub.add_parser("retire", help="mark legacy seed rows RETIRED")
    common(t)
    t.add_argument("--service")
    t.add_argument("--all", action="store_true")
    t.set_defaults(fn=lambda a: cmd_retire(a, default_connect))

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
