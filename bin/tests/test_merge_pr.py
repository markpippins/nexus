"""Hermetic tests for merge_pr.py — pure functions only, no I/O."""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "merge_pr.py"
spec = importlib.util.spec_from_file_location("merge_pr", BIN)
merge_pr = importlib.util.module_from_spec(spec)
sys.modules["merge_pr"] = merge_pr
spec.loader.exec_module(merge_pr)


# ── fixtures/helpers ─────────────────────────────────────────────────────

def check(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"name": name, "status": status, "conclusion": conclusion}


NOW_MS = int(time.time() * 1000)


def rec(rec_id="aaaa1111", role="tester", created_ms=None, tags=None,
        title="Tester attestation: PR #487 (30 passed)", content=""):
    return {
        "id": rec_id,
        "role": role,
        "createdAt": NOW_MS - 3600_000 if created_ms is None else created_ms,
        "tags": tags or [],
        "title": title,
        "content": content,
    }


# ── gate 2: checks_report ────────────────────────────────────────────────

def test_checks_empty_rollup_fails_closed():
    ok, detail = merge_pr.checks_report([])
    assert not ok
    assert "fail closed" in detail


def test_checks_all_success_pass():
    ok, detail = merge_pr.checks_report([check("build"), check("sonar")])
    assert ok
    assert "2 checks" in detail


def test_checks_pending_fails():
    ok, detail = merge_pr.checks_report([check("build"), check("lint", status="IN_PROGRESS")])
    assert not ok
    assert "not completed" in detail


def test_checks_failed_conclusion_fails():
    ok, detail = merge_pr.checks_report([check("build"), check("lint", conclusion="FAILURE")])
    assert not ok
    assert "1 failed check" in detail


def test_checks_skipped_and_neutral_do_not_block():
    ok, _ = merge_pr.checks_report([
        check("build"),
        check("optional", conclusion="SKIPPED"),
        check("neutral-thing", conclusion="NEUTRAL"),
    ])
    assert ok


# ── gate 3a: attestation_mentions_pr ─────────────────────────────────────

def test_match_by_pr_tag():
    assert merge_pr.attestation_mentions_pr(rec(tags=["to:tester", "pr:487"]), 487)


def test_match_by_title_number():
    assert merge_pr.attestation_mentions_pr(rec(title="Tester attestation: PR #487 verified"), 487)


def test_match_by_content_number():
    assert merge_pr.attestation_mentions_pr(rec(content="... covering PR #491 rows ..."), 491)


def test_no_word_boundary_false_positive():
    # "#48" must not match PR 487
    assert not merge_pr.attestation_mentions_pr(rec(title="attest #48 things"), 487)


def test_unrelated_pr_not_matched():
    assert not merge_pr.attestation_mentions_pr(rec(tags=["pr:487"]), 491)


# ── gate 3b: evaluate_attestation ────────────────────────────────────────

def test_no_attestation_fails():
    records = [rec(role="engineer", title="to:tester — attest PR #487")]
    ok, detail = merge_pr.evaluate_attestation(records, 487, NOW_MS)
    assert not ok
    assert "no tester attestation" in detail


def test_non_tester_role_does_not_count():
    records = [rec(role="engineer", title="attest PR #487")]
    ok, _ = merge_pr.evaluate_attestation(records, 487, NOW_MS)
    assert not ok


def test_fresh_attestation_passes():
    head_ms = NOW_MS - 7200_000  # head committed 2h ago
    records = [rec(created_ms=NOW_MS - 3600_000)]  # attested 1h ago
    ok, detail = merge_pr.evaluate_attestation(records, 487, head_ms)
    assert ok
    assert "postdates" in detail


def test_attestation_older_than_head_fails():
    head_ms = NOW_MS - 600_000  # head committed 10 min ago
    records = [rec(created_ms=NOW_MS - 3600_000)]  # attested 1h ago
    ok, detail = merge_pr.evaluate_attestation(records, 487, head_ms)
    assert not ok
    assert "predates head commit" in detail


def test_unknown_head_date_fails_closed():
    records = [rec()]
    ok, detail = merge_pr.evaluate_attestation(records, 487, None)
    assert not ok
    assert "unknown" in detail


def test_newest_of_multiple_is_used():
    head_ms = NOW_MS - 7200_000
    records = [
        rec(rec_id="old1", created_ms=NOW_MS - 86_400_000),   # old attestation
        rec(rec_id="new1", created_ms=NOW_MS - 3600_000),     # fresh attestation
    ]
    ok, detail = merge_pr.evaluate_attestation(records, 487, head_ms)
    assert ok
    assert "new1" in detail


# ── parse_iso_to_ms ──────────────────────────────────────────────────────

def test_parse_iso_z():
    assert merge_pr.parse_iso_to_ms("2026-09-23T16:02:10Z") > 1_700_000_000_000


