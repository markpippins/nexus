#!/usr/bin/env python3
"""merge_pr.py -- attestation-gated merge wrapper for nexus PRs.

Follow-up on the tester's process flag (attestation record 1790138485935:
PRs #476/#477 merged 2026-09-23T04:36-37Z before any tester row existed).
This tool makes the R8 merge conditions mechanically enforceable:

  Gate 1  PR is OPEN, not a draft, and MERGEABLE.
  Gate 2  Every reported CI check is COMPLETED and SUCCESSFUL (the "code
          has tests / tests are passing" conditions from AGENTS.md R8).
  Gate 3  A tester attestation agent record exists for this PR (nebula DB)
          AND was created at or after the PR's head commit date -- a
          force-push after attestation invalidates it.

Check-only is the DEFAULT and never mutates anything; exit 0 means "every
gate passed". ``--merge`` performs the squash merge only after all gates
pass. The attestation gate (and only that gate) may be bypassed by the
OPERATOR via NEXUS_ALLOW_UNATTESTED_MERGE=1 -- the bypass is reported
loudly in the gate output. Agents must not set it.

Known limitations (documented, not hidden):
- Attestation search scans the most recent NEBULA_RECORD_LIMIT agent
  records via REST; records older than that window are not visible here.
- GitHub-native required reviewers are a weak backstop in this fleet
  because all agents share the operator's GitHub identity; a required
  status check backed by an attestation-verification CI job (self-hosted
  runner with nebula reachability) is the proposed native enforcement and
  needs an architect/operator decision (R8.2 proposal, record 6a631ab6).

Usage:
  merge_pr.py 487            # check-only report
  merge_pr.py 487 --merge    # gate, then squash-merge
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

NEBULA_BASE = os.environ.get("NEBULA_BASE", "http://localhost:3101")
NEBULA_RECORD_LIMIT = 200
BYPASS_ENV = "NEXUS_ALLOW_UNATTESTED_MERGE"

# Conclusions that do not block a merge (skipped/neutral jobs are not failures).
PASSING_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}


@dataclass
class GateResult:
    """One gate evaluation with a human-readable detail line."""

    name: str
    passed: bool
    detail: str
    bypassed: bool = False


# ── I/O helpers (injectable for hermetic tests) ─────────────────────────

def _gh_json(*args: str) -> Any:
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


# ── Gate 1/2: GitHub PR state and CI checks ─────────────────────────────

def fetch_pr_state(
    pr_number: int, run_json: Callable[..., Any] = _gh_json
) -> Dict[str, Any]:
    return run_json(
        "pr", "view", str(pr_number), "--json",
        "number,state,isDraft,mergeable,mergeStateStatus,headRefOid,"
        "headRefName,statusCheckRollup,title",
    )


def fetch_head_commit_date_ms(
    pr_number: int, run_json: Callable[..., Any] = _gh_json
) -> Optional[int]:
    """Committer date of the PR head commit, as epoch ms (None if unknown)."""
    data = run_json("pr", "view", str(pr_number), "--json", "commits")
    commits = data.get("commits") or []
    if not commits:
        return None
    # gh (GraphQL) exposes committedDate at the top level of each commit
    # edge; the REST v3 shape nests it under commit.committer.date. Accept
    # both, return None (fail closed downstream) if neither is present.
    last = commits[-1]
    date = last.get("committedDate") or last.get("commit", {}).get(
        "committer", {}
    ).get("date")
    return parse_iso_to_ms(date) if date else None


def checks_report(rollup: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Gate 2: every check COMPLETED and passing. Empty rollup fails closed."""
    if not rollup:
        return False, "no CI checks reported (fail closed)"
    pending = [c.get("name") for c in rollup if c.get("status") != "COMPLETED"]
    failed = [
        f"{c.get('name')}={c.get('conclusion')}"
        for c in rollup
        if c.get("status") == "COMPLETED"
        and c.get("conclusion") not in PASSING_CONCLUSIONS
    ]
    if pending:
        return False, f"{len(pending)} check(s) not completed: {pending[:3]}"
    if failed:
        return False, f"{len(failed)} failed check(s): {failed[:3]}"
    return True, f"all {len(rollup)} checks completed successfully"


# ── Gate 3: tester attestation from the nebula agent records ────────────

def fetch_agent_records(
    http_get: Callable[[str], Any] = _http_get_json,
    base_url: str = NEBULA_BASE,
) -> List[Dict[str, Any]]:
    url = f"{base_url}/api/agent-records?limit={NEBULA_RECORD_LIMIT}"
    data = http_get(url)
    if isinstance(data, dict):
        return data.get("items") or []
    return data if isinstance(data, list) else []


