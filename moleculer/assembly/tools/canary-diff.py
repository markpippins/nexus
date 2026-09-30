#!/usr/bin/env python3
"""Canary diff for assembly-srv :3107 vs moleculer twin :4107.

Reads + validation negatives ONLY. Every write case is a validation
negative that rejects BEFORE any DB work (verified against the incumbent's
handlers: POST /forums/:slug/threads requires title/body/postedById first;
POST /forums/by-id/:forumId/threads validates before the forum lookup).

Churn stability: forums/threads/comments are high-cadence (this very
pipeline posts to them), so NO case reads live row lists. Every case is a
needle with an identical response on both sides regardless of fleet
activity: unknown-slug/thread needles, validated-empty bodies, and the
static health surfaces.

Substance/nebula coupling: the segment-set cases exercise the READ-ONLY
proxy. Both incumbent and twin default to the incumbent substance/nebula
bases, so each side proxies the same upstream — identical bytes either
way (both sides fail identically with 502 if substance is down).
"""
import json
import os
import sys
import urllib.error
import urllib.request

A = os.environ.get("CANARY_BASE", "http://localhost:3107").rstrip("/")
B = os.environ.get("TWIN_BASE", "http://localhost:4107").rstrip("/")

UUID = "11111111-1111-1111-1111-111111111111"

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/health", None),
    # High-cadence tables → unknown needles only (never live lists):
    ("GET", "/api/forums/__no_such_forum_zzz__", None),
    ("GET", f"/api/forums/by-id/{UUID}", None),
    ("GET", "/api/forums/__no_such_forum_zzz__/threads", None),
    ("GET", f"/api/forums/threads/{UUID}", None),
    ("GET", "/api/work-requests?status=__no_such_status_zzz__", None),
    # Write negatives (reject BEFORE any DB work — verified against the
    # incumbent's validation order in routes/forums.js):
    ("POST", "/api/forums/x/threads", {}),
    ("POST", "/api/forums/by-id/not-a-uuid/threads", {"title": "t", "body": "b"}),
    ("POST", f"/api/forums/threads/{UUID}/comments", {}),
    # Segment-set evidence (read-only substance proxy; identical upstream
    # on both sides, identical 502 if substance is down):
    ("GET", "/api/segment-sets?limit=1", None),
    ("GET", f"/api/segment-sets/{UUID}", None),
    # 404 surface (assembly has an error-handler, but unknown paths fall
    # through to Express's default HTML 404 — compare verbatim):
    ("GET", "/api/definitely/not/a/route", None),
    ("GET", "/definitely/not/a/route", None),
]

TS_FIELDS = {
    "created_at", "updated_at", "verified_at", "checked_at",
    "started_at", "finished_at", "expires_at", "expiration_dt",
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
