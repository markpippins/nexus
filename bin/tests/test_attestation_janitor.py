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
  - attestation prefilter: indexed rows -> attested; empty index requires an
    empty tester-mentions probe before a definitive skip (Decision 8 fix A);
    any probe doubt evaluates via the gate (fail-safe)
  - state file makes merges idempotent across cycles
  - change-log post fires on every merge
  - empty-rollup repair: fires ONLY for attested PRs whose sole failing gate
    is the empty-CI-rollup marker; head-unchanged verified first; cooldown +
    lifetime attempt cap; --apply only; never merges through a repair
  - dispatch fallback: after the attempt cap, gate workflows are dispatched
    against the head branch — once per PR, --apply only, partial failures
    retried on a later tick, cooldown still precedes it
  - stale-checkrun repair (CIR-5): a CI_PENDING refusal where the run already
    completed but its check-run never finalized gets `gh run rerun` — probe
    verified, head-unchanged first, cooldown 30m + 3 lifetime attempts per
    (PR, head), --apply only, never merges; genuinely running runs and
    nothing-stale probes stay on the silent CI_PENDING path
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
CLOSE_OK = {"returncode": 0, "stdout": "", "stderr": ""}
REOPEN_OK = {"returncode": 0, "stdout": "", "stderr": ""}

# Empty-rollup CI failure: the fail-closed marker from merge_pr.py when the
# PR's check-rollup is empty (no pull_request run fired against this base).
EMPTY_CI = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "tester attestation: record postdates head",
    ],
    fails=["ci green: no CI checks reported (fail closed)"],
)
REAL_CI_FAIL = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "tester attestation: record postdates head",
    ],
    fails=["ci green: 2 of 49 checks failed: build, wr-conf"],
)


def _view(head: str):
    return {"returncode": 0, "stdout": json.dumps({"headRefOid": head}), "stderr": ""}


HEAD_600 = "b" * 40  # DISCOVERY's headRefOid for PR 600
BRANCH_600 = "engineer/feature-x"
OK_CMD = {"returncode": 0, "stdout": "✓ queued", "stderr": ""}


def _branch_view(name: str):
    return {"returncode": 0, "stdout": json.dumps({"headRefName": name}), "stderr": ""}


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


# ── prefilter mentions-probe (Decision 8 fix A, defect 5c798810) ────────────

def _prefilter_with_fetch(get_map):
    """Run the real attestation_prefilter with a canned http_get.

    get_map maps URL substring -> response; an unmapped URL raises.
    Returns (result, probed_urls)."""
    probed = []

    def fetch(url):
        probed.append(url)
        for needle, payload in get_map.items():
            if needle in url:
                return payload
        raise RuntimeError("unexpected url " + url)

    return janitor.attestation_prefilter(600, "http://neb", http_get=fetch), probed


def test_prefilter_empty_index_with_mentions_is_unknown():
    """Empty indexed lookup + tester records mentioning the PR must NOT be a
    definitive skip — the index cannot see legacy-shape attestations, so the
    gate must run and emit ATT_SHAPE_UNSEEN (Decision 8 fix A)."""
    result, probed = _prefilter_with_fetch({
        "/api/attestations?pr=600": {"items": [], "total": 0, "pr": 600},
        "/api/agent-records?role=tester&tag=pr:600": {
            "items": [{"id": "x"}], "total": 1},
    })
    assert result is None, "mentions present -> unknown (gate evaluates)"
    assert any("/api/agent-records" in u for u in probed), "mentions probe ran"


def test_prefilter_empty_index_without_mentions_is_definitive():
    """Empty indexed lookup AND empty mentions probe is the only shape that
    still proves unattested."""
    result, _ = _prefilter_with_fetch({
        "/api/attestations?pr=600": {"items": [], "total": 0, "pr": 600},
        "/api/agent-records?role=tester&tag=pr:600": {
            "items": [], "total": 0},
    })
    assert result is False


def test_prefilter_mention_probe_failure_is_unknown():
    """A probe error (endpoint down, bad JSON) is never a definitive skip."""
    result, probed = _prefilter_with_fetch({
        "/api/attestations?pr=600": {"items": [], "total": 0, "pr": 600},
    })
    assert result is None
    assert any("/api/agent-records" in u for u in probed)


def test_prefilter_indexed_rows_short_circuit_without_probe():
    """Rows in the indexed lookup -> attested, no mentions probe spent."""
    result, probed = _prefilter_with_fetch({
        "/api/attestations?pr=600": {
            "items": [{"id": "att1"}], "total": 1, "pr": 600},
    })
    assert result is True
    assert not any("/api/agent-records" in u for u in probed)


