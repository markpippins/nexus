#!/usr/bin/env python3
"""Side-by-side canary diff: incumbent cascade-srv vs the moleculer port.

    python3 tools/canary-diff.py \
        --incumbent http://localhost:3106 --port http://localhost:4106

Issues the same requests to both implementations and byte-compares status +
body. Exit 0 = identical, 1 = differences (printed).

Caveat inherited from the surface: /cascade/events orders by event_timestamp
DESC and new events land continuously, so list bodies can shift between the
two calls. Every mismatch is therefore re-checked once, and a mismatch that
converges on recheck counts as a match. Time-of-request values
(/cascade/health `time`, /cascade/analytics buckets) are compared with the
volatile fields normalized.
"""
import argparse
import os
import re
import subprocess
import sys

INCUMBENT = "http://localhost:3106"
PORT = "http://localhost:4106"


def fetch(base, path, timeout="20"):
    p = subprocess.run(
        ["curl", "-s", "-w", "\n%{http_code}", "--max-time", timeout, base + path],
        capture_output=True,
        text=True,
    )
    out = p.stdout[:-1] if p.stdout.endswith("\n") else p.stdout
    body, _, code = out.rpartition("\n")
    return code.strip(), body


def normalize(body):
    """Blank out fields that legitimately differ call-to-call."""
    body = re.sub(r'"time":"[^"]*"', '"time":"<TIME>"', body)
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--incumbent", default=INCUMBENT)
    ap.add_argument("--port", default=PORT)
    args = ap.parse_args()

    reqs = [
        "/",
        "/cascade/health",
        "/cascade/events",
        "/cascade/events?limit=1",
        "/cascade/events?limit=500",          # clamp to 200
        "/cascade/events?limit=abc",          # fallback 50
        "/cascade/events?limit=5&offset=10",
        "/cascade/events?type=role.granted&limit=2",
        "/cascade/events?source=nebula-srv.role-leases&limit=2",
        "/cascade/events?aggregate_type=nonsense&limit=2",
        "/cascade/events/00000000-0000-0000-0000-000000000000",  # 404 envelope
        "/cascade/events/not-a-uuid",         # db error envelope
        "/cascade/lineage",                   # 400 envelope
        "/cascade/lineage?root=00000000-0000-0000-0000-000000000000",
        "/cascade/lineage?anchor=00000000-0000-0000-0000-000000000000&maxDepth=3",
        "/cascade/lineage?root=00000000-0000-0000-0000-000000000000&edgeType=triggered",
        "/cascade/analytics",
        "/cascade/analytics?range=7d&granularity=day",
        "/cascade/analytics?range=bogus&granularity=bogus",   # fallback window
        "/cascade/subscribers",
        "/cascade/subscribers/no-such-pattern",               # 404 envelope
        "/cascade/assessments?limit=2",
        "/cascade/assessments?outcome=bogus&limit=2",
        # PATCH with no fields → 400 {error:"No fields to update"}; the update
        # path itself is exercised read-only (both 404 on a bogus pattern)
        # because a real PATCH would mutate shared state under the incumbent.
        # The no-field 400 is safe (rejected before any write).
    ]

    # PATCH probes: body-less / empty-body via curl -X PATCH with no data.
    # moleculer-web aliases pass params from body; an absent body means
    # body?.enabled === undefined → 400 on both implementations.
    patch_paths = ["/cascade/subscribers/no-such-pattern"]

    print(f"comparing {len(reqs) + len(patch_paths)} requests: "
          f"{args.incumbent} (incumbent) vs {args.port} (port)\n")

    ok, diffs = 0, []

    def compare(path, method="GET"):
        nonlocal ok
        if method == "PATCH":
            def hit(base):
                p = subprocess.run(
                    ["curl", "-s", "-X", "PATCH", "-w", "\n%{http_code}",
                     "--max-time", "20", base + path],
                    capture_output=True, text=True)
                out = p.stdout[:-1] if p.stdout.endswith("\n") else p.stdout
                body, _, code = out.rpartition("\n")
                return code.strip(), body
        else:
            def hit(base):
                return fetch(base, path)

        c1, b1 = hit(args.incumbent)
        c2, b2 = hit(args.port)
        if c1 == c2 and normalize(b1) == normalize(b2):
            ok += 1
            print(f"  MATCH  {c1} {method} {path}")
            return
        c1b, b1b = hit(args.incumbent)
        c2b, b2b = hit(args.port)
        if c1b == c2b and normalize(b1b) == normalize(b2b):
            ok += 1
            print(f"  MATCH  {c1b} {method} {path} (stable on recheck)")
        else:
            diffs.append((f"{method} {path}", c1b, c2b, b1b, b2b))
            print(f"  DIFF   {method} {path}: incumbent={c1b} port={c2b}")

    for path in reqs:
        compare(path)
    for path in patch_paths:
        compare(path, method="PATCH")

    print(f"\n{ok}/{len(reqs) + len(patch_paths)} identical")
    if diffs:
        print("\n=== DIFFERENCES ===")
        for path, c1, c2, b1, b2 in diffs:
            print(f"\n--- {path}\n  incumbent [{c1}]: {b1[:400]}\n  port      [{c2}]: {b2[:400]}")
        return 1
    print("port is behaviorally identical on every probed path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
