#!/usr/bin/env python3
"""Canary diff for resolution-srv :3171 vs moleculer twin :4171.

Reads + boundary negatives ONLY (Decision 32 Ruling 3): the write family is
exercised exclusively via POST/PATCH-must-405 probes against known tables —
the incumbent's method guard rejects BEFORE any DB work, and the twin
replicates it verbatim — so no resolution.* row is created or mutated by any
case here. resolution.* is governance-canonical state.

Churn stability: every list case is a static needle (limit=1 on a table that
lists cleanly), every row case a nil UUID (404 not_found — stable on both
sides regardless of fleet activity). NOTE: some tables 500 on list (missing
created_at orderCol — e.g. semantic_type, producer_registry); those are NOT
canary needles (the error text is PG-server-dependent), while their /{id}
surfaces still are.

Group coverage: one list needle per TypeSpec TableGroup union (registry,
entity_proposition, reasoning, outcome, lineage) — from the compiled contract
main.tsp, so a new group in the contract without a needle here fails loudly.

Known live-data divergence (ACCEPTED, not a drift): GET /api/entity is a
stable 404 {error: unknown_table} — the entity table exists in tables.ts but
not yet in the live DB. Both sides serve it from the same registry, so the
envelopes stay byte-identical; kept as a registry-coupling probe.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3171").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4171").rstrip("/")

UUID = "00000000-0000-0000-0000-000000000000"

# One table per TypeSpec TableGroup union (main.tsp), first table that lists
# cleanly with ?limit=1 (some tables 500 on list — missing created_at
# orderCol; that is incumbent behavior, not a canary target).
GROUP_NEEDLES = {
    "registry": "receipt",
    "entity_proposition": "proposition",
    "reasoning": "rule",
    "outcome": "execution_claim",
    "lineage": "verified_statement",
}

CASES = [
    ("GET", "/health", None),
    # Fixed 404s by parity decision (see canary-run.sh / PR description):
    ("GET", "/api/health", None),
    ("GET", "/api/meta", None),
    ("GET", "/api/__no_such_table_zzz__", None),
    ("GET", f"/api/receipt/{UUID}", None),
    ("GET", "/api/receipt/not-a-uuid", None),
    # One churn-stable list needle per TypeSpec TableGroup union:
    *( ("GET", f"/api/{table}?limit=1", None) for table in GROUP_NEEDLES.values() ),
    ("GET", "/api/entity", None),
    ("GET", "/api/definitely/not/a/route", None),
    ("GET", "/api", None),
    # THE 405 BOUNDARY (Decision 32 Ruling 3 — the canary write family):
    # uniform read_only rejection BEFORE any DB work on known tables;
    # the boundary knows the registry — unknown tables 404 instead.
    ("POST", "/api/proposition", {}),
    ("PATCH", "/api/receipt/00000000-0000-0000-0000-000000000000", {}),
    ("DELETE", "/api/execution_claim/00000000-0000-0000-0000-000000000000", None),
    ("POST", "/api/__no_such_table_zzz__", {}),
    ("POST", "/api/meta", {}),
]

# /health's `time` is a wall-clock read (new Date() at request time) — it
# differs between the two sides by milliseconds and is not part of the parity
# contract, so it is scrubbed to <ts> like every other timestamp field.
TS_FIELDS = {
    "time",
    "created_at", "updated_at", "verified_at", "checked_at",
    "recorded_on_dt", "started_at", "finished_at",
}


def fetch(base, method, path, body):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def scrub(value):
    if isinstance(value, dict):
        return {
            k: ("<ts>" if k in TS_FIELDS and isinstance(v, str) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def normalize(status, body):
    try:
        parsed = json.loads(body)
    except Exception:
        return status, body
    return status, json.dumps(scrub(parsed), sort_keys=True).encode()


def content_type(headers):
    return headers.get("Content-Type") or headers.get("content-type")


def first_diff(left, right, path="$"):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left:
            if key not in right:
                return f"{path}.{key}: A={left[key]!r} B=<missing>"
            found = first_diff(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, dict) or isinstance(right, dict):
        return f"{path}: A={left!r} B={right!r}"
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}: list lengths {len(left)} vs {len(right)}"
        for index, (litem, ritem) in enumerate(zip(left, right)):
            found = first_diff(litem, ritem, f"{path}[{index}]")
            if found:
                return found
        return None
    if isinstance(left, list) or isinstance(right, list):
        return f"{path}: A={left!r} B={right!r}"
    return None if left == right else f"{path}: A={left!r} B={right!r}"


def main():
    differences = 0
    total = 0
    for method, path, body in CASES:
        total += 1
        astatus, aheaders, abody = fetch(A, method, path, body)
        bstatus, bheaders, bbody = fetch(B, method, path, body)
        anorm = normalize(astatus, abody)
        bnorm = normalize(bstatus, bbody)
        atype = content_type(aheaders)
        btype = content_type(bheaders)
        if anorm != bnorm or atype != btype:
            differences += 1
            print(f"DIFF {method} {path}")
            print(f"  A[{astatus}/{atype}]: {abody[:400]!r}")
            print(f"  B[{bstatus}/{btype}]: {bbody[:400]!r}")
        else:
            print(f"OK   {method} {path} [{astatus}]")
    print(f"\n{total - differences}/{total} cases byte-identical (after normalization)")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