def test_cycle_skips_only_when_index_and_mentions_both_empty():
    """End-to-end: a PR whose index is empty but who has tester mentions must
    reach the gate (which refuses with ATT_SHAPE_UNSEEN), not be skipped."""
    plan = [
        ("pr list", DISCOVERY),
        ("merge_pr.py 600", {"returncode": 1,
                             "stdout": "merge gate for PR #600:\n"
                                       "  [FAIL] tester attestation (ATT_SHAPE_UNSEEN): tester records "
                                       "mention the PR but none carries an attestation marker\n"
                                       "  => GATE FAILURE -- merge refused\n",
                             "stderr": ""}),
        ("merge_pr.py 601", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""}),
    ]
    runner = make_runner(plan)
    calls = []
    buf = io.StringIO()
    tmp = tempfile.mkdtemp()
    saved = (janitor.attestation_prefilter, janitor.post_change_log)

    def prefilter(n, b, http_get=None):
        calls.append(n)
        return False if n == 601 else None  # 601 truly empty; 600 unknown

    janitor.attestation_prefilter = prefilter
    janitor.post_change_log = lambda t, b: True
    try:
        rc = janitor.run_cycle(apply=False, cap=3, author="engineer-account",
                               base_url="http://localhost:3101",
                               state_path=Path(tmp) / "state.json",
                               runner=runner, out=buf)
    finally:
        janitor.attestation_prefilter, janitor.post_change_log = saved
    out = buf.getvalue()
    assert rc == 0
    assert any("merge_pr.py 600" in c for c in runner.calls), "PR 600 (unknown prefilter) must reach the gate"
    assert not any("merge_pr.py 601" in c for c in runner.calls), "PR 601 (definitive empty) must be skipped"
    assert "no attestation rows and no tester mentions" in out
    assert "gate refuses" in out and "ATT_SHAPE_UNSEEN" in out


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
    assert logs == [] and state["merged"] == {}, "passing check-only posts nothing"


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
    assert any("promoted draft PR #600" in t for t in logs), "promotion is change-logged"
    assert any("merged PR #600" in t for t in logs), "merge is change-logged"


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
    assert state["runs"][-1]["held"] == [600, 601]
    # both PRs look attested (harness default prefilter=True) -> anomaly logged
    assert len(logs) == 2 and all("ANOMALY" in t for t in logs)


def test_anomaly_logged_when_attested_pr_refused():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, _prefilter=True, only_pr=600)
    assert any("ANOMALY" in t and "#600" in t for t in logs)


def test_no_anomaly_log_when_unattested_pr_refused():
    # prefilter unknown (endpoint down) -> gate evaluates; refusal of an
    # unattested PR is routine, stays off the forum (anomaly fires only
    # when the prefilter PROVED attestation, pre is True).
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, _prefilter=None, only_pr=600)
    assert "gate refuses" in out and logs == [], "routine unattested refusals stay off the forum"


def test_check_only_refusal_of_attested_pr_still_surfaces_anomaly():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": ATTEST_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=False, _prefilter=True, only_pr=600)
    assert not any("--merge" in c for c in calls), "check-only never merges"
    assert any("ANOMALY" in t for t in logs), "anomalies surface even in check-only"


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
    assert any("BYPASS in gate report" in t and "#600" in t for t in logs), "bypass refusal is change-logged"


# ── cap ──────────────────────────────────────────────────────────────────────

def test_cap_bounds_merges_per_cycle():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 0, "stdout": PASSING, "stderr": ""}),
            ("merge_pr.py 600 --merge", MERGE_OK),
            ("merge_pr.py 601", {"returncode": 0, "stdout": PASSING, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, cap=1)
    assert out.count("squash-merge issued") == 1
    assert "cap 1 reached" in out and state["runs"][-1]["held"] == [601]
    assert any("held by per-cycle cap" in t and "#601" in t for t in logs), "cap-hold is change-logged"


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


# ── empty-rollup repair (close/reopen) ───────────────────────────────────

def test_repair_fires_for_empty_rollup_sole_ci_fail():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr close 600", CLOSE_OK),
            ("pr reopen 600", REOPEN_OK)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, only_pr=600)
    assert any("pr close 600" in c for c in calls) and any("pr reopen 600" in c for c in calls)
    assert not any("--merge" in c for c in calls), "repair never merges"
    assert "repair fired" in out and "attempt 1/3" in out
    assert state["repairs"]["600"]["attempts"] == 1
    assert state["runs"][-1]["held"] == [600]
    assert any("empty-CI-rollup repair" in t and "#600" in t for t in logs)


def test_repair_never_fires_for_real_ci_failure():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": REAL_CI_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, only_pr=600)
    assert not any("pr close" in c or "pr reopen" in c for c in calls), "genuine CI failures are never repaired"
    assert "gate refuses" in out
    assert any("ANOMALY" in t for t in logs), "attested refusal still surfaces"


