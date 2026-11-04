#!/usr/bin/env python3
"""vendor_attest — policy-bounded attestation for vendor (Dependabot) PRs.

Operator ruling 2026-11-03: automated tester attestation is approved for
VENDOR-ONLY diffs. This tool is that policy, expressed as a rule narrow
enough to be auditable, and it is deliberately the ONLY thing it can do:
it posts an evidence-bearing tester attestation. It never merges, never
edits a branch, and never sets NEXUS_ALLOW_UNATTESTED_MERGE. Merging stays
with bin/merge_pr.py, driven by bin/attestation_janitor.py.

Why this exists: 17 Dependabot PRs sat MERGEABLE + green behind gate 3,
each needing a hand-written attestation. Attesting them by hand is not a
control — it is a ritual that trains everyone to rubber-stamp. The control
is the allowlist below plus the gate's own re-verification at merge time.

One PR is attested only when EVERY rail holds:

  1. AUTHOR is a dependency bot (app/dependabot / dependabot[bot]).
  2. DIFF is vendor-only: every changed path's basename is a dependency
     manifest or lockfile, and no path lies under a governing directory
     (.github/, bin/, sql/, schemas/, scripts/, docs/, config/). A single
     out-of-policy path refuses the whole PR — a lockfile bump that also
     touches a workflow or a script is not a vendor bump.
  3. UPDATE TYPE is not semver-major. Majors change behaviour (PR #726:
     @adonisjs/core 6.21.0 -> 7.5.2) and keep requiring a human/tester
     attestation. Unclassifiable updates refuse too (fail closed).
  4. CI is green at the head: every check COMPLETED and in
     merge_pr.PASSING_CONCLUSIONS (the gate's own rule, imported — not
     reimplemented).
  5. EVIDENCE is real: workflow-run IDs are harvested from the head's
     check-run details URLs and re-verified through merge_pr.verify_ci_runs
     (conclusion == success AND head_sha binds to the PR head).
  6. HEAD is minted from the live remote by bin/pgie-evidence.py mint-head
     (Decision 10 — never hand-typed), and the attestation carries
     head:<sha7> so any later push invalidates it.
  7. No attestation already satisfies gate 3 at this head (idempotent
     across timer ticks and across re-runs).

Default mode is check-only; --apply is required to post a record.

Deployment: run before the janitor in attestation-janitor.service, then
let the janitor (with --extra-author app/dependabot) do the gated merge.

Dual-runnable tests: python3 bin/tests/test_vendor_attest.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Sibling modules (bin/merge_pr.py, bin/gate_codes.py); the shim keeps the
# import working when this file is loaded by path in tests rather than run
# as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_pr  # noqa: E402  (single authority for CI rules + run verification)

BIN_DIR = Path(__file__).resolve().parent
MERGE_PR = BIN_DIR / "merge_pr.py"
PGIE = BIN_DIR / "pgie-evidence.py"
POST_RECORD = BIN_DIR / "post-agent-record.py"

# Dependency-bot identities. Dependabot's PR author is the app slug
# `app/dependabot`; the bracket form appears in some API surfaces.
VENDOR_AUTHORS = ("app/dependabot", "dependabot[bot]", "dependabot")

# Basenames that are dependency manifests or lockfiles, i.e. what a vendor
# bump is allowed to touch. Anything else refuses the PR.
ALLOWED_BASENAMES = {
    # javascript / typescript
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
    # python
    "requirements.txt", "constraints.txt", "pyproject.toml",
    "poetry.lock", "Pipfile", "Pipfile.lock", "uv.lock",
    # java / kotlin / gradle
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    # rust / go / ruby / php
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
}
# Versioned requirement files (requirements-dev.txt, requirements-ci.txt, ...).
ALLOWED_BASENAME_RE = re.compile(r"^requirements[-_a-z0-9]*\.txt$")

# Governing paths: a dependency PR must never touch these, even though some
# of them could contain a file whose basename is allowlisted. Checked as
# path PREFIXES against the repo-relative path.
DENY_PREFIXES = (
    ".github/", "bin/", "sql/", "schemas/", "scripts/", "docs/",
    "config/", "audit/",
)

SEMVER_MAJOR = "version-update:semver-major"
ALLOWED_UPDATE_TYPES = {"version-update:semver-patch", "version-update:semver-minor"}

# Per-cycle attestation cap: a bound on how much of the queue one tick can
# convert, mirroring the janitor's merge cap.
DEFAULT_CAP = 5
# Bounded evidence set: vendor PRs run the full matrix, and many checks share
# one workflow run, so the deduped run count is small. A cap keeps the
# per-PR GitHub lookups sane when a lockfile change fans out.
MAX_CITED_RUNS = 10

ATTESTATION_TAG = "type:attestation"
POLICY_TAG = "type:vendor-bump"

_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")
_UPDATE_TYPE_RE = re.compile(r"update-type:\s*([A-Za-z0-9:_-]+)")
# Security-update bodies carry NO update-type metadata; their authoritative
# signal is the top-level `Updates `pkg` from X to Y` line. A blanket
# "from X to Y" scan over the body is wrong: Dependabot embeds the
# dependency's CHANGELOG, so an unrelated upstream line ("<li>Bump pillow
# from 12.3.0 to 13.0.0</li>") refused #823 for a major bump that was not
# being made. Anchoring to the update lines and the title is what keeps the
# fallback honest.
_UPDATES_LINE_RE = re.compile(
    r"^Updates? `([^`]+)` from (\S+) to (\S+)\s*$", re.MULTILINE
)
# The PR title always names the bumped package and the pair (grouped titles
# omit the pair, which is why the update lines stay the primary signal).
_TITLE_PAIR_RE = re.compile(
    r"\bbump\s+(?:the\s+)?`?([@\w./-]+)`?\s+from\s+v?([\w.+-]+)\s+to\s+v?([\w.+-]+)",
    re.IGNORECASE,
)
_DEP_NAME_RE = re.compile(r"dependency-name:\s*(\S+)")


def gh_json(*args: str, runner: Optional[Callable] = None) -> Any:
    run = runner or subprocess.run
    proc = run(["gh", *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout or "null")


def run_json_via(runner: Callable) -> Callable[..., Any]:
    """merge_pr.verify_ci_runs-shaped run_json bound to an injected runner.

    Keeps the whole cycle injectable through ONE callable (the janitor's
    convention): without this the default would be merge_pr._gh_json, and a
    caller that stubbed only the runner would silently reach the real `gh`
    during evidence verification — a test that passes while doing I/O, or a
    transient failure that looks like bad evidence.
    """
    def run_json(*args: str, **kwargs: Any) -> Any:
        proc = runner(["gh", *args], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                f"gh {' '.join(args)} failed: {(proc.stderr or '').strip()[:200]}"
            )
        return json.loads(proc.stdout or "null")
    return run_json


# ── Policy rails ────────────────────────────────────────────────────────

def classify_paths(paths: Sequence[str]) -> Tuple[bool, str]:
    """True when every path is a dependency manifest/lockfile.

    Fails closed on an empty path list (a PR with no files proves nothing).
    """
    if not paths:
        return False, "no changed files reported (fail closed)"
    offenders: List[str] = []
    for raw in paths:
        path = str(raw or "").replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        # Deny on the ORIGINAL leading component: `.github/` must not be
        # normalised away (an lstrip("./") would turn it into `github/`).
        lowered = path.lower()
        if any(lowered.startswith(p) for p in DENY_PREFIXES):
            offenders.append(f"{path} (governing path)")
            continue
        base = path.rsplit("/", 1)[-1]
        if base in ALLOWED_BASENAMES or ALLOWED_BASENAME_RE.match(base):
            continue
        offenders.append(f"{path} (not a dependency manifest)")
    if offenders:
        shown = "; ".join(offenders[:4])
        more = "" if len(offenders) <= 4 else f" (+{len(offenders) - 4} more)"
        return False, f"diff is not vendor-only: {shown}{more}"
    return True, f"{len(paths)} dependency manifest/lockfile path(s)"


def _same_major(a: str, b: str) -> bool:
    return a.split(".")[0].lstrip("v") == b.split(".")[0].lstrip("v")


def classify_update(body: str, title: str = "") -> Tuple[bool, str, List[str]]:
    """True when the update is patch/minor and classified.

    Signals, in order of authority:
      1. Dependabot's `update-type:` metadata (version-update bodies, one
         entry per dependency in a grouped PR);
      2. the security-update body's `Updates `pkg` from X to Y` lines;
      3. the PR title's `bump <pkg> from X to Y`.

    Only those are consulted — see _UPDATES_LINE_RE for why a blanket body
    scan refused a legitimate PR. Every observed pair must stay on the same
    major. No usable signal at all refuses (fail closed): "cannot tell" is
    not "safe".
    """
    kinds = [k.lower() for k in _UPDATE_TYPE_RE.findall(body or "")]
    if kinds:
        majors = [k for k in kinds if k == SEMVER_MAJOR]
        if majors:
            return False, f"semver-major update (update-type {SEMVER_MAJOR})", kinds
        unknown = [k for k in kinds if k not in ALLOWED_UPDATE_TYPES]
        if unknown:
            return False, f"unrecognised update-type(s): {sorted(set(unknown))}", kinds
        return True, f"update-type(s): {', '.join(sorted(set(kinds)))}", kinds

    pairs: List[Tuple[str, str, str]] = [
        (name, a, b) for name, a, b in _UPDATES_LINE_RE.findall(body or "")
    ]
    pairs += [
        (name, a, b) for name, a, b in _TITLE_PAIR_RE.findall(title or "")
    ]
    if not pairs:
        return False, "no update-type metadata and no 'from X to Y' pair (unclassified)", kinds
    majors = [p for p in pairs if not _same_major(p[1], p[2])]
    if majors:
        shown = ", ".join(f"{n} {a} -> {b}" for n, a, b in majors[:3])
        return False, f"major version change in version pair(s): {shown}", kinds
    return (
        True,
        f"{len(pairs)} version pair(s), all same-major (no update-type metadata)",
        kinds,
    )


def dependency_names(body: str) -> List[str]:
    """Dependency names from Dependabot's metadata block (best effort)."""
    names: List[str] = []
    for raw in _DEP_NAME_RE.findall(body or ""):
        name = raw.strip().strip("'\"")
        if name and name not in names:
            names.append(name)
    return names


