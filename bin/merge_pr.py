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

# Sibling module (bin/gate_codes.py); shim keeps the import working when this
# file is loaded by path in tests (importlib) rather than run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_codes
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
    # Structured failure code (gate_codes, spec 86017db0). None on pass or
    # when no specific code applies -- additive, never changes gate semantics.
    code: Optional[str] = None


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
        "headRefName,statusCheckRollup,title,author,body",
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


def checks_report(rollup: List[Dict[str, Any]]) -> Tuple[bool, str, Optional[str]]:
    """Gate 2: every check COMPLETED and passing. Empty rollup fails closed.

    Returns (ok, detail, code) -- code is a gate_codes value on failure
    (deterministic order: empty -> pending -> failed), None on pass.
    """
    if not rollup:
        return False, "no CI checks reported (fail closed)", gate_codes.CI_NO_CHECKS
    pending = [c.get("name") for c in rollup if c.get("status") != "COMPLETED"]
    failed = [
        f"{c.get('name')}={c.get('conclusion')}"
        for c in rollup
        if c.get("status") == "COMPLETED"
        and c.get("conclusion") not in PASSING_CONCLUSIONS
    ]
    if pending:
        return (False, f"{len(pending)} check(s) not completed: {pending[:3]}",
                gate_codes.CI_PENDING)
    if failed:
        return (False, f"{len(failed)} failed check(s): {failed[:3]}",
                gate_codes.CI_FAIL)
    return True, f"all {len(rollup)} checks completed successfully", None


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


# CI run reference extraction. The tester attestation must cite real CI runs
# ("CI run 12345678901: shrapnel gate OK") rather than an engine's stated test
# counts ("engine reports 54/54") — a stated count is self-attestation by
# proxy and this gate exists to replace it (PRs #615/#630 family; tester
# review ecfe5977 GAP 3). Head-SHA tokens are also captured so the gate can
# detect an attestation for a superseded head.
_CI_RUN_RE = re.compile(r"\bCI runs?\s+((?:#?\d{6,}[^,;.)\n]*(?:,\s*)?)+)", re.IGNORECASE)
_RUN_ID_RE = re.compile(r"\d{8,}")
_SHA_RE = re.compile(r"\bhead(?:\s+SHA)?\s+([0-9a-f]{7,40})\b", re.IGNORECASE)
# The rollup figure a policy-v2 row states it inspected ("76 checks",
# "(76 checks)", "76 check-runs"). Deliberately narrow: a bare number is not
# a claim about the rollup, so only a digit carrying the word "check(s)"
# counts.
_CHECK_COUNT_RE = re.compile(r"\b(\d{1,4})\s+checks?\b", re.IGNORECASE)


def extract_ci_run_ids(text: str) -> List[str]:
    """CI run IDs cited in an attestation, de-duplicated, order preserved.

    Accepts 'CI run 36463962966', 'CI runs 36463962966, 36459972178' and
    'run #36463962966'. IDs are 8+ digits (GitHub run IDs are 10-11 digits
    today; 8 keeps headroom for older/shorter IDs without matching PR
    numbers or timestamps)."""
    ids: List[str] = []
    for m in _CI_RUN_RE.finditer(text or ""):
        for raw in _RUN_ID_RE.findall(m.group(1)):
            if raw not in ids:
                ids.append(raw)
    return ids


def extract_head_shas(text: str) -> List[str]:
    """Head-SHA tokens cited in an attestation ('head SHA 09f1f6cd' or
    'head 09f1f6cd'), lowercase, de-duplicated, order preserved."""
    shas: List[str] = []
    for m in _SHA_RE.finditer(text or ""):
        s = m.group(1).lower()
        if s not in shas:
            shas.append(s)
    return shas


def extract_check_count(text: str) -> Optional[int]:
    """The rollup size a policy-v2 attestation states it inspected, or None.

    First match wins: the row's claim is the figure it leads with ("Rollup at
    <head>: 76 checks, all success"), and picking the first keeps the reading
    deterministic when a body mentions a count more than once. Both phrasings
    the card and bin/vendor_attest.py emit match — "76 checks" and "(76
    checks)" — as does "76 check-runs" (the word boundary falls before the
    hyphen)."""
    m = _CHECK_COUNT_RE.search(text or "")
    return int(m.group(1)) if m else None


def _is_definitive_absent(exc: Exception) -> bool:
    """True when a gh lookup failed DEFINITIVELY (run reference does not
    exist), as opposed to transiently (429/5xx/network/timeout).

    Classification direction is fail-safe (Decision 15 item D): treating a
    transient failure as 'absent evidence' makes the janitor nag the tester
    to re-attest on every GitHub blip (the bug); treating a genuinely absent
    reference as 'transient' only delays the refusal by a cycle. So anything
    that is not a definitive not-found classifies transient.

    gh surfaces a missing object as an HTTP 404 on stderr (subprocess
    CalledProcessError carries .stderr); no network path to GitHub carries
    neither marker. We require BOTH '404' and 'not found' so a rate-limit
    body that merely mentions a URL path never reads as absence.
    """
    stderr = str(getattr(exc, "stderr", "") or "")
    combined = f"{type(exc).__name__} {exc} {stderr}".lower()
    return "404" in combined and "not found" in combined