def test_repair_check_only_prints_without_mutating():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600))]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=False, only_pr=600)
    assert not any("pr close" in c for c in calls), "check-only must not close"
    assert "would fire under --apply" in out
    assert logs == [] and "repairs" not in state, "check-only records no repair attempts"


def test_repair_aborts_when_head_moved():
    moved = "d" * 40
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(moved))]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, only_pr=600)
    assert not any("pr close" in c for c in calls), "moved head aborts before mutation"
    assert "head moved" in out
    assert any("repair-blocked" in t and "head moved" in t for t in logs)
    assert rc == 1, "head drift under an active attestation is a tool error"


def test_repair_cooldown_waits_silently():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({
        "repairs": {"600": {"attempts": 1, "last_attempt": janitor.time.time() - 60}},
    }))
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600))]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert not any("pr close" in c for c in calls), "cooldown blocks mutation"
    assert "cooldown" in out and logs == []
    assert state["repairs"]["600"]["attempts"] == 1, "cooldown does not consume an attempt"


def test_repair_attempt_cap_alerts_once():
    # Exhaution now ESCALATES to the dispatch fallback (second rung) instead
    # of blocking: the alert still fires exactly once, then the fallback
    # fires and the PR waits for CI.
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({"repairs": {"600": {"attempts": 3}}}))
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr view 600", _branch_view(BRANCH_600)),
            ("workflow run wf-lint.yml", OK_CMD),
            ("workflow run sdk-drift-guard.yml", OK_CMD),
            ("workflow run seed-guard.yml", OK_CMD),
            ("workflow run apidocs.yml", OK_CMD)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert not any("pr close" in c for c in calls), "cap blocks close/reopen"
    assert "exhausted" in out and "escalating" in out
    assert any("repairs exhausted" in t for t in logs), "exhaustion surfaces once"
    assert any("dispatch fallback fired" in t for t in logs), "fallback fired in the same tick"
    assert state["repairs"]["600"].get("dispatch_attempted") is True
    # second cycle: still held, but no duplicate forum alert and no re-dispatch
    runner2 = make_runner([("pr list", DISCOVERY),
                           ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
                           ("pr view 600", _view(HEAD_600))])
    rc2, out2, state2, pre2, logs2 = run_cycle(
        runner=runner2, apply=True, only_pr=600, state_path=state_path)
    assert logs2 == [], "exhaustion alert fires once, not every tick"
    assert "waiting for CI" in out2
    assert state2["runs"][-1]["held"] == [600]


def test_repair_reopen_failure_leaves_loud_trail():
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr close 600", CLOSE_OK),
            ("pr reopen 600", {"returncode": 1, "stdout": "", "stderr": "GraphQL: error"})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(plan, apply=True, only_pr=600)
    assert rc == 1, "a close-without-reopen is a tool error"
    assert "PR IS CLOSED" in out
    assert any("closed but not reopened" in t for t in logs)
    assert "repairs" not in state, "a failed cycle records no attempt"


# ── dispatch fallback (second rung of the repair ladder) ───────────────


def _exhausted_state(state_path: Path) -> None:
    state_path.write_text(json.dumps({"repairs": {"600": {"attempts": 3}}}))


def test_dispatch_fallback_fires_once_on_exhaustion():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    _exhausted_state(state_path)
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr view 600", _branch_view(BRANCH_600)),
            ("workflow run wf-lint.yml", OK_CMD),
            ("workflow run sdk-drift-guard.yml", OK_CMD),
            ("workflow run seed-guard.yml", OK_CMD),
            ("workflow run apidocs.yml", OK_CMD)]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert rc == 0
    assert sum(1 for c in calls if "workflow run" in c) == len(janitor.DISPATCH_WORKFLOWS)
    assert all(f"gh workflow run {w} --ref {BRANCH_600}" in calls for w in janitor.DISPATCH_WORKFLOWS), \
        "every gate workflow is dispatched against the head branch"
    assert "dispatch fallback fired" in out
    assert state["repairs"]["600"]["dispatch_attempted"] is True
    assert any("dispatch fallback fired for PR #600" in t for t in logs)
    # second tick: no re-dispatch — waiting for CI
    plan2 = [("pr list", DISCOVERY),
             ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
             ("pr view 600", _view(HEAD_600))]
    runner2 = make_runner(plan2)
    rc2, out2, state2, pre2, logs2 = run_cycle(
        runner=runner2, apply=True, only_pr=600, state_path=state_path)
    assert not any("workflow run" in c for c in runner2.calls), "fallback fires once per PR"
    assert "waiting for CI" in out2 and logs2 == []
    assert rc2 == 0

