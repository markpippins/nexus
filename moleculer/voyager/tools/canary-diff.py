#!/usr/bin/env python3
"""Side-by-side canary diff: incumbent voyager-srv vs the moleculer port.

Issues the same read-only requests to both implementations and byte-compares
status + body. Both hit the same database, so any difference is a port defect —
or a live write landing between the two calls, which is why every mismatch is
re-checked once before it is reported.

    python3 tools/canary-diff.py \
        --incumbent http://localhost:3114 --port http://localhost:4114

Exit codes: 0 = identical on every probed path, 1 = differences (printed).

Request IDs are read from the database (the incumbent's ids are bigint for
scan epochs / entities and uuid elsewhere, so a hardcoded probe list would
mostly test error paths). Override the connection with PGHOST/PGUSER/PGDATABASE
/ PGPASSWORD.
"""
import argparse
import json
import os
import subprocess
import sys

# Commit-cited normalization — documented drift, not hidden drift.
# The LIVE incumbent at :3114 still emits these four zero-count keys in
# /api/stats: its dist predates 135e5565b (2026-08-08, "prune
# identity/entity/requirement routes — T04 physical observer only"), which
# removed them from routes.ts on main. The twin mirrors main's source, so it
# omits them. Normalized away (key dropped from both sides when present) so
# the stale-build artifact cannot mask real regressions; everything else in
# the body is compared strictly.
STATS_PRUNED_KEYS = [
    "identity_candidates",
    "entities",
    "entity_drifts",
    "requirement_candidates",
]


METHOD_PROBES = [
    ("POST", "/api/scan-epochs"),
    ("POST", "/health"),
]


def normalize_stats(body: str):
    """Drop the pre-prune zero-count keys; None when not a stats object."""
    try:
        d = json.loads(body)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    for k in STATS_PRUNED_KEYS:
        d.pop(k, None)
    return json.dumps(d, sort_keys=True)

PSQL = [
    "psql",
    "-h", os.environ.get("PGHOST", "localhost"),
    "-U", os.environ.get("PGUSER", "pguser"),
    "-d", os.environ.get("PGDATABASE", "nexus"),
    "-tAc",
]

PASSWORD = os.environ.get("PGPASSWORD", "pgpass")


def one(sql):
    """First row of a single-statement query.

    Schema-qualify the table instead of prefixing `set search_path=...` — two
    statements make psql print a leading `SET` line, which reads as a row.
    """
    p = subprocess.run(
        PSQL + [sql],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": PASSWORD},
    )
    if p.returncode != 0:
        return None
    return p.stdout.strip().splitlines()[0].strip() if p.stdout.strip() else None


def fetch(base, path, timeout="20", method=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "--max-time", timeout]
    if method:
        cmd += ["-X", method]
    cmd.append(base + path)
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    out = p.stdout[:-1] if p.stdout.endswith("\n") else p.stdout
    body, _, code = out.rpartition("\n")
    return code.strip(), body