def verify_ci_runs(
    run_ids: List[str],
    head_sha: Optional[str],
    run_json: Callable[..., Any] = _gh_json,
) -> Tuple[bool, str, bool]:
    """Every cited CI run must exist, be SUCCESS, and — when the run exposes
    a head SHA — belong to the PR's current head.

    Verification, not trust: the run ID proves a build happened, the API
    conclusion proves it passed, and the SHA binding proves it tested THIS
    code.

    Fail-closed on weakened conclusions (Decision 10 tightening, work order
    a49acfc9): a path-filtered *skipped* run carries the PR head_sha and
    would pass with zero test execution, and NEUTRAL likewise proves no run
    — both are rejected with a self-explaining detail. Runs that omit
    head_sha are also rejected: GitHub workflow-run objects always carry
    head_sha, so its absence is a data anomaly we refuse to pass on
    conclusion alone — every pass must bind to the code it tested.

    Transient-vs-absent split (Decision 15 item D): a gh lookup that fails
    transiently (429/5xx/network/timeout) is NOT 'no evidence' — the third
    return element is True and the caller routes the failure to the
    retryable channel (gate_codes.ATT_CI_LOOKUP_FAILED) so the janitor
    retries silently instead of nagging the tester. A definitive 404 (run
    genuinely absent) returns False there and routes ATT_NO_CI_EVIDENCE as
    before. See _is_definitive_absent.
    """
    if not run_ids:
        return False, "no CI run references", False
    ok_shas: List[str] = []
    for rid in run_ids:
        try:
            run = run_json("api", f"repos/{{owner}}/{{repo}}/actions/runs/{rid}",
                           "--jq", "{c: .conclusion, s: .head_sha}")
        except Exception as exc:
            if _is_definitive_absent(exc):
                return False, f"CI run {rid} lookup failed: {_exc_brief(exc)} (fail closed)", False
            return False, (
                f"CI run {rid} lookup failed: {_exc_brief(exc)} "
                "(transient — gate cannot reach GitHub; retry next cycle)"
            ), True
        conclusion = str((run or {}).get("c") or "").strip().lower()
        if conclusion != "success":
            return (
                False,
                f"CI run {rid} conclusion is '{conclusion or 'unknown'}' "
                "(not success; success-only per Decision 10 — skipped/neutral "
                "runs execute no tests and prove nothing)",
                False,
            )
        run_sha = str((run or {}).get("s") or "").strip().lower()
        if not run_sha:
            return (
                False,
                f"CI run {rid} omits head_sha — no code binding possible; "
                "every accepted run must bind to the PR head (fail closed)",
                False,
            )
        if head_sha:
            if not head_sha.lower().startswith(run_sha[:7]) and not run_sha.startswith(head_sha.lower()[:7]):
                return (
                    False,
                    f"CI run {rid} tested head {run_sha[:8]}, not this PR's head "
                    f"{str(head_sha)[:8]} — attested evidence is for superseded code",
                    False,
                )
            ok_shas.append(run_sha[:7])
    sha_note = f"; head SHA verified ({', '.join(ok_shas)})" if ok_shas else ""
    return True, f"{len(run_ids)} CI run(s) verified success{sha_note}", False


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


