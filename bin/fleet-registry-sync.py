#!/usr/bin/env python3
"""fleet-registry-sync — declared fleet -> registry.servers / registry.deployments.

Fleet slice 3 (R1 0d40c061). The declared/observed epistemology:

  - bin/config/fleet-manifest.json   DECLARED state (operator intent)
  - terrain.service_endpoints        OBSERVED state (probed, endpoint-register)
  - registry.servers / .deployments  DECLARED persistence (this tool)

The diff between declared and observed is fleet drift — terrain-drift-check
consumes it (test_terrain_drift_check.py already exists on main).

Behavior:
  - servers: upsert by UNIQUE(hostname); existing rows updated only on
    fields the manifest actually states; rows not in the manifest are
    NOT touched here — explicit retirement happens via the manifest's
    `retire.hostnames` list (status='RETIRED', rows never deleted —
    provenance). NEW hosts require a declared ip_address
    (registry.servers.ip_address is NOT NULL): unaddressable hosts are
    reported loudly and excluded — the tool never fabricates an address.
  - deployments: one row per manifest service on its host. Zero rows
    exist today; the table has no natural key, so the tool matches on
    (service name, host, port) and inserts when absent — idempotent by
    check-then-insert, not by constraint.
  - service names are resolved against registry.services UNIQUE(name);
    unknown names are reported loudly and skipped — UNLESS the manifest
    declares them under `services` (explicit archetype additions: name,
    default_port, description, framework, service_type). The manifest
    is the single declared source; the tool inserts what the operator
    declared and invents nothing on its own. framework/service_type
    default to Spring Boot / REST API when omitted and must resolve
    against the registry lookup tables — unknown names fail loudly.

DRY-RUN BY DEFAULT: without --apply the tool prints the full plan and
writes nothing. With --apply it executes the same plan in one
transaction per section.

Exit codes: 0 ok · 1 hard failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Optional

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
RETIRED_STATUS = "RETIRED"


def default_connect(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def load_manifest(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "fleet" not in data or not isinstance(data["fleet"], list):
        raise ValueError("manifest must carry a 'fleet' array")
    for host in data["fleet"]:
        if "hostname" not in host:
            raise ValueError(f"fleet entry missing hostname: {host!r}")
    data.setdefault("retire", {}).setdefault("hostnames", [])
    data.setdefault("services", [])
    return data


def _resolve_lookup(
    cur, table: str, name: Optional[str], ctx: str, kind: str,
    default: Optional[str] = None,
) -> int:
    """Resolve a NOT NULL lookup FK by name against a registry lookup table.

    `default` is the documented fallback when the manifest omits the name;
    None default + omitted name = loud failure. Unresolvable names fail
    loudly — the tool never invents lookup rows.
    """
    if name is None:
        if default is None:
            raise ValueError(
                f"{ctx}: {kind} is required "
                f"(registry.{table} is NOT NULL) — declare it in the manifest"
            )
        name = default
    cur.execute(f"SELECT id FROM registry.{table} WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(
            f"{ctx}: unknown {kind} {name!r} (not in registry.{table}) — "
            "declare an existing row or add it first"
        )
    return row[0]


def plan(conn, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the full sync plan against the live DB (read-only)."""
    out: dict[str, Any] = {
        "servers_upsert": [],
        "servers_retire": [],
        "deployments_insert": [],
        "unknown_services": [],
        "services_insert": [],
        "unaddressable_servers": [],
    }
    with conn.cursor() as cur:
        # host -> id for existing rows
        cur.execute("SELECT hostname, id FROM registry.servers")
        host_ids = {h: i for h, i in cur.fetchall()}

        cur.execute("SELECT name, id, default_port FROM registry.services")
        svc = {n: (i, p) for n, i, p in cur.fetchall()}

        # declared archetype additions (explicit in the manifest only)
        declared_names: set[str] = set()
        for s in manifest.get("services", []):
            name = s.get("name")
            if not name:
                raise ValueError(f"manifest services entry missing name: {s!r}")
            declared_names.add(name)
            if name not in svc:
                out["services_insert"].append(
                    {"name": name, "default_port": s.get("default_port"),
                     "description": s.get("description"),
                     "framework": s.get("framework"),
                     "service_type": s.get("service_type")}
                )

        for host in manifest["fleet"]:
            hn = host["hostname"]
            fields: dict[str, Any] = {}
            if "environment" in host:
                fields["environment_type_id"] = ("env", host["environment"])
            if "description" in host:
                fields["description"] = ("raw", host["description"])
            if "status" in host:
                fields["status"] = ("raw", host["status"])
            if "ip_address" in host:
                fields["ip_address"] = ("raw", host["ip_address"])
            exists = hn in host_ids
            # registry.servers NOT NULLs on NEW hosts: ip_address,
            # operating_system_id, server_type_id. A NEW host the manifest
            # cannot fully state is not fabricatable — report loudly per
            # missing field and exclude from the plan (absence stays data).
            if not exists:
                missing = [
                    k for k in ("ip_address", "os", "server_type")
                    if k not in host
                ]
                if missing:
                    out["unaddressable_servers"].append(
                        {"hostname": hn, "missing": missing})
                    continue
            if "os" in host:
                fields["operating_system_id"] = ("lookup", host["os"])
            if "server_type" in host:
                fields["server_type_id"] = ("lookup", host["server_type"])
            out["servers_upsert"].append(
                {"hostname": hn, "exists": exists, "fields": fields}
            )
            for name in host.get("services", []):
                if name not in svc:
                    if name in declared_names:
                        continue  # declared below — resolved post-insert
                    out["unknown_services"].append(
                        {"host": hn, "service": name}
                    )
                    continue
                sid, default_port = svc[name]
                out["deployments_insert"].append(
                    {"host": hn, "host_id": host_ids.get(hn), "service": name,
                     "service_id": sid, "port": default_port}
                )

        for hn in manifest["retire"]["hostnames"]:
            if hn in host_ids:
                out["servers_retire"].append(hn)

        # deployments referencing newly-declared services resolve after insert
        for host in manifest["fleet"]:
            hn = host["hostname"]
            for name in host.get("services", []):
                if name in svc:
                    continue  # already planned above
                declared = next(
                    (s for s in out["services_insert"] if s["name"] == name),
                    None,
                )
                if declared is not None:
                    out["deployments_insert"].append(
                        {"host": hn, "host_id": host_ids.get(hn),
                         "service": name, "service_id": None,
                         "port": declared["default_port"],
                         "new_service": True}
                    )
    return out


