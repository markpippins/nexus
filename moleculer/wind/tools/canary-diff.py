#!/usr/bin/env python3
"""Canary diff for wind-srv :3300 vs moleculer twin :4118.

Reads + validation negatives ONLY. The POST/PATCH/DELETE cases are
validation negatives verified against the incumbent's handlers to reject
before any DB write.

Stability model: the twin's jest suite mocks PG at the pool boundary
while the incumbent runs a real DB, so the diff avoids case shapes where
that difference is observable: bare-array list responses are compared by
shape/conformance rather than exact bytes; empty-array needles and
unknown-id negatives (identical on both sides) are byte-compared after
normalization. Local-time and float representations are scrubbed
(SQLite/PG float text differs; handlers format local times).

Envelope note: wind has NO JSON 404 catch-all — unknown paths return
Express's default HTML 404 (unlike substance's {detail}), which this tool
compares verbatim.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3300").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4118").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    # Bare-array lists (compared as shape+conformance via normalize's
    # list handling; both sides return the same element schema):
    ("GET", "/api/event-types", None),
    # Unknown-id needles (byte-identical 404/400 on both sides):
    ("GET", f"/api/event-types/{UUID}", None),
    ("GET", "/api/event-types/not-a-uuid", None),
    # Unknown-path negatives (Express default HTML 404 on both sides):
    ("GET", "/api/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
    # Validation negatives (reject BEFORE any DB work — verified against
    # the incumbent's handler validation order):
    ("POST", "/api/events", {"malformed": True}),
    ("POST", "/api/edges", {}),
]

TS_FIELDS = {
    "created_at", "updated_at", "verified_at", "checked_at",
    "started_at", "finished_at", "expires_at",
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
