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
the planner's ratification call.

When the spec also carries a `decomposition` block (the W-B blueprint,
plan 8261654), the checker validates it structurally — package bounds,
unified status vocabulary, evidence-slot completeness, aggregate-matrix
linkage, stop-condition delegation — and runs the W-B6 CD-1/CD-2/CD-3
regression fixtures through pure reference semantics. That validation is
a blueprint-conformance check only: passing it is not evidence that the
runtime projection agrees.

Exit codes: 0 = all plans consistent with expected_gating (and the
decomposition block, when present, conformant), 1 = any mismatch/finding,
2 = tool error.

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


def ev_plan_has_record(params: dict) -> bool:
    """Evidence mass: nebula.agent_records rows whose plan_ref points at
    the plan (id8 prefix match), at least params['min_count'] of them.
    Introduced with the fleet spec (#551) but not implemented until now —
    the spec's reference silently forced exit 1 on every run."""
    plan = params["plan"].replace("'", "")
    min_count = int(params.get("min_count", 1))
    count = _psql_scalar(
        "select count(*) from nebula.agent_records "
        f"where plan_ref like '{plan}%';")
    return (int(count) if count else 0) >= min_count


def load_conduit_state() -> dict:
    """plan_number -> derivedStatus, from conduit /state (via urllib to
    stay dependency-free). FALLBACK ONLY: /state's ticket projection
    hides open higher-generation rows behind closed same-role rows (the
    CD-1 defect escalated in record 5b64b3b3) — prefer
    load_canonical_state()."""
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


def load_canonical_state(plan_ids: list[str]) -> dict:
    """plan -> {derived, tickets{role: {status}}} from the canonical
    stores: vision.tickets for the ticket surface (where the flow
    actually writes; resolution.ticket is empty for these plans — the
    W-B6 split) and the latest resolution.receipt kind for derived
    status. Rows are read in created_at order and last-row-wins per
    role, so a live (open) higher-generation ticket is never hidden
    behind a closed earlier-generation one — the exact CD-1 blindness.
    """
    state = {p: {"derived": None, "tickets": {}} for p in plan_ids}
    patterns = ", ".join("'" + p.replace("'", "") + "%'" for p in plan_ids)
    rows = _psql_scalar(
        "select plan_id||'|'||role||'|'||status from vision.tickets "
        f"where plan_id like any(array[{patterns}]) order by created_at;")
    for line in (rows or "").splitlines():
        if not line or line.count("|") < 2:
            continue
        plan, role, status = line.split("|", 2)
        key = next((p for p in plan_ids if plan.startswith(p)), None)
        if key:
            state[key]["tickets"][role] = {"status": status}
    for p in plan_ids:
        kind = _psql_scalar(
            "select kind from resolution.receipt where payload->>'plan_id' "
            f"like '{p.replace(chr(39), "")}%' order by created_at desc limit 1;")
        state[p]["derived"] = (kind or "").upper() or None
    return state


def load_state(plan_ids: list[str]) -> tuple[dict, str]:
    """Canonical first; /state as documented fallback. Returns
    (state, source)."""
    try:
        return load_canonical_state(plan_ids), "canonical"
    except Exception:
        return load_conduit_state(), "conduit_state_fallback"


