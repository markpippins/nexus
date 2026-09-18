#!/usr/bin/env python3
"""Gated repair of registry.services.health_check_path (audit thread 70d507dc).

Reads the per-service verified truth table produced by the health-path
conformance audit and repairs registry.services rows via PUT /api/v1/services/{id}.
Every write is recorded (id, name, old_value, new_value, verified_path,
probe_evidence) into a JSONL audit file.

--dry-run is the default and is also the CI/test posture: the script makes no
network calls at all unless --live is passed. A live run additionally requires
--i-understand-live-writes. This implements the operator gate the audit thread
demands: no registry data mutation without explicit human intent, and a
reviewed, verified truth table as the only accepted input.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_REGISTRY = "http://localhost:8085"
DEFAULT_TABLE = Path("bin/service-registry/health_path_truth_table.json")


def load_truth_table(path: Path) -> list[dict]:
    with path.open() as f:
        doc = json.load(f)
    if isinstance(doc, dict):
        rows = doc.get("services") or doc
    else:
        rows = doc
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected {{\"services\": [...]}} or a list")
    out = []
    for i, row in enumerate(rows):
        name = row.get("name")
        verified = row.get("verified_health_path")
        if not name or not verified:
            raise SystemExit(f"{path}: entry {i} needs 'name' and 'verified_health_path'")
        out.append(row)
    return out


def fetch_services(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/api/v1/services?size=10000", timeout=10) as r:
        doc = json.load(r)
    return doc.get("data") or []


def fetch_service(base: str, sid: int) -> dict:
    with urllib.request.urlopen(f"{base}/api/v1/services/{sid}", timeout=10) as r:
        return json.load(r)


def put_service(base: str, sid: int, body: dict) -> int:
    req = urllib.request.Request(
        f"{base}/api/v1/services/{sid}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="restrict to these service names (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", action="store_true", help="actually PUT updates")
    ap.add_argument(
        "--i-understand-live-writes",
        action="store_true",
        help="required together with --live",
    )
    ap.add_argument(
        "--audit-log",
        type=Path,
        default=Path("bin/service-registry/health_path_repair_log.jsonl"),
    )
    args = ap.parse_args()

    if args.live and not args.i_understand_live_writes:
        ap.error("--live requires --i-understand-live-writes")
    live = args.live

    rows = load_truth_table(args.table)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r.get("name") in want]
        if not rows:
            print("no matching entries in truth table for --only", file=sys.stderr)
            return 2

    services = fetch_services(args.registry)
    by_name = {s.get("name"): s for s in services}

    changed = missing = 0
    audit_lines: list[str] = []
    for row in rows:
        name = row["name"]
        verified = row["verified_health_path"]
        svc = by_name.get(name)
        if svc is None:
            print(f"  ? {name}: not found in registry (skipped)")
            missing += 1
            continue
        sid = svc["id"]
        current_raw = svc.get("healthCheckPath")
        if current_raw == verified:
            continue  # already conforms
        body = fetch_service(args.registry, sid)
        body["healthCheckPath"] = verified
        if live:
            status = put_service(args.registry, sid, body)
            if status not in (200, 201):
                print(f"  ! {name} (id={sid}): PUT failed HTTP {status}", file=sys.stderr)
                continue
        audit_lines.append(
            json.dumps(
                {
                    "id": sid,
                    "name": name,
                    "old_value": current_raw,
                    "new_value": verified,
                    "verified_path": row.get("verified_health_path"),
                    "actual_status": row.get("actual"),
                    "probe_evidence": row.get("probe_evidence"),
                }
            )
        )
        mode = "WROTE" if live else "WOULD-WRITE"
        print(f"  {mode}: {name} (id={sid}): {current_raw!r} -> {verified!r}")
        changed += 1

    if audit_lines:
        args.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with args.audit_log.open("a") as f:
            f.write("\n".join(audit_lines) + "\n")

    print(f"\n{'live' if live else 'dry-run'}: {changed} row(s) "
          f"{'written' if live else 'would be written'}, {missing} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