def test_dispatch_fallback_check_only_does_not_fire():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    _exhausted_state(state_path)
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600))]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=False, only_pr=600, state_path=state_path)
    assert not any("workflow run" in c for c in calls)
    assert "would fire under --apply" in out


def test_dispatch_fallback_partial_failure_retries_next_tick():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    _exhausted_state(state_path)
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr view 600", _branch_view(BRANCH_600)),
            ("workflow run wf-lint.yml", OK_CMD),
            ("workflow run sdk-drift-guard.yml", {"returncode": 1, "stdout": "", "stderr": "rate limited"})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert rc == 1, "a partial dispatch is a tool error"
    assert "FAILED on sdk-drift-guard.yml" in out
    assert state["repairs"]["600"].get("dispatch_attempted") is None, \
        "partial failure leaves the guard flag unset for retry"
    assert all("dispatch fallback fired for PR #600" not in t for t in logs), \
        "no 'fired' entry when the dispatch did not complete"
    assert any("repairs exhausted" in t for t in logs), "exhaustion alert still logged"


def test_dispatch_fallback_branch_lookup_failure_is_tool_error():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    _exhausted_state(state_path)
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600)),
            ("pr view 600", {"returncode": 1, "stdout": "", "stderr": "boom"})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert rc == 1 and "branch lookup" in out
    assert not any("workflow run" in c for c in calls)


def test_dispatch_fallback_never_fires_before_exhaustion():
    # attempts < max: the cooldown path returns before the ladder's second rung
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({"repairs": {"600": {"attempts": 1, "last_attempt": janitor.time.time()}}}))
    plan = [("pr list", DISCOVERY),
            ("merge_pr.py 600", {"returncode": 1, "stdout": EMPTY_CI, "stderr": ""}),
            ("pr view 600", _view(HEAD_600))]
    rc, out, state, calls, pre, logs = _cycle_with_runner(
        plan, apply=True, only_pr=600, state_path=state_path)
    assert not any("workflow run" in c for c in calls), "cooldown precedes dispatch"
    assert "cooldown" in out


# ── structured codes + (kind, PR, codes, head) dedup (spec 86017db0) ─────

CONFLICT_FAIL = gate_report(
    passes=["ci green: all checks completed successfully",
            "tester attestation: record postdates head"],
    fails=["pr open & ready (MERGE_CONFLICT): state=OPEN draft=False mergeable=CONFLICTING head=abc"],
)
STALE_FAIL = gate_report(
    passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
            "ci green: all checks completed successfully"],
    fails=["tester attestation (ATT_STALE_HEAD): newest attestation predates head commit"],
)
TRANSIENT_FAIL = gate_report(
    passes=["ci green: all checks completed successfully",
            "tester attestation: record postdates head"],
    fails=["pr open & ready (MERGE_UNKNOWN): state=OPEN draft=False mergeable=UNKNOWN head=abc"],
)
DISCOVERY_600 = {"returncode": 0, "stdout": json.dumps([
    {"number": 600, "isDraft": False, "headRefOid": "b" * 40, "title": "A"},
]), "stderr": ""}
DISCOVERY_600_NEWHEAD = {"returncode": 0, "stdout": json.dumps([
    {"number": 600, "isDraft": False, "headRefOid": "c" * 40, "title": "A"},
]), "stderr": ""}
DISCOVERY_601 = {"returncode": 0, "stdout": json.dumps([
    {"number": 601, "isDraft": False, "headRefOid": "d" * 40, "title": "B"},
]), "stderr": ""}


def _refusal_cycle(report, state_path, discovery=DISCOVERY_600, rc=1):
    """One single-PR cycle; returns (rc, out, state, logs)."""
    rc_, out, state, _calls, _pre, logs = _cycle_with_runner(
        [("pr list", discovery),
         ("merge_pr.py 600", {"returncode": rc, "stdout": report, "stderr": ""})],
        state_path=state_path)
    return rc_, out, state, logs


def _refusal_cycle_pr(report, state_path, num=601, rc=1):
    """Like _refusal_cycle but for an arbitrary PR number (its own discovery
    row + head), for tests that must exercise cross-PR behavior."""
    discovery = {"returncode": 0, "stdout": json.dumps([
        {"number": num, "isDraft": False, "headRefOid": "e" * 40, "title": f"PR{num}"},
    ]), "stderr": ""}
    rc_, out, state, _calls, _pre, logs = _cycle_with_runner(
        [("pr list", discovery),
         (f"merge_pr.py {num}", {"returncode": rc, "stdout": report, "stderr": ""})],
        state_path=state_path)
    return rc_, out, state, logs


def test_refusal_dedup_same_head_posts_once():
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    _, _, _, logs1 = _refusal_cycle(ATTEST_FAIL, state_path)
    rc, out, _, logs2 = _refusal_cycle(ATTEST_FAIL, state_path)
    assert len(logs1) == 1 and "ANOMALY" in logs1[0]
    assert logs2 == [], "same (codes, head) refusal must be suppressed"
    assert "suppressed" in out


