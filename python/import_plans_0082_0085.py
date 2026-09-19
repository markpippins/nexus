#!/usr/bin/env python3
"""
Import the 4 docs-only completed plans (0082-0085) from
nexus/audit/PLANS/completed/*.md into nebula.implementation_plans.

These plans have full markdown docs with frontmatter (project, dependencies,
acceptance) but were never migrated to the DB. Status = completed (they live
in the completed/ folder).

Usage:
  python3 import_plans_0082_0085.py --dry-run
  python3 import_plans_0082_0085.py --write
"""

import argparse
import json
import os
import re
import subprocess
import sys

AUDIT_PLANS = "/home/codex/dev/nexus/audit/PLANS/completed"
FILES = {
    "0082": "0082-nebula-localstorage-to-postgres-migration.md",
    "0083": "0083-conduit-markdown-metadata-to-postgres.md",
    "0084": "0084-rename-conduit-io-to-conduit.md",
    "0085": "0085-migrate-mysql-to-postgres-nexus.md",
}

PG_ENV = dict(os.environ)
PG_ENV["PGPASSWORD"] = "pgpass"


def psql(sql: str) -> tuple:
    r = subprocess.run(
        ["psql", "-h", "localhost", "-p", "5432", "-U", "pguser", "-d", "nexus",
         "-t", "-A", "-c", sql],
        capture_output=True, text=True, env=PG_ENV,
    )
    if r.returncode != 0:
        print(f"PSQL ERROR: {r.stderr[:400]}", file=sys.stderr)
    return r.stdout.strip(), r.returncode


def sq(s: str) -> str:
    """Single-quoted SQL string literal (escapes embedded quotes)."""
    return "'" + s.replace("'", "''") + "'"


def sq_json(v) -> str:
    """JSON value as a single-quoted ::jsonb literal."""
    return sq(json.dumps(v, ensure_ascii=False)) + "::jsonb"


def parse_frontmatter(content: str) -> tuple:
    """Return (meta: dict, body: str)."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
    if not m:
        return {}, content
    raw, body = m.groups()
    meta = {}
    current_list_key = None
    for line in raw.splitlines():
        if re.match(r"^[a-z_]+:", line):
            key, _, val = line.partition(":")
            current_list_key = key.strip()
            val = val.strip()
            if val:
                # parse simple scalar (strip quotes)
                meta[current_list_key] = val.strip('"\'')
            else:
                meta[current_list_key] = []
        elif line.startswith("  - ") and current_list_key:
            meta[current_list_key].append(line[4:].strip().strip('"\''))
    return meta, body


def extract_plan(content: str, plan_number: str) -> dict:
    meta, body = parse_frontmatter(content)

    # Title from "# Plan 0082: ..." or first H1
    title = ""
    m = re.search(r"^#\s+Plan\s+\d+:\s*(.+)$", body, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            title = m.group(1).strip()

    # Goal from "**Goal:** ..." (may span lines until blank or next bold)
    goal = ""
    m = re.search(r"\*\*Goal:\*\*\s*(.+?)(?:\n\n|\n\*\*|\Z)", body, re.DOTALL)
    if m:
        goal = re.sub(r"\s+", " ", m.group(1)).strip()

    acceptance = meta.get("acceptance", [])
    if isinstance(acceptance, str):
        acceptance = [acceptance]
    deps = meta.get("dependencies", [])
    if isinstance(deps, str):
        deps = [deps] if deps else []

    return {
        "plan_number": plan_number,
        "title": title or f"Plan {plan_number}",
        "goal": goal,
        "content": content,  # full markdown incl. frontmatter
        "files_affected": [],  # docs don't declare explicit file list beyond acceptance
        "acceptance_criteria": acceptance,
        "dependencies": deps,
        "status": "completed",
        "tags": ["historic", "audit-plans", "pre-conduit"],
        "project": meta.get("project", ""),
        "source": "audit/PLANS/completed",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    plans = []
    for pn, fn in sorted(FILES.items()):
        path = os.path.join(AUDIT_PLANS, fn)
        if not os.path.exists(path):
            print(f"  !! MISSING: {path}")
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        plans.append(extract_plan(content, pn))

    print(f"Parsed {len(plans)} plan docs:")
    for p in plans:
        print(f"  {p['plan_number']}: {p['title'][:60]} | status={p['status']} | "
              f"acceptance={len(p['acceptance_criteria'])} | project={p['project']}")

    if args.dry_run:
        print("\nDRY RUN — no DB writes")
        return

    # Check for existing rows (idempotency) — read through the compat view,
    # which is backed by canonical nebula.blueprints_history (V171).
    existing, _ = psql(
        "SELECT plan_number FROM nebula.implementation_plans "
        "WHERE plan_number IN ('0082','0083','0084','0085')"
    )
    existing_set = set(existing.split("\n")) if existing else set()

    inserted = 0
    for p in plans:
        if p["plan_number"] in existing_set:
            print(f"  SKIP {p['plan_number']} (already in DB)")
            continue
        meta = {"source": p["source"], "project": p["project"]}
        meta = {k: v for k, v in meta.items() if v}
        tags_sql = ",".join(sq(t) for t in p["tags"])
        # Write to canonical blueprints_history with the payload fold
        # (same mapping as conduit's upsertPlan, PR #302). The legacy
        # implementation_plans surface is a read-only compat view over the
        # folded payload (V171); its mirror trigger retired in V176.
        import json as _json
        payload_sql = _json.dumps({
            "goal": p["goal"],
            "content": p["content"],
            "files_affected": [],
            "acceptance_criteria": p["acceptance_criteria"],
            "dependencies": [],
            "tags": p["tags"],
            "project": p["project"] or None,
            "source": p["source"],
        }).replace("'", "''")
        sql = f"""
            INSERT INTO nebula.blueprints_history
                (plan_number, title, payload, blueprint_status)
            VALUES (
                {sq(p['plan_number'])},
                {sq(p['title'])},
                '{payload_sql}'::jsonb,
                {sq(p['status'])}
            )
        """
        _, rc = psql(sql)
        if rc == 0:
            inserted += 1
            print(f"  INSERT {p['plan_number']}")
        else:
            print(f"  FAIL {p['plan_number']}")

    # Verify
    verify, _ = psql(
        "SELECT plan_number, title, status FROM nebula.implementation_plans "
        "WHERE plan_number IN ('0082','0083','0084','0085') ORDER BY plan_number"
    )
    print(f"\n=== DB after import (inserted={inserted}) ===")
    for row in (verify.split("\n") if verify else []):
        print(f"  {row}")


if __name__ == "__main__":
    main()