def fetch_attestations(
    http_get: Callable[[str], Any],
    pr_number: int,
    base_url: str = NEBULA_BASE,
) -> Optional[List[Dict[str, Any]]]:
    """Exact, indexed attestation lookup (nebula GET /api/attestations?pr=N).

    Returns the attestation-shaped tester rows for this PR, newest first, or
    None when the endpoint is unavailable (old server, error, unexpected
    shape) so the caller can fall back to the bounded scan. Never raises.
    """
    url = f"{base_url}/api/attestations?pr={pr_number}"
    try:
        data = http_get(url)
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return None


def fetch_tester_pr_mentions(
    http_get: Callable[[str], Any],
    pr_number: int,
    base_url: str = NEBULA_BASE,
) -> List[Dict[str, Any]]:
    """Newest few tester records carrying the pr:<N> tag (any shape) — used
    only to make 'no attestation' refusals self-explaining. Indexed via the
    same GIN tags index; never raises."""
    url = f"{base_url}/api/agent-records?role=tester&tag=pr:{pr_number}&limit=3"
    try:
        data = http_get(url)
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return []


def attestation_check(
    http_get: Callable[[str], Any],
    pr_number: int,
    head_date_ms: Optional[int],
) -> Tuple[bool, str]:
    """Gate 3 lookup, exact by preference: the indexed /api/attestations
    endpoint when available (server-side shape filter + GIN tags index),
    bounded-scan fallback otherwise. Both paths share the client-side
    shape + freshness rules in evaluate_attestation()."""
    rows = fetch_attestations(http_get, pr_number)
    if rows is not None:
        ok, detail = evaluate_attestation(rows, pr_number, head_date_ms)
        if not rows:
            detail = (
                f"no attestation-shaped tester record for PR #{pr_number} "
                "(indexed lookup)"
            )
            mentions = fetch_tester_pr_mentions(http_get, pr_number)
            if mentions:
                ids = ", ".join(str(r.get("id"))[:8] for r in mentions[:3])
                detail += (
                    f" — tester records mentioning the PR: {ids} — none "
                    "carries an attestation marker (type:attestation tag, "
                    "or recordType=assessment with type:approval+status:done)"
                )
        else:
            detail += " (indexed lookup)"
        return ok, detail
    records = fetch_agent_records(http_get)
    ok, detail = evaluate_attestation(records, pr_number, head_date_ms)
    return ok, detail + " (scan fallback: /api/attestations unavailable)"


def attestation_mentions_pr(record: Dict[str, Any], pr_number: int) -> bool:
    """Match convention: pr:<N> tag, or '#<N>' with a word boundary in
    title/content (so #48 does not match PR #487)."""
    tags = {str(t).lower() for t in (record.get("tags") or [])}
    if f"pr:{pr_number}" in tags:
        return True
    pattern = re.compile(rf"#{pr_number}\b")
    return bool(
        pattern.search(record.get("title") or "")
        or pattern.search(record.get("content") or "")
    )


def is_attestation_record(record: Dict[str, Any]) -> bool:
    """Fail-closed attestation-shape test (per tester finding 84ca2388).

    A record counts as a tester attestation only if it is an *explicit*
    attestation — not merely a tester record that mentions the PR:

    canonical: ``recordType=assessment`` AND tags include ``type:approval``
               AND ``status:done`` (the tester's current row shape)
    legacy:    tags include ``type:attestation`` (any recordType; the
               tester's historical rows before the current shape)

    Intent rows (``type:status-update``, ``engineering_log``), reports and
    findings never qualify — “I intend to attest” is not “attested”.
    """
    tags = {str(t or "").lower() for t in (record.get("tags") or [])}
    if "type:attestation" in tags:
        return True
    return (
        str(record.get("recordType") or "").lower() == "assessment"
        and "type:approval" in tags
        and "status:done" in tags
    )