def attestation_check(
    http_get: Callable[[str], Any],
    pr_number: int,
    head_date_ms: Optional[int],
    head_sha: Optional[str] = None,
    run_json: Callable[..., Any] = _gh_json,
    pr_state: Optional[Dict[str, Any]] = None,
    files_getter: Optional[Callable[[int], Optional[List[str]]]] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Gate 3 lookup, exact by preference: the indexed /api/attestations
    endpoint when it returns rows (server-side shape filter + GIN tags index),
    bounded-scan fallback otherwise. Both paths share the client-side
    shape + freshness + CI-evidence rules in evaluate_attestation().

    An EMPTY indexed result is inconclusive, not absence: the index matches
    tag conventions (pr:<N> / gate:<N>), and a tester attestation that names
    the PR only in its title has no matching tag, so the query is blind to it.
    Measured 2026-11-03: /api/attestations?pr=842 returned items:[] while the
    tester's real attestation sat in the table (tags type:attestation,
    gate:842, verdict:approve; '#842' in the title). Trusting the empty result
    reported ATT_MISSING for a PR whose attestation existed. Empty therefore
    falls through to the bounded scan, which finds it by title mention.

    Returns (ok, detail, code) like evaluate_attestation()."""
    rows = fetch_attestations(http_get, pr_number)
    if rows:
        # The indexed endpoint returns a projection WITHOUT record content,
        # but the CI-evidence rule reads the attestation body (cited run IDs).
        # Content-less rows are hydrated from the bounded scan before
        # evaluation; if the scan cannot find them either, evaluation runs on
        # the content-less rows and fails the evidence rule with a
        # self-explaining detail rather than guessing.
        if rows and all(not (r or {}).get("content") for r in rows):
            try:
                # [E] Decision 15: hydrate by record id FIRST — targeted
                # fetches do not depend on the record falling inside the
                # bounded scan's newest-N window (older attestations used to
                # fail closed with spurious re-attest nudges once they slid
                # past it). The bounded scan remains the fallback for
                # servers/routers where the per-id fetch is unavailable.
                by_id: Dict[str, Dict[str, Any]] = {}
                missing: List[str] = []
                for r in rows:
                    rid = str(r.get("id") or "")
                    if not rid:
                        continue
                    try:
                        one = http_get(f"{NEBULA_BASE}/api/agent-records/{rid}")
                    except Exception:
                        one = None
                    if isinstance(one, dict) and one.get("id"):
                        by_id[rid] = one
                    else:
                        missing.append(rid)
                if missing:
                    full = fetch_agent_records(http_get)
                    scan_by_id = {str(r.get("id")): r for r in full}
                    for rid in missing:
                        if rid in scan_by_id:
                            by_id[rid] = scan_by_id[rid]
                # DBA record 48ac13e2: the point endpoint historically did
                # not carry createdAt in the list contract's shape (camelCase
                # epoch-ms), so a naive swap replaces a good timestamp with
                # none and evaluate_attestation's `or 0` binds the row at
                # epoch 0 -> permanent ATT_STALE_HEAD. After the swap, repair
                # the timestamp: prefer the hydrated row's own createdAt
                # (post-48ac13e2 servers send it), then the indexed row's
                # createdAt, then coerce a snake_case created_at (ISO or
                # epoch) from the hydrated row. Content always comes from the
                # hydrated row; the timestamp never regresses to 0.
                indexed_by_id = {str((r or {}).get("id") or ""): r for r in rows}
                rows = [by_id.get(str(r.get("id")), r) for r in rows]
                for i, r in enumerate(rows):
                    if not isinstance(r, dict) or r.get("createdAt"):
                        continue
                    rid = str(r.get("id") or "")
                    idx_row = indexed_by_id.get(rid) or {}
                    if idx_row.get("createdAt"):
                        rows[i] = {**r, "createdAt": idx_row["createdAt"]}
                        continue
                    raw = r.get("created_at")
                    if isinstance(raw, str):
                        try:
                            rows[i] = {**r, "createdAt": parse_iso_to_ms(raw)}
                        except Exception:
                            pass
                    elif isinstance(raw, (int, float)):
                        rows[i] = {**r, "createdAt": int(raw)}
            except Exception:
                pass  # evaluate on projection rows; evidence rule fails closed
        ok, detail, code = evaluate_attestation(
            rows, pr_number, head_date_ms,
            head_sha=head_sha, run_json=run_json,
            pr_state=pr_state, files_getter=files_getter,
        )
        return ok, detail + " (indexed lookup)", code
    scan_note = (
        "/api/attestations returned no rows, which is inconclusive"
        if rows == []
        else "/api/attestations unavailable"
    )
    records = fetch_agent_records(http_get)
    ok, detail, code = evaluate_attestation(
        records, pr_number, head_date_ms,
        head_sha=head_sha, run_json=run_json,
        pr_state=pr_state, files_getter=files_getter,
    )
    return ok, detail + f" (scan fallback: {scan_note})", code


def attestation_mentions_pr(record: Dict[str, Any], pr_number: int) -> bool:
    """Match convention: pr:<N> tag, gate:<N> tag, or '#<N>' with a word
    boundary in title/content (so #48 does not match PR #487).

    gate:<N> is the tester's live attestation convention (every tester
    attestation row from #842 onward carries gate:<N> + verdict:approve and
    names the PR only in its title). It is not registered anywhere, which is
    how it stayed invisible to the indexed lookup; matching it here keeps the
    scan path exact instead of relying on title text alone."""
    tags = {str(t).lower() for t in (record.get("tags") or [])}
    if f"pr:{pr_number}" in tags or f"gate:{pr_number}" in tags:
        return True
    pattern = re.compile(rf"#{pr_number}\b")
    return bool(
        pattern.search(record.get("title") or "")
        or pattern.search(record.get("content") or "")
    )


# Tags that WITHDRAW a record without changing its shape. Decision 24
# (supersede the content / retire without a successor) drops every status:*
# tag and adds exactly one of these, deliberately leaving type:* and pr:*
# untouched — so a retired attestation keeps `type:attestation` (legacy) or
# `type:approval` (canonical) and sails through a shape test.
#
# Without this filter, retirement is not a withdrawal as far as gate 3 is
# concerned: the row still binds as "an attestation exists", so a withdrawn
# attestation can only ever be REPLACED, never removed. That made the
# retirement convention (supersede-record.sh retire) unable to repair a bad
# attestation — the exact situation the 2026-11-03 stub batch created, where
# twelve empty-bodied type:attestation rows took the whole queue from the
# routine-silent ATT_MISSING to a permanent ATT_NO_CI_EVIDENCE nag.
WITHDRAWN_STATUS_TAGS = frozenset({"status:retired", "status:superseded"})


def is_withdrawn(record: Dict[str, Any]) -> bool:
    """True when the record was retired or superseded (Decision 24).

    Withdrawn rows keep their shape tags, so they must be dropped BEFORE any
    attestation-shape or mention test rather than after it.
    """
    tags = {str(t or "").lower() for t in (record.get("tags") or [])}
    return bool(tags & WITHDRAWN_STATUS_TAGS)


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

    Withdrawal is NOT this function's business: retirement keeps the shape
    tags, so a withdrawn row is still attestation-SHAPED. Callers that decide
    whether an attestation *counts* must filter with :func:`is_withdrawn`
    first — see evaluate_attestation().
    """
    tags = {str(t or "").lower() for t in (record.get("tags") or [])}
    if "type:attestation" in tags:
        return True
    return (
        str(record.get("recordType") or "").lower() == "assessment"
        and "type:approval" in tags
        and "status:done" in tags
    )


# ── attestation verdict (record ebb0a911) ───────────────────────────────────
# Gate 3 read attestation SHAPE, freshness and CI citation, but never the
# tester's VERDICT: a row saying "Verdict: REJECTED" counted as a passing
# attestation as long as it cited a CI run. #726 and #823 were refused only
# for their missing citation, not for the rejection itself — one cited run
# away from merging code the tester had refused.
#
# The scan is LABEL-anchored: only an explicitly declared `verdict: <word>`
# is read. Looking for the bare word "rejected" anywhere in the body would
# fire on prose ("no rejection needed here"), and a false rejection blocks a
# good PR as wrongly as a missed one merges a bad one.
#
# Verdict words that refuse the PR. "pass/fail" are included because the
# tester rows use them as verdicts too ("VERDICT: PASS" / "FAIL").
_REJECTING_VERDICTS = frozenset({
    "rejected", "reject", "blocked", "failed", "fail",
    "change requested", "changes requested",
})
_APPROVING_VERDICTS = frozenset({
    "approved", "approve", "passed", "pass", "accepted", "accept",
})
# "hold" is a fourth judgement, not a malformed one: the tester looked and
# answered "not yet" without asserting a defect (row acc7f963, #841). Reading
# it as INVALID would tell the tester their token is unknown when it is
# deliberate, so it gets its own diagnosis.
_HOLD_VERDICTS = frozenset({"hold", "on hold"})
# An approval granted by the vendor-bump rule rather than by a tester's
# judgement of the code. It is a different authority, so it never passes as an
# ordinary approval — the gate re-proves the rule's own conditions first.
_RULE_VERDICTS = frozenset({"approved by rule"})
_VENDOR_TAG = "type:vendor-bump"

