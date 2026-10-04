#!/usr/bin/env python3
"""Export live role-provisioning state as a canonical, INDEPENDENT vintage seed.

WHY THIS EXISTS
---------------
The guards-db CI tier (Ruling 22 §7, engineer commit df6eff7db) runs the
file<->DB parity guards against a real PostgreSQL seeded from
sql/ci-bootstrap/nexus-ci-bootstrap.sql. That bootstrap creates the roles
SCHEMAS with ZERO role rows, and nothing in the repo seeds them -- so the job
deliberately exits 1 with an escalation rather than fake its way green.

The escalation (engineer-iii record 0e5af833) asked WHERE a canonical,
independent role seed comes from, and correctly refused to seed from
config/roles/roles.json: that file is the guards' own subject, so a seed
derived from it would make the primary assertion tautological.

THE DECISION (DBA, 2026-10-04)
------------------------------
The seed is a VINTAGE SNAPSHOT OF LIVE provisioning state, exported by this
script. Not roles.json (tautology), not hand-maintained (drift).

The snapshot covers the FULL provisionRole() footprint, because that is what
the tiered guards actually inspect:

  tackle.roles            delivery vocabulary (parity guard A1/A2)
  tackle.memory           procedure-card BODIES (verify-roles "cards present")
  tackle.role_memory      role<->card assignments with validity ranges
  tackle.prompts          personas (verify-rows "persona present")
  tackle.role_tool_access MCP tool grants

Exporting roles alone was the first draft, verified end-to-end: the parity
guard passes 8/8 on bootstrap+seed, but the transitional guard then fails on
missing personas -- verify-roles requires each closed role to have a persona,
active cards, and grants, not merely a name row. Live ground truth is 27/28
fully covered with sound-technician the named exception; a faithful snapshot
reproduces exactly that.

Independence is preserved because the seed is generated FROM THE DATABASE, by
the role that owns the database, at a recorded vintage -- never derived from
the files the guards check. Re-exporting is a DBA action (this script), not a
build step, so a registry edit can never silently reshape the seed.

EPHEMERAL ROWS
--------------
The snapshot is FAITHFUL: every row present at export time is written, rows
like `wr-conf-016-*` (CI-ephemeral probe roles minted by the E2E grant suites)
included. Filtering them out here would put a second copy of
EPHEMERAL_PREFIXES knowledge in a second file; the parity guard already owns
that convention and excludes them at assertion time.

USAGE
-----
  python3 bin/export_tackle_roles_seed.py                       # default DSN
  python3 bin/export_tackle_roles_seed.py --dsn postgresql://... --out path

Writes the seed to sql/ci-bootstrap/tackle_roles_vintage_seed.sql by default.
Exits non-zero without writing if tackle.roles is empty (an empty vocabulary
is the silent-drop failure the whole guard family exists to prevent).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    print("error: psycopg2 is required (the guards' own dependency)", file=sys.stderr)
    raise SystemExit(2)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DSN = os.environ.get("NEXUS_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_OUT = REPO / "sql" / "ci-bootstrap" / "tackle_roles_vintage_seed.sql"

EPHEMERAL_PREFIXES = ("wr-conf-",)


def redact(dsn: str) -> str:
    """DSN safe for the header: keep host/db, drop credentials."""
    if "@" in dsn:
        scheme, rest = dsn.split("://", 1)
        creds, hostpart = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{hostpart}"
    return dsn


def lit(value) -> str:
    """SQL literal for text.

    Deliberately NOT psycopg2's adapt(): its QuotedString str() encodes
    latin-1 by default and raises on em-dashes etc. in role descriptions.
    With standard_conforming_strings=on (Postgres default since 9.1) a
    single-quoted literal treats backslashes literally, so doubling the
    single quotes is the complete escaping rule for '...'.
    """
    if value is None:
        return "NULL"
    if not isinstance(value, str):
        value = str(value)
    return "'" + value.replace("'", "''") + "'"


def ts(value) -> str:
    """SQL literal for a timestamptz value (or NULL)."""
    if value is None:
        return "NULL"
    return lit(value.isoformat())


def arr(values) -> str:
    """SQL literal for a text[] column."""
    if values is None:
        return "ARRAY[]::text[]"
    if not values:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(lit(v) for v in values) + "]::text[]"


def jsonb(value) -> str:
    """SQL literal for a jsonb column (value arrives as a Python object)."""
    if value is None:
        return "'{}'::jsonb"
    return lit(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def fetch(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            roles = fetch(cur, "SELECT name, description FROM tackle.roles ORDER BY name")
            if not roles:
                print("error: tackle.roles is EMPTY -- refusing to write a zero-row "
                      "seed. An empty vocabulary would re-create in CI the exact "
                      "silent-drop failure the parity guards exist to prevent.",
                      file=sys.stderr)
                return 1

            memory = fetch(cur, """
                SELECT id, slug, title, summary, body_md, tags, triggers, mcp_tools
                FROM tackle.memory ORDER BY slug""")
            prompts = fetch(cur, """
                SELECT role, slug, version, title, body_md, parameter_schema, tags
                FROM tackle.prompts ORDER BY role, slug, version""")
            role_memory = fetch(cur, """
                SELECT memory_id, role, as_of_dt, expiration_dt
                FROM tackle.role_memory ORDER BY role, memory_id, as_of_dt""")
            grants = fetch(cur, """
                SELECT role, mcp_id, tool_slug FROM tackle.role_tool_access
                ORDER BY role, mcp_id, tool_slug""")
    finally:
        conn.close()

    names = [r[0] for r in roles]
    ephemeral = [n for n in names if n.startswith(EPHEMERAL_PREFIXES)]
    vintage = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        w = f.write

        w(f"""-- Canonical role-provisioning seed: VINTAGE SNAPSHOT of live state.
