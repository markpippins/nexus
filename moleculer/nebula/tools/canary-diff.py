#!/usr/bin/env python3
"""Canary diff for nebula-srv :3101 vs moleculer twin :4101.

Reads + validation negatives ONLY. The two POST cases are proven DB-safe by
the incumbent's own validation order (routes.ts POST /agent-records): an
empty body fails the recordType check and a level violation fails the range
check, both BEFORE the INSERT — no nebula.agent_records row is created or
mutated by any case here.

Churn stability: nebula.agent_records / harvests are high-cadence tables
(unlike aegis's registries), so NO case reads live row lists. Every case is
a needle that produces the same bytes on both sides regardless of fleet
activity: nonexistent UUIDs, validated-empty searches, null pointers, and
static validation errors.

Substance coupling: the segment-set cases exercise the READ-ONLY proxy
(GET /api/segment-sets → SUBSTANCE_BASE_URL). If substance is up, both
sides proxy the same upstream bytes; if it is down, both sides return the
same 502 {error: "substance unreachable ..."} — identical either way.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3101").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4101").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/health", None),
    # Agent records — nonexistent needles only (churn-stable):
    ("GET", f"/api/agent-records/{UUID}", None),
    ("GET", "/api/agent-records/not-a-uuid", None),
    ("GET", "/api/agent-records?search=__no_such_record_zzz__&limit=5", None),
    # Inbox pointer (Redis-backed; unknown role → null pointer, stable):
    ("GET", "/api/inbox-pointer/__no_such_role_zzz__", None),
    # Substance-coupled segment-set evidence reads (read-only proxy):
    ("GET", "/api/segment-sets?limit=1", None),
    ("GET", "/api/segment-sets/not-a-uuid", None),
    # 404 surface (Express default HTML — nebula has no JSON catch-all):
    ("GET", "/api/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
    # Write-route validation negatives (reject BEFORE any DB work —
    # proven by the incumbent's check order in POST /agent-records):
    ("POST", "/api/agent-records", {}),
    ("POST", "/api/agent-records", {"recordType": "report", "level": 9}),
]

TS_FIELDS = {
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
