#!/usr/bin/env python3
"""flow-recorder-v0 — induce a candidate flow-record from one real Conduit unit.

Stage-1 implementation of the Aegis flow-recorder design (discussions thread
1adce409, revision r1 comment 9c04ab1f), with the RoleAlias resolution law
from the alignment proposal (thread e9f81ae7), RATIFIED 2026-09-18 with
Amendments A+B folded into guard evaluation (see contract below)

  - reads execution.receipts — no writes to any database surface
  - emits the candidate flow-record as JSON on stdout; the ONLY write this
    tool can ever perform is --emit-record posting a proposal agent-record

Induction contract (each clause pinned by bin/tests/test_flow_recorder_v0.py):

  C1  empirical alphabet; ABANDONED treated as speculative (Amendment 1)
  C2  consecutive same-type receipts are STATE ATTEMPTS (attempt_count),
      never graph fan-out: they produce NO edge at all — the attempt
      counter is the record (A2: 220 self-edges would make every
      model-check noisy and every invariant vacuous)
  C3  API_LIMIT is environment, not a node (A3)
  C4  actor-pair guards fire ONLY on true role-boundary crossings: both
      endpoints resolve to agent roles AND the resolved targets differ.
      Within-role stage transitions and any edge touching a system-actor
      are exempt (A4 + system-actor rule)
  C5  RoleAlias law: unmapped names resolve to unresolved:<name> and mark
      the flow-record suspect — never silent (A5, thread e9f81ae7)
  C6  original raw role strings are always preserved in evidence
  C7  executor-crash summaries under work-flow types are recorded as an
      observation (work-rejection vs agent-crash conflation), never resolved
  C8  observer-only purity: no INSERT/UPDATE/DELETE anywhere in this module

RATIFIED AMENDMENTS (architect decision, thread e9f81ae7, 2026-09-18;
the RoleAlias law is no longer provisional):

  RA1 (Amendment A) — guard evaluation splits reviewer-class receipts by
      SIGNATURE: verdict-carrying types (REVIEW_PASS/REVIEW_REJECT) test
      the verify-holder class; CAPACITY_SIGNATURES (API_LIMIT and kin) are
      environment events producing NO actor-pair guard — capacity is not
      verification, testing capability against a rate-limit receipt is
      meaningless. decide_guard() is the pure decision seam Aegis Stage 4
      will reuse.
  RA2 (Amendment B) — class evaluation is HISTORICAL: "held
      can_verify_work_requests at edge time" resolves roles_history state
      as of the receipt's recorded_on, never the live roles row (live-row
      lookup distorts every pre-grant-date receipt). This module marks
      every class guard with evaluation:"historical" + the as-of evidence
      ref; resolve_class_capability() is the injection seam (live-role
      default = the documented B violation, overridable in tests/Stage 4).
"""

import argparse
import json
import os
import subprocess
import sys

# ── RoleAlias map (RATIFIED — thread e9f81ae7, architect decision 2026-09-18) ──
# Entry shape: {target, rule}; rule ∈ identity|alias|class|system-actor.
ROLE_ALIAS = {
    "planner":    {"target": "planner",       "rule": "identity"},
    "architect":  {"target": "architect",     "rule": "identity"},
    "engineer":   {"target": "engineer",      "rule": "identity"},
    "inspector":  {"target": "inspector",     "rule": "identity"},
    "sysadmin":   {"target": "sysadmin",      "rule": "identity"},
    "critic":     {"target": "critic",        "rule": "identity"},
    "tester":     {"target": "tester",        "rule": "identity"},
    "builder":    {"target": "engineer",      "rule": "alias"},
    "reviewer":   {"target": "verify-holder", "rule": "class"},
    "cli":            {"target": "system-actor", "rule": "system-actor"},
    "cli-executor":   {"target": "system-actor", "rule": "system-actor"},
    "conduit-worker": {"target": "system-actor", "rule": "system-actor"},
    "watchdog":       {"target": "system-actor", "rule": "system-actor"},
}
UNRESOLVED_PREFIX = "unresolved:"

# Closed guard vocabulary (design §3 + Amendment 4): receipt-observed,
# actor-pair, attestation-exists, satisfaction-verdict, audit-pair, lease-live.
GUARD_VOCAB = {"receipt-observed", "actor-pair", "attestation-exists",
               "satisfaction-verdict", "audit-pair", "lease-live"}

