#!/usr/bin/env python3
"""Canary diff for peb-srv :3111 vs moleculer twin :4111.

Reads + validation negatives ONLY. The four write routes
(POST /decisions, PATCH /decisions/:id, POST /decisions/:id/supersede,
POST /events/:receipt_id/replay) are exercised exclusively with
malformed-body negatives whose validation 400s fire before any DB work —
no peb.decisions / peb.governance_events rows are created or mutated.

Normalization: `port` on /health (the incumbent stringifies its env-provided
port; the twin's code default is numeric), and per-call timestamps inside
row payloads, plus list windows that can shift between the two reads.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3111").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4111").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/peb/health/circuit-breakers", None),
    ("GET", "/api/peb/health/entropy", None),
    ("GET", "/api/peb/health/violations/summary", None),
    ("GET", "/api/peb/transactions?limit=2", None),
    ("GET", "/api/peb/transactions?limit=1&entity_id=__no_such_entity__", None),
    ("GET", f"/api/peb/transactions/{UUID}", None),
    ("GET", f"/api/peb/transactions/{UUID}/lineage", None),
    ("GET", "/api/peb/transactions/bad%22id", None),
    ("GET", "/api/peb/decisions?limit=2", None),
    ("GET", "/api/peb/decisions/next-number", None),
    ("GET", f"/api/peb/decisions/{UUID}", None),
    ("GET", f"/api/peb/decisions/{UUID}/chain", None),
    ("GET", "/api/peb/binding-decisions?limit=2", None),
    ("GET", "/api/peb/binding-decisions/authority/__none__", None),
    ("GET", "/api/peb/events?limit=2", None),
    ("GET", f"/api/peb/events/{UUID}", None),
    ("GET", "/api/peb/entities/__no_such_entity__/capabilities", None),
    ("GET", "/api/peb/entities/__no_such_entity__/capability-gap", None),
    ("GET", "/api/peb/state/__no_such_key__/versions", None),
    ("GET", "/api/peb/state/__no_such_key__/diff", None),
    ("GET", "/api/peb/traces/999999/tree", None),
    ("GET", "/api/peb/events?since=-1", None),
    ("GET", "/api/peb/events?limit=zero", None),
    # Write routes — validation negatives only (400 before DB work):
    ("POST", "/api/peb/decisions", {}),
    ("POST", "/api/peb/decisions", {"title": "x"}),          # author_id missing
    ("POST", "/api/peb/decisions/bad%22id/supersede", {"summary": "s", "author_id": "a"}),
    (f"POST", f"/api/peb/decisions/{UUID}/supersede", {}),   # summary/author missing
    ("PATCH", "/api/peb/decisions/bad%22id", {}),
    ("POST", "/api/peb/events/bad%22id/replay", {}),
    # 404 catch-all (JSON, not finalhandler HTML):
    ("GET", "/api/peb/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
]

# Volatile JSON fields: per-call timestamps inside row payloads, plus the
# entropy trend's `day` bucket — the incumbent computes it from now() at
# query time, so consecutive reads differ by milliseconds regardless of
# which service answers.
TS_FIELDS = {
    "created_at", "committed_at", "replayed_at", "superseded_at",
    "decided_at", "updated_at", "started_at", "ended_at", "observed_at",
    "day",
}


def fetch(base, method, path, body):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def scrub(value):
    if isinstance(value, dict):
        return {
            k: (
                "<port>" if k == "port"
                else "<ts>" if k in TS_FIELDS and isinstance(v, str)
                else scrub(v)
            )
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
            found = first_diff(litem, right_ := ritem, f"{path}[{index}]")
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
            try:
                la = json.loads(anorm[1]) if isinstance(anorm[1], (bytes, bytearray)) else anorm[1]
            except Exception:
                la = None
            if la is not None:
                try:
                    lb = json.loads(bnorm[1]) if isinstance(bnorm[1], (bytes, bytearray)) else bnorm[1]
                    d = first_diff(la, lb)
                    if d:
                        print(f"  first_diff: {d}")
                except Exception:
                    pass
        else:
            print(f"OK   {method} {path} [{astatus}]")
    print(f"\n{total - differences}/{total} cases byte-identical (after normalization)")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
