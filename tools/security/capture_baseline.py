#!/usr/bin/env python3
"""Capture the dated CodeQL baseline snapshot of main's open alerts.

Architect consolidated queue item 6 (record 5f099036, ruling 6): the
engineer produces and maintains a **dated snapshot of main's alerts as a
CI input** — "a dated snapshot for diffing, not a suppression — no
exclusions, no threshold bump." The DBA stewards the accepted-risk entry;
the tester asserts the baseline is real.

Usage:
    python3 tools/security/capture_baseline.py --ref refs/heads/main \
        [--repo markpippins/nexus] [--out tools/security/codeql-baseline.json]

The snapshot records: capture timestamp (UTC), ref, head commit sha, open
alert count, and a fingerprint set ("rule|path" per alert). Consumers diff
future runs against it; nothing here suppresses anything.

Hermetic tests override the fetch via GITHUB_ALERTS_COMMAND (receives
"<repo> <ref>" argv, prints the alert JSON array) and can pin --now.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = DEFAULT_ROOT / "tools/security/codeql-baseline.json"


def fetch_alerts(repo: str, ref: str) -> list[dict]:
    override = os.environ.get("GITHUB_ALERTS_COMMAND")
    if override:
        out = subprocess.run(
            [override, repo, ref], capture_output=True, text=True, check=False
        )
        if out.returncode != 0:
            raise SystemExit(f"alert fetch failed: {out.stderr.strip()[:200]}")
        return json.loads(out.stdout)
    cmd = [
        "gh",
        "api",
        "--paginate",
        f"repos/{repo}/code-scanning/alerts?state=open&ref={ref}&per_page=100",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"gh api failed: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def fingerprint(rule_id: str, path: str) -> str:
    return f"{rule_id}|{path}"


def build_snapshot(
    alerts: list[dict], ref: str, commit: str, now: str
) -> dict:
    prints: set[str] = set()
    for a in alerts:
        rule = ((a.get("rule") or {}).get("id")) or "unknown-rule"
        loc = (
            ((a.get("most_recent_instance") or {}).get("location") or {}).get("path")
        ) or "unknown-path"
        prints.add(fingerprint(str(rule), str(loc)))
    return {
        "captured": now,
        "ref": ref,
        "commit": commit,
        "alert_count": len(alerts),
        "fingerprints": sorted(prints),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="refs/heads/main")
    parser.add_argument("--repo", default="markpippins/nexus")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--now", help="ISO timestamp override (tests); default = current UTC"
    )
    args = parser.parse_args(argv)

    alerts = fetch_alerts(args.repo, args.ref)

    commit = os.environ.get("BASELINE_COMMIT", "")
    if not commit:
        out = subprocess.run(
            ["gh", "api", f"repos/{args.repo}/commits/{args.ref}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise SystemExit(f"commit lookup failed: {out.stderr.strip()[:200]}")
        commit = json.loads(out.stdout)["sha"]

    now = args.now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = build_snapshot(alerts, args.ref, commit, now)
    args.out.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(
        f"OK {args.out}: captured={snapshot['captured']} ref={snapshot['ref']} "
        f"commit={snapshot['commit'][:12]} alerts={snapshot['alert_count']} "
        f"fingerprints={len(snapshot['fingerprints'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