--
-- Generated by bin/export_tackle_roles_seed.py -- DO NOT EDIT BY HAND.
-- A hand edit would silently break the seed's independence from the registry
-- files the parity guards check, which is the entire reason it exists.
--
-- Vintage (UTC):        {vintage}
-- Source:               {redact(args.dsn)}
-- tackle.roles          {len(roles)} rows ({len(ephemeral)} CI-ephemeral: {', '.join(sorted(ephemeral)) or 'none'})
-- tackle.memory         {len(memory)} rows (procedure-card bodies; ids PRESERVED -- role_memory references them)
-- tackle.prompts        {len(prompts)} rows (personas)
-- tackle.role_memory    {len(role_memory)} rows (role<->card assignments)
-- tackle.role_tool_access {len(grants)} rows (MCP grants)
--
-- Ephemeral probe roles are kept for vintage fidelity; the parity guards
-- exclude them at assertion time via EPHEMERAL_PREFIXES and own that
-- convention. This file deliberately does not filter.
--
-- Decision record: engineer-iii escalation 0e5af833 asked where a canonical
-- independent role seed comes from; DBA answered "the live provisioning state
-- itself, snapshotted" (see the exporter docstring for the full reasoning).
-- The guards-db job applies this file right after nexus-ci-bootstrap.sql.
--
-- Idempotency: keyed on each table's natural/unique key (roles.name,
-- memory.id, prompts (role,slug,version), grants (role,mcp_id,tool_slug));
-- role_memory uses INSERT..SELECT WHERE NOT EXISTS because its uniqueness is
-- an EXCLUDE constraint over a validity RANGE, which ON CONFLICT cannot
-- target cleanly. UUIDs are preserved for memory (FK target) and regenerated
-- everywhere else (FKs reference roles(name), so fresh ids are correct).
""")

        # 1. roles -- the delivery vocabulary. Everything else FKs off these names.
        w("INSERT INTO tackle.roles (name, description) VALUES\n  ")
        w(",\n  ".join(f"({lit(n)}, {lit(d)})" for n, d in roles))
        w("\nON CONFLICT (name) DO NOTHING;\n\n")

        # 2. memory -- card BODIES. ids preserved: role_memory.memory_id -> memory.id.
        if memory:
            w("INSERT INTO tackle.memory (id, slug, title, summary, body_md, tags, triggers, mcp_tools) VALUES\n  ")
            w(",\n  ".join(
                f"({lit(mid)}, {lit(slug)}, {lit(title)}, {lit(summary)}, {lit(body)}, "
                f"{arr(tags)}, {arr(triggers)}, {arr(tools)})"
                for mid, slug, title, summary, body, tags, triggers, tools in memory))
            w("\nON CONFLICT (id) DO NOTHING;\n\n")

        # 3. prompts -- personas. verify-roles requires one per closed role.
        if prompts:
            w("INSERT INTO tackle.prompts (role, slug, version, title, body_md, parameter_schema, tags) VALUES\n  ")
            w(",\n  ".join(
                f"({lit(role)}, {lit(slug)}, {int(ver)}, {lit(title)}, {lit(body)}, "
                f"{jsonb(schema)}, {arr(tags)})"
                for role, slug, ver, title, body, schema, tags in prompts))
            w("\nON CONFLICT (role, slug, version) DO NOTHING;\n\n")

        # 4. role_memory -- assignments. EXCLUDE-constrained; WHERE NOT EXISTS.
        if role_memory:
            w("-- role_memory: validity-range uniqueness is an EXCLUDE constraint,\n"
              "-- which ON CONFLICT cannot target; existence-guard instead.\n")
            for mid, role, as_of, exp in role_memory:
                w("INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt) "
                  "SELECT {mid}, {role}, {as_of}, {exp} "
                  "WHERE NOT EXISTS (SELECT 1 FROM tackle.role_memory r "
                  "WHERE r.memory_id = {mid} AND r.role = {role} AND r.as_of_dt = {as_of});\n".format(
                      mid=lit(str(mid)), role=lit(role), as_of=ts(as_of), exp=ts(exp)))
            w("\n")

        # 5. role_tool_access -- MCP grants.
        if grants:
            w("INSERT INTO tackle.role_tool_access (role, mcp_id, tool_slug) VALUES\n  ")
            w(",\n  ".join(f"({lit(role)}, {lit(mcp)}, {lit(tool)})" for role, mcp, tool in grants))
            w("\nON CONFLICT (role, mcp_id, tool_slug) DO NOTHING;\n")

    print(f"wrote {out} (vintage {vintage}: {len(roles)} roles, {len(memory)} memory, "
          f"{len(prompts)} prompts, {len(role_memory)} assignments, {len(grants)} grants, "
          f"{len(ephemeral)} ephemeral roles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
