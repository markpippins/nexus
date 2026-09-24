#!/usr/bin/env python3
"""Attestation auto-merge janitor — land attested PRs without waiting on a human.

One cycle:
  1. Discover open PRs authored by the configured account (`gh api user`,
     override with JANITOR_AUTHOR).
  2. Prefilter candidates via the indexed attestation lookup
     (GET {NEBULA_BASE}/api/attestations?pr=N, same NEBULA_BASE convention as
     bin/merge_pr.py): a 200 with zero rows proves the PR unattested -> skip.
     An unreachable/404 endpoint proves nothing -> evaluate anyway (fail-safe;
     the gate's own scan fallback still decides).
  3. Run bin/merge_pr.py <N> in CHECK-ONLY mode. Only if every gate passes:
     if --apply, merge via bin/merge_pr.py <N> --merge (the gate re-runs all
     checks at merge time), then post a change-log entry. If the sole failing
     gate is "pr open & ready" (draft state), promote with `gh pr ready` once
     and re-run the gate — a draft is never merged, and never promoted unless
     the attestation/CI gates already pass.

Safety rails (all non-negotiable):
  - --merge is passed to the gate only after an immediately-preceding
    check-only run of the SAME gate exited 0 for the SAME head.
  - Default mode is check-only: without --apply nothing is mutated.
  - Per-cycle merge cap (default 3) bounds blast radius.
  - PRs already recorded as merged in the state file are skipped (idempotent
    across timer ticks).
  - The janitor never edits branches, never forces anything, never bypasses
    a gate: a BYPASS line in the gate report is treated as failure.

Change-log audit trail — every ACTION is logged to the Assembly change-log
forum via bin/post-change-log.sh, individually:
  - merged    : gated squash-merge issued (with head + gate evidence)
  - promoted  : attested draft raised to ready (substantive gates already passed)
  - refused   : an ATTESTED PR the gate refused (anomaly — surfaced loudly)
  - bypass    : gate report contained BYPASS; merge refused
  - cap-hold  : attested+gated PR deferred by the per-cycle cap
Routine "not attested yet" skips are NOT change-logged (a 15-minute timer
over N open PRs would flood the forum); they are recorded in the state
file's per-run summary instead.

Deployment: systemd user units bin/attestation-janitor.{service,timer}
(in-repo source-of-truth doctrine, same as nexus-todo-lifecycle / sdk-drift-stamp).

Dual-runnable tests: python3 bin/tests/test_attestation_janitor.py
(or pytest bin/tests/test_attestation_janitor.py).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BIN_DIR = Path(__file__).resolve().parent
MERGE_PR = BIN_DIR / "merge_pr.py"
POST_CHANGE_LOG = BIN_DIR / "post-change-log.sh"
DEFAULT_NEBULA_BASE = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_STATE = os.path.expanduser("~/.cache/attestation-janitor.json")

PROMOTABLE_FAIL = "pr open & ready"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh_json(*args: str, runner: Optional[Callable] = None) -> Any:
    run = runner or subprocess.run
    proc = run(["gh", *args], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def discover_open_prs(author: str, runner: Callable) -> List[Dict[str, Any]]:
    """Open PRs authored by `author`, oldest first (stable merge order)."""
    prs = gh_json(
        "pr", "list", "--state", "open", "--author", author,
        "--json", "number,isDraft,headRefOid,title", "--limit", "200",
        runner=runner,
    )
    return sorted(prs, key=lambda p: p["number"])


def attestation_prefilter(pr_number: int, base_url: str) -> Optional[bool]:
    """True=attested, False=proven unattested, None=unknown (endpoint down).

    Uses the same endpoint the gate's indexed path uses. A definitive empty
    result lets the janitor skip without spending a gate run; anything else
    (404 while migration 055 is undeployed, timeout, bad JSON) is treated as
    unknown so the gate + its scan fallback remain the authority.
    """
    url = f"{base_url.rstrip('/')}/api/attestations?pr={pr_number}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        return len(data.get("items", [])) > 0
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def run_gate(pr_number: int, runner: Callable) -> "subprocess.CompletedProcess":
    return runner(
        [sys.executable, str(MERGE_PR), str(pr_number)],
        capture_output=True, text=True, timeout=180,
    )


def gate_failed_only_on_draft(proc: "subprocess.CompletedProcess", is_draft: bool) -> bool:
    """True when the only [FAIL] line is the draft-state gate."""
    if proc.returncode != 1 or not is_draft:
        return False
    fails = [ln for ln in proc.stdout.splitlines() if "[FAIL]" in ln]
    return bool(fails) and all(PROMOTABLE_FAIL in ln for ln in fails)


def load_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"merged": {}, "runs": []}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1) + "\n")


def post_change_log(title: str, body: str) -> bool:
    try:
        proc = subprocess.run(
            ["/usr/bin/env", "bash", str(POST_CHANGE_LOG), "--title", title, "--body", body],
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_cycle(
    *,
    apply: bool,
    cap: int,
    author: str,
    base_url: str,
    state_path: Path,
    only_pr: Optional[int] = None,
    runner: Optional[Callable] = None,
    out: Any = sys.stdout,
) -> int:
    """One janitor cycle. Returns process exit code (0 = no tool errors)."""
    runner = runner or subprocess.run
    state = load_state(state_path)
    merged_book = state.setdefault("merged", {})
    merged_count = 0
    held: List[int] = []
    tool_error = False

    try:
        prs = discover_open_prs(author, runner)
    except RuntimeError as exc:
        print(f"janitor: discovery failed: {exc}", file=out)
        return 1
    if only_pr is not None:
        prs = [p for p in prs if p["number"] == only_pr]
    print(f"janitor: {len(prs)} open PR(s) authored by {author} (apply={apply}, cap={cap})", file=out)

    for pr in prs:
        num = pr["number"]
        head = pr.get("headRefOid", "")[:12]
        if str(num) in merged_book:
            print(f"  #<>{num}: already merged by a previous cycle (state) — skip", file=out)
            continue
        pre = attestation_prefilter(num, base_url)
        if pre is False:
            print(f"  #{num}: no attestation rows (indexed lookup, definitive) — skip", file=out)
            continue
        if pre is None:
            print(f"  #{num}: attestation endpoint unavailable/unknown — evaluating via gate (fail-safe)", file=out)

        proc = run_gate(num, runner)
        if gate_failed_only_on_draft(proc, pr.get("isDraft", False)):
            if apply:
                print(f"  #{num}: all substantive gates pass; promoting draft via gh pr ready", file=out)
                r = runner(["gh", "pr", "ready", str(num)], capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    print(f"  #{num}: gh pr ready FAILED: {r.stderr.strip()[:160]} — held", file=out)
                    tool_error = True
                    held.append(num)
                    continue
                if not post_change_log(
                    f"attestation-janitor: promoted draft PR #{num} to ready",
                    f"Auto-promotion by bin/attestation_janitor.py at {_now_iso()}. PR #{num} "
                    f"(head {head}) is attested with CI green; the only failing gate was "
                    "draft state. Raised via `gh pr ready` and immediately re-gated before "
                    "any merge decision.",
                ):
                    print(f"  #{num}: WARNING — change-log post failed (promotion itself succeeded)", file=out)
                    tool_error = True
            else:
                print(f"  #{num}: substantive gates pass; draft would be promoted under --apply (no action taken)", file=out)
            proc = run_gate(num, runner)

        if proc.returncode != 0:
            fails = "; ".join(ln.strip() for ln in proc.stdout.splitlines() if "[FAIL]" in ln) or f"exit={proc.returncode}"
            print(f"  #{num}: gate refuses — {fails[:220]}", file=out)
            if pre is True:
                # Attested yet refused: anomaly worth a forum-visible alert.
                if not post_change_log(
                    f"attestation-janitor: ANOMALY — attested PR #{num} refused by gate",
                    f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} (head {head}) has "
                    "an attestation row but the merge gate refused: "
                    f"{fails[:400]}. Human attention requested — possible head drift "
                    "(attestation predates current head), CI regression, or stale row.",
                ):
                    tool_error = True
            held.append(num)
            continue
        if "BYPASS" in proc.stdout:
            print(f"  #{num}: gate report contains BYPASS — refusing to merge (bypasses are human decisions)", file=out)
            if not post_change_log(
                f"attestation-janitor: BYPASS in gate report for PR #{num} — merge refused",
                f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} (head {head}) passed "
                "the gates only via a BYPASS marker; the janitor never merges bypassed "
                "gates. Human decision required.",
            ):
                tool_error = True
            held.append(num)
            continue

        if not apply:
            print(f"  #{num}: ALL GATES PASS — check-only, no action (use --apply to merge)", file=out)
            continue
        if merged_count >= cap:
            print(f"  #{num}: attested+gated, but per-cycle cap {cap} reached — held for next cycle", file=out)
            if not post_change_log(
                f"attestation-janitor: PR #{num} held by per-cycle cap ({cap})",
                f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} (head {head}) is "
                "attested and passes every gate but the per-cycle merge cap was already "
                "reached this cycle; it merges on the next tick.",
            ):
                tool_error = True
            held.append(num)
            continue

        print(f"  #{num}: ALL GATES PASS — merging via gate (--merge)", file=out)
        merge = runner(
            [sys.executable, str(MERGE_PR), str(num), "--merge"],
            capture_output=True, text=True, timeout=180,
        )
        if merge.returncode != 0:
            print(f"  #{num}: gated merge FAILED: {(merge.stdout + merge.stderr).strip()[:220]}", file=out)
            tool_error = True
            held.append(num)
            continue
        merged_count += 1
        merged_book[str(num)] = {"head": head, "merged_at": _now_iso()}
        print(f"  #{num}: squash-merge issued at head {head}", file=out)
        ok = post_change_log(
            f"attestation-janitor: merged PR #{num} through the merge gate",
            f"Auto-merge by bin/attestation_janitor.py at {_now_iso()}. "
            f"PR #{num} at head {head} passed all three gates of bin/merge_pr.py "
            "(open+ready, CI green, tester attestation postdating head) and was "
            "squash-merged via the gate itself. Action: merged.",
        )
        if not ok:
            print(f"  #{num}: WARNING — change-log post failed (merge itself succeeded)", file=out)
            tool_error = True

    state.setdefault("runs", []).append({
        "at": _now_iso(), "apply": apply, "open": len(prs),
        "merged": merged_count, "held": held,
    })
    state["runs"] = state["runs"][-50:]
    save_state(state_path, state)
    print(f"janitor: cycle complete — merged {merged_count}, held {len(held)}", file=out)
    return 1 if tool_error else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Attestation auto-merge janitor (see module docstring).")
    ap.add_argument("--apply", action="store_true", help="perform gated merges (default: check-only)")
    ap.add_argument("--pr", type=int, default=None, help="restrict the cycle to one PR number")
    ap.add_argument("--cap", type=int, default=3, help="max merges per cycle (default 3)")
    ap.add_argument("--base-url", default=DEFAULT_NEBULA_BASE, help="nebula-srv base URL for attestation prefilter")
    ap.add_argument("--state", default=DEFAULT_STATE, help="state file path")
    ap.add_argument("--author", default=os.environ.get("JANITOR_AUTHOR"), help="PR author filter (default: gh api user)")
    args = ap.parse_args(argv)

    author = args.author
    if not author:
        try:
            author = gh_json("api", "user").get("login")
        except (RuntimeError, ValueError, AttributeError):
            print("janitor: cannot resolve author (gh api user failed); set --author or JANITOR_AUTHOR", file=sys.stderr)
            return 1

    return run_cycle(
        apply=args.apply, cap=max(1, args.cap), author=author,
        base_url=args.base_url, state_path=Path(args.state), only_pr=args.pr,
    )


if __name__ == "__main__":
    sys.exit(main())
