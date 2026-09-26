#!/usr/bin/env python3
"""Read/validation-negative canary diff for harness-srv :3420 vs twin :4420.

Canary posture: reads + validation negatives ONLY.
  - /run, /run-direct spawn agent processes and write PG events, governance
    receipts, and nebula records — NEVER invoked.
  - The POST probes below are malformed-body negatives that return 400 before
    any admission/DB work.
  - GET /jobs/:jobId + /events are probed with a random UUID that will not
    exist on either process (in-memory registry — 404 envelope parity).
  - health/session shapes are normalized for port/uptime/elapsed fields.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3420").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4420").rstrip("/")

CASES = [
    ("GET", "/health", None),
    ("GET", "/sessions", None),
    ("GET", "/jobs/00000000-0000-4000-8000-000000000000", None),
    ("GET", "/jobs/not-a-uuid/events?after=0", None),
    ("POST", "/run", {}),
    ("POST", "/run", {"no_task": True}),
    ("POST", "/resolve-context", {}),
    ("POST", "/run-direct", {}),
    ("POST", "/run-direct", {"role": "engineer"}),
    ("POST", "/run-direct", {"prompt": "hi"}),
    ("POST", "/jobs/00000000-0000-4000-8000-000000000000/interrupt", None),
    ("GET", "/definitely/not/a/route", None),
]

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


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
    """Normalize per-process fields: port, uptime, elapsed/started times, job ids."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "port":
                out[k] = "<port>"
            elif k == "uptime":
                out[k] = "<uptime>"
            elif k in ("startedAt", "started_at", "elapsedSeconds"):
                out[k] = "<elapsed>"
            elif k in ("job_id", "jobId") and isinstance(v, str) and UUID_RE.match(v):
                out[k] = "<uuid>"
            else:
                out[k] = scrub(v)
        return out
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