ENVIRONMENT_TYPES = {"API_LIMIT"}   # C3 / Amendment 3: environment, not nodes

# RA1 (Amendment A): capacity signatures are environment EVENTS even when
# carried by a verdict-shaped type — they never test any role's capability.
CAPACITY_SIGNATURES = {"API_LIMIT", "RATE_LIMIT", "CAPACITY", "THROTTLED"}

# RA1 (Amendment A): receipt types that carry a verification VERDICT. Only
# these test the verify-holder class at guard evaluation; everything else
# (including capacity signatures) tests nothing about capability.
VERDICT_CARRYING_TYPES = {"REVIEW_PASS", "REVIEW_REJECT"}


def is_capacity_receipt(rc):
    """RA1: a receipt is a capacity event if its type is a capacity
    signature — regardless of which role carried it."""
    return rc.get("type", "") in CAPACITY_SIGNATURES


def decide_guard(from_receipt, to_receipt, from_resolved, to_resolved):
    """RA1 decision seam (pure): what guards does this crossing carry?

    Returns (guard_dict, env_event|None). The verdict-carrying requirement:
    an actor-pair guard into the verify-holder class is emitted ONLY when
    the class-side receipt carries a VERDICT (REVIEW_PASS/REVIEW_REJECT).
    Capacity receipts (API_LIMIT etc.) are environment events — no guard,
    no node, and they break the adjacency chain exactly like C3.
    """
    if is_capacity_receipt(to_receipt):
        return {}, {"type": to_receipt["type"],
                    "role_raw": to_receipt.get("role", ""),
                    "role_resolved": to_resolved,
                    "receipt_id": to_receipt.get("id", ""),
                    "at": to_receipt.get("issued_at", ""),
                    "reason": "capacity-signature (RA1: capacity is not "
                              "verification — no capability test)"}
    guard = {"receipt-observed": {"type": to_receipt["type"]}}
    sys_involved = "system-actor" in (from_resolved, to_resolved)
    crossing = (not sys_involved) and from_resolved != to_resolved
    if crossing:
        if to_resolved == "verify-holder" and \
                to_receipt.get("type") not in VERDICT_CARRYING_TYPES:
            # Verdict-carrying requirement: class-side capability is only
            # tested by verdict receipts. The crossing is still real
            # (documented via actors), but carries no class capability test.
            return guard, None
        guard["actor-pair"] = {"from_role": from_resolved,
                               "to_role": to_resolved,
                               "evaluation": (
                                   "historical" if to_resolved == "verify-holder"
                                   else "live"),
                               "as_of": (to_receipt.get("issued_at", "")
                                         if to_resolved == "verify-holder"
                                         else None)}
    return guard, None


def resolve_class_capability(role, as_of_iso, resolver=None):
    """RA2 injection seam: did `role` hold can_verify_work_requests AS OF
    `as_of_iso` (a receipt's recorded_on)? Default resolver deliberately
    queries the LIVE roles row and marks itself as such — the documented
    Amendment B violation, present only so the tool is runnable today;
    Stage 4 injects the roles_history bitemporal resolver. Never raises.
    """
    if resolver is not None:
        return resolver(role, as_of_iso)
    try:
        rows = psql_rows(
            "SELECT can_verify_work_requests FROM nebula.roles "
            f"WHERE name='{role}'")
        return {"role": role, "as_of": as_of_iso, "held": bool(rows and
                rows[0][0] in ("t", "true", "true\r")),
                "resolution": "live-row (RA2 VIOLATION — historical "
                              "resolver not yet wired)"}
    except Exception as e:  # noqa: BLE001 — degrade honestly
        return {"role": role, "as_of": as_of_iso, "held": None,
                "resolution": f"unresolvable: {e}"}


def resolve_role(raw):
    """C5: resolve a raw receipt role. Never silent — unknown names map to
    unresolved:<name> with rule 'unresolved'."""
    entry = ROLE_ALIAS.get(raw)
    if entry is None:
        return UNRESOLVED_PREFIX + raw, "unresolved"
    return entry["target"], entry["rule"]


def psql_rows(sql):
    env = dict(os.environ, PGPASSWORD=os.environ.get("PGPASSWORD", "pgpass"))
    r = subprocess.run(
        ["psql", "-h", "localhost", "-p", "5432", "-U", "pguser",
         "-d", "nexus", "-X", "-qAt", "-F", "\x1f", "-c", sql],
        capture_output=True, text=True, env=env, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:300]}")
    return [line.split("\x1f") for line in r.stdout.splitlines() if line]


