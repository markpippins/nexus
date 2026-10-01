#!/usr/bin/env python3
"""forum_hygiene_sweep.py — weekly staleness audit for Assembly issue forums.

Compares each OPEN thread's statusRating (the `thread-status-ratings`
vocabulary: 0 Posted / 1 Specified / 2 Planned / 3 Implemented / 6 Reopened
are open; 4 Accepted / 5 Rejected / 7 Closed / 8 Approved are resolved)
against independently verifiable evidence:

  - PRs named in the thread title/body: MERGED via `gh pr list --state merged`
    (two bulk calls total — never one subprocess per thread).
  - Attestation / closure records in nebula (tester, DBA, architect,
    supervisor) whose title names the same PR(s) or shares distinctive
    title tokens.

A thread that is still rated open while its evidence says "resolved" is
reported as STALE. This tool is REPORT-ONLY: it never mutates ratings and
never posts comments — closing a thread is a role decision that must carry
its own evidence comment (see the 2026-10-01 issues-forum audit, R2
`9f31ea64`). The boot shim runs it weekly via a state file; standalone runs
are always available.

Heuristic limits (v1, documented on purpose):
  - Evidence detection covers PR-merges and record-title matches (exact
    PR numbers, or >= 3 distinctive shared title tokens from an
    evidence-bearing role). A thread resolved by means that name neither
    a PR nor a record title will not be flagged — absence of a finding
    is NOT a claim of staleness-free-ness.
  - `gh` absence or API unavailability degrades the sweep; it never fails.

Usage:
    forum_hygiene_sweep.py                     # sweep if weekly gate is due, write state
    forum_hygiene_sweep.py --force             # sweep now regardless of the gate
    forum_hygiene_sweep.py --check-only        # sweep, never write state (boot dry-run)
    forum_hygiene_sweep.py --json              # machine-readable report on stdout

Exit codes: 0 = sweep ran (findings or not) · 3 = not due (weekly gate) ·
1 = every dependency unreachable (report what was tried).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ASSEMBLY_API = os.environ.get("ASSEMBLY_API", "http://localhost:3107/api")
NEBULA_API = os.environ.get("NEBULA_API_BASE", "http://localhost:3101/api")
STATE_DIR = os.environ.get(
    "NEXUS_FORUM_HYGIENE_STATE_DIR",
    os.path.expanduser("~/.local/state/nexus"))
STATE_FILE = os.environ.get(
    "NEXUS_FORUM_HYGIENE_STATE",
    str(Path(STATE_DIR) / "forum-hygiene-sweep.json"))

WEEK_SECONDS = 7 * 86400

# The `thread-status-ratings` procedure card vocabulary, partitioned.
OPEN_RATINGS = {0, 1, 2, 3, 6}
RESOLVED_RATINGS = {4, 5, 7, 8}

FORUMS = ("issues-and-open-questions", "to-do")

PR_RE = re.compile(r"#(\d{2,5})")
_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")
_STOPWORDS = {
    "this", "that", "with", "from", "have", "has", "was", "were", "been",
    "their", "them", "they", "will", "would", "should", "could", "than",
    "then", "when", "what", "which", "into", "onto", "over", "under",
    "about", "after", "before", "between", "because", "while", "still",
    "even", "also", "very", "here", "there", "where", "thread", "PRs",
}


def http_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def thread_list(body):
    """Assembly returns either a bare list or {"items": [...]}``."""
    if isinstance(body, dict):
        return body.get("items") or []
    return body if isinstance(body, list) else []


def extract_pr_numbers(text: str) -> list[int]:
    """Distinct PR numbers (>= 2 digits, so '#1' style refs don't count)."""
    if not text:
        return []
    return sorted({int(m) for m in PR_RE.findall(text)})


def title_tokens(title: str) -> set[str]:
    toks = {t for t in _TOKEN_RE.findall((title or "").lower())
            if t not in _STOPWORDS}
    return toks


def record_epoch_ms(rec: dict) -> int | None:
    """createdAt arrives as epoch-ms (REST list) or ISO string; normalize."""
    ts = rec.get("createdAt")
    if isinstance(ts, (int, float)) and ts:
        return int(ts)
    if isinstance(ts, str) and ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def record_iso(rec: dict) -> str:
    ms = record_epoch_ms(rec)
    if ms is None:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


EVIDENCE_ROLES = {"tester", "DBA", "architect", "supervisor", "devops"}


def attestation_matches(rec: dict, thread: dict) -> bool:
    """A record 'matches' a thread when its title names one of the same
    PRs (precise), or — fallback — shares >= 3 distinctive title tokens
    AND comes from a role whose records typically carry resolution
    evidence. The fallback bar is deliberately high: a loose match
    floods the report and trains readers to ignore it."""
    r_title = rec.get("title") or ""
    thread_prs = set(extract_pr_numbers(thread.get("title", "")))
    if thread_prs and thread_prs & set(extract_pr_numbers(r_title)):
        return True
    if rec.get("role") not in EVIDENCE_ROLES:
        return False
    shared = title_tokens(r_title) & title_tokens(thread.get("title", ""))
    return len(shared) >= 3


# ── evidence fetchers (network side, thin + degradable) ─────────────────


def fetch_threads(slug: str) -> list[dict]:
    return thread_list(http_json(f"{ASSEMBLY_API}/forums/{slug}/threads"))


def fetch_pr_evidence_sets() -> tuple[set[int], set[int], list[str]]:
    """(merged, closed_unmerged, errors). TWO gh calls total, regardless of
    thread count — `gh pr list --state merged` and `--state closed`; the
    merged set is subtracted from the closed set. Numbers beyond gh's page
    windows are 'unknown' (no evidence either way), which is safe: the
    sweep reports on positive evidence only."""
    errors: list[str] = []
    if shutil.which("gh") is None:
        return set(), set(), ["gh CLI not on PATH"]

    def _nums(state: str, limit: int) -> set[int] | None:
        try:
            out = subprocess.run(
                ["gh", "pr", "list", "--state", state, "--limit", str(limit),
                 "--json", "number"],
                capture_output=True, text=True, timeout=30,
                cwd=os.environ.get("NEXUS_REPO_ROOT", "."))
            if out.returncode != 0:
                errors.append(f"gh pr list --state {state}: "
                              f"{(out.stderr or '').strip()[:120]}")
                return None
            return {int(p["number"]) for p in json.loads(out.stdout or "[]")}
        except Exception as e:  # noqa: BLE001 — degrade, never raise
            errors.append(f"gh pr list --state {state}: {e.__class__.__name__}")
            return None

    merged = _nums("merged", 300)
    closed = _nums("closed", 150)
    merged = merged or set()
    closed = (closed or set()) - merged
    return merged, closed, errors


def fetch_records() -> tuple[list[dict], list[str]]:
    """Nearest records across the roles that typically carry resolution
    evidence. List projection gives titles without content — enough."""
    errors: list[str] = []
    records: list[dict] = []
    for qs in ("limit=150", "role=tester&limit=60", "role=DBA&limit=60",
               "role=architect&limit=60", "role=supervisor&limit=60"):
        try:
            body = http_json(f"{NEBULA_API}/agent-records?{qs}")
            items = body.get("items") if isinstance(body, dict) else body
            records.extend(items or [])
        except Exception as e:  # noqa: BLE001 — degrade per call
            errors.append(f"nebula agent-records ({qs}): {e.__class__.__name__}")
    # dedupe by id, keep newest first
    seen: set[object] = set()
    unique: list[dict] = []
    for r in records:
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(r)
    return unique, errors


# ── the pure core ────────────────────────────────────────────────────────


def stale_findings(threads: list[dict], merged_prs: set[int],
                   closed_unmerged_prs: set[int],
                   records: list[dict]) -> list[dict]:
    """Threads rated open whose evidence says resolved. Pure: no I/O."""
    findings: list[dict] = []
    for t in threads:
        rating = t.get("statusRating") or 0
        if rating not in OPEN_RATINGS:
            continue
        prs = set(extract_pr_numbers(t.get("title", "")))
        evidence: list[str] = []
        merged_here = sorted(prs & merged_prs)
        closed_here = sorted(prs & closed_unmerged_prs)
        if merged_here:
            evidence.append(
                "PR " + ", ".join(f"#{n} MERGED" for n in merged_here))
        if closed_here:
            evidence.append(
                "PR " + ", ".join(f"#{n} CLOSED-unmerged" for n in closed_here))
        match = next((r for r in records if attestation_matches(r, t)), None)
        if match is not None:
            evidence.append(
                f"resolution record {(match.get('id') or '')[:8]} by "
                f"{match.get('role', '?')} at {record_iso(match)}: "
                f"{(match.get('title') or '')[:80]}")
        if not evidence:
            continue
        findings.append({
            "thread_id": t.get("id", ""),
            "title": (t.get("title") or "")[:90],
            "rating": rating,
            "severity": "likely-stale" if merged_here else "needs-review",
            "evidence": evidence,
        })
    return findings


# ── weekly gate + state ──────────────────────────────────────────────────


def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — missing/corrupt state = first run
        return {}


def save_state(state: dict, path: str = STATE_FILE) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def weekly_due(state: dict, now_epoch: float) -> bool:
    """Due when no state exists (first run sweeps) or the last run is
    older than WEEK_SECONDS."""
    last = state.get("last_run_epoch")
    if not isinstance(last, (int, float)):
        return True
    return (now_epoch - float(last)) >= WEEK_SECONDS


# ── orchestration ────────────────────────────────────────────────────────


def run_sweep(threads_fetcher=fetch_threads,
              pr_fetcher=fetch_pr_evidence_sets,
              records_fetcher=fetch_records) -> dict:
    """Gather evidence and produce the report dict. Never raises; every
    dependency failure lands in `degraded` instead."""
    degraded: list[str] = []
    threads: list[dict] = []
    for slug in FORUMS:
        try:
            threads.extend(threads_fetcher(slug))
        except Exception as e:  # noqa: BLE001
            degraded.append(f"{slug}: {e.__class__.__name__}: {str(e)[:100]}")
    try:
        merged, closed, gh_errors = pr_fetcher()
        degraded.extend(gh_errors)
    except Exception as e:  # noqa: BLE001
        merged, closed = set(), set()
        degraded.append(f"pr-evidence: {e.__class__.__name__}")
    try:
        records, rec_errors = records_fetcher()
        degraded.extend(rec_errors)
    except Exception as e:  # noqa: BLE001
        records = []
        degraded.append(f"records: {e.__class__.__name__}")

    findings = stale_findings(threads, merged, closed, records)
    return {
        "generated_at": datetime.now(tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "forums": list(FORUMS),
        "threads_scanned": len(threads),
        "open_threads": sum(1 for t in threads
                            if (t.get("statusRating") or 0) in OPEN_RATINGS),
        "stale_found": len(findings),
        "findings": findings,
        "degraded": degraded,
    }


def render(report: dict) -> str:
    lines = [
        f"== forum-hygiene sweep {report['generated_at']} — "
        f"{report['threads_scanned']} threads scanned "
        f"({report['open_threads']} rated open), "
        f"{report['stale_found']} stale =="]
    for f in report["findings"]:
        lines.append(f"  [{f['severity']}] rating={f['rating']} "
                     f"{f['title']}")
        for ev in f["evidence"]:
            lines.append(f"      - {ev}")
    for d in report["degraded"]:
        lines.append(f"  (degraded) {d}")
    if report["stale_found"] == 0 and not report["degraded"]:
        lines.append("  no staleness detected; ratings match evidence")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="forum_hygiene_sweep.py",
        description="Weekly staleness audit: open thread ratings vs "
                    "merge/attestation evidence (report-only).")
    ap.add_argument("--force", action="store_true",
                    help="run the sweep even if the weekly gate is not due")
    ap.add_argument("--check-only", action="store_true",
                    help="sweep but never write the state file (boot "
                         "dry-run mode)")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON instead of text")
    ap.add_argument("--state-file", default=STATE_FILE,
                    help="weekly-gate state file (default %(default)s)")
    args = ap.parse_args(argv)

    state = load_state(args.state_file)
    now = datetime.now(tz=timezone.utc).timestamp()
    if not args.force and not weekly_due(state, now):
        if args.json:
            print(json.dumps({"due": False, "last_run_epoch":
                              state.get("last_run_epoch")}))
        else:
            print("forum-hygiene sweep: not due (last run "
                  f"{state.get('last_run_iso', '?')}; weekly gate)")
        return 3

    report = run_sweep()
    total_deps = len(report["degraded"])
    report["due"] = True

    if not args.check_only:
        state.update({"last_run_epoch": int(now),
                      "last_run_iso": report["generated_at"],
                      "last_findings": report["stale_found"]})
        save_state(state, args.state_file)
        report["state_file"] = args.state_file

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))

    # 0 = ran with reachable deps; 1 = nothing was reachable (boot maps
    # this to degraded; standalone callers see a real failure).
    if total_deps and report["threads_scanned"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