def is_vendor_author(login: str) -> bool:
    return str(login or "").strip().lower() in {a.lower() for a in VENDOR_AUTHORS}


# ── Evidence ────────────────────────────────────────────────────────────

def cited_run_ids(rollup: Sequence[Dict[str, Any]]) -> List[str]:
    """Workflow-run IDs from successful check-runs' details URLs, deduped.

    Many checks share one run, so dedupe before the per-run verification
    call. Only SUCCESS check-runs are harvested: citing a failing or skipped
    check's run would cite a run that proves nothing (verify_ci_runs rejects
    non-success conclusions anyway — this just avoids the lookup).
    """
    ids: List[str] = []
    for check in rollup or []:
        if str(check.get("conclusion") or "").upper() != "SUCCESS":
            continue
        url = str(check.get("detailsUrl") or check.get("details_url") or "")
        m = _RUN_URL_RE.search(url)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids[:MAX_CITED_RUNS]


def resolve_repo(runner: Callable) -> str:
    """OWNER/REPO for the current checkout, or "" when unresolvable.

    mint-head cannot resolve it on its own from an R8.0 linked worktree
    (the remote lookup yields ::error::could not resolve OWNER/REPO), so the
    caller resolves it once and passes it through explicitly.
    """
    try:
        data = gh_json("repo", "view", "--json", "nameWithOwner", runner=runner)
        full = str((data or {}).get("nameWithOwner") or "").strip()
        if full.count("/") == 1:
            return full
    except (RuntimeError, ValueError):
        pass
    try:
        proc = runner(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            m = re.search(
                r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", (proc.stdout or "").strip()
            )
            if m:
                return m.group(1)
    except (OSError, RuntimeError):
        pass
    return ""


def mint_head(pr_number: int, runner: Callable, repo: str = "") -> str:
    """`head:<sha7>` minted from the live remote (Decision 10). Never typed."""
    argv = [sys.executable, str(PGIE), "mint-head", "--pr", str(pr_number)]
    if repo:
        argv += ["--repo", repo]
    proc = runner(
        argv,
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mint-head failed: {(proc.stderr or '').strip()[:160]}")
    tag = (proc.stdout or "").strip().splitlines()[-1].strip() if proc.stdout else ""
    if not re.fullmatch(r"head:[0-9a-f]{7,40}", tag):
        raise RuntimeError(f"mint-head produced no usable head tag: {tag[:40]!r}")
    return tag


def gate_report(pr_number: int, runner: Callable) -> "subprocess.CompletedProcess":
    return runner(
        [sys.executable, str(MERGE_PR), str(pr_number)],
        capture_output=True, text=True, timeout=180,
    )


def gate_attestation_passes(proc: "subprocess.CompletedProcess") -> bool:
    """True when gate 3 already passes for this head (idempotency probe)."""
    return any(
        "tester attestation" in ln and "[PASS]" in ln
        for ln in (proc.stdout or "").splitlines()
    )


def attestation_body(
    pr: Dict[str, Any],
    head_tag: str,
    paths: Sequence[str],
    update_detail: str,
    deps: Sequence[str],
    run_ids: Sequence[str],
    rollup: Sequence[Dict[str, Any]],
) -> str:
    head = head_tag.split(":", 1)[1]
    by_run: Dict[str, str] = {}
    for check in rollup or []:
        m = _RUN_URL_RE.search(str(check.get("detailsUrl") or check.get("details_url") or ""))
        if m and m.group(1) in run_ids and m.group(1) not in by_run:
            by_run[m.group(1)] = str(check.get("name") or "check")
    lines = [
        # Machine-readable declarations. `policy` is the ATTESTATION contract
        # (how this row must be read); `vendor rule` is the rule that granted
        # the approval. They are deliberately separate keys: gate 3 accepts an
        # `approved_by_rule` verdict only by re-proving the rule against the
        # live diff and author, and checks that the rule id is recorded here,
        # so an approval by rule is reproducible rather than a magic string.
        "policy: attestation/v2",
        "verdict: approved_by_rule",
        "vendor rule: vendor-bump/v1",
        "",
        f"Vendor attestation — PR #{pr.get('number')} (policy vendor-bump/v1).",
        "",
        f"PR: {str(pr.get('title') or '')[:200]}",
        f"Author: {(pr.get('author') or {}).get('login', '')} (dependency bot)",
        f"Head: {head} (minted via bin/pgie-evidence.py mint-head --pr {pr.get('number')})",
        "",
        f"Diff ({len(paths)} path(s), vendor manifests only):",
        *[f"  - {p}" for p in paths[:20]],
        "",
        f"Update: {update_detail}",
    ]
    if deps:
        lines.append(f"Dependencies: {', '.join(deps[:20])}")
    lines += ["", "Evidence (re-verified against GitHub at this head):"]
    for rid in run_ids:
        lines.append(
            f"CI run {rid}: {by_run.get(rid, 'workflow run')} — conclusion success, "
            f"head {head}"
        )
    lines += [
        "",
        "Policy rails applied by bin/vendor_attest.py (operator ruling 2026-11-03):",
        "- every changed path is a dependency manifest/lockfile; no .github/, bin/, "
        "sql/, schemas/, scripts/, docs/, config/ or audit/ path",
        "- no semver-major update (majors keep requiring a human/tester attestation)",
        f"- every check at this head completed successfully ({len(rollup)} checks)",
        "- each cited run verified for conclusion=success AND head_sha binding",
        "- attestation is head-bound; any later push invalidates it",
        "",
        "Attested by the vendor-bump rule, not by a tester's judgement of code: "
        "this covers dependency-manifest churn only. Anything outside that scope "
        "must be attested by a person or a role agent.",
    ]
    return "\n".join(lines) + "\n"


def post_attestation(
    pr_number: int,
    body: str,
    head_tag: str,
    runner: Callable,
) -> str:
    """Create the tester attestation record via the canonical writer."""
    tags = [
        ATTESTATION_TAG,
        f"pr:{pr_number}",
        head_tag,
        POLICY_TAG,
        "status:done",
    ]
    fd, path = tempfile.mkstemp(prefix=f"vendor-attest-{pr_number}-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = runner(
            [
                sys.executable, str(POST_RECORD),
                "--role", "tester",
                "--record-type", "assessment",
                "--level", "1",
                "--visibility", "all",
                "--title", f"Vendor attestation — PR #{pr_number} ({head_tag})",
                "--tags", ",".join(tags),
                "--file", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"record post failed: {(proc.stderr or '').strip()[:200]}")
        return (proc.stdout or "").strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── One PR ──────────────────────────────────────────────────────────────

def workflow_run_ids(head_sha: str, runner: Callable) -> List[str]:
    """Successful workflow runs at `head_sha`, from `gh run list --commit`.

    Fallback for PRs whose check-rollup carries no Actions details URLs
    (#788: green, but no run reference recoverable from the rollup), so the
    evidence rule still cites real runs instead of refusing a legitimate
    bump for a presentation quirk. Only completed successes qualify.
    """
    try:
        runs = gh_json(
            "run", "list", "--commit", head_sha, "--limit", "20",
            "--json", "databaseId,status,conclusion", runner=runner,
        )
    except (RuntimeError, ValueError):
        return []
    ids: List[str] = []
    for run in runs or []:
        if str(run.get("status") or "").lower() != "completed":
            continue
        if str(run.get("conclusion") or "").lower() != "success":
            continue
        rid = str(run.get("databaseId") or "")
        if rid and rid not in ids:
            ids.append(rid)
    return ids[:MAX_CITED_RUNS]


def attest_pr(
    pr_number: int,
    *,
    apply: bool,
    runner: Callable,
    run_json: Callable[..., Any],
    repo: str = "",
    out: Any = sys.stdout,
) -> str:
    """Evaluate and (under --apply) attest one PR.

    Returns one of: 'posted', 'would-post', 'attested', 'refused', 'error'.
    """
    try:
        pr = gh_json(
            "pr", "view", str(pr_number), "--json",
            "number,title,body,author,headRefOid,isDraft,mergeable,files,statusCheckRollup",
            runner=runner,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"  #{pr_number}: view failed — {exc}", file=out)
        return "error"
    if not isinstance(pr, dict):
        print(f"  #{pr_number}: unexpected view payload", file=out)
        return "error"

    login = str((pr.get("author") or {}).get("login") or "")
    if not is_vendor_author(login):
        print(f"  #{pr_number}: author '{login}' is not a dependency bot — skip", file=out)
        return "refused"
    if pr.get("isDraft"):
        print(f"  #{pr_number}: draft — vendor attestation does not cover drafts", file=out)
        return "refused"
    if str(pr.get("mergeable") or "").upper() != "MERGEABLE":
        print(f"  #{pr_number}: not MERGEABLE ({pr.get('mergeable')}) — skip", file=out)
        return "refused"

    files = pr.get("files") or []
    paths = [str(f.get("path") or "") for f in files]
    ok, detail = classify_paths(paths)
    if not ok:
        print(f"  #{pr_number}: REFUSED — {detail}", file=out)
        return "refused"

    ok, update_detail, _kinds = classify_update(
        pr.get("body") or "", pr.get("title") or ""
    )
    if not ok:
        print(f"  #{pr_number}: REFUSED — {update_detail} (needs human/tester attestation)", file=out)
        return "refused"

    rollup = pr.get("statusCheckRollup") or []
    ci_ok, ci_detail, _code = merge_pr.checks_report(rollup)
    if not ci_ok:
        print(f"  #{pr_number}: REFUSED — CI not green: {ci_detail}", file=out)
        return "refused"
    head_sha = str(pr.get("headRefOid") or "")
    run_ids = cited_run_ids(rollup) or workflow_run_ids(head_sha, runner)
    if not run_ids:
        print(
            f"  #{pr_number}: REFUSED — no successful workflow run IDs "
            "recoverable at this head",
            file=out,
        )
        return "refused"

    evidence_ok, evidence_detail, transient = merge_pr.verify_ci_runs(
        run_ids, head_sha, run_json
    )
    if not evidence_ok:
        kind = "transient lookup failure" if transient else "evidence rejected"
        print(f"  #{pr_number}: REFUSED — {kind}: {evidence_detail}", file=out)
        return "error" if transient else "refused"

    if gate_attestation_passes(gate_report(pr_number, runner)):
        print(f"  #{pr_number}: gate 3 already passes at this head — nothing to do", file=out)
        return "attested"

    try:
        head_tag = mint_head(pr_number, runner, repo)
    except (RuntimeError, OSError) as exc:
        print(f"  #{pr_number}: REFUSED — {exc}", file=out)
        return "error"

    deps = dependency_names(pr.get("body") or "")
    payload = attestation_body(pr, head_tag, paths, update_detail, deps, run_ids, rollup)

    if not apply:
        print(
            f"  #{pr_number}: vendor-only, non-major, CI green, {len(run_ids)} run(s) "
            f"verified at {head_tag} — WOULD POST (check-only)",
            file=out,
        )
        return "would-post"

    try:
        receipt = post_attestation(pr_number, payload, head_tag, runner)
    except (RuntimeError, OSError) as exc:
        print(f"  #{pr_number}: attestation post FAILED — {exc}", file=out)
        return "error"
    print(f"  #{pr_number}: ATTESTED at {head_tag} ({detail}; {evidence_detail}) {receipt[:60]}", file=out)
    return "posted"


# ── Cycle ───────────────────────────────────────────────────────────────

def discover_vendor_prs(author: str, runner: Callable) -> List[Dict[str, Any]]:
    prs = gh_json(
        "pr", "list", "--state", "open", "--author", author,
        "--json", "number,headRefOid", "--limit", "200",
        runner=runner,
    )
    return sorted(prs or [], key=lambda p: p["number"])


def run_cycle(
    *,
    apply: bool,
    cap: int,
    author: str,
    only_pr: Optional[int] = None,
    runner: Optional[Callable] = None,
    run_json: Optional[Callable[..., Any]] = None,
    out: Any = sys.stdout,
) -> int:
    """One attestation cycle. Returns 0 on success (refusals included), 1 on
    a tool error (discovery/lookup failure) so the caller can fail loudly."""
    runner = runner or subprocess.run
    run_json = run_json or run_json_via(runner)
    try:
        prs = discover_vendor_prs(author, runner)
    except (RuntimeError, ValueError) as exc:
        print(f"vendor_attest: discovery failed: {exc}", file=out)
        return 1
    if only_pr is not None:
        prs = [p for p in prs if p["number"] == only_pr]
    print(
        f"vendor_attest: {len(prs)} open PR(s) authored by {author} "
        f"(apply={apply}, cap={cap})",
        file=out,
    )

    repo = resolve_repo(runner)
    if not repo:
        print("vendor_attest: WARNING — repository could not be resolved; "
              "head minting may fail", file=out)
    posted = 0
    tool_error = False
    for pr in prs:
        num = pr["number"]
        if posted >= cap:
            print(f"  #{num}: per-cycle attestation cap {cap} reached — next tick", file=out)
            continue
        outcome = attest_pr(
            num, apply=apply, runner=runner, run_json=run_json, repo=repo, out=out
        )
        if outcome == "posted":
            posted += 1
        elif outcome == "error":
            tool_error = True
    print(f"vendor_attest: cycle complete — {posted} attestation(s) posted", file=out)
    return 1 if tool_error else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Policy-bounded vendor (Dependabot) attestation.")
    ap.add_argument("--apply", action="store_true", help="post attestation records (default: check-only)")
    ap.add_argument("--pr", type=int, default=None, help="restrict the cycle to one PR number")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP, help=f"max attestations per cycle (default {DEFAULT_CAP})")
    ap.add_argument("--author", default=VENDOR_AUTHORS[0], help="vendor bot author (default app/dependabot)")
    args = ap.parse_args(argv)
    return run_cycle(
        apply=args.apply,
        cap=max(1, args.cap),
        author=args.author,
        only_pr=args.pr,
    )


if __name__ == "__main__":
    sys.exit(main())
