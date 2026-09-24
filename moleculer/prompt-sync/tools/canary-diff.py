#!/usr/bin/env python3
"""Live canary diff: moleculer/prompt-sync (:4501) vs tackle-prompt-sync-srv (:3501).

A/B against the SAME shared live Redis + PG — the state is common, so the
diff isolates twin-vs-incumbent behavior, not state drift.

Cases:
  - /health (normalize: uptime is per-process)
  - /prompts/:role for the roles present in the shared cache (enumerated
    from Redis) + a missing role → [] (200)
  - /prompt/:role/:slug for sampled cards + missing slug → 404 exact string
  - /tasks/:role for the roles with task indices + missing role → [] (200)
  - POST /refresh on BOTH twins (shared-cache convergence; envelope diff,
    timestamp normalized)
  - post-refresh health parity again (both see the fresh shared stamp)
  - unmatched route → Express HTML 404 parity

Byte-identical after the declared normalizations = pass.
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

A = "http://localhost:3501"
B = "http://localhost:4501"

results = []
failures = 0


def fetch(base, path, method="GET", payload=None):
    url = base + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
    # Enumerate roles present in the shared cache (read-only redis-cli).
    def scan(pattern, prefix):
        out = subprocess.run(
            ["redis-cli", "--scan", "--pattern", pattern],
            capture_output=True, text=True,
        ).stdout
        return sorted(
            line[len(prefix):] for line in out.splitlines() if line.strip()
        )

    prompt_roles = scan("prompt:idx:*", "prompt:idx:")
    task_roles = scan("task:idx:*", "task:idx:")

    # 1. health — normalized (uptime per-process)
    sa, ca, ba = fetch(A, "/health")
    sb, cb, bb = fetch(B, "/health")
    case(
        "health (normalized: uptime per-process)",
        sa == sb and normalize_health(ba) == normalize_health(bb),
        f"A={sa} {ba[:140]} | B={sb} {bb[:140]}",
    )

    # 2. prompts/:role — every cached role + a missing one
    for role in prompt_roles + ["no-such-role"]:
        sa, ca, ba = fetch(A, f"/prompts/{role}")
        sb, cb, bb = fetch(B, f"/prompts/{role}")
        case(f"prompts/{role}", (sa, ba) == (sb, bb), f"A={sa} B={sb} lenA={len(ba)} lenB={len(bb)}")

    # 3. prompt/:role/:slug — sample cards via the first role index + a miss
    _, _, idx_body = fetch(A, f"/prompts/{prompt_roles[0] if prompt_roles else 'engineer'}")
    try:
        idx = json.loads(idx_body)
    except Exception:
        idx = []
    role0 = prompt_roles[0] if prompt_roles else "engineer"
    slugs = [e["slug"] for e in idx[:5]] + ["nonexistent-slug-zzz"]
    for slug in slugs:
        sa, ca, ba = fetch(A, f"/prompt/{role0}/{slug}")
        sb, cb, bb = fetch(B, f"/prompt/{role0}/{slug}")
        case(
            f"prompt/{role0}/{slug}",
            (sa, ba) == (sb, bb),
            f"A={sa} B={sb}",
        )

    # 4. tasks/:role — every task-index role + a missing one
    for role in task_roles + ["no-such-role"]:
        sa, ca, ba = fetch(A, f"/tasks/{role}")
        sb, cb, bb = fetch(B, f"/tasks/{role}")
        case(f"tasks/{role}", (sa, ba) == (sb, bb), f"A={sa} B={sb} lenA={len(ba)} lenB={len(bb)}")

    # 5. unmatched route → Express HTML 404 parity (exact bytes)
    sa, ca, ba = fetch(A, "/definitely/not/a/route")
    sb, cb, bb = fetch(B, "/definitely/not/a/route")
    case(
        "unmatched route → Express HTML 404",
        sa == sb == 404 and ca == cb and ba == bb,
        f"A={sa}/{ca} B={sb}/{cb}",
    )

    # 6. POST /refresh — both twins converge the shared cache from PG.
    # Envelope parity: {prompts, rolePromptIndices, tasks, roleTaskIndices,
    # timestamp}. timestamp is per-call (the write instant) — normalize it.
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
        f"A={sa} {ba[:140]} | B={sb} {bb[:140]}",
    )

    # 7. post-refresh: health parity again (both see the fresh shared stamp)
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
        "run_id": f"prompt-sync-canary-{stamp}",
        "date": datetime.now(timezone.utc).isoformat(),
        "incumbent": A,
        "twin": B,
        "cases": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "verdict": "IDENTICAL" if failures == 0 else "DIFFER",
    }
    path = f"/tmp/prompt-sync-canary-{stamp}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"results: {path}")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