# ── Attestation contract ─────────────────────────────────────────────────
# An attestation is read under the contract IT declares, not under whatever
# this gate happens to enforce on the day it runs (operator ruling
# 2026-11-04). Evidence written before verdicts were required keeps its
# original meaning: tightening the rule must not retroactively judge rows
# that were never asked the question.
#
#   policy: attestation/v2
#   verdict: approved
#
# Legacy rows (no policy line) keep the pre-v2 reading: shape + freshness +
# CI citation, and only an explicit rejection refuses. v2 rows must declare a
# verdict — otherwise green CI is read as a tester's judgement, which is the
# conflation this contract closes.
_POLICY_LINE_RE = re.compile(
    r"\bpolicy\b[\s*_`]*[:=][\s_*`]*`?(attestation/[A-Za-z0-9._-]+)`?",
    re.IGNORECASE,
)
_SUPPORTED_POLICIES = frozenset({"attestation/v2"})
# The rule a vendor approval was granted under, e.g. "vendor rule:
# vendor-bump/v1". Separate from `policy:` so a vendor attestation can declare
# BOTH its attestation contract and the rule that authorized it.
_VENDOR_RULE_LINE_RE = re.compile(
    r"\bvendor[\s_-]*rule\b[\s*_`]*[:=][\s_*`]*`?([A-Za-z0-9._/-]+)`?",
    re.IGNORECASE,
)
# The declared token itself: whatever word follows the label, recognized or
# not. Reading only a known-word shortlist would report "no verdict" for a
# tester who wrote `verdict: hold` — the judgement then looks absent when it
# happened. Recognizing the token separately from classifying it lets MISSING
# (nothing declared) stay distinct from INVALID (declared, unreadable).
_VERDICT_DECLARED_RE = re.compile(
    r"\bverdict\b[\s*_`]*[:=\-–—]{1,2}[\s_*`]*`?"
    # `changes requested` / `change requested` and `approved by rule` are the
    # multi-word verdicts; every other tail ("approved with nits") is read by
    # its first word.
    r"([A-Za-z][A-Za-z_-]{1,40}(?:\s+requested)?(?:\s+by\s+rule)?)",
    re.IGNORECASE,
)
# Legacy separator: real rows also write the verdict with a SPACE and no
# punctuation at all ("... unverified. Verdict REJECTED." — record bcaf01ad,
# #823). Requiring a colon silently downgraded that row from
# ATT_VERDICT_REJECTED to a re-attestation request, which reads as "cite a CI
# run" when the tester's answer was simply no. Only RECOGNIZED words are
# accepted here, so prose ("the verdict took a week") still cannot trip it.
_VERDICT_SPACE_RE = re.compile(
    r"\bverdict\b[\s*_`]{1,4}"
    r"([A-Za-z][A-Za-z_-]{1,40}(?:\s+requested)?(?:\s+by\s+rule)?)",
    re.IGNORECASE,
)


def _tag_value(record: Dict[str, Any], key: str) -> Optional[str]:
    """Value of the ``<key>:<value>`` tag, or None.

    The verdict is declared on two surfaces in practice: prose in the body
    (``VERDICT: APPROVED``) and tool-applied tags (``verdict:approve``, 13
    live rows). Reading only the body would report ATT_VERDICT_MISSING for a
    v2 row whose tester declared the verdict in the tag the convention asks
    for. The body wins when both are present — prose is the human statement,
    the tag is metadata about it.
    """
    want = f"{key.lower()}:"
    for tag in record.get("tags") or []:
        s = str(tag or "").strip()
        if s.lower().startswith(want):
            return s[len(want):].strip() or None
    return None


def _normalize_verdict(raw: str) -> str:
    return re.sub(r"[\s_]+", " ", str(raw or "").strip().lower()).strip()


def attestation_verdict(record: Dict[str, Any]) -> Optional[str]:
    """The last verdict the attestation DECLARES, normalized, or None.

    Real rows read ``TESTER VERDICT: REJECTED — <reason>`` (0cc7a949 for
    #726); markdown emphasis (``**Verdict:** REJECTED``) is tolerated.    Only the LAST declaration is returned: a body that quotes an earlier
    verdict and then states its own is read the way a human reads it. A body
    with no declaration falls back to the ``verdict:<token>`` tag.

    The DECLARED token is returned whether or not this gate recognizes it;
    classification (approved / rejected / hold / approved by rule / other)
    happens at the call site so an unreadable verdict is not mistaken for an
    absent one. None means "no verdict was declared": that is the legacy
    shape, and whether it is acceptable depends on the contract the row
    declares (see :func:`attestation_policy`), not on this parser.
    """
    text = f"{record.get('title') or ''}\n{record.get('content') or ''}"
    found = [_normalize_verdict(m.group(1)) for m in _VERDICT_DECLARED_RE.finditer(text)]
    if found:
        return found[-1]
    known = _APPROVING_VERDICTS | _REJECTING_VERDICTS | _HOLD_VERDICTS | _RULE_VERDICTS
    spaced = [
        _normalize_verdict(m.group(1))
        for m in _VERDICT_SPACE_RE.finditer(text)
    ]
    spaced = [v for v in spaced if v in known]
    if spaced:
        return spaced[-1]
    tag = _tag_value(record, "verdict")
    return _normalize_verdict(tag) if tag else None