def test_refusal_reposts_when_head_changes():
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    _, _, _, logs1 = _refusal_cycle(ATTEST_FAIL, state_path, discovery=DISCOVERY_600)
    _, _, _, logs2 = _refusal_cycle(ATTEST_FAIL, state_path, discovery=DISCOVERY_600_NEWHEAD)
    assert len(logs1) == 1 and len(logs2) == 1, "new head re-alerts"


def test_refusal_reposts_when_codes_change():
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    _, _, _, logs1 = _refusal_cycle(ATTEST_FAIL, state_path)
    _, _, _, logs2 = _refusal_cycle(STALE_FAIL, state_path)
    assert len(logs1) == 1 and len(logs2) == 1, "new failure codes re-alert"
    assert "RE-ATTESTATION REQUESTED" in logs2[0] and "ATT_STALE_HEAD" in logs2[0]


def test_merge_conflict_is_not_an_anomaly():
    rc, out, state, logs = _refusal_cycle(CONFLICT_FAIL, Path(tempfile.mkdtemp()) / "s.json")
    assert len(logs) == 1
    assert "BLOCKED (not an anomaly)" in logs[0] and "MERGE_CONFLICT" in logs[0]
    assert "ANOMALY" not in logs[0]


def test_transient_refusal_posts_nothing():
    rc, out, state, logs = _refusal_cycle(TRANSIENT_FAIL, Path(tempfile.mkdtemp()) / "s.json")
    assert logs == [], "MERGE_UNKNOWN is transient: retry silently"
    assert "transient refusal" in out


def test_ci_lookup_failed_transient_posts_nothing():
    """Decision 15 item D: a transient CI-run lookup failure surfaces as
    ATT_CI_LOOKUP_FAILED, which is TRANSIENT — the janitor retries silently
    and never nags the tester to re-attest on a GitHub blip."""
    lookup_fail = gate_report(
        passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
                "ci green: all checks completed successfully"],
        fails=["tester attestation (ATT_CI_LOOKUP_FAILED): newest attestation cites run(s) "
               "36000000001: CI run 36000000001 lookup failed: gh: HTTP 429 "
               "(transient — gate cannot reach GitHub; retry next cycle)"],
    )
    rc, out, state, logs = _refusal_cycle(lookup_fail, Path(tempfile.mkdtemp()) / "s.json")
    assert logs == [], "ATT_CI_LOOKUP_FAILED is transient: retry silently, no posts"
    assert "transient refusal" in out


def test_shape_unseen_routes_to_adjudication():
    rc, out, state, logs = _refusal_cycle(
        gate_report(
            passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
                    "ci green: all checks completed successfully"],
            fails=["tester attestation (ATT_SHAPE_UNSEEN): tester records mention the PR"]),
        Path(tempfile.mkdtemp()) / "s.json")
    assert len(logs) == 1 and "ADJUDICATION REQUESTED" in logs[0]


def test_bypass_refusal_dedup():
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    _, _, _, logs1 = _refusal_cycle(BYPASS_REPORT, state_path, rc=0)
    rc, out, _, logs2 = _refusal_cycle(BYPASS_REPORT, state_path, rc=0)
    assert len(logs1) == 1 and "BYPASS" in logs1[0]
    assert logs2 == [], "BYPASS refusals dedup on (kind, PR, codes, head)"


def test_dedup_state_prunes_after_14_days():
    import time as _time
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    _refusal_cycle(ATTEST_FAIL, state_path)
    state = json.loads(state_path.read_text())
    assert state.get("anomaly_dedup")
    for entry in state["anomaly_dedup"].values():
        entry["last_s"] = _time.time() - 15 * 86400
    state_path.write_text(json.dumps(state))
    _refusal_cycle(ATTEST_FAIL, state_path)
    state = json.loads(state_path.read_text())
    entries = state.get("anomaly_dedup", {})
    assert all(v.get("count") == 1 for v in entries.values()), "stale key pruned => re-alert"


# ── ATT_TIMESTAMP_MISSING: cross-PR server-defect routing ────────────────
# One record-endpoint serialization regression (the epoch-0 family) blocks
# many PRs at once. The finding must (a) route as SERVER DEFECT with
# operator-action wording — NOT the re-attestation wording the staleness
# codes get, NOT the generic ANOMALY — and (b) dedup ACROSS PRs and heads,
# keyed on the code set alone, so one episode posts once. Staleness
# (ATT_STALE_HEAD / ATT_NO_CI_EVIDENCE) keeps its own per-(PR, codes, head)
# routing: each of those needs ITS PR's tester to act.

