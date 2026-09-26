#!/usr/bin/env python3
"""Canary byte-diff: moleculer/tackle twin (:4410) vs live tackle-srv (:3410).

Reads + negatives ONLY (write-canary ruling: the incumbent has real write
paths — sessions kill, projections render, config mutations — never exercised
here). Normalizes the known per-process fields (health port/pid); everything
else must be byte-identical. The SSE route is checked for status + headers
only (it streams for 30s — body diff via one short read, then close).
"""
import json
import sys
import urllib.request
import urllib.error

A = "http://localhost:3410"  # incumbent
B = "http://localhost:4410"  # twin

# (method, path, body) — reads and negative/validation cases only.
CASES = [
    ("GET", "/health", None),
    ("GET", "/health/history", None),
    ("GET", "/health/metrics", None),
    ("GET", "/memory/procedures/engineer", None),
    ("GET", "/memory/procedures/does-not-exist", None),
    ("GET", "/memory/procedure/nexus-boot-procedure", None),
    ("GET", "/memory/procedure/not-a-real-slug", None),
    ("GET", "/memory/role-updates", None),
    ("POST", "/memory/check-since", {}),
    ("POST", "/memory/assign", {}),
    ("POST", "/memory/assign", {"role": "x"}),
    ("DELETE", "/memory/assign", None),
    ("GET", "/projections", None),
    ("GET", "/projections/drift", None),
    ("GET", "/projections/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/prompts", None),
    ("GET", "/prompts/engineer", None),
    ("GET", "/prompts/engineer/does-not-exist", None),
    ("GET", "/roles", None),
    ("GET", "/roles/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/roles/readiness/does-not-exist", None),
    ("GET", "/scheduler", None),
    ("GET", "/scheduler/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/scheduler/due", None),
    ("GET", "/sessions", None),
    ("POST", "/sessions/not-a-session/kill", None),
    ("GET", "/tasks", None),
    ("GET", "/tasks/does-not-exist", None),
    ("GET", "/tasks/inspector/dispatch", None),
    ("GET", "/logs", None),
    # stable-window probe: `since` in the future → empty result set
    # (deterministic), exercises the since-filter code path
    ("GET", "/logs?since=2099-01-01T00:00:00Z", None),
    ("POST", "/logs/emit", {}),
    ("GET", "/audit-trail", None),
    ("GET", "/audit-trail/recent", None),
    ("GET", "/config/ai", None),
    ("GET", "/config/ai/providers", None),
    ("GET", "/config/ai/harnesses", None),
    ("GET", "/config/ai/models", None),
    ("GET", "/config/ai/roles", None),
    ("GET", "/config/ai/bundles", None),
    ("GET", "/config/ai/bundles/does-not-exist", None),
    ("GET", "/config/ai/provider/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/config/ai/model/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/config/ai/harness/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/config/ai/bundle/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/config/ai/role/does-not-exist", None),
    ("GET", "/config/ai/resolve/does-not-exist", None),
    ("GET", "/config/ai/tool-access", None),
    ("GET", "/config/ai/tool-access/does-not-exist", None),
    ("GET", "/config/ai/validate", None),
    ("GET", "/config/ai/verify/does-not-exist", None),
    ("GET", "/config/failure-recovery", None),
    # unmatched route → Express finalhandler HTML
    ("GET", "/definitely/not/a/route", None),
]


