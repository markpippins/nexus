#!/usr/bin/env python3
"""Terrain drift check — registry claims vs live probe (R15 discipline).

What it does
------------
1. Loads terrain (8084) servers + runnable-services + mcp-servers.
2. For every ONLINE+activeFlag row with a port, TCP-probes it. The probe host
   comes from the row's healthCheckUrl hostname when present (that URL is the
   authoritative "where does this live" signal — runnable_services has no
   server column), else the server row's ipAddress (or titanium fallback when
   serverId is null).
3. TCP (socket) probes, NOT HTTP: postgres/redis/nats/mongo rows are
   registered with bare TCP ports and would false-DEAD under HTTP probing.
   Hostnames that do not resolve (or resolve to public IPs, e.g. `helium` on
   titanium → AT&T IPv6) are reported as probe-config defects, not outages.
4. Optionally POSTs a nebula agent record of the census
   (--record; needs nebula-srv on 3101).

Exit code: number of defects (capped at 125), so cron/systemd can gate on it.

Usage:
  python3 bin/terrain-drift-check.py            # census to stdout
  python3 bin/terrain-drift-check.py --record   # also file agent record
  python3 bin/terrain-drift-check.py --json     # machine-readable

Env: TERRAIN_URL (default http://localhost:8084), NEBULA_URL (3101).
"""

import argparse
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TERRAIN = os.environ.get("TERRAIN_URL", "http://localhost:8084")
NEBULA = os.environ.get("NEBULA_URL", "http://localhost:3101")
TIMEOUT = float(os.environ.get("TERRAIN_PROBE_TIMEOUT", "3"))

SELF_HOST = socket.gethostname().lower()


def get_json(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def tcp_open(host, port):
    """True/False probe result; raises ValueError on unresolvable/public host."""
    if not port:
        return None
    try:
        infos = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(f"hostname does not resolve: {host}")
    ip = infos[0][4][0]
    if ipaddress.ip_address(ip.split("%")[0]).is_global:
        raise ValueError(f"{host} resolves to PUBLIC {ip} — use LAN IP instead")
    try:
        with socket.create_connection((host, int(port)), timeout=TIMEOUT):
            return True
    except OSError:
        return False


def probe_target(row, servers_by_id):
    """Resolve (host, provenance) for a service row."""
    hcu = row.get("healthCheckUrl") or ""
    host = urllib.parse.urlparse(hcu).hostname if "://" in hcu else None
    if host:
        return host, "healthCheckUrl"
    sid = row.get("serverId")
    if sid is not None and sid in servers_by_id:
        ip = (servers_by_id[sid].get("ipAddress") or "").strip()
        if ip:
            return ("localhost" if ip == "127.0.0.1" else ip), "serverId"
    return "localhost", "default(titanium)"


def census():
    servers = get_json(f"{TERRAIN}/api/v1/servers?size=200").get("data", [])
    services = get_json(f"{TERRAIN}/api/v1/runnable-services?size=200").get("data", [])
    mcps = get_json(f"{TERRAIN}/{'api/v1/mcp-servers?size=200'}").get("data", [])
    servers_by_id = {s.get("id"): s for s in servers}

    known_hosts = {str(s.get("ipAddress") or "").strip() for s in servers}
    known_hosts |= {"localhost", "127.0.0.1"}
    known_hosts |= {str(s.get("hostname") or "").lower() for s in servers}
    known_hosts.discard("")

    defects, checked, skipped = [], 0, []
    for kind, rows in (("svc", services), ("mcp", mcps)):
        for row in rows:
            name = row.get("name") or f"(null-name#{row.get('id')})"
            if not row.get("activeFlag"):
                skipped.append((kind, name, "inactive"))
                continue
            if str(row.get("status") or "").upper() != "ONLINE":
                skipped.append((kind, name, f"status={row.get('status')}"))
                continue
            port = row.get("port")
            if not port:
                skipped.append((kind, name, "no port (worker/mcp row)"))
                continue
            host, prov = probe_target(row, servers_by_id)
            checked += 1
            try:
                alive = tcp_open(host, port)
            except ValueError as e:
                defects.append({
                    "kind": kind, "name": name, "port": port, "host": host,
                    "prov": prov, "class": "probe-config", "detail": str(e),
                })
                continue
            if alive is False:
                if host not in known_hosts:
                    defects.append({
                        "kind": kind, "name": name, "port": port, "host": host,
                        "prov": prov, "class": "probe-config",
                        "detail": f"probe host {host} not a registered server address",
                    })
                else:
                    defects.append({
                        "kind": kind, "name": name, "port": port, "host": host,
                        "prov": prov, "class": "dead",
                        "detail": "TCP connect failed",
                    })
    return servers, services, mcps, checked, skipped, defects


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", action="store_true",
                    help="POST census to nebula as an inspection agent record")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    args = ap.parse_args()

    t0 = time.time()
    try:
        servers, services, mcps, checked, skipped, defects = census()
    except Exception as e:
        print(f"FATAL: terrain unreachable at {TERRAIN}: {e}", file=sys.stderr)
        return 125

    dur = time.time() - t0
    dead = [d for d in defects if d["class"] == "dead"]
    cfg = [d for d in defects if d["class"] == "probe-config"]

    if args.as_json:
        print(json.dumps({
            "titanium": SELF_HOST, "duration_s": round(dur, 1),
            "servers": [s.get("hostname") for s in servers],
            "checked": checked, "skipped": len(skipped),
            "defects": defects,
        }, indent=2))
    else:
        print(f"terrain drift check — {len(servers)} servers, "
              f"{len(services)} services, {len(mcps)} mcp-servers "
              f"({dur:.1f}s)")
        print(f"  probed: {checked} ONLINE+active rows with ports "
              f"({len(skipped)} skipped: inactive / portless)")
        print(f"  defects: {len(dead)} dead, {len(cfg)} probe-config")
        for d in defects:
            print(f"    [{d['class']:12}] {d['kind']}/{d['name']} "
                  f"port={d['port']} host={d['host']} via {d['prov']}: "
                  f"{d['detail']}")

    if args.record:
        body = (
            "## Terrain drift census (automated)\n\n"
            f"- probed {checked} ONLINE+active rows ({len(skipped)} skipped)\n"
            f"- dead: {len(dead)}; probe-config defects: {len(cfg)}\n\n"
            + ("\n".join(
                f"- [{d['class']}] {d['kind']}/{d['name']} port={d['port']} "
                f"host={d['host']}: {d['detail']}" for d in defects)
              or "- no defects")
            + "\n"
        )
        try:
            req = urllib.request.Request(
                f"{NEBULA}/api/agent-records",
                data=json.dumps({
                    "recordType": "inspection", "role": "topologist",
                    "title": f"Terrain drift census {time.strftime('%Y-%m-%d %H:%M')}",
                    "content": body,
                    "tags": ["to:sysadmin", "type:status-update"],
                    "level": 1, "visibilityScope": "all",
                }).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                rid = json.load(r).get("id", "?")
            print(f"  record: {rid}")
        except Exception as e:
            print(f"  record POST failed: {e}", file=sys.stderr)

    return min(len(defects), 125)


if __name__ == "__main__":
    sys.exit(main())
