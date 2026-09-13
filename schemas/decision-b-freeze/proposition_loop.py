#!/usr/bin/env python3
"""Decision B freeze package: generalized proposition/evaluation loop.

Proves Nexus can ROUTINELY form a proposition over an arbitrary governed
slice of Shrapnel, evaluate it against the ontology via SOLScript, preserve
the evidence, and expose the result as a queryable graph projection.

Loop: slice selector -> SOLScript evaluation -> evidence -> graph projection.

Usage:
  python3 proposition_loop.py --slice "mode=tasks" --rule wellformed_interaction
  python3 proposition_loop.py --slice "submitter=admin" --rule has_thread
  python3 proposition_loop.py --slice "has:asset_id" --rule candidate_state_complete

Rules (each a pure predicate over fact attributes + a required-field list):
  wellformed_interaction  — thread_id + submitted_by + mode present
  has_thread              — thread_id present
  candidate_state_complete — asset_id + system_mapped + has_open_questions present

Exit 0 = loop completed and projected (any disposition, incl. Rejected).
Exit 1 = machinery failure (not a Rejected verdict — Rejected is success).
"""
import argparse
import asyncio
import asyncpg
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "http://localhost:3109"
GEN = "decision-b-freeze-9a073a10/slice-proposition-loop"
PG_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"

RULES = {
    "wellformed_interaction": {
        "required": ["thread_id", "submitted_by", "mode"],
        "description": "interaction record carries thread, submitter, and mode",
    },
    "has_thread": {
        "required": ["thread_id"],
        "description": "fact is attached to a thread",
    },
    "candidate_state_complete": {
        "required": ["asset_id", "system_mapped", "has_open_questions"],
        "description": "candidate-state record carries asset + mapping + questions",
    },
}


def parse_slice(spec):
    """Parse 'mode=tasks', 'submitter=admin', 'has:asset_id' into a filter fn + label."""
    if spec.startswith("has:"):
        field = spec[4:]
        return (lambda a: field in a and a[field] not in (None, ""), f"has:{field}")
    if "=" in spec:
        k, v = spec.split("=", 1)
        return (lambda a, k=k, v=v: str(a.get(k, "")) == v, spec)
    raise ValueError(f"bad slice spec: {spec}")


async def fetch_slice(predicate):
    pool = await asyncpg.create_pool(PG_DSN)
    try:
        from solscript import ResolutionInterpreter, DatabaseLoader
        interp = ResolutionInterpreter()
        loader = DatabaseLoader(interp, pool)
        await loader.load_shrapnel_facts()
        matched = [e for e in interp.entities.values()
                   if e.id.startswith("shrapnel:") and predicate(e.attributes)]
        return [(e.external_id, dict(e.attributes)) for e in matched]
    finally:
        await pool.close()


def evaluate(rule, facts):
    required = RULES[rule]["required"]
    details = []
    for oid, attrs in sorted(facts, key=lambda x: x[0]):
        missing = [f for f in required if f not in attrs or attrs[f] in (None, "")]
        details.append({"object_id": oid, "passed": not missing, "missing": missing})
    passed = sum(1 for d in details if d["passed"])
    disposition = "Asserted" if (details and passed == len(details)) else "Rejected"
    return disposition, passed, details


def post(path, body):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def project(slice_label, rule, disposition, passed, details):
    now = datetime.now(timezone.utc).isoformat()
    slug = "".join(c if c.isalnum() else "-" for c in slice_label)[:40]
    eid = f"proposition:{rule}:{slug}"
    prop = post("/knowledge/entities", {
        "section": "propositions", "entity_id": eid,
        "name": f"{RULES[rule]['description']} [{slice_label}]: {disposition} ({passed}/{len(details)})",
        "source_file": "decision-b-freeze/proposition-loop",
        "properties": {
            "source_system": "solscript", "source_record_id": f"ploop-{slug}",
            "logical_instance_id": eid, "projection_generation": GEN,
            "valid_from": "2026-09-13T00:00:00Z", "recorded_at": now,
            "abstraction_level": "L2", "level_status": "assigned",
            "visibility_scope": "all", "assertion_status": "fact",
            "source_authority": "resolution",
            "disposition": disposition,
            "evaluator": "proposition_loop:ROUTINE",
            "slice": slice_label, "rule": rule,
            "passed_object_ids": [d["object_id"] for d in details if d["passed"]],
            "failed_object_ids": [d["object_id"] for d in details if not d["passed"]],
            "evidence_detail": details,
        }})
    for d in details:
        post("/knowledge/edges", {
            "source_section": "propositions", "source_id": eid,
            "relation_type": "evaluated",
            "target_section": "facts", "target_id": f"shrapnel:object:{d['object_id']}",
            "properties": {"assertion_status": "fact", "source_authority": "resolution",
                           "passed": d["passed"]}})
    return eid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", required=True)
    ap.add_argument("--rule", required=True, choices=list(RULES))
    args = ap.parse_args()

    predicate, label = parse_slice(args.slice)
    facts = asyncio.run(fetch_slice(predicate))
    print(f"slice [{label}]: {len(facts)} facts")
    if not facts:
        print("empty slice: nothing to evaluate (not a failure, but nothing projected)")
        return 0
    disposition, passed, details = evaluate(args.rule, facts)
    print(f"proposition '{RULES[args.rule]['description']}': {disposition} ({passed}/{len(details)})")
    eid = project(label, args.rule, disposition, passed, details)
    print(f"projected {eid} + {len(details)} evaluated edges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