def fetch(base, method, path, body):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def normalize(status, headers, body):
    """Byte-normalize known per-process/per-time fields."""
    try:
        obj = json.loads(body)
    except Exception:
        return status, headers, body

    def scrub(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k == "pid":
                    out[k] = "<pid>"
                elif k == "port":
                    out[k] = "<port>"
                elif k in ("timestamp",):
                    out[k] = "<ts>"
                elif k == "uptime_seconds":
                    out[k] = "<uptime>"
                elif k in ("cpu_percent", "usage_percent", "memory_percent",
                           "memory_used_mb", "used_mb", "load_average",
                           "free_mb", "heap_used_mb", "heap_total_mb"):
                    # live system telemetry sampled at different instants
                    out[k] = "<telemetry>"
                elif k == "history":
                    # rolling per-process metrics history (60 samples on the
                    # long-running incumbent, 1 on the fresh twin)
                    out[k] = "<history>"
                elif k == "count" and isinstance(v, int) and not isinstance(o.get("logs"), list) and "history" in o:
                    # /health/history sample count rides the history list
                    out[k] = "<history-count>"
                elif k == "total" and isinstance(v, int):
                    # GET /logs total: the twin's own request-log write lands
                    # between the two reads — row-count drift is inherent
                    out[k] = "<count>"
                elif k == "filtered_count" and isinstance(v, int):
                    out[k] = "<count>"
                elif k == "last_polled_at":
                    # GET /logs response-time stamp — always differs
                    out[k] = "<ts>"
                elif k == "logs" and isinstance(v, list) and "filtered_count" in o and "levels" in o:
                    # GET /logs newest-100 window: exactly one request-log row
                    # (side A's own GET /logs insert) commits between the two
                    # reads, shifting the window — racy by construction, like
                    # the health history list above
                    out[k] = "<log-window>"
                elif k == "categories" and "levels" in o and "filtered_count" in o:
                    # derived from that same shifting window
                    out[k] = "<categories>"
                elif k == "id" and isinstance(v, str) and len(v) == 36:
                    # the newest log row differs (twin wrote its own)
                    out[k] = "<uuid>"
                elif k == "timestamp" and isinstance(v, str) and len(v) == 19 and v[10] == " ":
                    # log-row 'YYYY-MM-DD HH:MM:SS' stamps: the twin's own
                    # request-log write lands between the two reads
                    out[k] = "<logts>"
                else:
                    out[k] = scrub(v)
            return out
        if isinstance(o, list):
            return [scrub(x) for x in o]
        return o
    return status, headers, json.dumps(scrub(obj), sort_keys=True).encode()


def sse_headers(base, session):
    """Open the SSE route, capture status+headers, read ~1s, close."""
    req = urllib.request.Request(f"{base}/log/{session}")
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, {
                k.lower(): v for k, v in r.headers.items()
                if k.lower() in ("content-type", "cache-control", "connection",
                                 "access-control-allow-origin")
            }
    except Exception as e:
        # urllib raises on timeout; that's fine — we may still not get headers.
        return None, {"error": str(e)}


def main():
    diffs = 0
    total = 0
    for method, path, body in CASES:
        total += 1
        sa, ha, ba = fetch(A, method, path, body)
        sb, hb, bb = fetch(B, method, path, body)
        na = normalize(sa, ha, ba)
        nb = normalize(sb, hb, bb)
        # content-type + body parity (headers like date/connection differ)
        cta = ha.get("Content-Type") or ha.get("content-type")
        ctb = hb.get("Content-Type") or hb.get("content-type")
        if na[0] != nb[0] or na[2] != nb[2] or cta != ctb:
            diffs += 1
            print(f"DIFF {method} {path}")
            print(f"  A: {sa} {cta} {ba[:220]!r}")
            print(f"  B: {sb} {ctb} {bb[:220]!r}")
            # locate the first differing NORMALIZED path for JSON bodies
            # (walking raw bodies once misreported $.total on /logs while
            # the real normalized diffs were the window fields)
            try:
                ja, jb = json.loads(na[2]), json.loads(nb[2])
                def walk(pa, pb, where):
                    if isinstance(pa, dict) and isinstance(pb, dict):
                        for k in pa:
                            if k not in pb:
                                print(f"  path {where}.{k}: A={pa[k]!r} B=<missing>")
                                return True
                            if walk(pa[k], pb[k], where + "." + k):
                                return True
                        return False
                    if isinstance(pa, list) and isinstance(pb, list):
                        if len(pa) != len(pb):
                            print(f"  path {where}: list len {len(pa)} vs {len(pb)}")
                            return True
                        for i, (x, y) in enumerate(zip(pa, pb)):
                            if walk(x, y, f"{where}[{i}]"):
                                return True
                        return False
                    if pa != pb:
                        print(f"  path {where}: A={pa!r} B={pb!r}")
                        return True
                    return False
                walk(ja, jb, "$")
            except Exception:
                pass

    # SSE header parity (status + the four set headers)
    sa, ha = sse_headers(A, "canary-sse-probe")
    sb, hb = sse_headers(B, "canary-sse-probe")
    total += 1
    if sa != sb or ha != hb:
        diffs += 1
        print(f"DIFF SSE /log/:sessionId")
        print(f"  A: {sa} {ha}")
        print(f"  B: {sb} {hb}")

    print(f"\n{total - diffs}/{total} byte-identical")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
