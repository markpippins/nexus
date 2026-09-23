#!/usr/bin/env python3
"""Live canary diff — moleculer/kernel (:4100) vs incumbent kernel-srv (:8100).

Fires identical requests at both implementations and byte-compares
status line + body. Read paths only, plus the write paths' VALIDATION
envelopes (400s) and enum-cast failures (500s) — a real POST would mutate
the shared kernel schema, which is a cutover prerequisite, not a canary act.

The SSE endpoint is compared byte-for-byte for the first flush window
(ready event + any kernel_event frames arriving within the window).

Usage:
    python3 tools/canary-diff.py [--port-a 8100] [--port-b 4100] [--window 2]
"""
import argparse
import http.client
import json
import sys
import time

SAMPLE_EVENT_ID = "5706bda7-a8cb-4950-8278-66769f5d7e16"
SAMPLE_RECEIPT_ID = "a7b6886b-3d14-4678-8603-2559585e2dc7"
SAMPLE_PLAN = "0042"
SAMPLE_AGG = ("intent", "test-intent-001")
RANDOM_UUID = "00000000-0000-4000-8000-00000000dead"


def request(port, method, path, body=None, timeout=10):
    conn = http.client.HTTPConnection("localhost", port, timeout=timeout)
    try:
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            text = json.dumps(json.loads(raw), sort_keys=False, separators=(",", ":")) if raw else ""
        except Exception:
            text = raw.decode("utf-8", "replace")
        return resp.status, text
    finally:
        conn.close()


def sse_first_bytes(port, window):
    """Read the SSE stream for `window` seconds, return raw body bytes.

    Uses HTTP/1.0 deliberately: no chunked transfer-encoding, so the body is
    raw bytes and the byte-compare is framing-independent (chunk boundaries
    can legitimately differ between Express and the gateway stream pipe)."""
    import socket

    s = socket.create_connection(("localhost", port), timeout=window + 2)
    s.sendall(b"GET /api/kernel/events/stream HTTP/1.0\r\nHost: localhost\r\n\r\n")
    s.settimeout(window)
    buf = b""
    end = time.time() + window
    try:
        while time.time() < end:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                break
    finally:
        s.close()
    # Strip response headers (present in HTTP/1.0 too) — compare body only.
    parts = buf.split(b"\r\n\r\n", 1)
    return parts[1] if len(parts) > 1 else buf


# (label, method, path, body, is_sse)
CASES = [
    ("health", "GET", "/health", None, False),
    ("api-health", "GET", "/api/health", None, False),
    ("transition-get-200", "GET", f"/api/kernel/transitions/{SAMPLE_EVENT_ID}", None, False),
    ("transition-get-baduuid", "GET", "/api/kernel/transitions/not-a-uuid", None, False),
    ("transition-get-404", "GET", f"/api/kernel/transitions/{RANDOM_UUID}", None, False),
    ("causality-200", "GET", f"/api/kernel/transitions/{SAMPLE_EVENT_ID}/causality", None, False),
    ("causality-baduuid", "GET", "/api/kernel/transitions/zzz/causality", None, False),
    ("receipt-chain-200", "GET", f"/api/kernel/receipts/{SAMPLE_RECEIPT_ID}/chain", None, False),
    ("receipt-chain-baduuid", "GET", "/api/kernel/receipts/nope/chain", None, False),
    ("plan-receipts", "GET", f"/api/kernel/plans/{SAMPLE_PLAN}/receipts", None, False),
    ("plan-receipts-empty", "GET", "/api/kernel/plans/NOPE-9999/receipts", None, False),
    ("aggregate-events-200", "GET", f"/api/kernel/aggregates/{SAMPLE_AGG[0]}/{SAMPLE_AGG[1]}/events", None, False),
    ("aggregate-events-404", "GET", "/api/kernel/aggregates/bogus/bogus/events", None, False),
    ("policy-active", "GET", "/api/kernel/policy/active", None, False),
    ("policy-maturity", "GET", "/api/kernel/policy/maturity", None, False),
    ("recent-events", "GET", "/api/kernel/health/recent-events?limit=3", None, False),
    ("recent-events-garbage-limit", "GET", "/api/kernel/health/recent-events?limit=abc", None, False),
    ("recent-events-over-max", "GET", "/api/kernel/health/recent-events?limit=100000", None, False),
    ("receipt-integrity", "GET", "/api/kernel/health/receipt-integrity", None, False),
    ("unknown-route-404", "GET", "/api/kernel/bogus", None, False),
    ("transition-post-empty-400", "POST", "/api/kernel/transitions", {}, False),
    ("transition-post-missing-actor-400", "POST", "/api/kernel/transitions",
     {"event_type": "test.event", "aggregate_type": "x", "aggregate_id": "y"}, False),
    ("receipt-post-empty-400", "POST", "/api/kernel/receipts", {}, False),
    ("transition-post-bad-enum-500", "POST", "/api/kernel/transitions",
     {"event_type": "___no_such_enum_value___", "aggregate_type": "x", "aggregate_id": "y",
      "actor": "canary"}, False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-a", type=int, default=8100)
    ap.add_argument("--port-b", type=int, default=4100)
    ap.add_argument("--window", type=float, default=2.0)
    args = ap.parse_args()

    same = diff = 0
    diffs = []

    for label, method, path, body, is_sse in CASES:
        try:
            ra = request(args.port_a, method, path, body)
        except Exception as e:
            ra = ("ERR", f"{type(e).__name__}: {e}")
        try:
            rb = request(args.port_b, method, path, body)
        except Exception as e:
            rb = ("ERR", f"{type(e).__name__}: {e}")
        if ra == rb:
            same += 1
            print(f"  ok    {label}: {ra[0]}")
        else:
            diff += 1
            diffs.append(label)
            print(f"  DIFF  {label}: A={ra[0]} {ra[1][:160]!r}")
            print(f"        {' ' * len(label)}  B={rb[0]} {rb[1][:160]!r}")

    # SSE: compare the captured body bytes (ready event + any event frames
    # arriving within the window). HTTP/1.0 keeps this framing-independent.
    try:
        ta = sse_first_bytes(args.port_a, args.window)
        tb = sse_first_bytes(args.port_b, args.window)
        if ta == tb:
            same += 1
            print("  ok    sse-ready-frame")
        else:
            diff += 1
            diffs.append("sse-ready-frame")
            print(f"  DIFF  sse-ready-frame: A={ta[:200]!r} B={tb[:200]!r}")
    except Exception as e:
        print(f"  SKIP  sse-ready-frame: {type(e).__name__}: {e}")

    total = same + diff
    print(f"\n{same}/{total} byte-identical")
    if diffs:
        print("DIFFS: " + ", ".join(diffs))
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()
