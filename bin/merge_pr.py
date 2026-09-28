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


def verify_ci_runs(
    run_ids: List[str],
    head_sha: Optional[str],
    run_json: Callable[..., Any] = _gh_json,
) -> Tuple[bool, str]:
    """Every cited CI run must exist, be SUCCESS (or NEUTRAL/SKIPPED), and —
    when the run exposes a head SHA — belong to the PR's current head.

    Verification, not trust: the run ID proves a build happened, the API
    conclusion proves it passed, and the SHA binding proves it tested THIS
    code. Runs that omit headSha (some events) are accepted on conclusion
    alone and said so in the detail.
    """
    if not run_ids:
        return False, "no CI run references"
    ok_shas: List[str] = []
    for rid in run_ids:
        try:
            run = run_json("api", f"repos/{{owner}}/{{repo}}/actions/runs/{rid}",
                           "--jq", "{c: .conclusion, s: .head_sha}")
        except Exception as exc:
            return False, f"CI run {rid} lookup failed: {_exc_brief(exc)} (fail closed)"
        conclusion = str((run or {}).get("c") or "").strip().lower()
        if conclusion not in ("success", "neutral", "skipped"):
            return False, f"CI run {rid} conclusion is '{conclusion or 'unknown'}' (not success)"
        run_sha = str((run or {}).get("s") or "").strip().lower()
        if run_sha and head_sha:
            if not head_sha.lower().startswith(run_sha[:7]) and not run_sha.startswith(head_sha.lower()[:7]):
                return False, (
                    f"CI run {rid} tested head {run_sha[:8]}, not this PR's head "
                    f"{str(head_sha)[:8]} — attested evidence is for superseded code"
                )
            ok_shas.append(run_sha[:7])
    sha_note = f"; head SHA verified ({', '.join(ok_shas)})" if ok_shas else "; run head SHA not exposed by API (conclusion verified only)"
    return True, f"{len(run_ids)} CI run(s) verified success{sha_note}"


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
    head_sha: Optional[str] = None,
    run_json: Callable[..., Any] = _gh_json,
) -> Tuple[bool, str, Optional[str]]:
    """Gate 3 lookup, exact by preference: the indexed /api/attestations
    endpoint when available (server-side shape filter + GIN tags index),
    bounded-scan fallback otherwise. Both paths share the client-side
    shape + freshness + CI-evidence rules in evaluate_attestation().

    Returns (ok, detail, code) like evaluate_attestation()."""
    rows = fetch_attestations(http_get, pr_number)
    if rows is not None:
        # The indexed endpoint returns a projection WITHOUT record content,
        # but the CI-evidence rule reads the attestation body (cited run IDs).
        # Content-less rows are hydrated from the bounded scan before
        # evaluation; if the scan cannot find them either, evaluation runs on
        # the content-less rows and fails the evidence rule with a
        # self-explaining detail rather than guessing.
        if rows and all(not (r or {}).get("content") for r in rows):
            try:
                full = fetch_agent_records(http_get)
                by_id = {str(r.get("id")): r for r in full}
                rows = [by_id.get(str(r.get("id")), r) for r in rows]
            except Exception:
                pass  # evaluate on projection rows; evidence rule fails closed
        ok, detail, code = evaluate_attestation(
            rows, pr_number, head_date_ms,
            head_sha=head_sha, run_json=run_json,
        )
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
                code = gate_codes.ATT_SHAPE_UNSEEN
            else:
                code = gate_codes.ATT_MISSING
        else:
            detail += " (indexed lookup)"
        return ok, detail, code
    records = fetch_agent_records(http_get)
    ok, detail, code = evaluate_attestation(
        records, pr_number, head_date_ms,
        head_sha=head_sha, run_json=run_json,
    )
    return ok, detail + " (scan fallback: /api/attestations unavailable)", code


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
    head_sha: Optional[str] = None,
    run_json: Callable[..., Any] = _gh_json,
) -> Tuple[bool, str, Optional[str]]:
    """Gate 3: an explicit tester attestation for this PR must postdate the
    head commit (a push after attestation means the attested code is gone)
    and cite CI run IDs that verify against GitHub (conclusion success + head
    SHA binding). Stated test counts ("engine reports 54/54") are the
    engine's self-attestation and never satisfy the evidence rule. Records
    must satisfy :func:`is_attestation_record` — a tester record that merely
    mentions the PR (e.g. an intent row) does not count.

    Returns (ok, detail, code) — code is a gate_codes value on failure
    (ATT_MISSING / ATT_SHAPE_UNSEEN / HEAD_DATE_UNKNOWN / ATT_STALE_HEAD /
    ATT_NO_CI_EVIDENCE), None on pass."""
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
    newest = max(matches, key=lambda r: r.get("createdAt") or 0)
    created_ms = newest.get("createdAt") or 0
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
    runs_ok, runs_detail = verify_ci_runs(run_ids, head_sha, run_json)
    if not runs_ok:
        detail = (f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
                  f"cites run(s) {', '.join(run_ids)}: {runs_detail}")
        if cited_shas and head_sha and not any(
            head_sha.lower().startswith(s) for s in cited_shas
        ):
            detail += (f" — attestation cites head {cited_shas[0][:8]}, "
                       f"PR head is {str(head_sha)[:8]}")
        return (False, detail, gate_codes.ATT_NO_CI_EVIDENCE)
    return (
        True,
        f"tester attestation {created_iso} (record {str(newest.get('id'))[:8]}) "
        f"postdates head commit; {runs_detail}",
        None,
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