def evaluate_attestation(
    records: List[Dict[str, Any]],
    pr_number: int,
    head_date_ms: Optional[int],
) -> Tuple[bool, str]:
    """Gate 3: an explicit tester attestation for this PR must postdate the
    head commit (a push after attestation means the attested code is gone).
    Records must satisfy :func:`is_attestation_record` — a tester record that
    merely mentions the PR (e.g. an intent row) does not count."""
    mentions = [
        r
        for r in records
        if str(r.get("role") or "").lower() == "tester"
        and attestation_mentions_pr(r, pr_number)
    ]
    matches = [r for r in mentions if is_attestation_record(r)]
    if not matches:
        detail = (
            f"no tester attestation record for PR #{pr_number} found in the "
            f"{len(records)} most recent agent records"
        )
        if mentions:
            ids = ", ".join(str(r.get("id"))[:8] for r in mentions[:3])
            detail += (
                f" ({len(mentions)} tester record(s) mention the PR — {ids} — "
                "but none carries an attestation marker: type:attestation tag, "
                "or recordType=assessment with type:approval+status:done)"
            )
        return (False, detail)
    newest = max(matches, key=lambda r: r.get("createdAt") or 0)
    created_ms = newest.get("createdAt") or 0
    created_iso = datetime.fromtimestamp(
        created_ms / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if head_date_ms is None:
        return False, f"attestation exists ({created_iso}) but head commit date is unknown (fail closed)"
    if created_ms < head_date_ms:
        head_iso = datetime.fromtimestamp(
            head_date_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            False,
            f"newest attestation {created_iso} predates head commit {head_iso} "
            "(a push after attestation invalidates it)",
        )
    return (
        True,
        f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
        "postdates head commit",
    )


def parse_iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ── Full evaluation ──────────────────────────────────────────────────────

def _exc_brief(exc: Exception) -> str:
    """One-line, stderr-first summary of an exception for gate details."""
    stderr = getattr(exc, "stderr", None)
    if stderr:
        return f"{getattr(exc, 'cmd', 'command')}: {str(stderr).strip()[:140]}"
    return repr(exc)[:160]


def evaluate(
    pr_number: int,
    run_json: Callable[..., Any] = _gh_json,
    http_get: Callable[[str], Any] = _http_get_json,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[List[GateResult], Dict[str, Any]]:
    env = os.environ if env is None else env
    gates: List[GateResult] = []

    try:
        pr = fetch_pr_state(pr_number, run_json)
    except Exception as exc:
        # e.g. gh invoked outside a git repository, or auth failure —
        # a governance gate must fail closed with a clean report, never
        # a traceback.
        return (
            [GateResult("github pr lookup", False,
                        f"gh lookup failed: {_exc_brief(exc)} (fail closed)")],
            {},
        )
    ready = (
        pr.get("state") == "OPEN"
        and not pr.get("isDraft")
        and pr.get("mergeable") == "MERGEABLE"
    )
    gates.append(
        GateResult(
            "pr open & ready",
            ready,
            f"state={pr.get('state')} draft={pr.get('isDraft')} "
            f"mergeable={pr.get('mergeable')} head={str(pr.get('headRefOid'))[:8]}",
        )
    )

    ci_ok, ci_detail = checks_report(pr.get("statusCheckRollup") or [])
    gates.append(GateResult("ci green", ci_ok, ci_detail))

    bypassed = env.get(BYPASS_ENV) == "1"
    att_ok, att_detail = False, ""
    try:
        head_ms = fetch_head_commit_date_ms(pr_number, run_json)
        att_ok, att_detail = attestation_check(http_get, pr_number, head_ms)
    except Exception as exc:  # fail closed on any lookup failure
        att_detail = f"attestation lookup failed: {exc!r} (fail closed)"
    if bypassed and not att_ok:
        gates.append(
            GateResult(
                "tester attestation (BYPASSED)",
                True,
                f"{att_detail} [{BYPASS_ENV}=1 -- OPERATOR bypass; attestation gate only]",
                bypassed=True,
            )
        )
    else:
        gates.append(GateResult("tester attestation", att_ok, att_detail))

    return gates, pr


def format_report(pr_number: int, gates: List[GateResult]) -> str:
    lines = [f"merge gate for PR #{pr_number}:"]
    for g in gates:
        mark = "PASS" if g.passed else "FAIL"
        if g.bypassed:
            mark = "BYPASS"
        lines.append(f"  [{mark}] {g.name}: {g.detail}")
    ok = all(g.passed for g in gates)
    lines.append(f"  => {'ALL GATES PASS' if ok else 'GATE FAILURE -- merge refused'}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr_number", type=int, help="PR number to gate")
    parser.add_argument(
        "--merge",
        action="store_true",
        help="perform the squash merge if (and only if) every gate passes",
    )
    args = parser.parse_args(argv)

    gates, pr = evaluate(args.pr_number)
    print(format_report(args.pr_number, gates))
    if not all(g.passed for g in gates):
        return 1
    if not args.merge:
        print("check-only mode: no action taken (use --merge to squash-merge)")
        return 0

    _gh(
        "pr", "merge", str(args.pr_number), "--squash",
        "--subject", f"{pr.get('title', '')} (#{args.pr_number})",
        "--body",
        "Attestation-gated merge via bin/merge_pr.py. Gates at merge time: "
        + "; ".join(f"{g.name}={'PASS' if g.passed else 'BYPASS'}" for g in gates)
        + ".",
    )
    print(f"squash-merge of PR #{args.pr_number} issued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
