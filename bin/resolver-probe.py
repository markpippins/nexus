#!/usr/bin/env python3
"""bin/resolver-probe.py — daily synthetic resolver-check probe (DBA, P3).

The wind-resolver enforce-flip soak (gate design, thread 1adce409 comment
0402e9b2) needs decision-grade journal volume at current real-traffic
levels. This runner supplies it: one synthetic requirements resolution per
seeded lifecycle node per run, driven by a daily systemd timer — the
lease-probe pattern applied to the wind resolver.

Mechanics
---------
* GETs wind-srv /api/nodes/{uuid}/requirements?probe=synthetic for each node
  carrying open demands. The route marks the journal lines probe=synthetic
  and echoes `probe` in the response — only this runner sends that marker.
* Node resolution is LIVE (never hard-coded): wind.node_requirements joined
  to wind.workflow_nodes for the node name. Falls back to the four seeded
  Requirement Lifecycle node UUIDs when the DB query fails — a short probe
  beats a failed unit and zero evidence (lease-probe degradation stance).
* Exit code is ALWAYS 0 on completed runs: probe failures are data (the
  resolver-check journal line carries outcome=error), not unit failures.
  Exit 2 only for usage errors.
* --print emits the per-node outcome table to stdout for manual runs.

Usage:
    python3 bin/resolver-probe.py                 # timer path
    python3 bin/resolver-probe.py --print         # manual, see the table
    WIND_SRV_BASE=http://127.0.0.1:3300 python3 bin/resolver-probe.py --print
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

WIND_SRV_BASE = os.environ.get("WIND_SRV_BASE", "http://127.0.0.1:3300")
FALLBACK_NODES = [
    ("triage", "f0000000-0000-0000-0000-000000000001"),
    ("decide", "f0000000-0000-0000-0000-000000000002"),
    ("implement", "f0000000-0000-0000-0000-000000000003"),
    ("review", "f0000000-0000-0000-0000-000000000004"),
]
NODE_DISCOVERY_SQL = (
    "SELECT DISTINCT ON (n.name) n.name::text || '|' || n.id::text "
    "FROM wind.node_requirements r "
    "JOIN wind.workflow_nodes n ON n.id = r.node_id "
    "WHERE r.valid_until = '9999-12-31 00:00:00+00'::timestamptz "
    "ORDER BY n.name, n.created_at DESC"
)


def _log(line: str) -> None:
    print(f"resolver-probe {line}", file=sys.stderr)


def _discover_nodes() -> list:
    """Nodes carrying OPEN demands, from the live DB. Never raises."""
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser",
             "-d", "nexus", "-t", "-A", "-c", NODE_DISCOVERY_SQL],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            nodes = []
            for line in result.stdout.splitlines():
                if "|" in line:
                    name, nid = line.split("|", 1)
                    nodes.append((name.strip(), nid.strip()))
            if nodes:
                return nodes
    except Exception as e:  # noqa: BLE001 — degrade to fallback
        _log(f"node-discovery error={e!r} falling back")
    return list(FALLBACK_NODES)


def _http_get(url: str, timeout: int = 20) -> tuple:
    """Returns (status_code, parsed_json_or_None). Never raises on HTTP errors."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001 — body may be empty
            return e.code, None
    except Exception as e:  # noqa: BLE001 — transport errors are outcomes
        return 0, {"error": str(e)}


def probe_node(name: str, node_id: str) -> dict:
    """One synthetic probe for one node. Never raises."""
    url = f"{WIND_SRV_BASE}/api/nodes/{node_id}/requirements?probe=synthetic"
    status, body = _http_get(url)
    reqs = (body or {}).get("requirements") or []
    verdicts = [r.get("effective_verdict") for r in reqs]
    outcome = {
        "node": name,
        "node_id": node_id,
        "http_status": status,
        "mode": (body or {}).get("mode"),
        "probe": (body or {}).get("probe"),
        "demands": len(reqs),
        "verdicts": verdicts,
        "error": (body or {}).get("error"),
    }
    _log(
        f"node={name} http={status} demands={len(reqs)} "
        f"verdicts={'+'.join(v or '-' for v in verdicts) or '-'}"
    )
    return outcome


def run(nodes: list) -> list:
    return [probe_node(n, i) for n, i in nodes]


def main() -> int:
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    do_print = "--print" in args

    nodes = _discover_nodes()
    if not nodes:
        _log("no nodes with open demands resolved — nothing to probe")
        return 0
    _log(f"start nodes={','.join(n for n, _ in nodes)} base={WIND_SRV_BASE}")
    rows = run(nodes)
    if do_print:
        for r in rows:
            print(json.dumps(r, sort_keys=True))
    _log(f"done nodes={len(rows)}")
    return 0  # probe failures are data, not unit failures


if __name__ == "__main__":
    sys.exit(main())
