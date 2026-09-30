#!/usr/bin/env python3
"""Canary diff for substance-srv :3115 vs moleculer twin :4115.

Reads + validation negatives ONLY. No nebula.segment_sets rows are created
or mutated: every segment-set write case is a validation negative that
rejects before any DB work (404 on unknown segment_set_id, 422 on absent
body / bad shapes), and the two DELETE cases target nonexistent UUIDs on
unknown domain types (404 before lookup), verified against the incumbent's
route modules (links.ts, segment-sets.ts).

The Redis-cached resolve path (/segment-sets/:id) is included as a read;
its cache may be stale or warm on either side independently of this run
(the segment_expired listener is incumbent-owned), so the scrub drops
volatile fields and the churn-window note applies.

Envelope note: substance is the FastAPI-shaped twin ({detail} errors, 422
validation) — a JSON 404 catch-all EXISTS here (unlike nebula), so the
unknown-path cases compare substance's real {detail: "Not Found"}.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3115").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4115").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/healthz", None),
    # Segment-set reads (churn-stable needles; the Redis-resolve case may
    # 404 on both sides identically if the id is unknown to substance):
    ("GET", "/segment-sets?limit=1", None),
    ("GET", f"/segment-sets/{UUID}", None),
    ("GET", "/segment-sets/not-a-uuid", None),
    # Domain-links reads (unknown domain ids → 404 both sides):
    ("GET", f"/candidates/{UUID}/segment-sets", None),
    ("GET", f"/requirements/{UUID}/segment-sets", None),
    # Write-route negatives (reject BEFORE any DB work — verified against
    # the incumbent's validation order; unknown ids → 404, absent bodies
    # → 422, both without touching nebula.segment_sets):
    ("POST", "/segment-sets/from-segments", {}),
    ("POST", f"/segment-sets/{UUID}/members", {}),
    ("PATCH", f"/segment-sets/{UUID}", {}),
    ("DELETE", f"/segment-sets/{UUID}/members/not-a-uuid", None),
    ("POST", f"/candidates/{UUID}/segment-sets", {}),
    ("POST", f"/requirements/{UUID}/segment-sets", {}),
    ("DELETE", f"/candidates/{UUID}/segment-sets/{UUID}", None),
    # 404 surface (substance HAS a JSON {detail} catch-all, unlike nebula):
    ("GET", "/definitely/not/a/route", None),
]

TS_FIELDS = {
    "created_at", "updated_at", "verified_at", "checked_at",
    "started_at", "finished_at", "expires_at", "cached_at",
}


def fetch(base, method, path, body):
    data = None
    headers = {}
    if body is None:
        req = urllib.request.Request(base + path, method=method, headers=headers)
    else:
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