TS_MISSING_SOLE = gate_report(
    passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
            "ci green: all checks completed successfully"],
    fails=["tester attestation (ATT_TIMESTAMP_MISSING): newest attestation row "
           "has no usable createdAt; cannot evaluate freshness"],
)
TS_MISSING_MIXED = gate_report(
    passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc"],
    fails=["ci green (CI_PENDING): 1 check(s) not completed: ['CIR-5']",
           "tester attestation (ATT_TIMESTAMP_MISSING): newest attestation row "
           "has no usable createdAt"],
)
STALE_OTHER = STALE_FAIL
NO_CI_EVIDENCE_FAIL = gate_report(
    passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
            "ci green: all checks completed successfully"],
    fails=["tester attestation (ATT_NO_CI_EVIDENCE): newest attestation cites no CI run references"],
)


def test_server_defect_routes_with_operator_action_wording():
    rc, out, state, logs = _refusal_cycle(TS_MISSING_SOLE, Path(tempfile.mkdtemp()) / "s.json")
    assert len(logs) == 1
    assert "SERVER DEFECT" in logs[0] and "ATT_TIMESTAMP_MISSING" in logs[0]
    assert "ANOMALY" not in logs[0], "server defect is not the generic anomaly"
    assert "RE-ATTESTATION REQUESTED" not in logs[0], "must not nag the tester"


def test_server_defect_body_pins_re_attest_will_not_clear():
    rc, out, state, logs = _refusal_cycle(TS_MISSING_SOLE, Path(tempfile.mkdtemp()) / "s.json")
    assert len(logs) == 1
    # The finding must reach the OPERATOR (DBA), not the tester. The body is
    # not passed through the harness shim, so assert on the module directly.
    title, body = janitor._refusal_post_text(
        600, "a" * 40, ["ATT_TIMESTAMP_MISSING"], "refusal detail")
    assert "operator action" in body and "re-attesting will not clear it" in body
    assert "SERVER side" in body


def test_server_defect_mixed_codes_still_route_as_server_defect():
    """ATT_TIMESTAMP_MISSING present alongside a transient code (CI_PENDING)
    still routes as the cross-PR server defect, not as a silent transient."""
    rc, out, state, logs = _refusal_cycle(TS_MISSING_MIXED, Path(tempfile.mkdtemp()) / "s.json")
    assert len(logs) == 1
    assert "SERVER DEFECT" in logs[0] and "CI_PENDING+ATT_TIMESTAMP_MISSING" in logs[0]


def test_server_defect_dedup_is_cross_pr_and_cross_head():
    """Refusals sharing the server-defect code set — same PR at a new head, or
    a DIFFERENT PR entirely — all fold into ONE episode = ONE change-log post."""
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    rc1, out1, state1, logs1 = _refusal_cycle(TS_MISSING_SOLE, state_path)  # PR 600 @ head b
    rc2, out2, state2, logs2 = _refusal_cycle(
        TS_MISSING_SOLE, state_path, discovery=DISCOVERY_600_NEWHEAD)       # PR 600 @ head c
    assert len(logs1) == 1 and logs2 == [], "same PR at a new head folds into the episode"
    assert "same refusal as" in out2, "head change does not fork a server-defect episode"
    key = "SERVER-DEFECT:::ATT_TIMESTAMP_MISSING"
    assert state2["anomaly_dedup"][key]["count"] == 2
    assert state2["anomaly_dedup"][key]["prs"] == [600]
    # a DIFFERENT PR with the same defect folds in with the cross-PR message
    rc3, out3, state3, logs3 = _refusal_cycle_pr(TS_MISSING_SOLE, state_path, num=601)
    assert logs3 == []
    assert "folded into the existing finding" in out3
    assert state3["anomaly_dedup"][key]["count"] == 3
    assert state3["anomaly_dedup"][key]["prs"] == [600, 601]
    assert sum("SERVER DEFECT" in t for t in logs1 + logs2 + logs3) == 1, "one post per episode"


def test_server_defect_does_not_shadow_staleness_routing():
    """The re-attest-required staleness codes keep their own routing: a
    different PR refusing with ATT_STALE_HEAD / ATT_NO_CI_EVIDENCE during the
    same episode still posts ITS OWN per-(PR, codes, head) finding."""
    state_path = Path(tempfile.mkdtemp()) / "s.json"
    rc1, out1, state1, logs1 = _refusal_cycle(TS_MISSING_SOLE, state_path)
    rc2, out2, state2, logs2 = _refusal_cycle(STALE_OTHER, state_path)
    rc3, out3, state3, logs3 = _refusal_cycle(NO_CI_EVIDENCE_FAIL, state_path)
    assert len(logs1) == 1 and "SERVER DEFECT" in logs1[0]
    assert len(logs2) == 1 and "RE-ATTESTATION REQUESTED" in logs2[0] and "ATT_STALE_HEAD" in logs2[0]
    assert len(logs3) == 1 and "RE-ATTESTATION REQUESTED" in logs3[0] and "ATT_NO_CI_EVIDENCE" in logs3[0]
    keys = set(state3["anomaly_dedup"].keys())
    assert any(k.startswith("SERVER-DEFECT::") for k in keys), "server-defect key present"
    assert any(":600:" in k and k.startswith("REFUSAL") for k in keys), "per-PR REFUSAL keys untouched"


