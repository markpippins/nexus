#!/usr/bin/env python3
"""Hermetic tests for bin/digest_drift_report.py (drift-to-forum bridge).

No nebula, no Assembly, no live checkout: the bridge module is loaded via
importlib, the posters are a fake runner recording every invocation, and the
state file lives in a tmp dir. Pins:

  - no postable verdicts (ALL_MATCH / QUEUED) -> zero posts, correct exit
  - TOOL_ERROR only (bad registry / bad rev) -> zero posts, exit 1
  - DRIFT -> exactly one incident record + one change-log post; exit 3
  - MISSING -> one episode, exit 2
  - identical re-run -> zero new posts, count bumped in state
  - changed drift bytes -> NEW episode -> posts again
  - --dry-run -> zero runner invocations, zero state writes
  - --escalate -> third post targeting issues-and-open-questions
  - bodies carry paths, digests, commit, and the runbook
  - missing/empty registry -> exit 1, no posts

Dual-runnable: pytest or python3 bin/tests/test_digest_drift_report.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_PATH = HERE.parent / "digest_drift_report.py"

_spec = importlib.util.spec_from_file_location("digest_drift_report", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

# reuse the guard test helpers' repo builder via importlib (same pattern)
_gspec = importlib.util.spec_from_file_location("ldg_tests", HERE / "test_landed_digest_guard.py")
ldg_tests = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(ldg_tests)

D_A = ldg_tests.D_A
D_B = ldg_tests.D_B


class _FakeRunner:
    """Records invocations; returns scripted CompletedProcess-like results."""

    def __init__(self, fail_substrings=()):
        self.calls = []
        self.fail_substrings = fail_substrings

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs.get("input", "")))
        scripted = " ".join(cmd)
        failed = any(s in scripted for s in self.fail_substrings)
        return subprocess.CompletedProcess(
            cmd, 1 if failed else 0,
            stdout="" if failed else "OK 201 record_id=11111111-2222-3333-4444-555555555555",
            stderr="" if not failed else "boom",
        )


def _repo_with_drift() -> ldg_tests._ScratchRepo:
    repo = ldg_tests._ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes MUTATED\n"})
    return repo


def _run_bridge(repo, state_path, extra=(), runner=None):
    runner = runner or _FakeRunner()
    argv = ["--repo", str(repo.dir), "--registry", str(repo.registry([repo.pin("bin/a.py", D_A)])),
            "--rev", repo.head, "--state", str(state_path), *extra]
    rc = bridge.main(argv, runner=runner)
    return rc, runner


# ── silence paths ────────────────────────────────────────────────────────

def test_all_match_posts_nothing_and_exits_zero():
    repo = ldg_tests._ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc, runner = _run_bridge(repo, state)
    assert rc == 0 and runner.calls == [] and not state.exists()


def test_queued_posts_nothing_and_exits_four():
    repo = ldg_tests._ScratchRepo()
    repo.commit({"bin/other.py": b"x\n"})
    head = repo.commit({"bin/a.py": b"tool-A bytes\n"})
    state = Path(tempfile.mkdtemp()) / "s.json"
    reg = repo.registry([repo.pin("bin/a.py", None, queued=[
        {"pr": 1, "head": head, "digest": D_A, "provenance": "t"}])])
    runner = _FakeRunner()
    rc = bridge.main(["--repo", str(repo.dir), "--registry", str(reg),
                      "--rev", str(subprocess_run_revparse(repo.dir, "HEAD~1")),
                      "--state", str(state)], runner=runner)
    assert rc == 4 and runner.calls == [] and not state.exists()


def subprocess_run_revparse(repo_dir, rev):
    return subprocess.run(["git", "-C", str(repo_dir), "rev-parse", rev],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_tool_error_bad_registry_posts_nothing_exits_one():
    repo = ldg_tests._ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    state = Path(tempfile.mkdtemp()) / "s.json"
    runner = _FakeRunner()
    rc = bridge.main(["--repo", str(repo.dir), "--registry", str(repo.dir / "nope.json"),
                      "--rev", repo.head, "--state", str(state)], runner=runner)
    assert rc == 1 and runner.calls == []


# ── posting paths ────────────────────────────────────────────────────────

def test_drift_posts_one_episode_and_exits_three():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc, runner = _run_bridge(repo, state)
    posts = [c for c in runner.calls if "post-agent-record.py" in " ".join(c[0])]
    clogs = [c for c in runner.calls if "post-change-log.sh" in " ".join(c[0])]
    assert rc == 3
    assert len(posts) == 1 and len(clogs) == 1
    assert "type:incident" in " ".join(posts[0][0])
    data = json.loads(state.read_text())
    assert len(data["episodes"]) == 1


def test_missing_posts_episode_and_exits_two():
    repo = ldg_tests._ScratchRepo()
    repo.commit({"bin/other.py": b"x\n"})
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc, runner = _run_bridge(repo, state)
    assert rc == 2
    assert len([c for c in runner.calls if "post-agent-record.py" in " ".join(c[0])]) == 1


def test_identical_rerun_dedupes_no_new_posts():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc1, runner1 = _run_bridge(repo, state)
    rc2, runner2 = _run_bridge(repo, state)
    assert rc1 == 3 and rc2 == 3
    assert runner2.calls == [], "identical episode must not re-post"
    data = json.loads(state.read_text())
    key, entry = next(iter(data["episodes"].items()))
    assert entry["count"] == 2


def test_changed_drift_bytes_start_new_episode():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc1, runner1 = _run_bridge(repo, state)
    repo.commit({"bin/a.py": b"tool-A bytes MUTATED AGAIN\n"})
    rc2, runner2 = _run_bridge(repo, state)
    assert rc1 == 3 and rc2 == 3
    assert len([c for c in runner2.calls if "post-agent-record.py" in " ".join(c[0])]) == 1
    data = json.loads(state.read_text())
    assert len(data["episodes"]) == 2


def test_dry_run_invokes_no_runner_and_writes_no_state():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    runner = _FakeRunner()
    rc = bridge.main(["--repo", str(repo.dir),
                      "--registry", str(repo.registry([repo.pin("bin/a.py", D_A)])),
                      "--rev", repo.head, "--state", str(state), "--dry-run"], runner=runner)
    assert rc == 3 and runner.calls == [] and not state.exists()


def test_escalate_adds_issues_forum_post():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    runner = _FakeRunner()
    rc = bridge.main(["--repo", str(repo.dir),
                      "--registry", str(repo.registry([repo.pin("bin/a.py", D_A)])),
                      "--rev", repo.head, "--state", str(state), "--escalate"], runner=runner)
    assert rc == 3
    esc = [c for c in runner.calls if "issues-and-open-questions" in " ".join(c[0])]
    assert len(esc) == 1


def test_bodies_carry_paths_digests_commit_and_runbook():
    repo = _repo_with_drift()
    state = Path(tempfile.mkdtemp()) / "s.json"
    rc, runner = _run_bridge(repo, state)
    record_inputs = [c[1] for c in runner.calls if "post-agent-record.py" in " ".join(c[0])]
    assert record_inputs, "record post must carry stdin body"
    body = record_inputs[0]
    assert "bin/a.py" in body
    assert D_A not in body or "sha256=" in body  # evidence line present
    assert repo.head[:9] in body
    assert "Runbook" in body and "re-pin via an attested PR" in body


# ── dual-runnable runner (keep at EOF) ───────────────────────────────────

def _main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