def test_parse_iso_naive_treated_as_utc():
    assert (merge_pr.parse_iso_to_ms("2026-09-23T16:02:10")
            == merge_pr.parse_iso_to_ms("2026-09-23T16:02:10Z"))


# ── fetch_head_commit_date_ms (both gh/GraphQL and REST shapes) ─────────

def test_head_date_graphql_flat_shape():
    def run_json(*_a):
        return {"commits": [{"committedDate": "2026-09-23T15:57:19Z", "oid": "abc"}]}

    assert merge_pr.fetch_head_commit_date_ms(491, run_json) is not None


def test_head_date_rest_nested_shape():
    def run_json(*_a):
        return {"commits": [{"commit": {"committer": {"date": "2026-09-23T15:57:19Z"}}}]}

    assert merge_pr.fetch_head_commit_date_ms(491, run_json) is not None


def test_head_date_missing_fails_to_none():
    def run_json(*_a):
        return {"commits": [{"oid": "abc"}]}

    assert merge_pr.fetch_head_commit_date_ms(491, run_json) is None


# ── full evaluate() with fakes ───────────────────────────────────────────

def make_fakes(pr_overrides=None, rollup=None, records=None, head_date="2026-09-23T10:00:00Z"):
    pr = {
        "number": 487,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "headRefOid": "a8b1dfc600000000000000000000000000000000",
        "headRefName": "feat/x",
        "title": "test(probe): regression tests",
        "statusCheckRollup": rollup if rollup is not None else [check("build")],
    }
    pr.update(pr_overrides or {})
    commits = {"commits": [{"committedDate": head_date, "oid": "abc"}]}

    def run_json(*args):
        if "commits" in args:
            return commits
        return pr

    def http_get(url):
        return {"items": records if records is not None else []}

    return run_json, http_get


def test_evaluate_all_gates_pass():
    run_json, http_get = make_fakes(records=[rec(created_ms=NOW_MS - 3600_000)])
    gates, _pr = merge_pr.evaluate(487, run_json=run_json, http_get=http_get, env={})
    assert [g.name for g in gates] == ["pr open & ready", "ci green", "tester attestation"]
    assert all(g.passed for g in gates)


def test_evaluate_draft_blocks_gate1():
    run_json, http_get = make_fakes(pr_overrides={"isDraft": True})
    gates, _ = merge_pr.evaluate(487, run_json=run_json, http_get=http_get, env={})
    assert not gates[0].passed


def test_evaluate_missing_attestation_blocks_gate3():
    run_json, http_get = make_fakes(records=[])
    gates, _ = merge_pr.evaluate(487, run_json=run_json, http_get=http_get, env={})
    assert not gates[2].passed
    assert not gates[2].bypassed


def test_evaluate_bypass_flips_gate3_only_when_failing():
    run_json, http_get = make_fakes(records=[])
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json, http_get=http_get, env={merge_pr.BYPASS_ENV: "1"}
    )
    gate3 = gates[2]
    assert gate3.passed and gate3.bypassed and "BYPASSED" in gate3.name
    # gates 1 and 2 untouched by the bypass
    assert gates[0].passed and gates[1].passed


def test_evaluate_bypass_does_not_mask_already_passing_gate():
    run_json, http_get = make_fakes(records=[rec(created_ms=NOW_MS - 3600_000)])
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json, http_get=http_get, env={merge_pr.BYPASS_ENV: "1"}
    )
    gate3 = gates[2]
    assert gate3.passed and not gate3.bypassed  # normal PASS, no bypass label


def test_evaluate_attestation_lookup_failure_fails_closed():
    def boom_http_get(url):
        raise RuntimeError("nebula down")

    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(487, run_json=run_json, http_get=boom_http_get, env={})
    gate3 = gates[2]
    assert not gate3.passed
    assert "fail closed" in gate3.detail


def test_evaluate_gh_failure_yields_single_fail_closed_gate():
    """gh unavailable (e.g. outside a repo): clean refusal, no traceback."""
    def boom(*args):
        raise subprocess.CalledProcessError(
            1, ["gh", "pr", "view"], stderr="not a git repository (or any parent)"
        )

    gates, pr = merge_pr.evaluate(491, run_json=boom, http_get=lambda url: {}, env={})
    assert len(gates) == 1
    assert gates[0].name == "github pr lookup"
    assert not gates[0].passed
    assert "gh lookup failed" in gates[0].detail
    assert "fail closed" in gates[0].detail
    assert pr == {}


def test_format_report_marks_and_verdict():
    gates = [
        merge_pr.GateResult("pr open & ready", True, "ok"),
        merge_pr.GateResult("ci green", False, "1 failed check"),
        merge_pr.GateResult("tester attestation (BYPASSED)", True, "bypassed", bypassed=True),
    ]
    report = merge_pr.format_report(487, gates)
    assert "[PASS] pr open & ready" in report
    assert "[FAIL] ci green" in report
    assert "[BYPASS] tester attestation" in report
    assert "GATE FAILURE" in report
