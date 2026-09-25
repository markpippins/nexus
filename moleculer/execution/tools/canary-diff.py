#!/usr/bin/env python3
"""Read-only canary diff for execution-srv :3110 vs moleculer twin :4110.

The incumbent is read-only (SELECTs only), so every endpoint is safe to
exercise live. Timestamps and pagination row windows are the only volatile
fields; both services share the same PostgreSQL database.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3110").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4110").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/execution/health", None),
    ("GET", "/api/execution/requests?limit=2", None),
    ("GET", "/api/execution/leases?limit=2", None),
    ("GET", "/api/execution/attempts?limit=2", None),
    ("GET", "/api/execution/receipts?limit=2", None),
    ("GET", "/api/execution/requests?limit=1&status=__no_such_status__", None),
    ("GET", "/api/execution/requests?limit=1&search=__no_such_term__", None),
    ("GET", "/api/execution/leases/stale", None),
    ("GET", "/api/execution/health/by-executor", None),
    ("GET", "/api/execution/health/by-executor?executor_id=__no_such_executor__", None),
    ("GET", "/api/execution/health/status-distribution", None),
    ("GET", f"/api/execution/requests/{UUID}/state", None),
    ("GET", f"/api/execution/requests/{UUID}/attempts", None),
    ("GET", f"/api/execution/requests/{UUID}/receipts/lineage", None),
    ("GET", f"/api/execution/leases/{UUID}/lifecycle", None),
    ("GET", f"/api/execution/receipts/{UUID}/pipeline-origin", None),
    ("GET", "/api/execution/requests/not-a-uuid/state", None),
    ("GET", "/api/execution/witnessed-runs", None),
    ("GET", "/api/execution/witnessed-runs?workflow_instance_id=__none__&node_id=__none__", None),
    ("GET", "/api/execution/witnessed-runs/diagnostics?workflow_instance_id=__none__&node_id=__none__", None),
    ("GET", "/api/execution/projections/witnessed-runs?workflow_instance_id=__none__&node_id=__none__", None),
    ("GET", "/api/execution/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
]

# Volatile JSON fields: per-call timestamps, plus whole-body normalization for
# responses whose row windows can legitimately shift between the two reads.
TS_FIELDS = {"scanned_at", "generatedAt", "generated_at"}


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
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}: list lengths {len(left)} vs {len(right)}"
        for index, (litem, ritem) in enumerate(zip(left, right)):
            found = first_diff(litem, ritem, f"{path}[{index}]")
            if found:
                return found
        return None
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
            print(f"  A: {astatus} {atype} {abody[:300]!r}")
            print(f"  B: {bstatus} {btype} {bbody[:300]!r}")
            try:
                detail = first_diff(json.loads(anorm[1]), json.loads(bnorm[1]))
                if detail:
                    print(f"  {detail}")
            except Exception:
                pass

    print(f"\n{total - differences}/{total} byte-identical")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