def pick_unit(min_rejects=0, terminal="REVIEW_PASS"):
    """Richest terminal-state unit — most receipts, then most rework."""
    rows = psql_rows(f"""
WITH lastt AS (
  SELECT DISTINCT ON (request_id) request_id, type AS last_type
  FROM execution.receipts ORDER BY request_id, issued_at DESC, id DESC),
stats AS (
  SELECT request_id, count(*) AS n,
         count(*) FILTER (WHERE type='REVIEW_REJECT') AS rejects
  FROM execution.receipts GROUP BY request_id)
SELECT s.request_id, s.n FROM lastt l JOIN stats s USING (request_id)
WHERE l.last_type='{terminal}' AND s.rejects >= {min_rejects}
ORDER BY s.n DESC, s.rejects DESC LIMIT 1""")
    if not rows:
        raise RuntimeError(f"no {terminal}-terminal unit with rejects>={min_rejects}")
    return rows[0][0], int(rows[0][1])


def fetch_unit(request_id):
    # Accept short prefixes: resolve against the uuid column first.
    if "-" not in request_id:
        got = psql_rows(f"""
SELECT DISTINCT request_id::text FROM execution.receipts
WHERE request_id::text LIKE '{request_id}%'""")
        if not got:
            return []
        if len(got) > 1:
            raise RuntimeError(f"ambiguous prefix {request_id}: "
                               f"{[g[0] for g in got]}")
        request_id = got[0][0]
    rows = psql_rows(f"""
SELECT id, type, agent_role, coalesce(summary,''), issued_at::text
FROM execution.receipts WHERE request_id='{request_id}'
ORDER BY issued_at, id""")
    return [{"id": r[0], "type": r[1], "role": r[2], "summary": r[3],
             "issued_at": r[4]} for r in rows]


