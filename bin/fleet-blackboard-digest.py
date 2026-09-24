#!/usr/bin/env python3
"""bin/fleet-blackboard-digest.py — daily fleet blackboard digest → agent record.

Renders the per-role coordination blackboard digest (V192 companion view
`nebula.v_coordination_blackboard`, via continuity.blackboard.render_role_digest)
for EVERY fleet role and files the reading as ONE nebula agent record, so
adoption drift (inbox/todo accumulation, checkpoint staleness, digest inertness)
becomes a queryable time series instead of per-session anecdotes.

Design stance
-------------
* READ-ONLY on the blackboard: renders digests with advance=False. Advancing a
  role's inbox/todo checkpoints is the deliberate, human-reviewed R17.1 act of
  an actual session — a timer must never do it on a role's behalf, or "reviewed
  up to T" becomes a lie.
* Role resolution: distinct routed_role from the blackboard view UNION distinct
  role from nebula.agent_records — i.e. every role the system has ever routed
  to or heard from, plus the governed-role fallback list. Empty blackboards are
  data too: a role with zero items must still appear in the series.
* Never raise on environmental conditions (view absent → INERT entries; redis
  down → cache=off; nebula down → record skipped with a loud journal line).
  Exit 0 on any completed run — failures are data, not unit failures (mirrors
  lease-probe). Exit 2 only for usage errors.
* Files as the `DBA` service identity (fleet-hygiene instrument, same role the
  lease-probe/blackboard-advance operators use), recordType `inspection`,
  tagged type:status-update + to:sysadmin — visible, not inbox-noise for the
  measured roles.

Usage:
    python3 bin/fleet-blackboard-digest.py                # render + file record
    python3 bin/fleet-blackboard-digest.py --print        # render, no record
    python3 bin/fleet-blackboard-digest.py --no-record    # alias of --print
    python3 bin/fleet-blackboard-digest.py --json         # machine-readable

Env: NEXUS_BLACKBOARD_DSN (default postgresql://pguser:pgpass@localhost:5432/nexus),
     NEXUS_REDIS_HOST / NEXUS_REDIS_PORT, NEBULA_URL (default http://localhost:3101),
     FLEET_BLACKBOARD_FALLBACK_ROLES (comma-separated override of the fallback list).
"""

import argparse
import json
import os
import sys
import time
import urllib.request

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "..", "python"))

from continuity.blackboard import RedisCache, render_role_digest  # noqa: E402

DSN = os.environ.get(
    "NEXUS_BLACKBOARD_DSN",
    "postgresql://pguser:pgpass@localhost:5432/nexus")
NEBULA = os.environ.get("NEBULA_URL", "http://localhost:3101")

# Governed-role canon (AGENTS.md R12) + the operator + service identities seen
# in practice. Only used when DB role resolution fails — the DB union always
# wins when reachable.
FALLBACK_ROLES = [
    "architect", "engineer", "planner", "reviewer", "analyst", "inspector",
    "critic", "sound-technician", "layout-mechanic", "design-synthesist",
    "operator", "supervisor", "DBA",
]


def _log(line: str) -> None:
    print(f"fleet-blackboard {line}", file=sys.stderr)


def resolve_roles(dsn: str) -> tuple[list[str], str]:
    """DB union of routed roles + known record authors; (roles, source)."""
    sql = (
        "SELECT DISTINCT routed_role FROM nebula.v_coordination_blackboard "
        "WHERE routed_role IS NOT NULL "
        "UNION SELECT DISTINCT role FROM nebula.agent_records "
        "WHERE role IS NOT NULL ORDER BY 1")
    try:
        import psycopg2
        with psycopg2.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(sql)
            roles = sorted({r[0] for r in cur.fetchall() if r[0]})
        if roles:
            return roles, "db-union"
        return list(FALLBACK_ROLES), "db-empty->fallback"
    except Exception as e:  # noqa: BLE001 — degradation is a valid outcome
        _log(f"role resolution degraded ({e.__class__.__name__}); using fallback")
        env = os.environ.get("FLEET_BLACKBOARD_FALLBACK_ROLES")
        return (env.split(",") if env else list(FALLBACK_ROLES)), "db-error->fallback"


def render_fleet(roles: list[str]) -> tuple[list[dict], list[str]]:
    """Render every role. Returns (per-role results, journal notes)."""
    cache = RedisCache(
        host=os.environ.get("NEXUS_REDIS_HOST", "localhost"),
        port=int(os.environ.get("NEXUS_REDIS_PORT", "6379")))
    results, notes = [], []
    for role in roles:
        try:
            r = render_role_digest(role, DSN, cache=cache, advance=False)
        except Exception as e:  # noqa: BLE001 — a raise is data, not a crash
            r = {"status": "error", "reason": f"{e.__class__.__name__}: {e}"}
        r["role"] = role
        results.append(r)
        notes.append(f"{role}: {r['status']}")
        if r["status"] != "ok":
            notes.append(f"  reason: {r.get('reason', '?')}")
    return results, notes


def format_fleet(results: list[dict]) -> str:
    lines = [
        "# Fleet blackboard digest",
        "",
        f"Rendered {time.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
        f"{len(results)} role(s), read-only (no checkpoint advance).",
        "",
        "| role | status | todo:AN | todo:stale | inbox:new | inbox:seen | ckpt:never |",
        "|------|--------|---------|------------|-----------|------------|------------|",
    ]
    for r in results:
        c = r.get("digest", {}).get("counts", {})
        lines.append(
            f"| {r['role']} | {r['status']} | "
            f"{c.get('todo_action_needed', '—')} | "
            f"{c.get('todo_stale', '—')} | "
            f"{c.get('inbox_action_needed', '—')} | "
            f"{c.get('inbox_seen', '—')} | "
            f"{c.get('checkpoints_never_reviewed', '—')} |")
    lines.append("")
    for r in results:
        lines.append(f"## {r['role']} — {r['status']}")
        if r.get("format"):
            lines.append("```")
            lines.append(r["format"].rstrip())
            lines.append("```")
        else:
            lines.append(f"_{r.get('reason', 'no digest')}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def file_record(content: str, roles: list[str]) -> str | None:
    payload = {
        "recordType": "inspection", "role": "DBA",
        "title": f"Fleet blackboard digest {time.strftime('%Y-%m-%d %H:%M')} "
                 f"({len(roles)} roles)",
        "content": content,
        "tags": ["type:status-update", "to:sysadmin", "source:fleet-blackboard"],
        "level": 1, "visibilityScope": "all",
    }
    req = urllib.request.Request(
        f"{NEBULA}/api/agent-records", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("id")
    except Exception as e:  # noqa: BLE001
        _log(f"record filing FAILED ({e.__class__.__name__}: {e}) — "
             f"digest lost from the series; nebula-srv on 3101 reachable?")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", "--no-record", dest="print_only", action="store_true",
                    help="render to stdout without filing the agent record")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    roles, source = resolve_roles(DSN)
    _log(f"roles={len(roles)} source={source}")
    results, notes = render_fleet(roles)
    for n in notes:
        _log(n)
    content = format_fleet(results)

    if args.json:
        print(json.dumps({"roles": roles, "source": source,
                          "results": [{k: v for k, v in r.items()
                                       if k in ("role", "status", "reason")}
                                      for r in results]}, indent=2))
    elif args.print_only:
        print(content)
    else:
        rid = file_record(content, roles)
        _log(f"record {'filed ' + rid if rid else 'NOT FILED'} "
             f"({len(roles)} roles)")

    # Completed runs always exit 0: degraded renders are data, not failures.
    return 0


if __name__ == "__main__":
    sys.exit(main())