def test_server_defect_key_helper():
    assert janitor._server_defect_key(["ATT_TIMESTAMP_MISSING"]) == "SERVER-DEFECT:::ATT_TIMESTAMP_MISSING"
    assert janitor._server_defect_key(
        ["CI_PENDING", "ATT_TIMESTAMP_MISSING"]) == "SERVER-DEFECT:::ATT_TIMESTAMP_MISSING+CI_PENDING"
    assert janitor._server_defect_key(["ATT_STALE_HEAD"]) is None
    assert janitor._server_defect_key([]) is None


# ── stale-checkrun repair (CIR-5): completed-but-unfinalized check-runs ────

PENDING_CI = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "tester attestation: record postdates head",
    ],
    fails=["ci green (CI_PENDING): 1 check(s) not completed: ['wr-conf-032']"],
)
PENDING_PLUS_ATT_FAIL = gate_report(
    passes=["pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc"],
    fails=["ci green (CI_PENDING): 1 check(s) not completed: ['wr-conf-032']",
           "tester attestation (ATT_NO_CI_EVIDENCE): cites no CI run references"],
)
RUN_LIST_STALE = {"returncode": 0, "stdout": json.dumps([
    {"databaseId": 36680804542, "status": "completed", "conclusion": None, "headSha": "b" * 40},
]), "stderr": ""}
RUN_LIST_RUNNING = {"returncode": 0, "stdout": json.dumps([
    {"databaseId": 36680804542, "status": "in_progress", "conclusion": None, "headSha": "b" * 40},
]), "stderr": ""}
RUN_LIST_TERMINAL = {"returncode": 0, "stdout": json.dumps([
    {"databaseId": 36680804542, "status": "completed", "conclusion": "success", "headSha": "b" * 40},
]), "stderr": ""}
RUN_LIST_OTHER_HEAD = {"returncode": 0, "stdout": json.dumps([
    {"databaseId": 36680804542, "status": "completed", "conclusion": None, "headSha": "e" * 40},
]), "stderr": ""}
RERUN_OK = {"returncode": 0, "stdout": "", "stderr": ""}


def _stale_cycle(plan, **kw):
    """_cycle_with_runner with the CI_PENDING report pre-wired; pass a plan
    of EXTRA commands after the gate."""
    full = [("pr list", DISCOVERY_600),
            ("merge_pr.py 600", {"returncode": 1, "stdout": PENDING_CI, "stderr": ""})]
    full.extend(plan)
    return _cycle_with_runner(full, **kw)


def test_stale_checkrun_probe_finds_completed_unfinalized_run():
    assert janitor.find_stale_checkrun(600, HEAD_600, _probe_runner(RUN_LIST_STALE)) == "36680804542"


def test_stale_checkrun_probe_ignores_genuinely_running_run():
    assert janitor.find_stale_checkrun(600, HEAD_600, _probe_runner(RUN_LIST_RUNNING)) is None


def test_stale_checkrun_probe_ignores_terminal_conclusions():
    assert janitor.find_stale_checkrun(600, HEAD_600, _probe_runner(RUN_LIST_TERMINAL)) is None


def test_stale_checkrun_probe_ignores_other_head_shas():
    assert janitor.find_stale_checkrun(600, HEAD_600, _probe_runner(RUN_LIST_OTHER_HEAD)) is None


def test_stale_checkrun_probe_fail_safe_on_error():
    r = make_runner([("run list", {"returncode": 1, "stdout": "", "stderr": "rate limited"})])
    assert janitor.find_stale_checkrun(600, HEAD_600, r) is None


def test_sole_fail_is_pending_ci_helper():
    assert janitor._sole_fail_is_pending_ci(
        type("P", (), {"stdout": PENDING_CI, "returncode": 1})())
    assert not janitor._sole_fail_is_pending_ci(
        type("P", (), {"stdout": PENDING_PLUS_ATT_FAIL, "returncode": 1})())
    assert not janitor._sole_fail_is_pending_ci(
        type("P", (), {"stdout": EMPTY_CI, "returncode": 1})())


