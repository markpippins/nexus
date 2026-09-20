#!/usr/bin/env python3
"""Reconcile historical conduit plans into nebula.blueprints_history.

Sweep-tooling item 6/6 (to-do 3c204f0c), per user rulings A1/A2/B1/B2/C1/C2
and planner confirmation 5d77804b:

- A1: ALL plans live in nebula.blueprints_history (the canonical bitemporal
      surface, V171; the implementation_plans view is a read-only compat fold
      over payload). Historical plans become rows with
      blueprint_status='archived'; pointer fields ride inside the payload
      jsonb (origin_subdir, source_path, file_mtime, kg_entity_id,
      resolution_state). NO DDL — 'archived' passes the existing CHECK.
- A2: KG holds canonical references for outliers; we link via kg_entity_id,
      never duplicate KG content into plan rows.
- B1: tri-state. This job writes live-known and archived-known facts only;
      resolution_state starts 'unknown' everywhere — sweeps promote it later
      via implementation-evidence xrefs.
- B2: origin_subdir is first-class closure signal (completed/ vs pending/);
      pending-dir plans are NOT completed — they land in the sweep bucket
      likely_completed_unverified at query time.
- C2: deterministic, idempotent (skip existing plan_number), re-runnable.

Sources merged:
  1. audit/IMPLEMENTATION_PLANS/{completed,pending,planning,proposed}/*.md
  2. KG plans-section entities (knowledge-mcp) not already covered

Writes go to nebula.blueprints_history directly with the payload fold (same
mapping as conduit's upsertPlan, PR #302). The legacy
nebula.implementation_plans_history table is frozen post-V176 — writes there
no longer propagate anywhere (the V171 mirror trigger is retired) and would
silently vanish; idempotency checks likewise read the compat view, which is
backed by the canonical surface.

Usage:
  python3 bin/reconcile-historical-plans.py --dry-run   # print plan
  python3 bin/reconcile-historical-plans.py --apply     # write rows
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
AUDIT_DIR = REPO / "audit" / "IMPLEMENTATION_PLANS"
SUBDIRS = ["completed", "pending", "planning", "proposed", "active"]
NEBULA_MCP = "http://localhost:3102"
CLIENT = REPO / "python" / "nebula-mcp-client" / "nebula_mcp_client.py"

# psql connection (same fallback chain nebula-srv uses)
import os


def db_connect_args():
    return [
        "psql", "-h", os.environ.get("PG_HOST", "localhost"),
        "-U", os.environ.get("PG_USER", "pguser"),
        "-d", os.environ.get("PG_DB_NAME", "nexus"),
    ]


def psql(sql: str, tuples=False):
    env = dict(os.environ)
    env["PGPASSWORD"] = os.environ.get("PG_PASSWORD", "pgpass")
    cmd = db_connect_args() + ["-c", sql]
    if tuples:
        cmd += ["-At", "-F", "\t"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr[:400]}")
    return r.stdout.strip()


def kg_plan_entities():
    """All KG plans-section entities via knowledge-mcp."""
    out = subprocess.run(
        ["python3", str(CLIENT), NEBULA_MCP, "call",
         "knowledge_list_entities", json.dumps({"section": "plans", "limit": 500})],
        capture_output=True, text=True, timeout=60)
    d = json.loads(out.stdout)
    return d.get("entities", [])


def title_from_md(path: pathlib.Path) -> str:
    """First '# heading', else filename stem."""
    try:
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem[:200]


def number_from_name(name: str) -> str | None:
    m = re.match(r"^(\d{3,5})", name)
    if m:
        return m.group(1)
    m = re.search(r"-v(\d{3,5})\.md$", name)
    return m.group(1) if m else None


def collect() -> list[dict]:
    """Build unified candidate list from filesystem + KG, deduped by number."""
    candidates: dict[str, dict] = {}

    # 1. filesystem projections
    for sub in SUBDIRS:
        for md in sorted((AUDIT_DIR / sub).glob("*.md")):
            num = number_from_name(md.name)
            key = num or f"file:{md.name}"
            candidates[key] = {
                "plan_number": num,
                "title": title_from_md(md),
                "status": "archived",
                "metadata": {
                    "origin_subdir": sub,
                    "source_path": str(md.relative_to(REPO)),
                    "file_mtime": datetime.fromtimestamp(
                        md.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "resolution_state": "unknown",
                },
            }

    # 2. KG entities (A2: link, don't duplicate)
    for ent in kg_plan_entities():
        eid = str(ent.get("entity_id", "")) if isinstance(ent, dict) else ""
        num = eid if re.fullmatch(r"\d{3,5}", eid) else None
        key = num or f"kg:{ent.get('id')}"
        if key in candidates:
            # enrich the fs-derived row with the KG link
            candidates[key]["metadata"]["kg_entity_id"] = ent.get("id")
            continue
        candidates[key] = {
            "plan_number": num,
            "title": (ent.get("name") or "")[:200] or f"(untitled {eid})",
            "status": "archived",
            "metadata": {
                "origin_subdir": "kg-only",
                "kg_entity_id": ent.get("id"),
                "kg_status": ent.get("status") or "",
                "resolution_state": "unknown",
            },
        }
    return list(candidates.values())


def existing_numbers() -> set[str]:
    # Read through the compat view (backed by canonical blueprints_history,
    # V171) — the legacy table is frozen post-V176 and must not be consulted
    # for idempotency, or reruns would re-insert rows that already exist
    # canonically.
    out = psql("SELECT DISTINCT plan_number FROM nebula.implementation_plans;",
               tuples=True)
    return {line.split("\t")[0] for line in out.splitlines() if line}


def insert_row(c: dict) -> str:
    pid = str(uuid.uuid4())
    num = c["plan_number"] or c["metadata"].get("kg_entity_id", "")[:8]
    title = c["title"].replace("'", "''")
    # Payload fold (same mapping as conduit's upsertPlan, PR #302). The
    # legacy implementation_plans_history table is frozen post-V176 — direct
    # writes there no longer propagate anywhere and would silently vanish.
    payload = json.dumps({
        "goal": "",
        "tags": ["historical-reconcile"],
        "source": "historical-reconcile",
        "metadata": c["metadata"],
    }).replace("'", "''")
    return (
        "INSERT INTO nebula.blueprints_history "
        "(id, plan_number, title, payload, blueprint_status) VALUES ("
        f"'{pid}', '{num}', '{title}', '{payload}'::jsonb, 'archived') "
        f"ON CONFLICT (plan_number) DO NOTHING;"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write rows (default dry-run)")
    args = ap.parse_args()

    rows = collect()
    have = existing_numbers()
    todo = [r for r in rows if (r["plan_number"] or f"kg:{r['metadata'].get('kg_entity_id')}") not in have
            and r["plan_number"] not in have]
    # kg-only entries without numeric ids get synthetic keys; check them too
    todo = [r for r in rows if r["plan_number"] and r["plan_number"] not in have]

    print(f"sources: {len(rows)} candidates, {len(have)} existing numbers, {len(todo)} to insert")
    by_origin = {}
    for r in todo:
        by_origin[r["metadata"]["origin_subdir"]] = by_origin.get(r["metadata"]["origin_subdir"], 0) + 1
    print("by origin:", json.dumps(by_origin))

    if args.apply:
        for r in todo:
            psql(insert_row(r))
        print(f"APPLIED: {len(todo)} rows inserted")
    else:
        for r in todo[:10]:
            print(" would insert:", r["plan_number"], "|", r["metadata"]["origin_subdir"], "|", r["title"][:50])
        print("(dry-run; --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
