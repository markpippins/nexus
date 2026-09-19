#!/usr/bin/env python3
"""terrain-status-sync — terrain.servers status/active_flag derive from
registry.servers (R1 1b15e5b7, operator ruling: one declared truth).

Why
---
terrain.servers is a declared surface (a claim about what should be), but
it carried its own hand-set ONLINE status — the drift report caught it
claiming barium ONLINE while the fleet census (registry.servers, #359
census + #361 single source) declares OFFLINE. Two declared layers that
disagree are worse than one wrong layer. Per the runtime-facts-flow-one-
way discipline, terrain's *declared* server status now derives from the
registry; the OBSERVED layer (probes, terrain.service_endpoints) remains
untouched and stays authoritative for liveness.

Scope (deliberately narrow):
  - hostname join (lowercased) between registry.servers and terrain.servers
  - syncs exactly two fields: status, active_flag — with an EXPLICIT
    vocabulary translation, because the two tables speak different
    dialects of the same idea:

      registry.servers   ->  terrain.servers   meaning
      ACTIVE             ->  ONLINE            treat as up (probeable)
      OFFLINE            ->  OFFLINE           real-but-powered-down
      RETIRED            ->  OFFLINE (+flag=f) deactivated member
      (anything else)    ->  REFUSED loudly — no silent invention

    Verbatim copying would be wrong twice: ACTIVE terrain rows would
    blind terrain-drift-check (it probes status==ONLINE only), and
    terrain has no RETIRED concept (active_flag=false is its mechanism).
  - active_flag is copied as-is: both tables share the #359 semantics
    (OFFLINE machines stay members, flag true; RETIRED deactivates).
  - NO row creation: a registry host absent from terrain is reported, not
    inserted (terrain row lifecycle is terrain's own)
  - terrain-only hosts (no registry row) are reported 'unknown' and left
    alone — absence of a declaration is data, not a write instruction

Usage:
  python3 bin/terrain-status-sync.py            # plan to stdout (no writes)
  python3 bin/terrain-status-sync.py --apply    # PUT diverging terrain rows
  python3 bin/terrain-status-sync.py --json     # machine-readable plan

Env: TERRAIN_URL (default http://localhost:8084), REGISTRY_DSN
(default postgresql://pguser:pgpass@localhost:5432/nexus).
Exit: 0 always on completed plans (divergence is data, reported); 125 on
unreachable terrain.
"""

import argparse
import json
import os
import sys
import urllib.request

TERRAIN = os.environ.get("TERRAIN_URL", "http://localhost:8084")
REGISTRY_DSN = os.environ.get(
    "REGISTRY_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")


def load_registry_servers(dsn=None):
    """Declared layer: {lower(hostname): {'status':…, 'active_flag':…}}.
    Raises on DB failure (caller decides degraded vs fatal)."""
    dsn = dsn or REGISTRY_DSN
    import psycopg2
    out = {}
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lower(hostname), upper(status), active_flag "
                "FROM registry.servers")
            for hn, status, flag in cur.fetchall():
                out[hn] = {"status": status, "active_flag": bool(flag)}
    return out


def load_terrain_servers():
    """Observed-side REST: full terrain.servers rows (for round-trip PUT)."""
    with urllib.request.urlopen(f"{TERRAIN}/api/v1/servers?size=200",
                                timeout=15) as r:
        rows = json.load(r).get("data", [])
    return {str(s.get("hostname") or "").lower(): s for s in rows}


# declared vocabulary translation (see module docstring)
STATUS_MAP = {"ACTIVE": "ONLINE", "OFFLINE": "OFFLINE", "RETIRED": "OFFLINE"}


def translate(rrow, hostname):
    """registry row -> terrain (status, active_flag); unknown registry
    status refuses loudly rather than inventing a terrain value."""
    status = STATUS_MAP.get(rrow["status"])
    if status is None:
        raise ValueError(
            f"registry status {rrow['status']!r} on {hostname!r} has no "
            "terrain translation — extend STATUS_MAP explicitly")
    return {"status": status, "active_flag": rrow["active_flag"]}