EVALUATORS = {
    "db_table_exists": ev_db_table_exists,
    "record_exists": ev_record_exists,
    "plan_has_record": ev_plan_has_record,
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
      BLOCKED + OPEN   -> BLOCKED-AND-OPEN (live exhibit: a plan
                          presents builder-ready work its own ACs forbid)
      BLOCKED + EXPIRED (no open) -> BLOCKED-WITH-EXPIRED (post-CD-2:
                          expiry enforced, no live work presented; the
                          stale expired row is planner-disposition
                          hygiene, not a live gating defect)
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

    if has_open:
        findings.append("ticket presents builder-ready work whose own start "
                        "conditions are unmet (the W-B4 exhibit, live)")
        return "BLOCKED-AND-OPEN", findings
    if has_expired:
        findings.append("expired ticket row on a blocked plan with no open "
                        "ticket (post-CD-2 hygiene: expiry enforced by the "
                        "sweeper, row awaits planner disposition)")
        return "BLOCKED-WITH-EXPIRED", findings
    return "BLOCKED-CLOSED", findings


# ─────────────────────────────────────────────────────────────────────────────
# W-B decomposition blueprint conformance (plan 8261654)
#
# The `decomposition` block of a gating spec is a *blueprint*: packages, a
# unified status vocabulary, evidence slots, an aggregate matrix, and the W-B6
# contract (ruling 1867c88d). The helpers below validate its structure and run
# the W-B6 fixtures through reference semantics. They are a conformance harness
# only — nothing here is wired into conduit-mcp, and passing fixtures is not
# evidence that the runtime projection agrees (that is delegated to child
# execution WorkRequests by the blueprint itself).
# ─────────────────────────────────────────────────────────────────────────────

NON_TERMINAL_TICKET_STATES = frozenset({"open", "claimed", "stale"})
WB_CANONICAL_STATES = ["pending", "active", "completed", "archived"]
WB_SLOT_FIELDS = (
    "artifact_or_query",
    "observed_field",
    "timestamp",
    "source_reference",
    "verification_owner",
)
WB_PACKAGE_FIELDS = (
    "stop_condition",
    "child_work_request",
)
WB_REQUIRED_FIXTURE_KINDS = {
    "CD-1": "projection",
    "CD-2": "expiry",
    "CD-3": "routing",
}


def _ticket_generation(row: dict, index: int) -> int:
    value = row.get("generation", row.get("position"))
    return int(value) if value is not None else index + 1


def project_ticket_generations(rows: list[dict]) -> dict:
    """CD-1 reference semantics: project per (plan, role) by generation
    position, never by row order. Returns the newest generation per role,
    the roles whose newest generation is live, and any live generation that
    would be hidden behind a newer row (the defect signal)."""
    newest: dict[str, dict] = {}
    for index, row in enumerate(rows):
        role = row["role"]
        generation = _ticket_generation(row, index)
        current = newest.get(role)
        if (
            current is None
            or generation > current["generation"]
            or (
                generation == current["generation"]
                and str(row.get("created_at", "")) >= current["_created_at"]
            )
        ):
            newest[role] = {
                "ticket_id": row["ticket_id"],
                "generation": generation,
                "status": row["status"],
                "_created_at": str(row.get("created_at", "")),
            }
    projected = {
        role: {
            "ticket_id": n["ticket_id"],
            "generation": n["generation"],
            "status": n["status"],
        }
        for role, n in newest.items()
    }
    open_generations = {
        role: n["generation"]
        for role, n in newest.items()
        if n["status"] in NON_TERMINAL_TICKET_STATES
    }
    hidden = sorted(
        row["ticket_id"]
        for index, row in enumerate(rows)
        if row["status"] in NON_TERMINAL_TICKET_STATES
        and newest[row["role"]]["ticket_id"] != row["ticket_id"]
    )
    return {
        "newest": projected,
        "open_generations": open_generations,
        "hidden_open_generations": hidden,
    }


def _parse_ts(value: str):
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def expiry_audit_events(tickets: list[dict], now: str) -> dict:
    """CD-2 reference semantics: one durable expiry audit event per expired
    non-terminal generation; expiry never implies completion; terminal rows
    are untouched."""
    now_dt = _parse_ts(now)
    events: list[dict] = []
    unexpired: list[str] = []
    untouched: list[str] = []
    eligible: set[str] = set()
    for index, ticket in enumerate(tickets):
        ticket_id = ticket["ticket_id"]
        if ticket["status"] not in NON_TERMINAL_TICKET_STATES:
            untouched.append(ticket_id)
            continue
        expires_at = ticket.get("expires_at")
        if expires_at and _parse_ts(expires_at) <= now_dt:
            eligible.add(ticket_id)
            events.append(
                {
                    "ticket_id": ticket_id,
                    "plan_id": ticket.get("plan_id"),
                    "role": ticket["role"],
                    "generation": _ticket_generation(ticket, index),
                    "from_status": ticket["status"],
                    "to_status": "expired",
                    "reason": "expiry_detection",
                    "implies_completion": False,
                    "expires_at": expires_at,
                }
            )
        else:
            unexpired.append(ticket_id)
    emitted = {event["ticket_id"] for event in events}
    return {
        "events": events,
        "unexpired": sorted(unexpired),
        "already_terminal_untouched": sorted(untouched),
        "every_expired_generation_has_event": emitted == eligible
        and len(events) == len(eligible),
    }


def route_review_reject(payload: dict | None) -> dict:
    """CD-3 reference semantics: blueprint-scope rejects return to the
    Planner as a re-scope event (no builder ticket); implementation scope
    (also the documented default when absent) routes to builder rework."""
    scope = (payload or {}).get("scope") or "implementation"
    if scope == "blueprint":
        return {
            "route": "planner_rescope_event",
            "scope": "blueprint",
            "creates_builder_ticket": False,
            "preserves_evidence": True,
            "stops_builder_loop": True,
        }
    return {
        "route": "builder_rework_ticket",
        "scope": scope,
        "creates_builder_ticket": True,
        "preserves_evidence": True,
        "stops_builder_loop": False,
    }


def evaluate_wb6_fixture(fixture: dict) -> list[str]:
    """Assert one fixture's cases against the reference semantics."""
    errors: list[str] = []
    fid = fixture.get("id", "(unnamed)")
    kind = fixture.get("kind")
    if kind == "projection":
        if not fixture.get("cases"):
            return [f"{fid}: projection fixture has no cases"]
        for case in fixture["cases"]:
            got = project_ticket_generations(case.get("rows") or [])
            expect = case.get("expect") or {}
            name = case.get("name", "(unnamed)")
            if (
                "open_generations" in expect
                and got["open_generations"] != expect["open_generations"]
            ):
                errors.append(
                    f"{fid}/{name}: open_generations {got['open_generations']} "
                    f"!= {expect['open_generations']}"
                )
            if (
                "hidden_open_generations" in expect
                and got["hidden_open_generations"] != expect["hidden_open_generations"]
            ):
                errors.append(
                    f"{fid}/{name}: hidden_open_generations "
                    f"{got['hidden_open_generations']} != "
                    f"{expect['hidden_open_generations']}"
                )
            if "newest_status" in expect:
                got_status = {
                    role: entry["status"] for role, entry in got["newest"].items()
                }
                if got_status != expect["newest_status"]:
                    errors.append(
                        f"{fid}/{name}: newest_status {got_status} != "
                        f"{expect['newest_status']}"
                    )
    elif kind == "expiry":
        if "tickets" not in fixture or "now" not in fixture:
            return [f"{fid}: expiry fixture needs tickets + now"]
        got = expiry_audit_events(fixture["tickets"], fixture["now"])
        expect = fixture.get("expect") or {}
        for key in (
            "events",
            "unexpired",
            "already_terminal_untouched",
            "every_expired_generation_has_event",
        ):
            if key in expect and got[key] != expect[key]:
                errors.append(f"{fid}: {key} {got[key]} != {expect[key]}")
    elif kind == "routing":
        if not fixture.get("cases"):
            return [f"{fid}: routing fixture has no cases"]
        for case in fixture["cases"]:
            got = route_review_reject(case.get("payload") or {})
            name = case.get("name", "(unnamed)")
            for key, want in (case.get("expect") or {}).items():
                if got.get(key) != want:
                    errors.append(
                        f"{fid}/{name}: {key} {got.get(key)!r} != {want!r}"
                    )
    else:
        errors.append(f"{fid}: unknown fixture kind {kind!r}")
    return errors


def validate_decomposition(spec: dict) -> list[str]:
    """Structural conformance for a spec's `decomposition` block.

    Returns a list of findings (empty == conformant). Read-only: no DB, no
    network, no runtime projection-sync log.
    """
    dec = spec.get("decomposition")
    if not isinstance(dec, dict):
        return ["decomposition block missing"]
    errors: list[str] = []

    if dec.get("scope_class") != "decomposition-blueprint":
        errors.append(
            "decomposition.scope_class must be 'decomposition-blueprint'"
        )

    packages = dec.get("packages") or {}
    expected_packages = {f"W-B{i}" for i in range(1, 7)}
    if set(packages) != expected_packages:
        errors.append(
            f"packages must be exactly {sorted(expected_packages)}; "
            f"got {sorted(packages)}"
        )

    schema = dec.get("evidence_slot_schema") or {}
    if list(schema.get("required_fields") or []) != list(WB_SLOT_FIELDS):
        errors.append(
            f"evidence_slot_schema.required_fields must be {list(WB_SLOT_FIELDS)}"
        )
    if schema.get("requires_runtime_projection_sync_logs") is not False:
        errors.append(
            "evidence_slot_schema.requires_runtime_projection_sync_logs must be false"
        )

    slots = dec.get("evidence_slots") or {}
    for slot_id, slot in slots.items():
        for field in WB_SLOT_FIELDS:
            value = slot.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{slot_id}: missing/empty {field}")
        if slot.get("package") not in packages:
            errors.append(f"{slot_id}: package {slot.get('package')!r} unknown")

    for package_id, package in packages.items():
        owner = package.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            errors.append(f"{package_id}: exactly one owner required")
        if not isinstance(package.get("dependencies"), list):
            errors.append(f"{package_id}: dependencies must be a list")
        if not isinstance(package.get("independent"), bool):
            errors.append(f"{package_id}: independent must be a boolean")
        for field in WB_PACKAGE_FIELDS:
            value = package.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{package_id}: missing/empty {field}")
        outputs = package.get("evidence_outputs") or []
        if not outputs:
            errors.append(f"{package_id}: at least one evidence output required")
        for slot_id in outputs:
            if slot_id not in slots:
                errors.append(f"{package_id}: evidence output {slot_id!r} unknown")
            elif slots[slot_id].get("package") != package_id:
                errors.append(
                    f"{package_id}: slot {slot_id} belongs to "
                    f"{slots[slot_id].get('package')}"
                )

    vocab = dec.get("status_vocabulary") or {}
    if list(vocab.get("canonical_states") or []) != WB_CANONICAL_STATES:
        errors.append(f"canonical_states must be {WB_CANONICAL_STATES}")
    conduit_states = vocab.get("conduit_receipt_states") or {}
    nebula_states = vocab.get("nebula_projection_states") or {}
    if set(conduit_states) != set(WB_CANONICAL_STATES):
        errors.append("conduit_receipt_states must cover every canonical state")
    if set(nebula_states) != set(WB_CANONICAL_STATES):
        errors.append("nebula_projection_states must cover every canonical state")
    seen: dict[str, str] = {}
    for canonical_state, states in conduit_states.items():
        if not isinstance(states, list) or not states:
            errors.append(f"{canonical_state}: no conduit receipt states mapped")
            continue
        for state in states:
            if state in seen:
                errors.append(f"conduit state {state} mapped more than once")
            seen[state] = canonical_state
    for row in vocab.get("rows") or []:
        conduit_state = row.get("conduit")
        canonical_state = row.get("canonical")
        if seen.get(conduit_state) != canonical_state:
            errors.append(
                f"vocabulary row {conduit_state}: canonical {canonical_state} "
                f"!= {seen.get(conduit_state)}"
            )
        if nebula_states.get(canonical_state) != row.get("nebula"):
            errors.append(
                f"vocabulary row {conduit_state}: nebula projection "
                f"{row.get('nebula')} != {nebula_states.get(canonical_state)}"
            )

    matrix = dec.get("aggregate_matrix") or {}
    if matrix.get("requires_runtime_projection_sync_logs") is not False:
        errors.append(
            "aggregate_matrix.requires_runtime_projection_sync_logs must be false"
        )
    linked: dict[str, str] = {}
    for link in matrix.get("links") or []:
        package_id = link.get("package")
        if package_id not in packages:
            errors.append(f"matrix link: unknown package {package_id!r}")
        if not link.get("satisfied_when"):
            errors.append(f"matrix link {package_id}: satisfied_when required")
        for slot_id in link.get("slots") or []:
            if slot_id not in slots:
                errors.append(f"matrix link {package_id}: slot {slot_id!r} unknown")
            if slot_id in linked:
                errors.append(f"matrix: slot {slot_id} linked more than once")
            linked[slot_id] = package_id
    if set(linked) != set(slots):
        errors.append(
            "matrix must link every evidence slot exactly once "
            f"(linked={sorted(linked)} slots={sorted(slots)})"
        )
    for package_id, package in packages.items():
        for slot_id in package.get("evidence_outputs") or []:
            if linked.get(slot_id) != package_id:
                errors.append(
                    f"{package_id}: slot {slot_id} is not linked to the package"
                )

    wb6 = dec.get("wb6_contract") or {}
    for key in ("generation_projection", "expiry_audit", "reject_routing"):
        block = wb6.get(key)
        if (
            not isinstance(block, dict)
            or not block.get("invariant")
            or not block.get("required_semantics")
        ):
            errors.append(f"wb6_contract.{key}: invariant + required_semantics required")
    fixtures = wb6.get("fixtures") or []
    by_clause: dict[str, dict] = {}
    for fixture in fixtures:
        clause = fixture.get("clause")
        fid = fixture.get("id", "(unnamed)")
        if WB_REQUIRED_FIXTURE_KINDS.get(clause) != fixture.get("kind"):
            errors.append(
                f"{fid}: clause {clause!r} requires kind "
                f"{WB_REQUIRED_FIXTURE_KINDS.get(clause)!r}"
            )
        if clause in by_clause:
            errors.append(f"{fid}: more than one fixture for {clause}")
        by_clause[clause] = fixture
        errors.extend(evaluate_wb6_fixture(fixture))
    missing = set(WB_REQUIRED_FIXTURE_KINDS) - set(by_clause)
    if missing:
        errors.append(f"wb6_contract.fixtures missing clauses {sorted(missing)}")

    stop = dec.get("stop_condition") or {}
    if not stop.get("decomposition"):
        errors.append("stop_condition.decomposition required")
    if not stop.get("open_builder_ticket_guard"):
        errors.append("stop_condition.open_builder_ticket_guard required")
    if stop.get("claims_runtime_agreement") is not False:
        errors.append(
            "stop_condition.claims_runtime_agreement must be false "
            "(runtime agreement is delegated)"
        )
    if set(stop.get("linked_packages") or []) != set(packages):
        errors.append("stop_condition.linked_packages must cover every package")
    delegation = stop.get("delegation") or {}
    if not delegation.get("runtime_agreement"):
        errors.append("stop_condition.delegation.runtime_agreement required")
    if delegation.get("st01_dependency") is not False:
        errors.append("stop_condition.delegation.st01_dependency must be false")
    if "W-A" not in (delegation.get("parallel_with") or []):
        errors.append("stop_condition.delegation.parallel_with must include W-A")

    return errors


def main() -> int:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    base = Path(__file__).resolve().parent / "plan-gating-specs"
    try:
        path = Path(spec_path) if spec_path else sorted(base.glob("*.json"))[0]
        spec = json.loads(path.read_text())
        plan_ids = list(spec["plans"].keys())
        conduit_state, source = load_state(plan_ids)
    except Exception as exc:  # noqa: BLE001
        print(f"tool error: {exc}", file=sys.stderr)
        return 2

    report = {"spec": str(path), "state_source": source, "plans": {}}
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

    if "decomposition" in spec:
        decomposition_findings = validate_decomposition(spec)
        report["decomposition"] = {
            "valid": not decomposition_findings,
            "findings": decomposition_findings,
        }
        if decomposition_findings:
            mismatches += 1

    print(json.dumps(report, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