def attestation_policy(record: Dict[str, Any]) -> Optional[str]:
    """The attestation contract this row declares, or None (legacy).

    Declared in the evidence itself — a ``policy: attestation/v2`` line in the
    body, or a ``policy:attestation/v2`` tag — so the row is interpreted
    under the contract that governed it rather than under today's gate. A
    future v3 can therefore tighten the rules without rewriting what v1 and
    v2 evidence meant.
    """
    text = f"{record.get('title') or ''}\n{record.get('content') or ''}"
    m = _POLICY_LINE_RE.search(text)
    if m:
        return m.group(1).strip().lower()
    tag = _tag_value(record, "policy")
    return tag.strip().lower() if tag else None


def verify_vendor_rule(
    record: Dict[str, Any],
    pr_state: Optional[Dict[str, Any]],
    changed_paths: Optional[List[str]],
) -> Tuple[bool, str]:
    """Prove an ``approved by rule`` verdict — or refuse it.

    A rule approval is a different authority from a tester's judgement, so
    the gate accepts it only when all four conditions hold:

    1. the row identifies itself as a vendor-rule attestation (type:vendor-bump)
    2. the PR is actually classified as a vendor bump (dependency-bot author)
    3. the diff satisfies the vendor rule (manifests/lockfiles only, no
       semver-major) — re-checked HERE, at gate time, from the live diff
    4. the row records the rule/version that authorized acceptance

    Condition 3 is the point: a classification label is not evidence of
    eligibility. Rows #860/#861 carry type:vendor-bump while touching bin/
    and config/, which is exactly why the tag alone must not buy leniency.
    """
    tags = {str(t or "").strip().lower() for t in record.get("tags") or []}
    if _VENDOR_TAG not in tags:
        return (False,
                "declares 'approved by rule' but carries no type:vendor-bump tag, "
                "so it is not a vendor-rule attestation")
    text = f"{record.get('title') or ''}\n{record.get('content') or ''}"
    m = _VENDOR_RULE_LINE_RE.search(text)
    if not m:
        return (False,
                "an approval by rule must record the rule that authorized it "
                "(expected a 'vendor rule: <id>' line) so the decision is "
                "reproducible rather than a magic string")
    rule = m.group(1).strip().lower()
    if pr_state is None or changed_paths is None:
        return (False,
                f"vendor rule {rule}: PR diff/author unavailable, so the rule's "
                "conditions cannot be re-verified (fail closed)")
    try:
        import vendor_attest as va
    except Exception as exc:  # pragma: no cover - import guard
        return (False, f"vendor rule {rule}: vendor_attest unavailable ({exc})")
    author = str(((pr_state.get("author") or {}).get("login")) or "")
    if not va.is_vendor_author(author):
        return (False,
                f"vendor rule {rule}: PR author '{author or 'unknown'}' is not a "
                "dependency bot, so this is not a vendor bump")
    paths_ok, path_detail = va.classify_paths(changed_paths)
    if not paths_ok:
        return (False, f"vendor rule {rule}: diff is not vendor-only — {path_detail}")
    upd_ok, upd_detail, _ = va.classify_update(
        str(pr_state.get("body") or ""), str(pr_state.get("title") or ""))
    if not upd_ok:
        return (False, f"vendor rule {rule}: {upd_detail}")
    return (True, f"vendor rule {rule} verified ({path_detail}; {upd_detail})")


def _fmt_ms(ms: Optional[int]) -> str:
    """Epoch-ms as an ISO-8601 UTC stamp for gate details ("unknown" if absent)."""
    if ms is None:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def fetch_pr_files(
    pr_number: int, run_json: Callable[..., Any] = _gh_json
) -> Optional[List[str]]:
    """Changed paths of the PR, or None when GitHub cannot be asked.

    Only consulted for an ``approved by rule`` verdict, so the normal gate
    path pays nothing for it. The gate re-checks the vendor rule against this
    live diff instead of trusting the attestation's own claim of eligibility.
    """
    try:
        data = run_json(
            "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/files", "--paginate")
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [str(f.get("filename") or "") for f in data if isinstance(f, dict)]