def plan(registry, terrain):
    """Pure join: per-host action list. terrain rows are never mutated here."""
    actions = {"update": [], "in-sync": [], "unknown": [], "missing-in-terrain": []}
    for hn, trow in terrain.items():
        rrow = registry.get(hn)
        if rrow is None:
            actions["unknown"].append(
                {"hostname": hn, "terrain_status": trow.get("status"),
                 "note": "no registry.servers row — left alone"})
            continue
        want = translate(rrow, hn)
        want = {"status": want["status"], "active_flag": want["active_flag"]}
        have = {"status": str(trow.get("status") or "").upper(),
                "active_flag": bool(trow.get("activeFlag"))}
        if want != have:
            actions["update"].append({
                "hostname": hn, "id": trow.get("id"),
                "from": have, "to": want})
        else:
            actions["in-sync"].append(hn)
    for hn in sorted(set(registry) - set(terrain)):
        actions["missing-in-terrain"].append(
            {"hostname": hn, "registry_status": registry[hn]["status"],
             "note": "declared in registry, absent from terrain — reported, not created"})
    return actions


def apply_updates(actions, dry_run=True):
    """PUT the full terrain row with the two synced fields applied."""
    changed = 0
    for act in actions["update"]:
        row = load_terrain_row(act["id"])
        if row is None:
            print(f"  {act['hostname']:10} ❌ terrain row {act['id']} vanished")
            continue
        row["status"] = act["to"]["status"]
        row["activeFlag"] = act["to"]["active_flag"]
        label = f"{act['hostname']:10} {act['from']['status']}→{act['to']['status']}" \
                f" active={act['to']['active_flag']}"
        if dry_run:
            print(f"  {label}  (would PUT)")
            continue
        body = json.dumps(row).encode()
        req = urllib.request.Request(
            f"{TERRAIN}/api/v1/servers/{act['id']}", data=body, method="PUT",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.load(r)
        ok = (str(resp.get("status") or "").upper() == act["to"]["status"]
              and bool(resp.get("activeFlag")) == act["to"]["active_flag"])
        print(f"  {label}  {'✅' if ok else '❌'}")
        changed += 1 if ok else 0
    return changed


def load_terrain_row(row_id):
    try:
        with urllib.request.urlopen(f"{TERRAIN}/api/v1/servers/{row_id}",
                                    timeout=15) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001 — vanished mid-run is reported, not fatal
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="PUT diverging terrain rows (default: plan only)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable plan")
    args = ap.parse_args()

    try:
        registry = load_registry_servers()
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: registry unreachable at {REGISTRY_DSN}: "
              f"{e.__class__.__name__}", file=sys.stderr)
        return 125
    try:
        terrain = load_terrain_servers()
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: terrain unreachable at {TERRAIN}: "
              f"{e.__class__.__name__}", file=sys.stderr)
        return 125

    actions = plan(registry, terrain)

    if args.as_json:
        print(json.dumps(actions, indent=2))
        return 0

    print(f"terrain-status-sync — registry.servers → terrain.servers "
          f"({len(registry)} declared, {len(terrain)} terrain rows)")
    print(f"  in-sync: {len(actions['in-sync'])}; "
          f"to-update: {len(actions['update'])}; "
          f"unknown-to-registry: {len(actions['unknown'])}; "
          f"missing-in-terrain: {len(actions['missing-in-terrain'])}")
    for a in actions["unknown"]:
        print(f"  [unknown] {a['hostname']}: terrain status="
              f"{a['terrain_status']} — {a['note']}")
    for a in actions["missing-in-terrain"]:
        print(f"  [missing] {a['hostname']}: registry={a['registry_status']}"
              f" — {a['note']}")

    if actions["update"]:
        print()
        if not args.apply:
            print("  plan only — pass --apply to write:")
        apply_updates(actions, dry_run=not args.apply)
    elif args.apply:
        print("  nothing to update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
