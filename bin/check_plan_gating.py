#!/usr/bin/env python3
"""W-B4 evidence prototype: evaluate structured plan-gating specs against
live state.

Reads a gating spec (bin/plan-gating-specs/*.json) and computes each
plan's start_conditions against live evidence:

  - db_table_exists: table resolves in the nexus DB (psql to_regclass)
  - record_exists:   nebula.agent_records row with that id prefix
  - plan_status_in:  conduit /state derivedStatus ∈ accept list

and cross-checks the result against Conduit's *actual* ticket surface
(open/expired/failed tickets on the same plan), emitting per plan:

  - condition verdicts (met/unmet with evidence refs)
  - gating class: STARTABLE | BLOCKED-AND-OPEN | BLOCKED-BUT-OPEN
    (expired ticket) | IN-REWORK | UNKNOWN (tool error)
  - W-B4 duplication findings: parents whose scope is carried by
    children (duplicate-parent role)

This is the DBA evidence prototype for package W-B4 (structured
start-condition gating + family/supersession links). It lands nothing:
plan metadata schema, ticket-spawning behavior, and any enforcement are
the planner's ratification call. Exit codes: 0 = all plans consistent
with expected_gating, 1 = any mismatch/finding, 2 = tool error.

DSN/STATE env overrides: SRCDSN, CONDUIT_STATE_URL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DSN = os.environ.get("SRCDSN",
                     "postgresql://pguser:pgpass@localhost:5432/nexus")
STATE_URL = os.environ.get("CONDUIT_STATE_URL", "http://localhost:3100/state")


def _psql_scalar(sql: str) -> str | None:
    ran = subprocess.run(["psql", DSN, "-Atc", sql],
                         capture_output=True, text=True, timeout=30)
    if ran.returncode != 0:
        raise RuntimeError(f"psql failed: {ran.stderr.strip()[-200:]}")
    return (ran.stdout or "").strip()


def ev_db_table_exists(params: dict) -> bool:
    table = params["table"].replace("'", "''")
    return _psql_scalar(
        f"select to_regclass('{table}') is not null;") == "t"


def ev_record_exists(params: dict) -> bool:
    id8 = params["record_id8"].replace("'", "")
    return _psql_scalar(
        "select count(*) from nebula.agent_records "
        f"where id::text like '{id8}%';") not in ("", "0")


def load_conduit_state() -> dict:
    """plan_number -> derivedStatus, from conduit /state (via urllib to
    stay dependency-free)."""
    import urllib.request
    with urllib.request.urlopen(STATE_URL, timeout=15) as resp:
        state = json.load(resp)
    out = {}
    for plans in (state.get("plans") or {}).values():
        for p in plans:
            out[str(p.get("planNumber"))] = {
                "derived": p.get("derivedStatus") or p.get("status"),
                "tickets": p.get("ticketStatuses") or {},
            }
    return out


EVALUATORS = {
    "db_table_exists": ev_db_table_exists,
    "record_exists": ev_record_exists,
}


def evaluate_conditions(conditions: list[dict], conduit_state: dict) -> tuple[list[dict], bool]:
    verdicts = []
    tool_error = False
    for cond in conditions:
        ev = EVALUATORS.get(cond["evaluator"])
        if ev is None:
            if cond["evaluator"] == "plan_status_in":
                plan = cond["params"]["plan"]
                entry = conduit_state.get(plan)
                derived = (entry or {}).get("derived")
                met = derived in cond["params"]["accept"]
                verdicts.append({**cond, "met": met, "observed": derived})
            else:
                verdicts.append({**cond, "met": None,
                                 "observed": f"unknown evaluator {cond['evaluator']}"})
                tool_error = True
            continue
        try:
            met = bool(ev(cond["params"]))
        except Exception as exc:  # noqa: BLE001
            verdicts.append({**cond, "met": None, "observed": str(exc)[:160]})
            tool_error = True
            continue
        verdicts.append({**cond, "met": met})
    return verdicts, tool_error


def classify(plan_spec: dict, verdicts: list[dict],
             conduit_entry: dict | None) -> tuple[str, list[str]]:
    """Two facets — conditions (READY/BLOCKED) x ticket surface
    (OPEN/EXPIRED/NONE) — collapse to one gating class:

      READY + OPEN     -> STARTABLE
      READY + EXPIRED  -> BLOCKED-BUT-OPEN (flow defect: no respawn)
      READY + NONE     -> UNKNOWN (state quirk)
      BLOCKED + ticket -> BLOCKED-AND-OPEN (the W-B4 exhibit: a plan
                          presents builder-ready work its own ACs forbid)
      BLOCKED + NONE   -> BLOCKED-CLOSED (gating working as intended)
    """
    findings: list[str] = []
    derived = (conduit_entry or {}).get("derived")
    tickets = (conduit_entry or {}).get("tickets") or {}
    has_open = any(t.get("status") == "open" for t in tickets.values())
    has_expired = any(t.get("status") == "expired" for t in tickets.values())
    unmet = [v["id"] for v in verdicts if v.get("met") is False]

    if derived == "REVIEW_REJECT":
        return "IN-REWORK", findings

    ready = not unmet
    if ready and has_open:
        return "STARTABLE", findings
    if ready and has_expired:
        findings.append("conditions met but the builder ticket expired with "
                        "no respawn (flow defect, walk-through finding)")
        return "BLOCKED-BUT-OPEN", findings
    if ready:
        findings.append("conditions met and no ticket row present (state quirk)")
        return "UNKNOWN", findings

    if has_open or has_expired:
        findings.append("ticket presents builder-ready work whose own start "
                        "conditions are unmet (the W-B4 exhibit)")
        return "BLOCKED-AND-OPEN", findings
    return "BLOCKED-CLOSED", findings


def main() -> int:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    base = Path(__file__).resolve().parent / "plan-gating-specs"
    try:
        path = Path(spec_path) if spec_path else sorted(base.glob("*.json"))[0]
        spec = json.loads(path.read_text())
        conduit_state = load_conduit_state()
    except Exception as exc:  # noqa: BLE001
        print(f"tool error: {exc}", file=sys.stderr)
        return 2

    report = {"spec": str(path), "plans": {}}
    mismatches = 0
    for plan_id, plan_spec in spec["plans"].items():
        verdicts, tool_error = evaluate_conditions(
            plan_spec.get("start_conditions", []), conduit_state)
        entry = conduit_state.get(plan_id)
        gating, findings = classify(plan_spec, verdicts, entry)
        if plan_spec.get("family", {}).get("role") == "duplicate-parent":
            findings.append(
                "duplicate-parent: scope fully carried by children "
                + ", ".join(plan_spec["family"]["children"])
                + " — candidate for supersession link")
        expected = plan_spec.get("expected_gating", "")
        match = expected.split(" ")[0].strip().upper() == gating
        if not match or tool_error:
            mismatches += 1
        report["plans"][plan_id] = {
            "gating": gating,
            "expected": expected,
            "consistent": match,
            "conditions": verdicts,
            "family": plan_spec.get("family", {}),
            "derived": (entry or {}).get("derived"),
            "findings": findings,
        }

    print(json.dumps(report, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