def test_stale_checkrun_repair_fires_rerun():
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_STALE),
        ("run rerun 36680804542", RERUN_OK),
    ], apply=True, only_pr=600)
    assert rc == 0
    assert "run rerun 36680804542" in " ".join(calls)
    assert "stale-checkrun repair fired" in out
    rec = state["stale_checkrun_repairs"][f"600:{HEAD_600[:12]}"]
    assert rec["attempts"] == 1 and rec["last_run_id"] == "36680804542"
    assert any("CIR-5 stale-checkrun repair for PR #600" in t for t in logs)


def test_stale_checkrun_repair_check_only_is_readonly():
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_STALE),
    ], apply=False, only_pr=600)
    assert not any("run rerun" in c for c in calls), "check-only must not mutate"
    assert "would fire under --apply" in out
    assert "stale_checkrun_repairs" not in state, "no ledger for a readonly cycle"


def test_stale_checkrun_nothing_stale_stays_silent_transient():
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_TERMINAL),
    ], apply=True, only_pr=600)
    assert not any("run rerun" in c for c in calls)
    assert "ordinary CI_PENDING" in out
    assert not logs, "no post when the probe finds nothing stale"
    assert "stale_checkrun_repairs" not in state


def test_stale_checkrun_head_moved_aborts_and_alerts_once():
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view("c" * 40)),
    ], apply=True, only_pr=600)
    assert rc == 1 and "head moved" in out
    assert not any("run rerun" in c for c in calls)
    assert any("head moved during stale-checkrun repair" in t for t in logs)
    assert state["stale_checkrun_repairs"][f"600:{HEAD_600[:12]}"].get("head_moved_at")


def test_stale_checkrun_head_moved_no_duplicate_alert():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    all_logs = []
    for _ in range(2):
        rc, out, state, calls, pre, logs = _stale_cycle([
            ("pr view 600", _view("c" * 40)),
        ], apply=True, only_pr=600, state_path=state_path)
        all_logs.extend(logs)
    alerts = [t for t in all_logs if "head moved during stale-checkrun repair" in t]
    assert len(alerts) == 1, "alert once per drift event, not per tick"


def test_stale_checkrun_cap_and_exhaustion_alert_once():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({
        "stale_checkrun_repairs": {f"600:{HEAD_600[:12]}": {"attempts": 3}},
    }))
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_STALE),
    ], apply=True, only_pr=600, state_path=state_path)
    assert not any("run rerun" in c for c in calls), "cap precedes any mutation"
    assert "exhausted" in out
    assert any("stale-checkrun reruns exhausted" in t for t in logs)
    assert state["stale_checkrun_repairs"][f"600:{HEAD_600[:12]}"].get("exhausted_logged")


def test_stale_checkrun_cooldown_waits_silently():
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({
        "stale_checkrun_repairs": {
            f"600:{HEAD_600[:12]}": {"attempts": 1, "last_attempt": janitor.time.time()},
        },
    }))
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_STALE),
    ], apply=True, only_pr=600, state_path=state_path)
    assert not any("run rerun" in c for c in calls)
    assert "cooldown" in out
    assert not logs, "cooldown is silent"


def test_stale_checkrun_rerun_failure_is_tool_error():
    rc, out, state, calls, pre, logs = _stale_cycle([
        ("pr view 600", _view(HEAD_600)),
        ("run list", RUN_LIST_STALE),
        ("run rerun 36680804542", {"returncode": 1, "stdout": "", "stderr": "boom"}),
    ], apply=True, only_pr=600)
    assert rc == 1 and "rerun FAILED" in out
    assert "stale_checkrun_repairs" not in state, "failed attempt is not counted"
    assert not logs, "no change-log entry for a failed rerun"


def test_stale_checkrun_requires_attestation_gate_pass():
    """A CI_PENDING refusal alongside a failing attestation gate is NOT
    repairable — the whole repair branch requires the attestation to pass."""
    full = [("pr list", DISCOVERY_600),
            ("merge_pr.py 600", {"returncode": 1, "stdout": PENDING_PLUS_ATT_FAIL, "stderr": ""})]
    rc, out, state, calls, pre, logs = _cycle_with_runner(full, apply=True, only_pr=600)
    assert not any("run list" in c for c in calls), "no probe when attestation fails"
    assert not any("run rerun" in c for c in calls)


def _probe_runner(result):
    """Minimal runner stubbing just `gh run list` for direct probe tests."""
    calls = []

    def runner(cmd, **kwargs):
        cmdstr = " ".join(str(c) for c in cmd)
        calls.append(cmdstr)
        if "run list" in cmdstr:
            return FakeResult(**result) if isinstance(result, dict) else result
        raise AssertionError(f"unexpected command: {cmdstr}")

    runner.calls = calls
    return runner


# ── dual-runnable runner (keep at EOF: collects every test_ defined above) ──

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
