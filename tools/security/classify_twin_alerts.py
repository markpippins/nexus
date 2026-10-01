#!/usr/bin/env python3
"""Classify CodeQL alerts on moleculer twins as copied vs novel.

Architect consolidated queue item 4 (record 5f099036, ruling 1/4):

    For each CodeQL alert on a twin, resolve twin path -> incumbent path via
    the port registry; evaluate the same rule there.
      copied       -> ledger entry, does not block
      novel        -> blocks
      unclassified -> CI fails (silence is not classification)

Input: a JSON array of code-scanning alert objects (the REST API shape:
{"rule": {"id": ...}, "most_recent_instance": {"location": {"path": ...}}}).
Feed it from the API, e.g.:

    gh api --paginate \
      "repos/OWNER/REPO/code-scanning/alerts?state=open&ref=refs/pull/N/merge&per_page=100" \
      | python3 tools/security/classify_twin_alerts.py --fail-on novel

Twin app -> incumbent mapping comes from the canary rows of
moleculer/ports.yaml (single source of truth, same registry that drives
tools/api-docs/gen_port_registry.py).

Exit codes (--fail-on, repeatable):
    copied        3   (also requires --ledger; see below)
    novel         4
    unclassified  5
With --classify-only the exit code is always 0 and the JSON report is the
only output.

Ledger cross-check (queue item 5): with --ledger FILE, every `copied`
finding must have a ledger entry keyed
(service, rule_id, incumbent_path); a copied finding without one is a
hard failure (exit 2) regardless of --fail-on — a copied classification
without a ledger entry is exactly the "silence is not classification"
hole.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REL = "moleculer/ports.yaml"

EXIT_USAGE = 64
EXIT_API_FAILURE = 7
EXIT_COPIED = 3
EXIT_NOVEL = 4
EXIT_UNCLASSIFIED = 5
EXIT_LEDGER = 2


# ---------------------------------------------------------------------------
# Registry resolution (pure)


def load_twin_map(root: Path = DEFAULT_ROOT) -> dict[str, str]:
    """Return {twin_app_dirname: incumbent_relpath} from ports.yaml canary rows."""
    registry = yaml.safe_load((root / REGISTRY_REL).read_text())
    twins: dict[str, str] = {}
    for row in registry.get("canary", []):
        if isinstance(row, dict) and row.get("incumbent"):
            name = row.get("name")
            if not name:
                continue
            twins[str(name)] = str(row["incumbent"]).rstrip("/")
    return twins


def twin_app_of(path: str, twins: dict[str, str]) -> str | None:
    """moleculer/<app>/... -> <app> when <app> is a registry twin, else None."""
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "moleculer" and parts[1] in twins:
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Classification (pure)


def classify_alerts(
    alerts: list[dict[str, Any]],
    twins: dict[str, str],
    ledger_entries: list[dict[str, Any]] | None = None,
    incumbent_alerts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify each alert; returns a JSON-serializable report dict.

    `incumbent_alerts` is the incumbent truth — main's open alerts (or any
    alert set covering the incumbent tree). Default-setup CodeQL analyses a
    PR merge ref with a NEW-alerts-only filter, so a copied finding's
    incumbent-path alert never appears in the PR payload; without this
    second source every copied finding would misclassify as novel and
    wrongly block. Payload hits are still preferred (fresher); the
    incumbent-alerts set is the fallback.
    """
    entries = ledger_entries or []
    inc_alerts = incumbent_alerts or []

    def ledger_has(service: str, rule_id: str, incumbent_path: str) -> bool:
        return any(
            e.get("service") == service
            and e.get("rule_id") == rule_id
            and e.get("incumbent_path") == incumbent_path
            for e in entries
        )

    findings: list[dict[str, Any]] = []
    for alert in alerts:
        rule = (alert.get("rule") or {}).get("id")
        loc = ((alert.get("most_recent_instance") or {}).get("location") or {}).get(
            "path"
        )
        number = alert.get("number")
        if not rule or not loc:
            findings.append(
                {
                    "alert": number,
                    "classification": "malformed",
                    "rule_id": rule,
                    "path": loc,
                }
            )
            continue
        app = twin_app_of(str(loc), twins)
        if app is None:
            findings.append(
                {
                    "alert": number,
                    "classification": "unclassified",
                    "rule_id": str(rule),
                    "path": str(loc),
                }
            )
            continue
        incumbent = twins[app]
        incumbent_prefix = incumbent + "/"

        def matches_incumbent(a: Any) -> bool:
            return (
                isinstance(a, dict)
                and ((a.get("rule") or {}).get("id") == rule)
                and str(
                    ((a.get("most_recent_instance") or {}).get("location") or {}).get(
                        "path"
                    )
                    or ""
                ).startswith(incumbent_prefix)
            )

        incumbent_hit = any(matches_incumbent(a) for a in alerts) or any(
            matches_incumbent(a) for a in inc_alerts
        )
        service = incumbent  # ledger key: service == incumbent relpath
        if incumbent_hit:
            has_entry = ledger_has(service, str(rule), incumbent)
            findings.append(
                {
                    "alert": number,
                    "classification": "copied",
                    "rule_id": str(rule),
                    "path": str(loc),
                    "twin_app": app,
                    "incumbent": incumbent,
                    "ledger_entry_present": has_entry,
                }
            )
        else:
            findings.append(
                {
                    "alert": number,
                    "classification": "novel",
                    "rule_id": str(rule),
                    "path": str(loc),
                    "twin_app": app,
                    "incumbent": incumbent,
                }
            )

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["classification"]] = counts.get(f["classification"], 0) + 1

    return {
        "total": len(alerts),
        "counts": counts,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Ledger (pure)


def load_ledger(root: Path = DEFAULT_ROOT) -> list[dict[str, Any]]:
    ledger_path = root / "tools/security/backfill-ledger.yaml"
    if not ledger_path.exists():
        return []
    data = yaml.safe_load(ledger_path.read_text()) or {}
    return list(data.get("entries", []))


# ---------------------------------------------------------------------------
# Alert fetching (impure, env-overridable for tests)


def fetch_alerts(ref: str, repo: str) -> list[dict[str, Any]]:
    """Fetch open code-scanning alerts for a ref via `gh api --paginate`.

    GITHUB_CURL_COMMAND (optional) overrides the fetch command for hermetic
    tests; it receives "<repo> <ref>" as argv and must print the JSON array.
    """
    override = os.environ.get("GITHUB_CURL_COMMAND")
    if override:
        out = subprocess.run(
            [override, repo, ref], capture_output=True, text=True, check=False
        )
        if out.returncode != 0:
            raise RuntimeError(f"alert fetch failed: {out.stderr.strip()[:200]}")
        return json.loads(out.stdout)
    cmd = [
        "gh",
        "api",
        "--paginate",
        f"repos/{repo}/code-scanning/alerts?state=open&ref={ref}&per_page=100",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"gh api failed: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--alerts",
        type=Path,
        help="JSON file with the PR alert array (default: stdin)",
    )
    parser.add_argument(
        "--incumbent-alerts",
        type=Path,
        help=(
            "JSON file with main's open alerts — the incumbent truth "
            "(PR merge-ref payloads carry new alerts only, so a copied "
            "finding's incumbent alert is never in the PR payload)"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repo root for ports.yaml and the ledger (tests)",
    )
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="cross-check copied findings against tools/security/backfill-ledger.yaml",
    )
    parser.add_argument(
        "--fail-on",
        action="append",
        choices=["copied", "novel", "unclassified"],
        default=[],
        help="exit non-zero if any finding has this classification (repeatable)",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="never exit non-zero; just print the report",
    )
    args = parser.parse_args(argv)

    raw = (
        args.alerts.read_text()
        if args.alerts
        else sys.stdin.read()
    )
    try:
        alerts = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"alert payload is not JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(alerts, list):
        print("alert payload must be a JSON array", file=sys.stderr)
        return EXIT_USAGE

    twins = load_twin_map(args.root)
    entries = load_ledger(args.root) if args.ledger else None
    incumbent_alerts: list[dict[str, Any]] | None = None
    if args.incumbent_alerts:
        try:
            incumbent_alerts = json.loads(args.incumbent_alerts.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"incumbent alert payload unusable: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(incumbent_alerts, list):
            print("incumbent alert payload must be a JSON array", file=sys.stderr)
            return EXIT_USAGE
    report = classify_alerts(alerts, twins, entries, incumbent_alerts)
    print(json.dumps(report, indent=2))

    if args.classify_only:
        return 0

    # Copied-without-ledger-entry is always a hard failure when the ledger
    # check is on: a copied classification without a time-boxed entry is
    # exactly the silence the ruling forbids.
    if args.ledger:
        missing = [
            f
            for f in report["findings"]
            if f["classification"] == "copied" and not f.get("ledger_entry_present")
        ]
        if missing:
            for f in missing:
                print(
                    f"COPIED WITHOUT LEDGER ENTRY: {f['rule_id']} @ {f['path']} "
                    f"(add (service={f['incumbent']}, rule_id={f['rule_id']}, "
                    f"incumbent_path={f['incumbent']}) to tools/security/backfill-ledger.yaml)",
                    file=sys.stderr,
                )
            return EXIT_LEDGER

    code_map = {"copied": EXIT_COPIED, "novel": EXIT_NOVEL, "unclassified": EXIT_UNCLASSIFIED}
    for kind, code in code_map.items():
        if kind in args.fail_on and report["counts"].get(kind, 0) > 0:
            print(f"FAIL: {report['counts'][kind]} {kind} finding(s)", file=sys.stderr)
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