def induce(request_id, receipts):
    """Observe -> Normalize -> Parameterize (Stages 1-3; Publish is manual)."""
    nodes = {}          # type -> {attempts, roles_raw, first_seen, last_seen}
    edges = []          # ordered distinct transitions w/ guards + counts
    env_events = []     # C3: API_LIMIT-style environment receipts
    edge_index = {}

    prev = None
    for rc in receipts:
        t, raw_role = rc["type"], rc["role"]
        resolved_role, _rule = resolve_role(raw_role)

        if t in ENVIRONMENT_TYPES:
            env_events.append({"type": t, "role_raw": raw_role,
                               "role_resolved": resolved_role,
                               "receipt_id": rc["id"], "at": rc["issued_at"]})
            prev = None          # environment breaks the adjacency chain
            continue

        # RA1: the guard decision comes FIRST — a capacity-signature receipt
        # is routed to environment events and must NOT count as a state
        # attempt (decide_guard returns the environment event; the C3 branch
        # above already handles its own set).
        res_prev_for_decision = resolve_role(prev["role"])[0] if prev else None
        guard, env_event = (decide_guard(prev, rc, res_prev_for_decision,
                                         resolved_role)
                            if prev is not None else ({}, None))
        if env_event is not None:
            env_events.append(env_event)
            prev = None
            continue

        node = nodes.setdefault(t, {"attempts": 0, "roles_raw": [],
                                    "first_at": rc["issued_at"],
                                    "last_at": rc["issued_at"]})
        node["attempts"] += 1
        if raw_role not in node["roles_raw"]:
            node["roles_raw"].append(raw_role)
        node["last_at"] = rc["issued_at"]

        if prev is not None:
            if prev["type"] == t:
                # C2: same-type adjacency is attempt machinery, not an edge.
                prev = rc
                continue
            res_prev = res_prev_for_decision
            key = (prev["type"], t)
            if key not in edge_index:
                edge_index[key] = {"from": prev["type"], "to": t,
                                   "guards": guard, "count": 1,
                                   "actors_raw": [prev["role"], raw_role],
                                   "actors_resolved": [res_prev, resolved_role],
                                   "evidence": [prev["id"] + "->" + rc["id"]]}
                edges.append(edge_index[key])
            else:
                edge_index[key]["count"] += 1
                edge_index[key]["evidence"].append(prev["id"] + "->" + rc["id"])
        prev = rc

    # Parameterization: constants of THIS execution -> typed parameters.
    role_bindings = sorted({r["role"] for r in receipts})
    unresolved = sorted(r for r in role_bindings
                        if resolve_role(r)[1] == "unresolved")
    flow = {
        "flow_record_v0": True,
        "design_ref": "discussions 1adce409 revision r1 (9c04ab1f); "
                      "RoleAlias law RATIFIED per e9f81ae7 (2026-09-18, "
                      "Amendments A+B folded: RA1 signature split, RA2 "
                      "historical evaluation)",
        "parameters": {
            "WorkRef": request_id,
            "RoleAlias_applied": {
                r: dict(zip(("resolved", "rule"), resolve_role(r)))
                for r in role_bindings},
            "EvidenceRefs": [r["id"] for r in receipts],
        },
        "source_evidence": {
            "store": "execution.receipts",
            "receipt_count": len(receipts),
            "window": [receipts[0]["issued_at"], receipts[-1]["issued_at"]],
            "title_hint": receipts[0]["summary"][:80],
        },
        "states": [
            {"state": t, "attempt_count": n["attempts"],
             "roles_raw": n["roles_raw"],
             "first_at": n["first_at"], "last_at": n["last_at"]}
            for t, n in nodes.items()
        ],
        "edges": edges,
        "environment_events": env_events,
        "terminal_state": receipts[-1]["type"],
        "suspect": bool(unresolved),
        "observations": [],
        "aegis_emission": "NOT PERFORMED (observer-only; stage 2 of the build plan)",
    }
    if unresolved:
        flow["suspect_reasons"] = [
            "unresolved role strings (C5, thread e9f81ae7): "
            + ", ".join(unresolved)]

    # C7: honest induction — record what the data ambiguates.
    crashish = [r for r in receipts if "failed exit=" in (r["summary"] or "")]
    if crashish:
        flow["observations"].append(
            f"{len(crashish)} receipts carry executor-failure summaries "
            f"(failed exit=N) under work-flow types (BLOCK/REVIEW_REJECT): the "
            f"receipt type conflates work-rejection with agent-crash. The "
            f"attempt-counter model absorbs both (each is an attempt), but the "
            f"ambiguity is recorded rather than resolved.")
    if receipts[-1]["type"] not in ("REVIEW_PASS", "CANCELLED",
                                    "EXECUTION_COMPLETE"):
        flow["observations"].append("unit is parked at a non-terminal state")
    return flow


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--request-id", help="unit to induce (default: richest REVIEW_PASS unit)")
    ap.add_argument("--list-candidates", action="store_true",
                    help="list candidate units and exit")
    ap.add_argument("--emit-record", action="store_true",
                    help="post the flow-record as a proposal agent-record "
                         "(the only write this tool performs)")
    args = ap.parse_args(argv)

    if args.list_candidates:
        for r in psql_rows("""
WITH lastt AS (SELECT DISTINCT ON (request_id) request_id, type AS last_type
               FROM execution.receipts ORDER BY request_id, issued_at DESC, id DESC),
stats AS (SELECT request_id, count(*) AS n,
                 count(*) FILTER (WHERE type='REVIEW_REJECT') AS rejects
          FROM execution.receipts GROUP BY request_id)
SELECT s.request_id, s.n, s.rejects FROM lastt l JOIN stats s USING (request_id)
WHERE l.last_type='REVIEW_PASS' AND s.rejects >= 1 ORDER BY s.n DESC LIMIT 5"""):
            print(r[0], f"receipts={r[1]} rejects={r[2]}")
        return 0

    rid = args.request_id
    if not rid:
        rid, _ = pick_unit()
    receipts = fetch_unit(rid)
    if not receipts:
        print(f"no receipts for {rid}", file=sys.stderr)
        return 1
    flow = induce(rid, receipts)

    print(json.dumps(flow, indent=2))

    if args.emit_record:
        body = json.dumps(flow, indent=2)
        payload = {
            "recordType": "assessment", "role": "DBA",
            "title": f"Flow-record v0 (candidate): {flow['source_evidence']['title_hint']}",
            "content": ("```json\n" + body + "\n```\n\n"
                        "Induced by bin/flow-recorder-v0.py (observer-only) per "
                        "design 1adce409 r1; RoleAlias law per e9f81ae7 "
                        "(provisional). Candidate only — pending roundtable "
                        "review before any Aegis emission."),
            "tags": ["type:proposal", "flow-recorder", "flow-record",
                     "conduit", "aegis", "candidate"],
            "level": 2,
        }
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:3101/api/agent-records",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read().decode())
        print(f"\nproposal record: {out.get('id')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
