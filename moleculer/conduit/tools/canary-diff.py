#!/usr/bin/env python3
"""Read/negative canary diff for conduit-srv :3104 vs moleculer twin :4104.

Write routes that mutate pipeline state are intentionally omitted. The only POST
probe is the validation-negative work-request request, which returns before SQL.
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3104").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4104").rstrip("/")

CASES = [
    ("GET", "/", None),
    ("GET", "/health", None),
    ("GET", "/workflows", None),
    ("GET", "/tickets/lineage/canary-missing", None),
    ("GET", "/tokens/plan/canary-missing", None),
    ("GET", "/tokens/role/engineer", None),
    ("GET", "/tokens/ticket/canary-missing", None),
    ("GET", "/config/cron", None),
    ("GET", "/config/failure-recovery", None),
    ("GET", "/log/bad.id", None),
    ("GET", "/governance/events?limit=1", None),
    ("GET", "/vision/work-requests?limit=1", None),
    ("GET", "/vision/work-requests/canary-missing", None),
    ("GET", "/vision/receipts", None),
    ("GET", "/wr/00000000-0000-0000-0000-000000000000/projection-drift", None),
    ("GET", "/wr/drift-scan?limit=1", None),
    ("POST", "/vision/work-requests", {}),
    ("GET", "/definitely/not/a/route", None),
]


def fetch(base, method, path, body):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def scrub(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ("timestamp", "created_at", "updated_at", "recorded_on_dt", "start_iso", "end_iso"):
                result[key] = "<timestamp>"
            elif key == "port":
                result[key] = "<port>"
            elif key == "pid":
                result[key] = "<pid>"
            else:
                result[key] = scrub(item)
        return result
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


def sse_headers(base, session):
    req = urllib.request.Request(f"{base}/log/{session}")
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            picked = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in (
                    "content-type", "cache-control", "connection",
                    "access-control-allow-origin",
                )
            }
            return response.status, picked
    except Exception as err:
        return None, {"error": str(err)}


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

    total += 1
    asse, ahead = sse_headers(A, "canary-sse-probe")
    bsse, bhead = sse_headers(B, "canary-sse-probe")
    if asse != bsse or ahead != bhead:
        differences += 1
        print("DIFF SSE /log/:sessionId")
        print(f"  A: {asse} {ahead}")
        print(f"  B: {bsse} {bhead}")

    print(f"\n{total - differences}/{total} byte-identical")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