def evaluate_attestation(
    records: List[Dict[str, Any]],
    pr_number: int,
    head_date_ms: Optional[int],
    head_sha: Optional[str] = None,
    run_json: Callable[..., Any] = _gh_json,
    pr_state: Optional[Dict[str, Any]] = None,
    files_getter: Optional[Callable[[int], Optional[List[str]]]] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Gate 3: an explicit tester attestation for this PR must postdate the
    head commit (a push after attestation means the attested code is gone)
    and cite CI run IDs that verify against GitHub (conclusion success + head
    SHA binding). Stated test counts ("engine reports 54/54") are the
    engine's self-attestation and never satisfy the evidence rule. Records
    must satisfy :func:`is_attestation_record` — a tester record that merely
    mentions the PR (e.g. an intent row) does not count.    Returns (ok, detail, code) — code is a gate_codes value on failure
    (ATT_MISSING / ATT_SHAPE_UNSEEN / HEAD_DATE_UNKNOWN / ATT_STALE_HEAD /
    ATT_NO_CI_EVIDENCE / ATT_CI_LOOKUP_FAILED / ATT_POLICY_UNSUPPORTED /
    ATT_VERDICT_MISSING / ATT_VERDICT_INVALID / ATT_VERDICT_HOLD /
    ATT_VERDICT_REJECTED / ATT_VENDOR_RULE_UNVERIFIED / ATT_CI_COVERAGE),
    None on pass.

    Withdrawn rows (status:retired / status:superseded) are removed first, so
    they count neither as an attestation (`matches`) nor as a mention. Dropping
    them only from `matches` would still leave them in `mentions`, which turns
    clear code ATT_MISSING into noisy ATT_SHAPE_UNSEEN — the same nag the
    withdrawal was meant to end.
    """
    records = [r for r in records if not is_withdrawn(r)]
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
        code = gate_codes.ATT_SHAPE_UNSEEN if mentions else gate_codes.ATT_MISSING
        return (False, detail, code)
    newest = max(matches, key=lambda r: _created_ms_of(r) or 0)
    # Verdict first: it is the tester's actual decision and it outranks every
    # mechanical check below. Reporting it ahead of freshness/CI means a
    # refused PR says "the tester rejected this" instead of whichever lesser
    # symptom the same row also carries. An ABSENT verdict falls through to
    # the legacy checks (additive, like the head: binding), so existing rows
    # keep their meaning; only an EXPLICIT rejection fails closed here.
    policy = attestation_policy(newest)
    verdict = attestation_verdict(newest)
    rule_note = ""
    if policy is not None and policy not in _SUPPORTED_POLICIES:
        return (
            False,
            f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
            f"declares policy '{policy}', which this gate cannot interpret "
            f"(supported: {', '.join(sorted(_SUPPORTED_POLICIES))}). Reading a "
            "newer contract under looser v2 rules could accept evidence it was "
            "written to forbid, so this fails closed: upgrade the gate",
            gate_codes.ATT_POLICY_UNSUPPORTED,
        )
    if verdict in _REJECTING_VERDICTS:
        return (
            False,
            f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
            f"declares verdict '{verdict}' — a rejection is not an approval, "
            "even when it cites verified CI runs; address the rejection, then "
            "re-attest after the fix",
            gate_codes.ATT_VERDICT_REJECTED,
        )
    if policy is not None:
        # v2: the verdict is REQUIRED. Green CI + a fresh head + a cited run
        # prove the mechanical checks passed at this head; they cannot prove a
        # tester exercised judgement, and reading them as if they did is the
        # defect this contract closes. Each way a required verdict can be
        # absent or unusable gets its own diagnosis, because they call for
        # different actions: MISSING = the judgement never happened (ask for
        # it), INVALID = something was declared that the gate cannot read
        # (adjudicate), HOLD = the judgement happened and the answer was "not
        # yet" (author action), RULE = automation claiming human authority
        # (re-prove the rule).
        if verdict in _APPROVING_VERDICTS:
            pass
        elif verdict in _HOLD_VERDICTS:
            return (
                False,
                f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
                f"declares verdict '{verdict}' — the tester withheld approval "
                "without asserting a specific defect; this is author action "
                "(ask what is blocking, or wait for the tester's re-verdict), "
                "not a mergeable approval",
                gate_codes.ATT_VERDICT_HOLD,
            )
        elif verdict in _RULE_VERDICTS:
            paths = files_getter(pr_number) if files_getter else None
            rule_ok, rule_detail = verify_vendor_rule(newest, pr_state, paths)
            if not rule_ok:
                return (
                    False,
                    f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
                    f"claims 'approved by rule': {rule_detail}",
                    gate_codes.ATT_VENDOR_RULE_UNVERIFIED,
                )
            rule_note = f"; {rule_detail}"
        else:
            if verdict is None:
                return (
                    False,
                    f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
                    "declares policy attestation/v2 but states NO verdict: green CI "
                    f"and a fresh head ({_fmt_ms(head_date_ms)}) prove the checks "
                    "passed at this head, not that a tester judged the work. "
                    "Re-attest with a 'verdict: approved' line (or rejected/hold) "
                    "under the same head",
                    gate_codes.ATT_VERDICT_MISSING,
                )
            return (
                False,
                f"tester attestation {str(newest.get('id'))[:8]} for PR #{pr_number} "
                f"declares policy attestation/v2 with verdict '{verdict}', which "
                "is not a verdict this gate can read (expected approved / "
                "rejected / hold / approved_by_rule) — tester/analyst "
                "adjudication requested rather than a guess",                    gate_codes.ATT_VERDICT_INVALID,
                )
    created_ms = _created_ms_of(newest)
    if created_ms is None:
        # Fail closed with a DEDICATED code. The previous `or 0` coercion
        # bound this row at epoch 0 and reported ATT_STALE_HEAD, which tells
        # the tester to re-attest for a condition no re-attestation can
        # ever fix — the misdiagnosis that froze the merge queue (DBA
        # records 48ac13e2 / 2a51e900). A row with no usable createdAt is
        # a SERVER serialization defect, so it must not masquerade as a
        # freshness verdict. Note this is checked BEFORE the head-date
        # check: with no attestation timestamp there is nothing to compare,
        # so HEAD_DATE_UNKNOWN would be equally uninformative.
        return (
            False,
            f"attestation record {str(newest.get('id'))[:8]} for PR #{pr_number} "
            "carries no usable createdAt, so its freshness cannot be evaluated "
            "(fail closed). This is a record-endpoint serialization defect, "
            "not a tester action: re-attesting will not clear it.",
            gate_codes.ATT_TIMESTAMP_MISSING,
        )
    created_iso = datetime.fromtimestamp(
        created_ms / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    tags_newest = {
        str(t).split(":", 1)[0].lower(): str(t).split(":", 1)[1]
        for t in (newest.get("tags") or [])
        if ":" in str(t or "")
    }
    if head_date_ms is None:
        return (False,
                f"attestation exists ({created_iso}) but head commit date is unknown (fail closed)",
                gate_codes.HEAD_DATE_UNKNOWN)
    # Decision 10: the head BINDING is a `head:<sha>` tag compared against
    # headRefOid (minted only by the pgie-evidence.py head-minting helper via
    # ls-remote at attestation time — never hand-typed). The timestamp
    # predicate (created_ms >= head_date_ms) is SUBSUMED by the binding —
    # a rebase/force-push changes headRefOid, so a stale attestation fails
    # equality regardless of when it was posted. Prefix comparison either
    # direction (the minted tag may carry 7-40 hex chars). Evaluated only
    # when both sides exist; absent tags fall through to the content-based
    # checks so the binding rule stays additive (legacy rows keep semantics).
    if "head" in tags_newest and head_sha:
        bound = tags_newest["head"].lower()
        h = head_sha.lower()
        # [C] Decision 15: a PRESENT but malformed head: tag fails on SHAPE
        # (ATT_SHAPE_UNSEEN -> tester/analyst adjudication) and never
        # silently prefix-binds — a 4-hex tag would bind at ~1/65536. An
        # ABSENT tag keeps the legacy fallthrough to the content-based
        # checks below (documented additive divergence from the binding
        # rule). Shape: 7-40 lowercase hex, same grammar extract_head_shas
        # and the minting tool emit.
        if not re.fullmatch(r"[0-9a-f]{7,40}", bound or ""):
            return (
                False,
                f"attestation head binding tag is malformed: '{str(bound)[:40]}' "
                "(expected 7-40 hex chars; Decision 15 item C — re-mint via "
                f"pgie-evidence.py mint-head --pr {pr_number})",
                gate_codes.ATT_SHAPE_UNSEEN,
            )
        if not (h.startswith(bound) or bound.startswith(h[:7])):
            return (
                False,
                f"attestation head binding {bound[:8]} != PR head {h[:8]} "
                "(Decision 10: binding is by SHA; re-issue the attestation via "
                "the head-minting helper after the push)",
                gate_codes.ATT_STALE_HEAD,
            )
    # Timestamp predicate kept unconditionally (belt-and-braces beneath the
    # binding; the binding short-circuits above when a head: tag exists).
    if created_ms < head_date_ms:
        head_iso = datetime.fromtimestamp(
            head_date_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            False,
            f"newest attestation {created_iso} predates head commit {head_iso} "
            "(a push after attestation invalidates it)",
            gate_codes.ATT_STALE_HEAD,
        )

    # CI-evidence rule: the attestation body must cite CI run IDs, and every
    # cited run must verify against GitHub (conclusion + head SHA binding).
    # Stated test counts ("engine reports 54/54", "all tests pass") are the
    # engine's self-attestation and are not verification.
    att_text = f"{newest.get('title') or ''}\n{newest.get('content') or ''}"
    run_ids = extract_ci_run_ids(att_text)
    if not run_ids:
        return (
            False,
            f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
            "cites no CI run references — stated test counts are the engine's "
            "self-attestation, not verification; cite 'CI run <id>' lines whose "
            "conclusion + head SHA the gate can check against GitHub",
            gate_codes.ATT_NO_CI_EVIDENCE,
        )
    cited_shas = extract_head_shas(att_text)
    runs_ok, runs_detail, runs_transient = verify_ci_runs(run_ids, head_sha, run_json)
    if not runs_ok:
        detail = (f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
                  f"cites run(s) {', '.join(run_ids)}: {runs_detail}")
        if cited_shas and head_sha and not any(
            head_sha.lower().startswith(s) for s in cited_shas
        ):
            detail += (f" — attestation cites head {cited_shas[0][:8]}, "
                       f"PR head is {str(head_sha)[:8]}")
        if not runs_transient:
            # Measured 2026-11-04 (record 9309bea5, PR #863): the row cited
            # 41914723987, which 404s — CI was fully green at the head, so the
            # number was not a workflow run of this repo at all. Without this
            # hint that reads as "CI hasn't finished", and the tester waits
            # for a run that will never appear under that id.
            detail += (
                " — cite ids from `gh run list --commit <head>`; a 404 usually "
                "means the number is not a workflow run in this repository "
                "(a check-run id, or a run from another repo), not that CI is "
                "still in progress")
        return (False, detail,
                gate_codes.ATT_CI_LOOKUP_FAILED if runs_transient
                else gate_codes.ATT_NO_CI_EVIDENCE)
    # Coverage of the head the row claims to have inspected (policy v2). The
    # run-ID rule above proves the runs a row CITES really passed; it cannot
    # say how much of the head was cited, so a row citing one green run at a
    # 76-check head satisfied every check in this function while describing a
    # head it never read. That is the shape of the approval that nearly merged
    # PR #865 (record 467962a1, finding F8): gate 2 caught what the
    # attestation contract claimed to have looked at, which is backwards.
    # Comparing the stated figure against the gate's own rollup turns "did you
    # inspect the head?" from an unwitnessed claim into a cross-reference —
    # the same move the run-ID rule made for "did you verify anything?".
    #
    # Scoped to rows DECLARING attestation/v2: that contract introduced the
    # requirement, so legacy rows keep their original meaning. Skipped when
    # there is no rollup to compare against — an EMPTY rollup is gate 2's
    # CI_NO_CHECKS diagnosis, and repeating that absence here would report one
    # condition twice under two names.
    rollup = (pr_state or {}).get("statusCheckRollup") or []
    if policy is not None and rollup:
        stated = extract_check_count(att_text)
        head_label = str(head_sha)[:8] if head_sha else "head"
        if stated is None:
            return (
                False,
                f"tester attestation {created_iso} (record "
                f"{str(newest.get('id'))[:8]}) declares policy attestation/v2 "
                "but states no rollup count: the run-ID rule proves the runs a "
                "row cites passed, not that it read the head. State the rollup "
                f"it inspected ('Rollup at {head_label}: {len(rollup)} checks, "
                "all success') — policy v2 requires it, and matching it is how "
                "the gate can tell a whole-head read from a single green run",
                gate_codes.ATT_CI_COVERAGE,
            )
        if stated != len(rollup):
            return (
                False,
                f"tester attestation {created_iso} (record "
                f"{str(newest.get('id'))[:8]}) states {stated} checks but head "
                f"{head_label} has {len(rollup)}: either the row was written "
                "against a different head/rollup, or a subset was described as "
                "the whole. Re-attest against the current rollup rather than "
                "restating the figure — the number is the check that the head "
                "was actually read",
                gate_codes.ATT_CI_COVERAGE,
            )
    return (
        True,
        f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
        f"postdates head commit; {runs_detail}"
        + (f"; rollup {len(rollup)} checks stated and matched" if rollup
           and policy is not None else "")
        + rule_note,
        None,
    )


def parse_iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _created_ms_of(record: Dict[str, Any]) -> Optional[int]:
    """The record's createdAt as epoch-ms, or None when it is absent or
    unparseable.

    Accepts the three shapes observed across the record endpoints (DBA record
    ca9ba66c): integer epoch-ms (nebula list :3101 and, post-#652, the point
    endpoint), an ISO-8601 string (the assembly-srv :3107 proxy, which calls
    snakeToCamel without epoch conversion), and snake_case ``created_at`` in
    either ISO or epoch form.

    Returning None — rather than 0 — is the whole point: 0 is a falsy
    timestamp that silently sorts to the bottom and renders as
    1970-01-01, which is indistinguishable from a genuinely ancient record.
    """
    raw = record.get("createdAt")
    if raw is None:
        raw = record.get("created_at")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return parse_iso_to_ms(raw.strip())
        except Exception:
            return None
    return None


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
                        f"gh lookup failed: {_exc_brief(exc)} (fail closed)",
                        code=gate_codes.GH_LOOKUP_FAILED)],
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
            code=None if ready else pr_ready_code(pr),
        )
    )

    ci_ok, ci_detail, ci_code = checks_report(pr.get("statusCheckRollup") or [])
    gates.append(GateResult("ci green", ci_ok, ci_detail, code=ci_code))

    bypassed = env.get(BYPASS_ENV) == "1"
    att_ok, att_detail, att_code = False, "", None
    try:
        head_ms = fetch_head_commit_date_ms(pr_number, run_json)
        att_ok, att_detail, att_code = attestation_check(
            http_get, pr_number, head_ms,
            head_sha=pr.get("headRefOid"), run_json=run_json,
            pr_state=pr,
            files_getter=lambda n: fetch_pr_files(n, run_json),
        )
    except Exception as exc:  # fail closed on any lookup failure
        att_detail = f"attestation lookup failed: {exc!r} (fail closed)"
        att_code = gate_codes.ATT_LOOKUP_FAILED
    if bypassed and not att_ok:
        gates.append(
            GateResult(
                "tester attestation (BYPASSED)",
                True,
                f"{att_detail} [{BYPASS_ENV}=1 -- OPERATOR bypass; attestation gate only]",
                bypassed=True,
                code=gate_codes.ATT_BYPASSED,
            )
        )
    else:
        gates.append(GateResult("tester attestation", att_ok, att_detail, code=att_code))

    return gates, pr


def pr_ready_code(pr: Dict[str, Any]) -> Optional[str]:
    """Gate 1 failure code (deterministic order: closed > draft > conflict >
    unknown-mergeable). Only called when the gate fails."""
    if pr.get("state") != "OPEN":
        return gate_codes.PR_NOT_OPEN
    if pr.get("isDraft"):
        return gate_codes.PR_DRAFT
    if pr.get("mergeable") == "CONFLICTING":
        return gate_codes.MERGE_CONFLICT
    return gate_codes.MERGE_UNKNOWN


def format_report(pr_number: int, gates: List[GateResult]) -> str:
    lines = [f"merge gate for PR #{pr_number}:"]
    for g in gates:
        mark = "PASS" if g.passed else "FAIL"
        if g.bypassed:
            mark = "BYPASS"
        code = f" ({g.code})" if g.code else ""
        lines.append(f"  [{mark}] {g.name}{code}: {g.detail}")
    ok = all(g.passed for g in gates)
    lines.append(f"  => {'ALL GATES PASS' if ok else 'GATE FAILURE -- merge refused'}")
    return "\n".join(lines)


def gates_to_json(
    pr_number: int, gates: List[GateResult], pr: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Machine-readable gate report (--json, spec 86017db0). Codes ride
    alongside the unchanged human detail strings; consumers treat a null
    code exactly as a missing one."""
    return {
        "pr": pr_number,
        "head": (pr or {}).get("headRefOid"),
        "ok": all(g.passed for g in gates),
        "gates": [
            {
                "name": g.name,
                "passed": g.passed,
                "bypassed": g.bypassed,
                "code": g.code,
                "detail": g.detail,
            }
            for g in gates
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr_number", type=int, help="PR number to gate")
    parser.add_argument(
        "--merge",
        action="store_true",
        help="perform the squash merge if (and only if) every gate passes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the machine-readable report (gates_to_json) instead of text",
    )
    args = parser.parse_args(argv)

    gates, pr = evaluate(args.pr_number)
    if args.json:
        print(json.dumps(gates_to_json(args.pr_number, gates, pr), indent=1))
    else:
        print(format_report(args.pr_number, gates))
    if not all(g.passed for g in gates):
        return 1
    if not args.merge:
        if not args.json:
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
