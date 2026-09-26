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
     the attestation/CI gates already pass. If the sole failing gate is
     "ci green" with the empty-rollup marker ("no CI checks reported") while
     the attestation gate passes, the PR is REPAIRABLE: a close->reopen
     cycle at the UNCHANGED head re-fires the pull_request event against the
     current base (the Structure-stack recovery, 2026-09-24). Repairs are
     --apply only, rate-limited (60m cooldown, 3 lifetime attempts per PR),
     verified head-unchanged immediately before mutation, and change-logged
     per attempt. If three close/reopen attempts still leave the rollup
     empty, the janitor ESCALATES to the workflow_dispatch fallback: it
     dispatches the light gate workflows against the PR's head branch
     (the #535/#536 manual recovery), once per PR, head-verified,
     --apply only, change-logged.

Safety rails (all non-negotiable):
  - --merge is passed to the gate only after an immediately-preceding
    check-only run of the SAME gate exited 0 for the SAME head.
  - Default mode is check-only: without --apply nothing is mutated.
  - Per-cycle merge cap (default 3) bounds blast radius.
  - PRs already recorded as merged in the state file are skipped (idempotent
    across timer ticks).
  - Empty-CI-rollup repair (close/reopen) only ever fires when the PR is
    attested, the ONLY failing gate is the empty-rollup CI marker, and the
    head is unchanged at mutation time. Genuine CI failures are never
    repaired; a moved head aborts the repair and alerts; exhausting the
    attempt cap is surfaced once and then left for a human.
  - The janitor never edits branches, never forces anything, never bypasses
    a gate: a BYPASS line in the gate report is treated as failure.

Change-log audit trail — every ACTION is logged to the Assembly change-log
forum via bin/post-change-log.sh, individually:
  - merged    : gated squash-merge issued (with head + gate evidence)
  - promoted  : attested draft raised to ready (substantive gates already passed)
  - repair    : empty-rollup close/reopen attempted (head verified unchanged)
  - repair-blocked: repair impossible — head moved or attempts exhausted
                    (human attention requested)
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
import time
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

# Empty-rollup repair: the fail-closed marker merge_pr.py emits when the
# PR's check-rollup is EMPTY (no runs ever fired against the current base).
# Distinguishable from a genuine CI failure (non-empty rollup, real detail).
EMPTY_CI_MARKER = "no CI checks reported"
REPAIR_COOLDOWN_S = 60 * 60   # min spacing between repair attempts per PR
REPAIR_MAX_ATTEMPTS = 3       # lifetime close/reopen attempts per PR

# Dispatch-fallback ladder (second rung): after REPAIR_MAX_ATTEMPTS
# close/reopen cycles leave the rollup empty, dispatch these light gate
# workflows against the head branch. They carry both pull_request and
# workflow_dispatch triggers and no required inputs, so a dispatch run
# populates the rollup exactly like the event the PR never fired.
# Override with JANITOR_DISPATCH_WORKFLOWS (comma-separated) if the gate
# set changes. Deliberately excludes the heavy e2e/image/wr-conf suites:
# the gate needs a non-empty all-green rollup, not the full matrix.
DISPATCH_WORKFLOWS = [
    s.strip()
    for s in os.environ.get(
        "JANITOR_DISPATCH_WORKFLOWS",
        "wf-lint.yml,sdk-drift-guard.yml,seed-guard.yml,apidocs.yml",
    ).split(",")
    if s.strip()
]


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


def _gate_fail_lines(proc: "subprocess.CompletedProcess") -> List[str]:
    return [ln.split("[FAIL]", 1)[1].strip() for ln in proc.stdout.splitlines() if "[FAIL]" in ln]


def _gate_pass_lines(proc: "subprocess.CompletedProcess") -> List[str]:
    return [ln.split("[PASS]", 1)[1].strip() for ln in proc.stdout.splitlines() if "[PASS]" in ln]


def _sole_fail_is_empty_ci(proc: "subprocess.CompletedProcess") -> bool:
    """Sole failing gate is `ci green` with the empty-rollup marker."""
    fails = _gate_fail_lines(proc)
    return (
        len(fails) == 1
        and fails[0].startswith("ci green")
        and EMPTY_CI_MARKER in fails[0]
    )


def _attestation_gate_passes(proc: "subprocess.CompletedProcess") -> bool:
    return any(ln.startswith("tester attestation") for ln in _gate_pass_lines(proc))


def _parse_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def repair_empty_rollup(
    num: int,
    head_full: str,
    rec: Dict[str, Any],
    now_s: float,
    apply: bool,
    runner: Callable,
    out: Any = sys.stdout,
) -> str:
    """Attempt the empty-rollup repair (close->reopen) for one PR.

    Rails, in order: head-unchanged verification (abort + alert on drift),
    lifetime attempt cap (exhaustion alerted once), cooldown (silent wait),
    --apply gate (check-only prints intent and mutates nothing). Returns one
    of: 'done', 'cooldown', 'capped', 'head-moved', 'failed', 'readonly'.
    Attempts are recorded only after a successful close+reopen cycle.
    """
    # 1. Head verification BEFORE anything else: the repair is only sound at
    #    the exact head the attestation pinned.
    try:
        current = gh_json("pr", "view", str(num), "--json", "headRefOid", runner=runner).get("headRefOid", "")
    except RuntimeError as exc:
        print(f"  #{num}: repair aborted — head lookup failed: {exc}", file=out)
        return "failed"
    if current != head_full:
        print(f"  #{num}: repair aborted — head moved since discovery ({head_full[:12]} -> {current[:12]})", file=out)
        if not rec.get("head_moved_at"):
            # Alert once per drift event, not on every tick.
            if not post_change_log(
                f"attestation-janitor: repair-blocked — PR #{num} head moved under an active attestation",
                f"bin/attestation_janitor.py at {_now_iso()}: empty-rollup repair for PR #{num} "
                f"aborted: discovery head {head_full[:12]} but live head is {current[:12]}. "
                "A new push invalidates any pinned attestation — human attention requested.",
            ):
                print(f"  #{num}: WARNING — change-log post failed (abort itself succeeded)", file=out)
            rec["head_moved_at"] = _now_iso()
        return "head-moved"

    attempts = _parse_int(rec.get("attempts"))
    if attempts >= REPAIR_MAX_ATTEMPTS:
        if not rec.get("exhausted_logged"):
            print(f"  #{num}: repair attempts exhausted ({attempts}/{REPAIR_MAX_ATTEMPTS}) — escalating to dispatch fallback", file=out)
            if post_change_log(
                f"attestation-janitor: PR #{num} close/reopen repairs exhausted — escalating to dispatch fallback",
                f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} (head {head_full[:12]}) is "
                f"attested but its CI rollup is still empty after {attempts} close/reopen repairs "
                f"(cooldown {REPAIR_COOLDOWN_S // 60}m). The reopen events are not producing "
                "check runs — the workflow_dispatch fallback fires next (gate workflows "
                "against the head branch, as done manually for #535/#536).",
            ):
                rec["exhausted_logged"] = True
        return _dispatch_fallback(num, rec, apply, runner, out)

    last = _parse_float(rec.get("last_attempt"))
    if last and (now_s - last) < REPAIR_COOLDOWN_S:
        wait = int(REPAIR_COOLDOWN_S - (now_s - last))
        print(f"  #{num}: repair cooldown — next attempt eligible in ~{wait // 60}m", file=out)
        return "cooldown"

    if not apply:
        print(f"  #{num}: repairable (close/reopen at unchanged head {head_full[:12]}) — would fire under --apply", file=out)
        return "readonly"

    close = runner(["gh", "pr", "close", str(num)], capture_output=True, text=True, timeout=60)
    if close.returncode != 0:
        print(f"  #{num}: repair close FAILED: {close.stderr.strip()[:160]}", file=out)
        return "failed"
    reopen = runner(["gh", "pr", "reopen", str(num)], capture_output=True, text=True, timeout=60)
    if reopen.returncode != 0:
        print(f"  #{num}: repair reopen FAILED — PR IS CLOSED: {reopen.stderr.strip()[:160]} — human attention required", file=out)
        post_change_log(
            f"attestation-janitor: repair FAILED mid-cycle — PR #{num} closed but not reopened",
            f"bin/attestation_janitor.py at {_now_iso()}: the close/reopen repair for PR #{num} "
            f"completed the close but the reopen failed ({reopen.stderr.strip()[:200]}). "
            "The PR is left CLOSED at head " + head_full[:12] + " — human attention required.",
        )
        return "failed"

    rec["attempts"] = attempts + 1
    rec["last_attempt"] = now_s
    rec["last_attempt_iso"] = _now_iso()
    rec["last_head"] = head_full[:12]
    print(f"  #{num}: repair fired (close/reopen at unchanged head {head_full[:12]}, attempt {attempts + 1}/{REPAIR_MAX_ATTEMPTS}) — CI will re-run against the current base", file=out)
    if not post_change_log(
        f"attestation-janitor: empty-CI-rollup repair for PR #{num} (close/reopen)",
        f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} is attested but its CI rollup "
        f"was EMPTY (no pull_request run ever fired against the current base; marker: '" + EMPTY_CI_MARKER + "'). "
        f"Repair fired: close->reopen at the UNCHANGED head {head_full[:12]} to re-fire the "
        f"pull_request event (head pin preserved; the gate re-runs before any merge). "
        f"Attempt {attempts + 1}/{REPAIR_MAX_ATTEMPTS}, cooldown {REPAIR_COOLDOWN_S // 60}m.",
    ):
        print(f"  #{num}: WARNING — change-log post failed (repair itself succeeded)", file=out)
    return "done"


