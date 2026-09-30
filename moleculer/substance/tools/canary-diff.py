#!/usr/bin/env python3
"""Canary diff for aegis-srv :3116 vs moleculer twin :4116.

Reads + validation negatives ONLY. The 40 write routes (registries CRUD,
revisions, validate, model-check, wind-compilations, and the six table-CRUD
families) are exercised exclusively with malformed/absent-id negatives that
reject before any DB mutation — and model-check never reaches the java/TLC
spawn on those probes. No aegis.* rows are created or mutated.

Normalization: row timestamps (registries are live DB rows shared with the
incumbent; reads are byte-identical unless a row changes between the two
fetches, which the registry CRUD cadence makes negligible).
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3116").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4116").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/registries", None),
    ("GET", "/api/registries/name/__no_such_registry__", None),
    ("GET", f"/api/registries/{UUID}", None),
    ("GET", "/api/registries/not-a-uuid", None),
    # Child family reads (registry existence gate → 404, byte-identical):
    ("GET", f"/api/registries/{UUID}/revisions", None),
    ("GET", f"/api/registries/{UUID}/constants", None),
    ("GET", f"/api/registries/{UUID}/variables", None),
    ("GET", f"/api/registries/{UUID}/states", None),
    ("GET", f"/api/registries/{UUID}/transitions", None),
    ("GET", f"/api/registries/{UUID}/invariants", None),
    ("GET", f"/api/registries/{UUID}/properties", None),
    ("GET", f"/api/registries/{UUID}/temporal-properties", None),
    ("GET", f"/api/registries/{UUID}/attribute-mappings", None),
    ("GET", f"/api/registries/{UUID}/concept-mappings", None),
    ("GET", f"/api/registries/{UUID}/relationship-mappings", None),
    ("GET", f"/api/registries/{UUID}/execution-log", None),
    ("GET", f"/api/registries/{UUID}/validation-results", None),
    ("GET", f"/api/registries/{UUID}/model-check-results", None),
    ("GET", f"/api/registries/{UUID}/wind-compilations", None),
    # Child detail id negatives:
    ("GET", f"/api/registries/{UUID}/constants/not-a-uuid", None),
    ("GET", f"/api/registries/{UUID}/transitions/not-a-uuid", None),
    # Write-route negatives (no DB work):
    ("POST", "/api/registries", {}),
    ("POST", f"/api/registries/{UUID}/revisions", {}),
    ("POST", f"/api/registries/{UUID}/validate", {}),
    ("POST", f"/api/registries/{UUID}/model-check", {}),
    ("POST", f"/api/registries/{UUID}/wind-compilations", {}),
    ("POST", "/api/registries/not-a-uuid/constants", {}),
    ("PATCH", f"/api/registries/{UUID}/properties/not-a-uuid", {}),
    ("DELETE", f"/api/registries/{UUID}/transitions/not-a-uuid", None),
    ("DELETE", f"/api/registries/{UUID}", None),
    # 404 catch-all (JSON {error:"not found"}):
    ("GET", "/api/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
]

TS_FIELDS = {
    "created_at", "updated_at", "verified_at", "checked_at",
    "compiled_at", "started_at", "finished_at",
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
