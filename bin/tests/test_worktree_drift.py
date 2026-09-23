"""Hermetic tests for bin/worktree-drift.py.

Builds a synthetic fleet in tmp_path: a bare 'origin', a seed clone for
pushing, and a fleet clone with child worktrees exercising every
classification (current / stale / dirty / ahead / diverged-squash-landed /
diverged-unmerged / detached / broken / pushed-vs-unpushed). The real
script runs via subprocess with --repo pointed at the synthetic fleet.

The squash-landed case is constructed exactly: the branch makes two commits
(WIP tree T1, final tree T2); the "maintainer" then lands ONLY the final
content on main as one squash commit whose tree equals T2. Result: branch
tip tree ∈ main history (landed_exact) but the WIP tree is not
(¬commits_merged) — the same situation as every ghost worktree on titanium.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "worktree-drift.py"


def run(*args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{args} failed in {cwd}:\n{r.stderr}")
    return r


def git(*args, cwd, check=True):
    return run("git", *args, cwd=cwd, check=check)


def commit_file(repo, path, content, msg):
    f = Path(repo) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    git("add", path, cwd=repo)
    git("commit", "-m", msg, cwd=repo)


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    root = tmp_path_factory.mktemp("fleet")
    origin = root / "origin.git"
    seed = root / "seed"
    repo = root / "fleet"

    git("init", "--bare", "origin.git", cwd=root)
    git("clone", str(origin), "seed", cwd=root)
    git("config", "user.email", "test@example.com", cwd=seed)
    git("config", "user.name", "Test", cwd=seed)
    git("config", "commit.gpgsign", "false", cwd=seed)

    # c1 on main, pushed
    commit_file(seed, "base.txt", "one\n", "c1 seed")
    git("branch", "-M", "main", cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)  # clone default
    git("clone", str(origin), "fleet", cwd=root)
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)

    # --- stale: branch at c1, then main moves to c2 ---
    git("branch", "stale-b", cwd=repo)
    git("worktree", "add", "../wt-stale", "stale-b", cwd=repo)
    commit_file(seed, "second.txt", "two\n", "c2 second")
    git("push", "origin", "main", cwd=seed)
    git("fetch", "origin", cwd=repo)
    git("merge", "--ff-only", "origin/main", cwd=repo)  # fleet tracks up; wt-stale stays at c1

    # --- dirty: branch at c1, uncommitted tracked edit ---
    git("branch", "dirty-b", "c1..", cwd=repo) if False else git("branch", "dirty-b", "stale-b", cwd=repo)
    git("worktree", "add", "../wt-dirty", "dirty-b", cwd=repo)
    (root / "wt-dirty" / "base.txt").write_text("one\ndirty\n")

    # --- diverged + squash-landed: WIP tree not in main, tip tree is ---
    git("worktree", "add", "-b", "sq-b", "../wt-sq", "origin/main", cwd=repo)
    commit_file(root / "wt-sq", "feature.txt", "alpha\n", "sq WIP")
    commit_file(root / "wt-sq", "feature.txt", "alpha\nbeta\n", "sq final")
    # maintainer squash: same final content, one commit on main
    commit_file(seed, "feature.txt", "alpha\nbeta\n", "sq land (squash of sq-b)")
    git("push", "origin", "main", cwd=seed)
    git("fetch", "origin", cwd=repo)

    # --- ahead: branch from the FINAL main tip + one commit, PUSHED ---
    git("worktree", "add", "-b", "ahead-b", "../wt-ahead", "origin/main", cwd=repo)
    commit_file(root / "wt-ahead", "ahead.txt", "ahead\n", "ahead work")
    git("push", "origin", "ahead-b", cwd=repo)

    # --- diverged + unmerged: unique content main lacks ---
    git("worktree", "add", "-b", "un-b", "../wt-un", "c1..", cwd=repo) if False else \
        git("worktree", "add", "-b", "un-b", "../wt-un", "stale-b", cwd=repo)
    commit_file(root / "wt-un", "unique.txt", "only here\n", "unmerged work")

    # --- detached + broken ---
    git("worktree", "add", "--detach", "../wt-det", "HEAD", cwd=repo)
    git("worktree", "add", "-b", "doomed-b", "../wt-broken", "HEAD", cwd=repo)
    import shutil
    shutil.rmtree(root / "wt-broken")

    # --- current: created at the final tip, clean, tracks origin/main ---
    git("worktree", "add", "-b", "cur-b", "../wt-cur", "origin/main", cwd=repo)

    return {"root": root, "repo": repo}


def run_script(*extra, fleet):
    # exit 1 is meaningful (attention items exist) — callers assert on it.
    # --repo must ALWAYS be passed: without it the script scans its own repo.
    return run(sys.executable, str(SCRIPT), "--repo", str(fleet["repo"]),
               *extra, cwd=fleet["repo"], check=False)


def load_json(*extra, fleet):
    r = run_script("--json", *extra, fleet=fleet)
    return json.loads(r.stdout)


def test_states_are_classified(fleet):
    data = load_json(fleet=fleet)
    ws = {w["worktree"]: w for w in data["worktrees"]}
    assert ws["fleet"]["state"] == "stale"      # main moved after its ff
    assert ws["wt-cur"]["state"] == "current"   # created at the final tip
    assert ws["wt-stale"]["state"] == "stale"
    assert ws["wt-dirty"]["state"] == "dirty"
    assert ws["wt-ahead"]["state"] == "ahead"
    assert ws["wt-sq"]["state"] == "diverged"
    assert ws["wt-un"]["state"] == "diverged"
    assert ws["wt-det"]["state"] == "detached"
    assert ws["wt-broken"]["state"] == "broken"


def test_unpushed_flag(fleet):
    data = load_json(fleet=fleet)
    ws = {w["worktree"]: w for w in data["worktrees"]}
    assert "UNPUSHED" in ws["wt-un"]["flags"]
    assert "UNPUSHED" not in ws["wt-ahead"]["flags"]  # pushed branch
    assert "UNPUSHED" not in ws["fleet"]["flags"]     # main tracks origin/main


def test_deterministic_merged_detection(fleet):
    """Tree-hash tests: exact construction, exact answers."""
    data = load_json(fleet=fleet)
    ws = {w["worktree"]: w for w in data["worktrees"]}

    # squash case: tip content in main, WIP tree not -> squash-landed, not 'landed'
    sq = ws["wt-sq"]
    assert sq["landed_exact"] is True
    assert sq["commits_merged"] is False
    assert "squash-landed" in sq["flags"]
    assert "landed" not in sq["flags"]

    # unmerged case: main lacks the work entirely
    un = ws["wt-un"]
    assert un["landed_exact"] is False
    assert un["commits_merged"] is False
    assert "landed" not in un["flags"]

    # current worktree: trivially merged
    cur = ws["fleet"]
    assert cur["commits_merged"] is True


def test_cleanup_candidates_are_conservative(fleet):
    data = load_json(fleet=fleet)
    cands = set(data["cleanup_candidates"])
    # safe: content provably in main, clean
    assert "wt-stale" in cands
    # unsafe: work main does not have, or not classifiable as clean
    assert "wt-sq" not in cands        # squash-landed but WIP tree missing
    assert "wt-un" not in cands        # unmerged content
    assert "wt-dirty" not in cands     # dirty
    assert "wt-det" not in cands       # detached: conservative
    assert "wt-broken" not in cands    # broken


def test_table_and_exit_codes(fleet):
    full = run_script(fleet=fleet)
    assert full.returncode == 1  # attention items exist
    assert "ATTENTION" in full.stdout
    assert "DATA-LOSS RISK" in full.stdout

    att = run_script("--attention", fleet=fleet)
    assert "wt-un" in att.stdout
    assert "wt-stale" not in att.stdout  # stale is not attention

    cl = run_script("--cleanup-candidates", fleet=fleet)
    assert "CLEANUP CANDIDATES" in cl.stdout
    assert "wt-stale" in cl.stdout
    assert "never deletes" in cl.stdout
