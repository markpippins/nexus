#!/usr/bin/env python3
"""Seed the DBA opencode-persona into tackle.prompts from the checked-in file.

Ratification-gated helper for the DBA charter (architect record 2dca56d4:
DBA authors -> Supervisor validates -> Architect ratifies). The single
source of the persona body is config/harnesses/opencode/agents/dba.md;
this loader exists so seeding is mechanical and drift-free — the markdown
is never hand-copied into SQL.

Default is DRY-RUN: prints exactly what would be inserted and touches
nothing. --apply performs the insert (new version = max+1) and no-ops if
the latest version already carries the identical body hash.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PERSONA_PATH = REPO / "config" / "harnesses" / "opencode" / "agents" / "dba.md"
ROLE = "DBA"
SLUG = "opencode-persona"
TITLE = "DBA — role charter (opencode-persona)"
TAGS = ["opencode-persona", "role-charter", "v0.1-draft"]

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"


def load_persona_body(path: Path = PERSONA_PATH) -> str:
    """Return the persona markdown body with YAML frontmatter stripped."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\A---\n.*?\n---\n", text, flags=re.DOTALL)
    body = text[m.end():] if m else text
    body = body.lstrip("\n")
    if not body.startswith("# Role: DBA"):
        raise ValueError(
            f"persona body must start with '# Role: DBA' (got: {body[:40]!r})"
        )
    return body


def plan_version(existing: list[tuple[int, str]], body: str) -> dict | None:
    """Pure version planner: given (version, body_sha256) rows, decide action.

    Returns None for a no-op (identical body already at latest version),
    or a dict describing the insert. Raises on a duplicate-version input.
    """
    if not existing:
        return {"action": "insert", "version": 1, "reason": "no prior persona"}
    versions = [v for v, _ in existing]
    if len(versions) != len(set(versions)):
        raise ValueError(f"duplicate versions in tackle.prompts for {ROLE}/{SLUG}")
    latest_v, latest_hash = max(existing, key=lambda t: t[0])
    if latest_hash == hashlib.sha256(body.encode()).hexdigest():
        return None
    return {
        "action": "insert",
        "version": latest_v + 1,
        "reason": f"body differs from latest version {latest_v}",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="perform the insert (default: dry-run)")
    ap.add_argument("--dsn", default=os.environ.get("SRCDSN", DEFAULT_DSN))
    args = ap.parse_args()

    body = load_persona_body()
    print(f"persona source : {PERSONA_PATH.relative_to(REPO)}")
    print(f"body           : {len(body)} bytes, sha256 {hashlib.sha256(body.encode()).hexdigest()[:12]}")

    import psycopg2  # imported after arg parsing so --help needs no driver

    with psycopg2.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT version, encode(digest(body_md, 'sha256'), 'hex') "
            "FROM tackle.prompts WHERE role=%s AND slug=%s ORDER BY version",
            (ROLE, SLUG),
        )
        rows = [(v, h) for v, h in cur.fetchall()]
        plan = plan_version(rows, body)
        if plan is None:
            print("no-op: latest version already carries this exact body")
            return 0
        print(f"plan           : {plan['action']} version {plan['version']} "
              f"({plan['reason']})")
        if not args.apply:
            print("DRY-RUN: nothing written. Pass --apply after ratification.")
            return 0
        cur.execute(
            "INSERT INTO tackle.prompts (role, slug, version, title, body_md, tags) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (ROLE, SLUG, plan["version"], TITLE, body, TAGS),
        )
    print(f"APPLIED: {ROLE}/{SLUG} v{plan['version']} seeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
