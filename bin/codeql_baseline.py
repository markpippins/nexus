#!/usr/bin/env python3
"""Dated CodeQL alert baseline of main — a CI diff input (Ruling 6/4).

Architect Ruling 6/4 (engineer work-queue item 6): the fleet needs a
baseline snapshot of main's alerts so "new alert" detection is meaningful;
without it, every port PR is evaluated against a multi-hundred-alert
haystack. The snapshot is a **dated diff input, not a suppression**:

  - NO exclusions, NO threshold change, NO ignore list. Every open
    code-scanning alert on the ref enters the snapshot, regardless of
    severity, language, or directory.
  - The diff is a REPORT: it makes the delta loud, it does not block and
    it does not waive. Blocking classification is the copied-vs-novel
    gate (bin/classify_codeql_alerts.py, Ruling 1/4) — the two are
    siblings in .github/workflows/codeql-twin-classification.yml.
  - The committed baseline is regenerated deliberately (engineer, dated,
    agent-recorded) when main's alert set legitimately changes, e.g. after
    a backfill lands (resolved entries shrink the baseline) or new debt is
    ratified. The diff report is what forces those moments to be visible.

Alert identity: (path, rule_id, line) as a MULTISET — two alerts may share
a site; counts are compared multiplicatively, never deduplicated.

This tool reuses bin/classify_codeql_alerts.fetch_gh_open_alerts so the
alert fetch semantics (state filter, pagination, field extraction) can
never fork between the classification gate and the baseline.

Subcommands:
  snapshot  Capture the ref's full open-alert set into a dated JSON file.
            Refuses to overwrite an existing baseline without --force.
  diff      Compare a ref's live alert set against a committed baseline.
            Exit 0 in every compared outcome (report-only); exit 2 on
            setup errors (missing baseline, gh failure, unanalyzed ref).
  verify    Offline integrity check of a baseline: schema, count, and
            content hash agree. Exit 0/2 (and 1 on tamper, so a red
            verification reads as a failure, not a crash).

Examples:
    python3 bin/codeql_baseline.py snapshot --out bin/codeql-baseline.json --ref main
    python3 bin/codeql_baseline.py diff --baseline bin/codeql-baseline.json --ref main --json
    python3 bin/codeql_baseline.py verify --baseline bin/codeql-baseline.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_codeql_alerts as cc  # noqa: E402  (sibling bin/ module)

SCHEMA = "codeql-baseline/1"
DEFAULT_BASELINE = "bin/codeql-baseline.json"
REQUIRED_FIELDS = ("schema", "captured_at", "ref", "commit_sha", "alert_count", "alerts")


class SetupError(Exception):
    """Missing baseline, gh failure, unanalyzed ref — exit 2."""


# ── raw alert access ─────────────────────────────────────────────────────
def severity_of(alert: dict) -> str:
    return (alert.get("rule") or {}).get("security_severity_level") or "none"


def message_head_of(alert: dict) -> str:
    return ((alert.get("most_recent_instance") or {}).get("message") or {}).get("text", "")[:100]


def number_of(alert: dict) -> int | None:
    return alert.get("number")


def build_sites(repo: str, ref: str, gh: str) -> tuple[list[dict], str]:
    """One pass over the raw alert dicts: entries for the snapshot + sha.

    Deliberately NOT routed through the classifier's Alert dataclass: the
    snapshot keeps severity and message context for the diff report, and
    the classifier's fetch returns (Alert, pairs) without them. The state
    filter, pagination, and path/rule extraction still come from the
    classifier's gh call so fetch semantics stay identical.
    """
    alerts = _fetch_raw_open_alerts(repo, ref, gh)
    sha = _fetch_analyzed_commit(repo, ref, gh)
    entries = []
    for a in alerts:
        mri = a.get("most_recent_instance") or {}
        loc = mri.get("location") or {}
        path, rule_id = loc.get("path"), (a.get("rule") or {}).get("id")
        if not path or not rule_id:
            continue
        entries.append(
            {
                "path": path,
                "rule_id": rule_id,
                "line": loc.get("start_line") or 0,
                "number": a.get("number"),
                "severity": severity_of(a),
                "message_head": message_head_of(a),
            }
        )
    entries.sort(key=lambda e: (e["path"], e["rule_id"], e["line"], e["number"] or 0))
    return entries, sha


def _fetch_raw_open_alerts(repo: str, ref: str, gh: str) -> list[dict]:
    url = (
        f"repos/{repo}/code-scanning/alerts"
        f"?ref={cc.normalize_ref(ref)}&per_page=100&state=open"
    )
    proc = subprocess.run([gh, "api", url, "--paginate"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SetupError(f"gh api failed for {url}: {(proc.stderr or proc.stdout).strip()[:400]}")
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        page = json.loads(line)
        if isinstance(page, dict):
            page = page.get("items", [page])
        for a in page:
            if a.get("state") and a["state"] != "open":
                continue
            out.append(a)
    return out


def _fetch_analyzed_commit(repo: str, ref: str, gh: str) -> str:
    url = (
        f"repos/{repo}/code-scanning/analyses"
        f"?ref={cc.normalize_ref(ref)}&per_page=1"
    )
    proc = subprocess.run([gh, "api", url], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SetupError(
            f"gh api failed for {url}: {(proc.stderr or proc.stdout).strip()[:400]}"
        )
    stdout = proc.stdout.strip()
    if not stdout:
        raise SetupError(
            f"no CodeQL analysis found for ref {ref!r} — the scanner has not "
            f"analyzed this ref; a baseline of it would be a fabrication"
        )
    # gh prints a bare JSON array for raw api calls and JSON-lines when a
    # --jq projection yields scalars; accept both shapes.
    first = json.loads(stdout.splitlines()[0])
    if isinstance(first, list):
        if not first:
            raise SetupError(
                f"no CodeQL analysis found for ref {ref!r} — the scanner has "
                f"not analyzed this ref; a baseline of it would be a fabrication"
            )
        first = first[0]
    sha = first.get("commit_sha") if isinstance(first, dict) else None
    if not sha:
        raise SetupError(f"analysis for ref {ref!r} carries no commit_sha")
    return sha


# ── content hash ─────────────────────────────────────────────────────────────
def content_sha256(alerts: list[dict]) -> str:
    canonical = json.dumps(alerts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── snapshot ─────────────────────────────────────────────────────────────────
def cmd_snapshot(args: argparse.Namespace) -> int:
    out = args.out
    if os.path.exists(out) and os.path.getsize(out) > 0 and not args.force:
        raise SetupError(
            f"{out} already exists — pass --force to regenerate it deliberately "
            f"(baseline regeneration is a dated, recorded act, never a side effect)"
        )
    entries, sha = build_sites(args.repo, args.ref, gh=args.gh)
    by_severity: Counter[str] = Counter(e["severity"] for e in entries)
    doc = {
        "schema": SCHEMA,
        "captured_at": _utcnow(),
        "ref": args.ref,
        "commit_sha": sha,
        "alert_count": len(entries),
        "by_severity": dict(sorted(by_severity.items())),
        "content_sha256": content_sha256(entries),
        "alerts": entries,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(
        f"snapshot: {len(entries)} open alerts on {args.ref} @ {sha[:12]} "
        f"-> {out} ({doc['captured_at']})"
    )
    return 0


def _utcnow() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── verify ───────────────────────────────────────────────────────────────────
def load_baseline(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError as e:
        raise SetupError(f"baseline not found: {path}") from e
    except json.JSONDecodeError as e:
        raise SetupError(f"{path}: invalid JSON: {e}") from e
    for field_name in REQUIRED_FIELDS:
        if field_name not in doc:
            raise SetupError(f"{path}: missing required field '{field_name}'")
    if doc["schema"] != SCHEMA:
        raise SetupError(f"{path}: schema {doc['schema']!r} != {SCHEMA!r}")
    if not isinstance(doc["alerts"], list):
        raise SetupError(f"{path}: 'alerts' must be a list")
    if doc["alert_count"] != len(doc["alerts"]):
        raise SetupError(
            f"{path}: alert_count {doc['alert_count']} != len(alerts) {len(doc['alerts'])}"
        )
    for i, e in enumerate(doc["alerts"]):
        for field_name in ("path", "rule_id", "line"):
            if field_name not in e:
                raise SetupError(f"{path}: alert #{i + 1} missing '{field_name}'")
    digest = content_sha256(doc["alerts"])
    if doc.get("content_sha256") != digest:
        raise SetupError(
            f"{path}: content_sha256 mismatch — the alert list was tampered with "
            f"(expected {doc.get('content_sha256')}, computed {digest})"
        )
    return doc


def cmd_verify(args: argparse.Namespace) -> int:
    doc = load_baseline(args.baseline)
    print(
        f"verify: {doc['alert_count']} alerts @ {doc['commit_sha'][:12]} "
        f"(captured {doc['captured_at']}) — schema, count, and hash consistent"
    )
    return 0


# ── diff ─────────────────────────────────────────────────────────────────────
def _multiset(entries: list[dict]) -> Counter:
    return Counter((e["path"], e["rule_id"], e["line"]) for e in entries)


def cmd_diff(args: argparse.Namespace) -> int:
    doc = load_baseline(args.baseline)
    current, sha = build_sites(args.repo, args.ref, gh=args.gh)
    base_ms, cur_ms = _multiset(doc["alerts"]), _multiset(current)

    new_ms = cur_ms - base_ms
    resolved_ms = base_ms - cur_ms
    unchanged = sum((cur_ms & base_ms).values())

    new = [e for e in current if new_ms.get((e["path"], e["rule_id"], e["line"]), 0) > 0]
    resolved = [
        e
        for e in doc["alerts"]
        if resolved_ms.get((e["path"], e["rule_id"], e["line"]), 0) > 0
    ]

    payload = {
        "baseline": {
            "captured_at": doc["captured_at"],
            "ref": doc["ref"],
            "commit_sha": doc["commit_sha"],
            "alert_count": doc["alert_count"],
        },
        "compared_ref": args.ref,
        "compared_commit_sha": sha,
        "new_count": sum(new_ms.values()),
        "resolved_count": sum(resolved_ms.values()),
        "unchanged_count": unchanged,
        "new": _dedupe_sites(new),
        "resolved": _dedupe_sites(resolved),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_render_diff(payload))
    # Ruling 6: the diff is a REPORT. New alerts annotate loudly but do not
    # block — blocking classification is the copied-vs-novel gate's job.
    if payload["new_count"]:
        print(
            f"::warning::{payload['new_count']} CodeQL alert(s) NEW vs baseline "
            f"{os.path.basename(args.baseline)} (report-only per Ruling 6)",
            file=sys.stderr,
        )
    return 0


def _dedupe_sites(entries: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for e in entries:
        key = (e["path"], e["rule_id"], e["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append({k: e[k] for k in ("path", "rule_id", "line", "severity", "message_head")})
    return out


def _render_diff(p: dict) -> str:
    b, c = p["baseline"], p["compared_commit_sha"] or "?"
    lines = [
        "CodeQL alert baseline diff (Ruling 6/4 — report-only, no exclusions)",
        f"  baseline : {b['alert_count']} alerts on {b['ref']} @ {b['commit_sha'][:12]} (captured {b['captured_at']})",
        f"  current  : ref {p['compared_ref']} @ {c[:12]}",
        f"  delta    : +{p['new_count']} new / -{p['resolved_count']} resolved / ={p['unchanged_count']} unchanged",
        "",
    ]
    if p["new"]:
        lines.append(f"NEW ({len(p['new'])} site(s), first 40):")
        for e in p["new"][:40]:
            lines.append(f"  + {e['path']}:{e['line']} {e['rule_id']} [{e['severity']}]")
        if len(p["new"]) > 40:
            lines.append(f"  + ... and {len(p['new']) - 40} more")
    if p["resolved"]:
        lines.append(f"RESOLVED ({len(p['resolved'])} site(s), first 40):")
        for e in p["resolved"][:40]:
            lines.append(f"  - {e['path']}:{e['line']} {e['rule_id']}")
        if len(p["resolved"]) > 40:
            lines.append(f"  - ... and {len(p['resolved']) - 40} more")
    if not p["new"] and not p["resolved"]:
        lines.append("no delta against the committed baseline")
    return "\n".join(lines)


# ── entry ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", cc.DEFAULT_REPO))
        p.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME", cc.DEFAULT_REF))
        p.add_argument("--gh", default="gh")

    p = sub.add_parser("snapshot", help="capture the ref's open alerts into a dated baseline")
    common(p)
    p.add_argument("--out", default=DEFAULT_BASELINE)
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("diff", help="compare a ref against the committed baseline (report-only)")
    common(p)
    p.add_argument("--baseline", default=DEFAULT_BASELINE)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("verify", help="offline integrity check of a baseline")
    p.add_argument("--baseline", default=DEFAULT_BASELINE)

    args = ap.parse_args(argv)
    try:
        return {"snapshot": cmd_snapshot, "diff": cmd_diff, "verify": cmd_verify}[args.cmd](args)
    except SetupError as e:
        print(f"SETUP ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