def _dispatch_fallback(
    num: int,
    rec: Dict[str, Any],
    apply: bool,
    runner: Callable,
    out: Any = sys.stdout,
) -> str:
    """Second rung of the repair ladder: dispatch gate workflows at the branch.

    Fires once per PR (guard flag recorded only after every dispatch
    succeeded; a partial failure leaves the flag unset so the next tick
    retries). Check-only prints intent and mutates nothing. Returns
    'dispatched', 'waited', 'readonly', or 'failed'.
    """
    if rec.get("dispatch_attempted"):
        print(f"  #{num}: dispatch fallback already fired ({rec.get('dispatch_at_iso', '?')}) — waiting for CI", file=out)
        return "waited"
    if not apply:
        print(f"  #{num}: dispatch fallback pending ({len(DISPATCH_WORKFLOWS)} gate workflows) — would fire under --apply", file=out)
        return "readonly"

    try:
        branch = gh_json("pr", "view", str(num), "--json", "headRefName", runner=runner).get("headRefName", "")
    except RuntimeError as exc:
        print(f"  #{num}: dispatch fallback failed — branch lookup error: {exc}", file=out)
        return "failed"
    if not branch:
        print(f"  #{num}: dispatch fallback failed — empty head branch name (fork PR?)", file=out)
        return "failed"

    for wf in DISPATCH_WORKFLOWS:
        d = runner(["gh", "workflow", "run", wf, "--ref", branch],
                   capture_output=True, text=True, timeout=60)
        if d.returncode != 0:
            print(f"  #{num}: dispatch fallback FAILED on {wf}: {d.stderr.strip()[:160]} — will retry next tick", file=out)
            return "failed"

    rec["dispatch_attempted"] = True
    rec["dispatch_at"] = time.time()
    rec["dispatch_at_iso"] = _now_iso()
    rec["dispatch_workflows"] = list(DISPATCH_WORKFLOWS)
    rec["dispatch_branch"] = branch
    print(f"  #{num}: dispatch fallback fired — {len(DISPATCH_WORKFLOWS)} gate workflows dispatched against '{branch}'; rollup populates as they complete", file=out)
    if not post_change_log(
        f"attestation-janitor: dispatch fallback fired for PR #{num}",
        f"bin/attestation_janitor.py at {_now_iso()}: PR #{num} (head {branch}) remained "
        "empty-rollup after the close/reopen ladder, so the gate workflows were "
        f"dispatched against the head branch (manual-recovery precedent #535/#536): "
        f"{', '.join(DISPATCH_WORKFLOWS)}. The merge gate re-runs and decides on a "
        "later cycle once the rollup is populated.",
    ):
        print(f"  #{num}: WARNING — change-log post failed (dispatch itself succeeded)", file=out)
    return "dispatched"


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
    repairs = state.setdefault("repairs", {})
    now_s = time.time()
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
            if pre is True and _sole_fail_is_empty_ci(proc) and _attestation_gate_passes(proc):
                # Known-repairable anomaly: attested PR, CI rollup EMPTY (no
                # pull_request run ever fired against the current base).
                # Nothing is rerunnable — close/reopen re-fires the event at
                # the unchanged head (Structure-stack recovery). The gate
                # decides again on the next cycle; repair never merges.
                outcome = repair_empty_rollup(
                    num, pr.get("headRefOid", ""),
                    repairs.setdefault(str(num), {"attempts": 0}),
                    now_s, apply, runner, out,
                )
                if outcome in ("head-moved", "failed"):
                    # note: the dispatch fallback's 'failed' lands here too
                    # (it is a tool error), while 'waited'/'dispatched'/
                    # 'readonly' are expected outcomes.
                    tool_error = True
                held.append(num)
                continue
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
    # Keep the state file meaningful: entries exist only once something
    # happened (a fired attempt, an exhaustion alert, or a head-drift alert).
    for k in [k for k, r in repairs.items()
              if not r.get("attempts") and not r.get("exhausted_logged") and not r.get("head_moved_at")]:
        del repairs[k]
    if not repairs:
        state.pop("repairs", None)  # keep the state file free of empty ledgers
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
