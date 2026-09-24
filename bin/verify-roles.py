#!/usr/bin/env python3
"""
verify-roles.py — end-to-end role-surface verification (b80f0bdf).

Reads the canonical role expectations from config/roles/roles.json and
verifies every expected surface against the LIVE system:

  persona         tackle.prompts has a persona row for the role
  procedures      tackle.role_memory has >= 1 active (expiration_dt IS NULL) card
  assemblyAlias   assembly-srv user row exists with alias == role name
  harnessFile     config/harnesses/opencode/agents/<role>.md exists
  nebulaCheck     nebula.agent_records role CHECK allows the role (case-insensitive match)
  governance      harness-srv KNOWN_EXECUTORS allowlist contains the role

Exit 0 = all expected surfaces present; exit 1 = one or more FAIL.
WARN = informational (unexpected presence, case-variant notes) — not fatal.

Usage: bin/verify-roles.py [--json]
"""
import argparse
import json
import os
import re
import sys
import urllib.request

PG_DSN = os.environ.get(
    "NEXUS_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus"
)
ASSEMBLY_URL = os.environ.get("ASSEMBLY_URL", "http://localhost:3107")
ROLES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "roles", "roles.json"
)
GOVERNANCE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "typescript",
    "harness-srv",
    "src",
    "governance.ts",
)
HARNESS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "config",
    "harnesses",
    "opencode",
    "agents",
)


def pg_query(sql):
    import subprocess

    out = subprocess.run(
        ["psql", PG_DSN, "-tA", "-c", sql], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    spec = json.load(open(ROLES_FILE))
    defaults = spec["roleDefaults"]
    expectations = {
        role: {**defaults, **(cfg or {})}
        for role, cfg in spec["roles"].items()
    }

    # ── Load live surfaces ───────────────────────────────────────────
    db_roles = set(pg_query("SELECT name FROM tackle.roles").splitlines())

    # nebula.agent_records role CHECK — extract the role list
    check_sql = (
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'agent_records_role_check'"
    )
    check_def = pg_query(check_sql)
    check_roles = set(            re.findall(r"'([A-Za-z0-9][A-Za-z0-9-]*)'(?:::\w+)?", check_def.split("CHECK", 1)[1])
    )

    persona_roles = set(
        pg_query("SELECT DISTINCT role FROM tackle.prompts").splitlines()
    )
    proc_roles = set(
        pg_query(
            "SELECT DISTINCT role FROM tackle.role_memory "
            "WHERE expiration_dt IS NULL"
        ).splitlines()
    )

    try:
        assembly_users = {u["name"] for u in fetch_json(f"{ASSEMBLY_URL}/api/users")}
    except Exception as e:  # noqa: BLE001
        print(f"!! assembly-srv unreachable: {e}", file=sys.stderr)
        assembly_users = None

    harness_files = {
        f[:-3] for f in os.listdir(HARNESS_DIR) if f.endswith(".md")
    }

    gov_text = open(GOVERNANCE_FILE).read()
    m = re.search(r"KNOWN_EXECUTORS\s*=\s*new Set\(\[(.*?)\]\)", gov_text, re.S)
    gov_roles = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()

    # CI-ephemeral roles are minted by wr-conf E2E grant suites on throwaway
    # databases and dropped with them; they must never enter the expectations
    # file, and their presence in a long-lived DB is reportable but not a
    # coverage failure.
    CI_EPHEMERAL_PREFIXES = ("wr-conf-",)

    # ── Verify ───────────────────────────────────────────────────────
    results = []
    unknown = sorted(
        r
        for r in db_roles - set(expectations)
        if not r.startswith(CI_EPHEMERAL_PREFIXES)
    )
    unseeded = sorted(set(expectations) - db_roles)

    for role, exp in sorted(expectations.items()):
        if role not in db_roles:
            results.append((role, "FAIL", "not present in tackle.roles"))
            continue
        fails, warns = [], []
        low = role.lower()
        in_db = db_roles
        in_check = check_roles | {r.lower() for r in check_roles}
        in_harness = harness_files | {f.lower() for f in harness_files}
        in_assembly = assembly_users or set()

        if exp.get("persona") and role not in persona_roles:
            fails.append("persona missing (tackle.prompts)")
        if exp.get("procedures") and role not in proc_roles:
            fails.append("no active procedure cards (tackle.role_memory)")
        if exp.get("assemblyAlias"):
            if in_assembly is None:
                warns.append("assembly unreachable — skipped")
            elif role not in in_assembly:
                fails.append("assembly alias missing")
        if exp.get("harnessFile") and low not in in_harness:
            fails.append("harness agent file missing")
        if exp.get("nebulaCheck"):
            if role not in check_roles and low not in in_check:
                fails.append("not allowed by nebula.agent_records role CHECK")
            elif role not in check_roles and low in in_check:
                warns.append(
                    f"nebula CHECK only allows lowercase '{low}' (case mismatch — needs ratification)"
                )
        if exp.get("governance") and role not in gov_roles:
            fails.append("not in harness-srv KNOWN_EXECUTORS")

        if role in persona_roles and not exp.get("persona"):
            warns.append("persona present but not expected")
        if role in proc_roles and not exp.get("procedures"):
            warns.append("procedure cards present but not expected")
        if in_assembly and role in in_assembly and not exp.get("assemblyAlias"):
            warns.append("assembly alias present but not expected")
        if role in gov_roles and not exp.get("governance"):
            warns.append("in KNOWN_EXECUTORS but not expected")

        status = "FAIL" if fails else ("WARN" if warns else "PASS")
        results.append((role, status, "; ".join(fails + warns)))

    # ── Report ───────────────────────────────────────────────────────
    if args.json:
        print(
            json.dumps(
                {
                    "results": [
                        {"role": r, "status": s, "detail": d} for r, s, d in results
                    ],
                    "unknownRoles": unknown,
                    "rolesMissingFromDB": unseeded,
                },
                indent=1,
            )
        )
        sys.exit(1 if any(s == "FAIL" for _, s, _ in results) else 0)

    print(f"tackle.roles ({len(db_roles)}): {', '.join(sorted(db_roles))}")
    print(f"expectations file: {len(expectations)} roles")
    print()
    for role, status, detail in results:
        print(f"  [{status:4}] {role:18} {detail}")
    if unknown:
        print(f"\n⚠ roles in DB not in expectations file: {', '.join(unknown)}")
    if unseeded:
        print(f"⚠ expectations not in DB: {', '.join(unseeded)}")
    fails = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"\n{len(results) - fails}/{len(results)} roles fully covered")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
