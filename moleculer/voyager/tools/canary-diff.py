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
import os
import subprocess
import sys

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


def fetch(base, path, timeout="20"):
    p = subprocess.run(
        ["curl", "-s", "-w", "\n%{http_code}", "--max-time", timeout, base + path],
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
    return reqs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--incumbent", default="http://localhost:3114")
    ap.add_argument("--port", default="http://localhost:4114")
    args = ap.parse_args()

    reqs = build_requests()
    print(f"\ncomparing {len(reqs)} requests: {args.incumbent} (incumbent) vs {args.port} (port)\n")

    ok, diffs = 0, []
    for path in reqs:
        c1, b1 = fetch(args.incumbent, path)
        c2, b2 = fetch(args.port, path)
        if c1 == c2 and b1 == b2:
            ok += 1
            print(f"  MATCH  {c1} {path}")
            continue
        c1b, b1b = fetch(args.incumbent, path)   # re-check: a write may have landed mid-probe
        c2b, b2b = fetch(args.port, path)
        if c1b == c2b and b1b == b2b:
            ok += 1
            print(f"  MATCH  {c1b} {path} (stable on recheck)")
        else:
            diffs.append((path, c1b, c2b, b1b, b2b))
            print(f"  DIFF   {path}: incumbent={c1b} port={c2b}")

    print(f"\n{ok}/{len(reqs)} identical")
    if diffs:
        print("\n=== DIFFERENCES ===")
        for path, c1, c2, b1, b2 in diffs:
            print(f"\n--- {path}\n  incumbent [{c1}]: {b1[:400]}\n  port      [{c2}]: {b2[:400]}")
        return 1
    print("port is behaviorally identical on every probed path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
