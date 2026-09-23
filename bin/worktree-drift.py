#!/usr/bin/env python3
"""worktree-drift.py — fleet drift report for nexus worktrees.

One-glance answer to: which worktrees hold uncommitted changes, or commits
not yet on origin/main? Born from the 2026-09-23 sweep that found 15
worktrees with unlanded work, 12 of them on branches that had never been
pushed (unpushed + unlanded = one `git worktree remove` from permanent loss).

Classifications:
  broken     worktree dir missing or git cannot read it
  diverged   has local commits AND main has moved (needs rebase, not ff)
  dirty      tracked modifications/unmerged index entries in the worktree
  ahead      has local commits, main has not moved past its base
  detached   detached HEAD (often a throwaway; surfaced so it is not a surprise)
  stale      clean, strictly behind origin/main (safe fast-forward, not at risk)
  current    clean and at origin/main

Flags:
  UNPUSHED        branch has no origin/<branch> ref — no remote backup
  squash-landed   the branch TIP's tree exists in origin/main's history —
                  content is merged even though commits are not (tree-hash test)
  landed          EVERY tree the branch ever visited exists in origin/main's
                  history — the branch contains no content main lacks
  untracked=N     untracked files present (counted separately from tracked dirt)

Deterministic merged-detection (tree hashes, no heuristics):

  Git object IDs are content hashes. `git log --format=%T` enumerates the
  exact tree (full-content snapshot) of every commit. Therefore:

    tip-in-main    = HEAD's tree OID ∈ {tree OIDs in origin/main history}
    commits_merged = every tree OID on origin/main..HEAD ∈ that same set

  Both are exact answers to "is this content in main?", regardless of how
  it got there — normal merge, squash merge, rebase, cherry-pick. If a
  branch's tree equals some main commit's tree, the entire content state
  exists in main history; this does not depend on commit ancestry.

  `--cleanup-candidates` lists worktrees that are clean AND commits_merged
  (or simply at origin/main). Deleting those cannot lose work. The tool
  NEVER deletes anything itself — it only names candidates for a human.

Usage:
  bin/worktree-drift.py                       # table, attention items first
  bin/worktree-drift.py --attention           # only worktrees needing attention
  bin/worktree-drift.py --fetch               # refresh origin refs first (network)
  bin/worktree-drift.py --json                # machine-readable
  bin/worktree-drift.py --cleanup-candidates  # provably safe to remove
  bin/worktree-drift.py --repo /some/repo     # inspect another repo (tests)

Exit codes: 0 = everything current, 1 = attention items exist, 2 = git failure.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STATE_RANK = {"broken": 0, "diverged": 1, "dirty": 2, "ahead": 3, "detached": 4, "stale": 5, "current": 6}
ATTENTION_STATES = {"broken", "diverged", "dirty", "ahead", "detached"}


def git(*args, cwd=REPO_ROOT, check=True):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def list_worktrees(repo):
    """Parse `git worktree list --porcelain` into dicts."""
    out = git("worktree", "list", "--porcelain", cwd=repo).stdout
    worktrees, entry = [], {}
    for line in out.splitlines():
        if not line:
            if entry:
                worktrees.append(entry)
            entry = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            entry["path"] = Path(value)
        elif key == "HEAD":
            entry["head"] = value
        elif key == "branch":
            entry["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            entry["detached"] = True
    if entry:
        worktrees.append(entry)
    return worktrees


def merged_analysis(g, ahead):
    """Exact content-merged tests via tree OIDs. g() runs git in the worktree."""
    if ahead == 0:
        return {"landed_exact": True, "commits_merged": True}
    # git log lists newest first; the first tree is the branch tip's.
    branch_trees = g("log", "--format=%T", "origin/main..HEAD").stdout.split()
    main_trees = set(g("log", "--format=%T", "origin/main").stdout.split())
    in_main = [t in main_trees for t in branch_trees]
    return {
        "landed_exact": bool(in_main) and in_main[0],   # tip content in main
        "commits_merged": bool(branch_trees) and all(in_main),  # every state in main
    }


def inspect(wt, repo):
    """Classify one worktree. Returns an info dict; git failures mark it broken."""
    info = {
        "worktree": wt["path"].name,
        "path": str(wt["path"]),
        "branch": wt.get("branch"),
        "state": None,
        "ahead": 0,
        "behind": 0,
        "dirty_tracked": 0,
        "dirty_untracked": 0,
        "landed_exact": None,
        "commits_merged": None,
        "flags": [],
    }
    if wt.get("detached"):
        info["state"] = "detached"
        return info
    path = wt["path"]

    def g(*args, check=True):
        return git(*args, cwd=path, check=check)

    # Behind/ahead vs origin/main
    try:
        ahead = g("rev-list", "--count", "origin/main..HEAD").stdout.strip()
        behind = g("rev-list", "--count", "HEAD..origin/main").stdout.strip()
        info["ahead"], info["behind"] = int(ahead), int(behind)
    except RuntimeError:
        info["state"] = "broken"
        info["flags"].append("unreadable")
        return info

    # Dirty split: tracked changes vs untracked files
    status = g("status", "--porcelain").stdout.splitlines()
    info["dirty_tracked"] = sum(1 for line in status if not line.startswith("??"))
    info["dirty_untracked"] = sum(1 for line in status if line.startswith("??"))
    if info["dirty_untracked"]:
        info["flags"].append(f"untracked={info['dirty_untracked']}")

    # Unpushed? (no remote ref for this branch — no backup, no PR possible)
    if info["branch"] and g("rev-parse", "-q", "--verify",
                            f"refs/remotes/origin/{info['branch']}",
                            check=False).returncode != 0:
        info["flags"].append("UNPUSHED")

    # Deterministic merged-detection (tree hashes) — always computed so
    # ahead==0 worktrees (current/stale) still report commits_merged=True.
    try:
        merged = merged_analysis(g, info["ahead"])
    except RuntimeError:
        info["state"] = "broken"
        info["flags"].append("unreadable")
        return info
    info.update(merged)
    if info["ahead"] > 0:
        if merged["landed_exact"] and not merged["commits_merged"]:
            info["flags"].append("squash-landed")
        if merged["commits_merged"]:
            info["flags"].append("landed")

    if info["ahead"] > 0 and info["behind"] > 0:
        info["state"] = "diverged"
    elif info["dirty_tracked"] > 0:
        info["state"] = "dirty"
    elif info["ahead"] > 0:
        info["state"] = "ahead"
    elif info["behind"] > 0:
        info["state"] = "stale"
    else:
        info["state"] = "current"
    return info


def broken_stub(wt, flag):
    return {
        "worktree": wt["path"].name,
        "path": str(wt["path"]),
        "branch": wt.get("branch"),
        "state": "broken",
        "ahead": 0,
        "behind": 0,
        "dirty_tracked": 0,
        "dirty_untracked": 0,
        "landed_exact": None,
        "commits_merged": None,
        "flags": [flag],
    }


def collect(repo):
    """Return (results_all, main_tip). A report must never die on one bad worktree."""
    worktrees = list_worktrees(repo)
    results = []
    for wt in worktrees:
        if not wt["path"].exists():
            results.append(broken_stub(wt, "missing"))
            continue
        try:
            results.append(inspect(wt, repo))
        except Exception as e:
            results.append(broken_stub(wt, f"error: {e}"))
    results.sort(key=lambda r: (STATE_RANK.get(r["state"], 9), r["worktree"]))
    main_tip = git("log", "-1", "--format=%h %ad %s", "--date=short",
                   "origin/main", cwd=repo).stdout.strip()
    return results, main_tip


def at_risk_count(records):
    """Unpushed branches carrying work that exists nowhere else."""
    return sum(1 for r in records
               if "UNPUSHED" in r["flags"] and (r["ahead"] > 0 or r["dirty_tracked"] > 0))


def cleanup_candidates(records):
    """Worktrees whose removal provably cannot lose work.

    Safe iff: clean (no tracked dirt) AND the branch's content is fully in
    main (commits_merged). Detached/checked-out states are handled by the
    caller — this tool never removes anything; it only names candidates.
    """
    return [r for r in records
            if r["dirty_tracked"] == 0
            and r["state"] in ("current", "ahead", "stale", "diverged")
            and r["commits_merged"] is True]


def print_table(results, results_all, attention, main_tip):
    print(f"origin/main: {main_tip}")
    print(f"worktrees: {len(results_all)} total | attention: {len(attention)} | "
          + " ".join(f"{s}={sum(1 for r in results_all if r['state'] == s)}" for s in STATE_RANK))
    print()

    def fmt(r):
        branch = r["branch"] or "(detached)"
        return (r["worktree"], branch, r["state"], str(r["ahead"]),
                str(r["behind"]), f"{r['dirty_tracked']}/{r['dirty_untracked']}",
                " ".join(r["flags"]))

    rows = [fmt(r) for r in results]
    header = ("WORKTREE", "BRANCH", "STATE", "AHD", "BHD", "DIRTY(T/U)", "FLAGS")
    widths = [max(len(h), max((len(row[i]) for row in rows), default=0))
              for i, h in enumerate(header)]
    if rows:
        print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    else:
        print("(nothing to show)")

    if attention:
        at_risk = at_risk_count(attention)
        print(f"\nATTENTION: {len(attention)} worktree(s) need triage "
              f"({', '.join(sorted({r['state'] for r in attention}))}).")
        if at_risk:
            print(f"DATA-LOSS RISK: {at_risk} of them sit on UNPUSHED branches with work "
                  f"that exists nowhere else — disposition before any worktree removal.")
    else:
        print("\nAll worktrees clean and current.")


def print_cleanup(candidates, results_all):
    print(f"CLEANUP CANDIDATES: {len(candidates)} of {len(results_all)} worktrees "
          f"are provably safe to remove (clean + every branch tree already in "
          f"origin/main history). This tool never deletes — humans decide.\n")
    for r in candidates:
        if r["commits_merged"] and not r["landed_exact"]:
            note = "every tree in main"
        elif r["landed_exact"] and not r["commits_merged"]:
            note = "tip tree in main (squash ghost)"
        else:
            note = "at a historical main commit"
        print(f"  {r['worktree']:40s} {r['branch'] or '(detached)':40s} {note}")
    unsafe = [r for r in results_all if r not in candidates and r["state"] != "current"]
    if unsafe:
        print(f"\nNOT candidates ({len(unsafe)}): dirty, detached, or content missing "
              f"from main — triage before any removal.")


def main():
    ap = argparse.ArgumentParser(description="Nexus worktree drift report")
    ap.add_argument("--repo", default=str(REPO_ROOT),
                    help="repo to inspect (default: the nexus checkout this script lives in)")
    ap.add_argument("--fetch", action="store_true",
                    help="git fetch --prune origin first (accurate, touches network)")
    ap.add_argument("--attention", action="store_true",
                    help="show only worktrees needing attention")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--cleanup-candidates", action="store_true",
                    help="list worktrees provably safe to remove (never removes anything)")
    args = ap.parse_args()
    repo = str(Path(args.repo).resolve())

    try:
        if args.fetch:
            git("fetch", "--prune", "origin", cwd=repo)
        results_all, main_tip = collect(repo)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    attention = [r for r in results_all if r["state"] in ATTENTION_STATES]
    results = attention if args.attention else results_all

    if args.json:
        print(json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "repo": repo,
            "origin_main": main_tip,
            "counts": {s: sum(1 for r in results_all if r["state"] == s)
                       for s in STATE_RANK},
            "attention_count": len(attention),
            "unpushed_at_risk": at_risk_count(results_all),
            "cleanup_candidates": [r["worktree"] for r in cleanup_candidates(results_all)],
            "worktrees": results,
        }, indent=2))
    elif args.cleanup_candidates:
        print_cleanup(cleanup_candidates(results_all), results_all)
    else:
        print_table(results, results_all, attention, main_tip)

    return 1 if attention else 0


if __name__ == "__main__":
    sys.exit(main())
