#!/usr/bin/env python3
"""bin/lease-probe.py — daily synthetic lease-check probe (DBA, soak evidence).

The enforce-flip soak (thread 65fe85a8, soak review comment 42a11672) found
that at ~0.5 real /chat calls/day, warn-mode observations never reach
decision-grade volume by passive accumulation. This runner supplies the
missing evidence: one synthetic adoption resolution per observed role per
run, driven by a daily systemd timer.

Mechanics
---------
* POSTs /chat with {"probe": true, ...} — the operator_svc handler runs the
  lease check, logs the journal line (probe=synthetic), and returns WITHOUT
  calling the LLM or writing chat history. Probes are free and clean.
* Role list resolution: LEASE_PROBE_ROLES env (comma-separated) if set;
  otherwise the union of {roles seen in operator.prompts_responses} and
  {"operator"} — i.e. the roles that actually drive /chat traffic, probed
  where the evidence will be judged. Falls back to ["operator"] when the
  role query fails (never raise; degraded is a valid probe outcome).
* Exit code is ALWAYS 0 on completed runs: probe failures are data (the
  journal line carries outcome=error), not unit failures. Exit 2 only for
  usage errors. This mirrors the operator-lease ensure degradation stance:
  the timer must not look broken because a dependency blinked.
* --print emits the per-role outcome table to stdout for manual runs.

Usage:
    python3 bin/lease-probe.py                 # timer path
    python3 bin/lease-probe.py --print         # manual, see the table
    LEASE_PROBE_ROLES=dba,planner python3 bin/lease-probe.py --print
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

OPERATOR_BASE = os.environ.get("OPERATOR_BASE", "http://127.0.0.1:3018")
ROLE_DISCOVERY_SQL = (
    "SELECT DISTINCT role FROM operator.prompts_responses "
    "ORDER BY 1"
)
FALLBACK_ROLES = ["operator"]


def _log(line: str) -> None:
    """Structured line to stderr → journald (stderr of the oneshot unit)."""
    print(f"lease-probe {line}", file=sys.stderr)


def _http_post(url: str, payload: dict, timeout: int = 20) -> tuple:
    """Returns (status_code, parsed_json_or_None). Never raises on HTTP errors."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001 — body may be empty
            return e.code, None
    except Exception as e:  # noqa: BLE001 — transport errors are outcomes
        return 0, {"error": str(e)}


def _discover_roles() -> list:
    """Roles with /chat history (operator.prompts_responses) + operator."""
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser",
             "-d", "nexus", "-t", "-A", "-c", ROLE_DISCOVERY_SQL],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            roles = {line.strip() for line in result.stdout.splitlines() if line.strip()}
            roles.add("operator")
            return sorted(roles)
    except Exception as e:  # noqa: BLE001 — degrade to fallback
        _log(f"role-discovery error={e!r} falling back")
    return list(FALLBACK_ROLES)


def resolve_roles() -> list:
    """LEASE_PROBE_ROLES env wins; else live discovery; else fallback.

    Never raises: a discovery failure degrades to the fallback list — a
    short probe run is better than a failed unit and zero evidence.
    """
    env = (os.environ.get("LEASE_PROBE_ROLES") or "").strip()
    if env:
        return [r.strip() for r in env.split(",") if r.strip()]
    try:
        return _discover_roles()
    except Exception as e:  # noqa: BLE001 — degrade, never fail the unit
        _log(f"role-discovery raised error={e!r} falling back")
        return list(FALLBACK_ROLES)


def probe_role(role: str) -> dict:
    """One synthetic probe for one role. Never raises — returns the outcome row."""
    try:
        status, body = _http_post(
            f"{OPERATOR_BASE}/chat",
            {"message": "", "role": role, "probe": True},
        )
    except Exception as e:  # noqa: BLE001 — transport blowups are outcomes too
        status, body = 0, {"error": str(e)}
    lc = (body or {}).get("lease_check") or {}
    outcome = {
        "role": role,
        "http_status": status,
        "probe": True,
        "mode": lc.get("mode"),
        "adopted": lc.get("adopted"),
        "lease_ref": lc.get("lease_ref"),
        "reason": lc.get("reason") or (body or {}).get("error"),
    }
    _log("role={role} http={http} mode={mode} adopted={adopted} lease_ref={lease_ref}".format(
        role=role, http=status, mode=outcome["mode"],
        adopted=outcome["adopted"], lease_ref=outcome["lease_ref"],
    ))
    return outcome


def run(roles: list) -> list:
    """Probe every role; always returns the rows (never raises)."""
    return [probe_role(r) for r in roles]


def main() -> int:
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    do_print = "--print" in args

    roles = resolve_roles()
    if not roles:
        _log("no roles resolved — nothing to probe")
        return 0
    _log(f"start roles={','.join(roles)} base={OPERATOR_BASE}")
    rows = run(roles)
    if do_print:
        for r in rows:
            print(json.dumps(r, sort_keys=True))
    adopted = sum(1 for r in rows if r["adopted"])
    _log(f"done roles={len(rows)} adopted={adopted} unadopted={len(rows) - adopted}")
    return 0  # probe failures are data, not unit failures


if __name__ == "__main__":
    sys.exit(main())
