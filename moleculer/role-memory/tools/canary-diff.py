#!/usr/bin/env python3
"""Live canary diff: moleculer/role-memory (:4150) vs typescript/role-memory-srv (:3500).

A/B against the SAME shared live Redis + PG — the state is common, so the
diff isolates twin-vs-incumbent behavior, not state drift.

Cases:
  - /health (normalize: uptime is per-process, stale is time-dependent)
  - /procedures/:role for every role visible in the incumbent's index set
  - /procedures/missing-role → [] (200)
  - /procedure/:slug for sampled cards + missing slug → 404 exact string
  - POST /refresh on BOTH twins (shared-cache convergence; envelope diff)
  - unmatched route → Express HTML 404 parity

Byte-identical after the declared normalizations = pass.
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

A = "http://localhost:3500"
B = "http://localhost:4150"

results = []
failures = 0


def fetch(base, path, method="GET", payload=None):
    url = base + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            return r.status, r.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode()


def normalize_health(body: str) -> str:
    d = json.loads(body)
    d.pop("uptime", None)  # per-process by design
    return json.dumps(d, sort_keys=True)


def case(name, ok, detail=""):
    global failures
    results.append((name, ok, detail))
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def main():
    # 1. health — normalized (uptime per-process; stale time-dependent)
    sa, ca, ba = fetch(A, "/health")
    sb, cb, bb = fetch(B, "/health")
    case(
        "health (normalized: uptime per-process)",
        sa == sb and normalize_health(ba) == normalize_health(bb),
        f"A={sa} {ba[:120]} | B={sb} {bb[:120]}",
    )

    # 2. procedures/:role — enumerate roles present in the shared cache by
    # probing the roles the fleet uses (incumbent idx keys via health counts
    # can't enumerate; probe known roles + a missing one).
    roles = [
        "engineer", "architect", "planner", "dba", "tester", "analyst",
        "reviewer", "operator", "devops", "inspector", "lead-engineer",
        "engineer-ii", "engineer-iii", "topologist", "design-synthesist",
        "layout-mechanic", "ontologist", "sound-technician", "critic",
        "no-such-role",
    ]
    for role in roles:
        sa, ca, ba = fetch(A, f"/procedures/{role}")
        sb, cb, bb = fetch(B, f"/procedures/{role}")
        case(f"procedures/{role}", (sa, ba) == (sb, bb), f"A={sa} B={sb} lenA={len(ba)} lenB={len(bb)}")

    # 3. procedure/:slug — sample cards via the engineer index + a missing slug
    _, _, idx_body = fetch(A, "/procedures/engineer")
    try:
        idx = json.loads(idx_body)
    except Exception:
        idx = []
    slugs = [e["slug"] for e in idx[:5]] + ["nonexistent-slug-zzz"]
    for slug in slugs:
        sa, ca, ba = fetch(A, f"/procedure/{slug}")
        sb, cb, bb = fetch(B, f"/procedure/{slug}")
        case(
            f"procedure/{slug}",
            (sa, ba) == (sb, bb),
            f"A={sa} B={sb}",
        )

    # 4. unmatched route → Express HTML 404 parity (exact bytes)
    sa, ca, ba = fetch(A, "/definitely/not/a/route")
    sb, cb, bb = fetch(B, "/definitely/not/a/route")
    case(
        "unmatched route → Express HTML 404",
        sa == sb == 404 and ca == cb and ba == bb,
        f"A={sa}/{ca} B={sb}/{cb}",
    )

    # 5. POST /refresh — both twins converge the shared cache from PG.
    # Envelope parity: {procedures, roleIndices, timestamp}. timestamp is
    # per-call (the write instant) — normalize it, compare the counts.
    sa, ca, ba = fetch(A, "/refresh", method="POST", payload={})
    sb, cb, bb = fetch(B, "/refresh", method="POST", payload={})

    def norm_refresh(body):
        try:
            d = json.loads(body)
            if isinstance(d, dict) and "timestamp" in d:
                d.pop("timestamp")
            return json.dumps(d, sort_keys=True)
        except Exception:
            return f"UNPARSEABLE {body[:100]}"

    case(
        "POST /refresh (normalized: timestamp per-call)",
        sa == sb and norm_refresh(ba) == norm_refresh(bb),
        f"A={sa} {ba[:120]} | B={sb} {bb[:120]}",
    )

    # 6. post-refresh: health parity again (both see the fresh shared stamp)
    sa, _, ba = fetch(A, "/health")
    sb, _, bb = fetch(B, "/health")
    case(
        "health post-refresh (normalized)",
        sa == sb and normalize_health(ba) == normalize_health(bb),
        f"A={sa} | B={sb}",
    )

    print()
    print(f"{len(results) - failures}/{len(results)} cases identical")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "run_id": f"role-memory-canary-{stamp}",
        "date": datetime.now(timezone.utc).isoformat(),
        "incumbent": A,
        "twin": B,
        "cases": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "verdict": "IDENTICAL" if failures == 0 else "DIFFER",
    }
    path = f"/tmp/role-memory-canary-{stamp}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"results: {path}")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