def build_requests():
    epoch = one("select id from voyager.scan_epoch order by started_at desc limit 1")
    obs_id = one("select id from voyager.file_observation limit 1")
    obs_ext = one("select observation_id from voyager.file_observation limit 1")
    dev_id = one("select device_id from voyager.file_observation limit 1")
    sig_id = one("select id from voyager.topology_signal limit 1")
    span_id = one("select id from voyager.metadata_span limit 1")
    span_type = one("select span_type from voyager.metadata_span limit 1")
    print(
        f"ids: epoch={epoch} obs={obs_id} obs_ext={obs_ext} dev={dev_id} "
        f"signal={sig_id} span={span_id} span_type={span_type}"
    )

    reqs = ["/health", "/api/health"]

    # scan epochs
    reqs += [
        "/api/scan-epochs",
        "/api/scan-epochs?page=2&pageSize=5",
        "/api/scan-epochs?pageSize=1000",
        "/api/scan-epochs?page=0&pageSize=0",
        "/api/scan-epochs/00000000-0000-0000-0000-000000000000",
        "/api/scan-epochs/not-a-uuid",
    ]
    if epoch:
        reqs.append(f"/api/scan-epochs/{epoch}")

    # file / directory observations
    reqs += [
        "/api/observations/files?pageSize=3",
        "/api/observations/files?pageSize=2&page=3",
        "/api/observations/files?pageSize=2&path=.md",
        "/api/observations/files/by-id/nope",
        "/api/observations/files/00000000-0000-0000-0000-000000000000",
        "/api/observations/directories?pageSize=3",
        "/api/observations/directories?pageSize=2&path=nexus",
    ]
    if dev_id:
        reqs.append(f"/api/observations/files?pageSize=2&deviceId={dev_id}")
    if epoch:
        reqs.append(f"/api/observations/files?pageSize=2&scanEpochId={epoch}")
    if obs_ext:
        reqs.append(f"/api/observations/files/by-id/{obs_ext}")
    if obs_id:
        reqs.append(f"/api/observations/files/{obs_id}")

    # topology
    reqs += [
        "/api/topology/signals?pageSize=3",
        "/api/topology/signals?pageSize=2&structureType=directory",
        "/api/topology/signals/00000000-0000-0000-0000-000000000000",
        "/api/topology/edge-hints?pageSize=3",
        "/api/topology/edge-hints?pageSize=2&minConfidence=0.5",
    ]
    if sig_id:
        reqs.append(f"/api/topology/signals/{sig_id}")

    # entities
    reqs += [
        "/api/entities",
        "/api/entities?pageSize=5&minStability=0.5",
        "/api/entities/by-id/nope",
        "/api/entities/00000000-0000-0000-0000-000000000000",
    ]

    # spans
    reqs += [
        "/api/spans?pageSize=3",
        "/api/spans?pageSize=2&minConfidence=0.5",
        "/api/spans/00000000-0000-0000-0000-000000000000",
    ]
    if span_type:
        reqs.append(f"/api/spans?pageSize=2&spanType={span_type}")
    if span_id:
        reqs.append(f"/api/spans/{span_id}")

    reqs.append("/api/stats")

    # Unmatched routes — the incumbent is Express, whose finalhandler prints
    # the default HTML error page (Cannot <method> <originalUrl>). Both mounts
    # plus the root prefix-of-last-resort, exact-bytes compared below. The
    # bare mount (/api) matters: that path used to render as /api/ when the
    # twin reconstructed it from route.path + req.url.
    reqs += [
        "/api/definitely/not/a/route",
        "/api",
        "/api/",
        "/topology/signals",          # real route shape, missing /api prefix
        "/definitely/not/a/route",
        "/?x=1",                      # query excluded: finalhandler prints parseurl pathname
        "/api/stats?x=1",             # query on a MATCHED route stays JSON
    ]
    return reqs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--incumbent", default="http://localhost:3114")
    ap.add_argument("--port", default="http://localhost:4114")
    args = ap.parse_args()

    reqs = build_requests()
    print(f"\ncomparing {len(reqs)} requests: {args.incumbent} (incumbent) vs {args.port} (port)\n")

    ok, diffs = 0, []
    total = len(reqs) + len(METHOD_PROBES)

    def probe(method, path):
        """A/B one request with one re-check; returns (ok, detail)."""
        c1, b1 = fetch(args.incumbent, path, method=method)
        c2, b2 = fetch(args.port, path, method=method)
        if c1 == c2 and b1 == b2:
            return True, (c1, c2, b1, b2)
        c1b, b1b = fetch(args.incumbent, path, method=method)   # re-check: a write may have landed mid-probe
        c2b, b2b = fetch(args.port, path, method=method)
        if c1b == c2b and b1b == b2b:
            return True, (c1b, c2b, b1b, b2b)
        return False, (c1b, c2b, b1b, b2b)

    for path in reqs:
        good, (c1, c2, b1, b2) = probe(None, path)
        # /api/stats carries the one commit-cited normalization (stale
        # incumbent dist still emits the pre-135e5565b zero-count keys).
        if path.startswith("/api/stats") and normalize_stats(b1) is not None \
                and normalize_stats(b1) == normalize_stats(b2):
            ok += 1
            print(f"  MATCH  {c1} {path} (normalized: pre-prune zero-count keys)")
            continue
        if good:
            ok += 1
            print(f"  MATCH  {c1} {path}")
            continue
        diffs.append((path, c1, c2, b1, b2))
        print(f"  DIFF   {path}: incumbent={c1} port={c2}")

    # Wrong-method probes: the incumbent surface is GET-only, so a POST to
    # any path falls through to Express finalhandler (HTML 404) — never a
    # 405. The twin's aliases are GET-only too, so moleculer-web must take
    # the same path (NotFoundError → onError HTML emulation).
    for method, path in METHOD_PROBES:
        label = f"[{method}] {path}"
        good, (c1, c2, b1, b2) = probe(method, path)
        if good and c1 == "404" and b1.lstrip().startswith("<!DOCTYPE html>") and f"Cannot {method}" in b1:
            ok += 1
            print(f"  MATCH  {c1} {label} (finalhandler HTML)")
            continue
        diffs.append((label, c1, c2, b1, b2))
        print(f"  DIFF   {label}: incumbent={c1} port={c2}")

    print(f"\n{ok}/{total} probes identical")
    if diffs:
        print("\n=== DIFFERENCES ===")
        for path, c1, c2, b1, b2 in diffs:
            print(f"\n--- {path}\n  incumbent [{c1}]: {b1[:400]}\n  port      [{c2}]: {b2[:400]}")
        return 1
    print("port is behaviorally identical on every probed path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