def apply_plan(conn, p: dict[str, Any], host_env: dict[str, int]) -> dict[str, int]:
    """Execute the plan (single transaction per section)."""
    counts = {"servers": 0, "retired": 0, "deployments": 0, "services": 0}
    with conn.cursor() as cur:
        # declared archetype additions first (deployments may reference them)
        for s in p["services_insert"]:
            cur.execute(
                "SELECT id FROM registry.services WHERE name = %s", (s["name"],)
            )
            row = cur.fetchone()
            if row is None:
                fw = _resolve_lookup(
                    cur, "frameworks", s.get("framework"),
                    f"service {s['name']!r}", "framework",
                    default="Spring Boot",
                )
                st = _resolve_lookup(
                    cur, "service_type", s.get("service_type"),
                    f"service {s['name']!r}", "service_type",
                    default="REST API",
                )
                cur.execute(
                    "INSERT INTO registry.services (name, default_port, "
                    "description, framework_id, service_type_id, active_flag, "
                    "origin) VALUES (%s, %s, %s, %s, %s, true, 'fleet-manifest')",
                    (s["name"], s["default_port"], s["description"], fw, st),
                )
                counts["services"] += 1

        for entry in p["servers_upsert"]:
            hn = entry["hostname"]
            if entry["exists"]:
                sets, params = [], {}
                _lookup_tables = {
                    "environment_type_id": "environment_type",
                    "operating_system_id": "operating_systems",
                    "server_type_id": "server_type",
                }
                for col, (kind, val) in entry["fields"].items():
                    if kind in ("env", "lookup"):
                        table = _lookup_tables[col]
                        cur.execute(
                            f"SELECT id FROM registry.{table} WHERE name = %s",
                            (val,),
                        )
                        row = cur.fetchone()
                        if row is None:
                            raise ValueError(
                                f"unknown {table} name: {val!r} (host {hn})"
                            )
                        params[col] = row[0]
                    else:
                        params[col] = val
                    sets.append(f"{col} = %({col})s")
                if sets:
                    params["hostname"] = hn
                    cur.execute(
                        f"UPDATE registry.servers SET {', '.join(sets)} "
                        "WHERE hostname = %(hostname)s",
                        params,
                    )
                    counts["servers"] += cur.rowcount or 0
            else:
                # plan() guarantees every NEW-host NOT NULL is present here —
                # unaddressable/undescribed hosts are excluded before this runs
                env_id = _resolve_lookup(
                    cur, "environment_type",
                    entry["fields"].get("environment_type_id", (None,))[1],
                    f"server {hn!r}", "environment", default="Production",
                )
                os_id = _resolve_lookup(
                    cur, "operating_systems", entry["fields"]["operating_system_id"][1],
                    f"server {hn!r}", "operating_system",
                )
                styp_id = _resolve_lookup(
                    cur, "server_type", entry["fields"]["server_type_id"][1],
                    f"server {hn!r}", "server_type",
                )
                cur.execute(
                    "INSERT INTO registry.servers (hostname, environment_type_id, "
                    "operating_system_id, server_type_id, ip_address, "
                    "description, status, active_flag) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, true)",
                    (
                        hn,
                        env_id,
                        os_id,
                        styp_id,
                        entry["fields"]["ip_address"][1],
                        entry["fields"].get("description", ("raw", None))[1],
                        entry["fields"].get("status", ("raw", "ACTIVE"))[1],
                    ),
                )
                host_env[hn] = cur.lastrowid if hasattr(cur, "lastrowid") else None
                counts["servers"] += 1

        for hn in p["servers_retire"]:
            cur.execute(
                "UPDATE registry.servers SET status = %s, active_flag = false "
                "WHERE hostname = %s",
                (RETIRED_STATUS, hn),
            )
            counts["retired"] += cur.rowcount or 0

        # deployments: re-read host ids (new servers included); deployments
        # inherit their host's environment (environment_id NOT NULL)
        cur.execute("SELECT hostname, id FROM registry.servers")
        host_ids = {h: i for h, i in cur.fetchall()}
        cur.execute("SELECT hostname, environment_type_id FROM registry.servers")
        host_env_ids = {h: e for h, e in cur.fetchall()}
        cur.execute("SELECT name, id FROM registry.services")
        svc_ids = {n: i for n, i in cur.fetchall()}
        for dep in p["deployments_insert"]:
            hn = dep["host"]
            hid = host_ids.get(hn) or dep["host_id"]
            sid = svc_ids.get(dep["service"]) or dep["service_id"]
            env_id = host_env_ids.get(hn)
            if env_id is None:
                raise ValueError(
                    f"deployment {dep['service']} @ {hn}: host has no "
                    "environment (registry.servers.environment_type_id is "
                    "NOT NULL) — declare one in the manifest"
                )
            cur.execute(
                "SELECT id FROM registry.deployments WHERE service_id = %s "
                "AND host_id = %s AND port IS NOT DISTINCT FROM %s",
                (sid, hid, dep["port"]),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO registry.deployments "
                    "(service_id, host_id, environment_id, port, status, "
                    "active_flag) VALUES (%s, %s, %s, %s, 'RUNNING', true)",
                    (sid, hid, env_id, dep["port"]),
                )
                counts["deployments"] += 1
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config", "fleet-manifest.json"))
    p.add_argument("--dsn", default=os.environ.get("NEXUS_PG_DSN", DEFAULT_DSN))
    p.add_argument("--apply", action="store_true",
                   help="execute the plan (default: dry-run, prints only)")
    args = p.parse_args(argv)

    manifest = load_manifest(args.manifest)
    conn = default_connect(args.dsn)
    try:
        p = plan(conn, manifest)
        if p["unknown_services"]:
            print("UNKNOWN SERVICES (skipped — archetype rows are never "
                  "invented by sync):")
            for u in p["unknown_services"]:
                print(f"  {u['host']}: {u['service']}")
        if p["services_insert"]:
            print("DECLARED SERVICES (manifest archetype additions):")
            for s in p["services_insert"]:
                print(f"  [service] {s['name']} "
                      f"port={s['default_port'] if s['default_port'] is not None else '-'} "
                      f"framework={s.get('framework') or 'Spring Boot(default)'} "
                      f"type={s.get('service_type') or 'REST API(default)'}")
        if p["unaddressable_servers"]:
            print("UNADDRESSABLE SERVERS (declared but missing fields a NEW "
                  "registry.servers row requires — ip_address/os/server_type "
                  "are NOT NULL; add them to the manifest to include the host):")
            for u in p["unaddressable_servers"]:
                print(f"  [skip] server {u['hostname']} missing={u['missing']}")
        print(f"plan: {len(p['servers_upsert'])} server(s) to upsert, "
              f"{len(p['servers_retire'])} to retire, "
              f"{len(p['deployments_insert'])} deployment(s) to ensure")
        if not args.apply:
            for e in p["servers_upsert"]:
                action = "update" if e["exists"] else "insert"
                print(f"  [{action}] server {e['hostname']} "
                      f"fields={sorted(e['fields'])}")
            for hn in p["servers_retire"]:
                print(f"  [retire] server {hn} -> {RETIRED_STATUS}")
            for d in p["deployments_insert"]:
                print(f"  [deployment] {d['service']} @ {d['host']}"
                      f":{d['port'] if d['port'] is not None else '-'}")
            print("dry-run only — pass --apply to execute")
            return 0
        counts = apply_plan(conn, p, {})
        conn.commit()
        print(f"applied: {counts}")
        return 0
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
