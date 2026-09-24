#!/usr/bin/env python3
"""Hermetic tests for bin/attestation_janitor.py.

No gh, no HTTP, no DB: every external call goes through an injected runner
callable or a patched module function; state lives in a tmp file. Dual-runnable:
pytest-compatible functions plus `python3 bin/tests/test_attestation_janitor.py`
(bin/tests/test_merge_pr.py convention).

Pins the safety rails from the janitor docstring:
  - check-only default (no merge/promotion without --apply)
  - draft promotion ONLY when the sole failing gate is the draft gate
  - BYPASS in a gate report refuses the merge
  - per-cycle cap bounds merges
  - attestation prefilter: definitive-empty skips; unknown evaluates (fail-safe)
  - state file makes merges idempotent across cycles
  - change-log post fires on every merge
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE.parent / "attestation_janitor.py"
_spec = importlib.util.spec_from_file_location("attestation_janitor", MOD_PATH)
janitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(janitor)


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def gate_report(passes=None, fails=None):
    """Render a merge_pr.py-shaped report. passes/fails are gate-name strings."""
    lines = ["merge gate for PR #X:"]
    for name in passes or []:
        lines.append(f"  [PASS] {name}")
    for name in fails or []:
        lines.append(f"  [FAIL] {name}")
    lines.append("  => " + ("ALL GATES PASS" if not fails else "GATE FAILURE -- merge refused"))
    if not fails:
        lines.append("check-only mode: no action taken (use --merge to squash-merge)")
    return "\n".join(lines) + "\n"


PASSING = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "ci green: all checks completed successfully",
        "tester attestation: record postdates head",
    ]
)
DRAFT_FAIL = gate_report(
    passes=["ci green: all checks completed successfully", "tester attestation: record postdates head"],
    fails=["pr open & ready: state=OPEN draft=True mergeable=MERGEABLE head=abc"],
)
ATTEST_FAIL = gate_report(
    passes=["pr open & ready: state=OPEN draft=False head=abc", "ci green: all checks completed successfully"],
    fails=["tester attestation: no canonical attestation row"],
)
BYPASS_REPORT = PASSING.replace("ci green: all checks", "ci green: (BYPASS via env) all checks")

READY_OK = {"returncode": 0, "stdout": "✓ marked ready", "stderr": ""}
MERGE_OK = {"returncode": 0, "stdout": "squash-merge of PR #X issued", "stderr": ""}


def make_runner(plan):
    """plan: ordered list of (substring, FakeResult|dict); entries consumed in order."""
    calls = []
    remaining = list(plan)

    def runner(cmd, **kwargs):
        cmdstr = " ".join(str(c) for c in cmd)
        calls.append(cmdstr)
        for i, (pat, res) in enumerate(remaining):
            if pat in cmdstr:
                remaining.pop(i)
                return FakeResult(**res) if isinstance(res, dict) else res
        raise AssertionError(f"unexpected command: {cmdstr}")

    runner.calls = calls
    return runner


def run_cycle(**overrides):
    buf = io.StringIO()
    tmp = tempfile.mkdtemp()
    _sentinel = object()
    pre_val = overrides.pop("_prefilter", _sentinel)
    kwargs = dict(
        apply=False, cap=3, author="engineer-account",
        base_url="http://localhost:3101",
        state_path=Path(tmp) / "state.json",
        runner=None, out=buf,
    )
    kwargs.update(overrides)
    state_path = kwargs["state_path"]
    saved = (janitor.attestation_prefilter, janitor.post_change_log)
    pre_calls, log_calls = [], []
    # Default: every PR looks attested (True) so gate-focused tests reach the
    # gate; _prefilter=False/None inject the definitive-empty/unknown cases.
    janitor.attestation_prefilter = lambda n, b: (
        pre_calls.append(n) or (True if pre_val is _sentinel else pre_val)
    )
    janitor.post_change_log = lambda t, b: (log_calls.append(t) or True)
    try:
        rc = janitor.run_cycle(**kwargs)
    finally:
        janitor.attestation_prefilter, janitor.post_change_log = saved
    return rc, buf.getvalue(), json.loads(Path(state_path).read_text()), pre_calls, log_calls


def _cycle_with_runner(plan, **kw):
    runner = make_runner(plan)
    rc, out, state, pre, logs = run_cycle(runner=runner, **kw)
    return rc, out, state, runner.calls, pre, logs


# ── discovery ────────────────────────────────────────────────────────────────

DISCOVERY = {"returncode": 0, "stdout": json.dumps([
    {"number": 601, "isDraft": False, "headRefOid": "a" * 40, "title": "B"},
    {"number": 600, "isDraft": False, "headRefOid": "b" * 40, "title": "A"},
]), "stderr": ""}


def test_discovery_oldest_first_and_only_open_authored():
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        [("pr list", DISCOVERY),
         ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
         ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""})])
    assert rc == 0
    assert pre == [600, 601], "PRs evaluated oldest-first"


def test_discovery_failure_is_tool_error():
    runner = make_runner([("pr list", {"returncode": 1, "stdout": "", "stderr": "boom"})])
    buf = io.StringIO()
    tmp = tempfile.mkdtemp()
    rc = janitor.run_cycle(apply=False, cap=3, author="x", base_url="http://x",
                           state_path=Path(tmp) / "s.json", runner=runner, out=buf)
    assert rc == 1 and "discovery failed" in buf.getvalue()


# ── prefilter ────────────────────────────────────────────────────────────────

def test_prefilter_definitive_empty_skips_gate():
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        [("pr list", DISCOVERY)], _prefilter=False)
    assert not any("merge_pr.py" in c for c in calls), "gate must not run on definitive empty"
    assert "no attestation rows" in out


def test_prefilter_unknown_evaluates_gate_failsafe():
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        [("pr list", DISCOVERY), ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""}),
         ("merge_pr.py 601", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})],
        _prefilter=None)
    assert "evaluating via gate" in out
    assert "gate refuses" in out


# ── check-only vs apply ─────────────────────────────────────────────────────

def test_check_only_default_never_merges_or_promotes():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan)
    assert rc == 0
    assert not any("--merge" in c for c in calls), "check-only must not merge"
    assert not any("pr ready" in c for c in calls), "check-only must not promote"
    assert out.count("check-only, no action") == 2
    assert logs == [] and state["merged"] == {}


def test_apply_merges_and_posts_change_log():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 600 --merge", MERGE_OK),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 601 --merge", MERGE_OK)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True)
    assert rc == 0
    assert out.count("squash-merge issued") == 2
    assert len(logs) == 2 and "merged PR #600" in logs[0]
    assert state["merged"]["600"]["head"] == "b" * 12
    assert any("merge_pr.py 600 --merge" in c for c in calls), "merge goes through the gate"


# ── draft promotion ─────────────────────────────────────────────────────────

def test_draft_promoted_only_when_sole_failure_is_draft_gate():
    draft_pr = {"number": 600, "isDraft": True, "headRefOid": "c" * 40, "title": "D"}
    discovery = {"returncode": 0, "stdout": json.dumps([draft_pr]), "stderr": ""}
    plan = [("pr list", discovery),
            ("merge_pr.py 600", {"returncode": 1, "stdout": DRAFT_FAIL, "stderr": ""}),
            ("pr ready", READY_OK),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 600 --merge", MERGE_OK)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True)
    assert rc == 0
    assert any("pr ready" in c for c in calls), "draft promoted exactly once"
    assert out.count("pr ready") >= 1 and "squash-merge issued" in out
    assert "600" in state["merged"]


def test_draft_not_promoted_when_substantive_gate_fails():
    draft_pr = {"number": 600, "isDraft": True, "headRefOid": "c" * 40, "title": "D"}
    discovery = {"returncode": 0, "stdout": json.dumps([draft_pr]), "stderr": ""}
    plan = [("pr list", discovery),
            ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True)
    assert not any("pr ready" in c for c in calls), "must not promote past a substantive failure"
    assert not any("--merge" in c for c in calls)
    assert "gate refuses" in out


def test_non_draft_gate_failure_reports_and_holds():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""}),
            ("merge_pr.py 601", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True)
    assert rc == 0, "gate refusals are expected outcomes, not tool errors"
    assert out.count("gate refuses") == 2
    assert state["runs"][-1]["held"] == [600, 601] and logs == []


# ── bypass guard ────────────────────────────────────────────────────────────

def test_bypass_report_refuses_merge():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": BYPASS_REPORT, "stderr": ""}),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 601 --merge", MERGE_OK)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True)
    assert not any("merge_pr.py 600 --merge" in c for c in calls)
    assert "BYPASS" in out and "refusing" in out
    assert "601" in state["merged"], "clean PR unaffected"


# ── cap ──────────────────────────────────────────────────────────────────────

def test_cap_bounds_merges_per_cycle():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 600 --merge", MERGE_OK),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, cap=1)
    assert out.count("squash-merge issued") == 1
    assert "cap 1 reached" in out and state["runs"][-1]["held"] == [601]


# ── state / idempotence ─────────────────────────────────────────────────────

def test_state_makes_merges_idempotent_across_cycles():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 600 --merge", MERGE_OK),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 601 --merge", MERGE_OK)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, state_path=state_path)
    assert state["merged"].keys() == {"600", "601"}
    # second cycle over the same world: both skipped, no gate, no merge
    runner2 = make_runner([("pr list", DISCOVERY)])
    rc2, out2, state2, pre2, logs2 = run_cycle(
        runner=runner2, apply=True, state_path=state_path,
        _prefilter=False)
    assert "already merged by a previous cycle" in out2
    assert not any("merge_pr.py" in c for c in runner2.calls)
    assert logs2 == [] and state2["merged"].keys() == {"600", "601"}


def test_only_pr_restriction():
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        [("pr list", DISCOVERY), ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""})],
        only_pr=601)
    assert pre == [601]
    assert not any("merge_pr.py 600" in c for c in calls)


def test_corrupt_state_file_is_not_fatal():
    tmp = tempfile.mkdtemp()
    p = Path(tmp) / "state.json"
    p.write_text("{not json")
    runner = make_runner([("pr list", DISCOVERY)])
    buf = io.StringIO()
    saved = janitor.attestation_prefilter
    janitor.attestation_prefilter = lambda n, b: False
    try:
        rc = janitor.run_cycle(apply=False, cap=3, author="x", base_url="http://x",
                               state_path=p, runner=runner, out=buf)
    finally:
        janitor.attestation_prefilter = saved
    assert rc == 0 and "2 open PR(s)" in buf.getvalue()


# ── dual-runnable runner ─────────────────────────────────────────────────────

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
