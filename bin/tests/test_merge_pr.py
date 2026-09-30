"""Hermetic tests for merge_pr.py — pure functions only, no I/O."""

import importlib.util
import subprocess
import sys
import time
from datetime import datetime, timezone
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

# The default attestation body cites CI runs whose run_json fake (make_fakes
# and evidence_run_json below) reports SUCCESS at the PR head — the contract
# under test since the CI-evidence rule: stated test counts alone are the
# engine's self-attestation and fail the gate.
EVIDENCE_TITLE = "Tester attestation: PR #487 — CI run 36000000001 success"
EVIDENCE_CONTENT = (
    "Verified via service-test-gates. CI runs 36000000001, 36000000002: "
    "gate jobs success at head a8b1dfc6.\n"
    "Engine-reported counts (54/54) were NOT relied on."
)


def rec(rec_id="aaaa1111", role="tester", created_ms=None, tags=None,
        title=None, content=None,
        recordType="assessment"):
    """Default shape = the tester's canonical attestation row (assessment +
    type:approval + status:done), matching real records like b47510d6, and
    citing verifiable CI run references (the post-d7989f31 contract)."""
    return {
        "id": rec_id,
        "role": role,
        "createdAt": NOW_MS - 3600_000 if created_ms is None else created_ms,
        "tags": tags if tags is not None else ["type:approval", "status:done", "pr:487"],
        "title": title if title is not None else EVIDENCE_TITLE,
        "content": content if content is not None else EVIDENCE_CONTENT,
        "recordType": recordType,
    }


def evidence_run_json(*args):
    """Fake `gh api repos/{owner}/{repo}/actions/runs/<id>` for the two
    fixture run IDs: both SUCCESS at head a8b1dfc6…."""
    args = tuple(a for a in args if not str(a).startswith("--jq"))
    q = " ".join(str(a) for a in args)
    if "36000000001" in q:
        return {"c": "success", "s": "a8b1dfc600000000000000000000000000000000"}
    if "36000000002" in q:
        return {"c": "success", "s": "a8b1dfc600000000000000000000000000000000"}
    raise RuntimeError(f"gh api lookup failed: {q}")


# ── gate 2: checks_report ────────────────────────────────────────────────

def test_checks_empty_rollup_fails_closed():
    ok, detail, code = merge_pr.checks_report([])
    assert not ok
    assert "fail closed" in detail


def test_checks_all_success_pass():
    ok, detail, code = merge_pr.checks_report([check("build"), check("sonar")])
    assert ok
    assert "2 checks" in detail


def test_checks_pending_fails():
    ok, detail, code = merge_pr.checks_report([check("build"), check("lint", status="IN_PROGRESS")])
    assert not ok
    assert "not completed" in detail


def test_checks_failed_conclusion_fails():
    ok, detail, code = merge_pr.checks_report([check("build"), check("lint", conclusion="FAILURE")])
    assert not ok
    assert "1 failed check" in detail


def test_checks_skipped_and_neutral_do_not_block():
    ok, _, _ = merge_pr.checks_report([
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
    assert not merge_pr.attestation_mentions_pr(rec(title="attest #48 things", tags=[]), 487)


def test_unrelated_pr_not_matched():
    assert not merge_pr.attestation_mentions_pr(rec(tags=["pr:487"]), 491)


# ── gate 3b: evaluate_attestation ────────────────────────────────────────

def test_no_attestation_fails():
    records = [rec(role="engineer", title="to:tester — attest PR #487")]
    ok, detail, code = merge_pr.evaluate_attestation(records, 487, NOW_MS)
    assert not ok
    assert "no tester attestation" in detail


def test_non_tester_role_does_not_count():
    records = [rec(role="engineer", title="attest PR #487")]
    ok, _, _ = merge_pr.evaluate_attestation(records, 487, NOW_MS)
    assert not ok


# ── gate 3a: attestation-shape rule (tester finding 84ca2388) ────────────

def test_intent_record_does_not_satisfy_gate3():
    """Regression for the exact #492 reproduction: a tester INTENT row
    (engineering_log, type:status-update) must not count as an attestation."""
    intent = rec(
        rec_id="b8acd611",
        recordType="engineering_log",
        tags=["to:engineer", "type:status-update", "attestations", "pr:492"],
        title="Tester intent: attest PR #491 seed file and PR #492 merge wrapper",
    )
    ok, detail, code = merge_pr.evaluate_attestation([intent], 492, NOW_MS)
    assert not ok
    assert "attestation marker" in detail
    assert "b8acd611" in detail


def test_rejection_finding_does_not_count():
    finding = rec(
        rec_id="84ca2388",
        recordType="inspection",
        tags=["to:engineer", "type:rejection", "status:open", "pr:492"],
        title="Tester finding: PR #492 attestation gate accepts non-attestation records",
    )
    ok, detail, code = merge_pr.evaluate_attestation([finding], 492, NOW_MS)
    assert not ok
    assert "attestation marker" in detail


def test_canonical_attestation_shape_passes():
    head_ms = NOW_MS - 7200_000
    att = rec(
        rec_id="b47510d6",
        recordType="assessment",
        tags=["to:engineer", "type:approval", "status:done", "attestations", "pr:491"],
    )
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 491, head_ms, run_json=evidence_run_json)
    assert ok
    assert "postdates" in detail
    assert "CI run(s) verified" in detail


def test_legacy_type_attestation_tag_passes():
    """Historical tester rows (75d069f8, bf9776e6, c4be406f) attest via an
    explicit type:attestation tag with varying recordTypes."""
    legacy = rec(
        rec_id="75d069f8",
        recordType="report",
        tags=["to:dba", "type:attestation", "attestations", "pr:487"],
    )
    ok, detail, code = merge_pr.evaluate_attestation(
        [legacy], 487, NOW_MS - 7200_000, run_json=evidence_run_json)
    assert ok
    assert "postdates" in detail


def test_assessment_without_approval_tag_fails():
    almost = rec(tags=["status:done", "pr:487"])
    ok, detail, code = merge_pr.evaluate_attestation([almost], 487, NOW_MS)
    assert not ok
    assert "attestation marker" in detail


def test_is_attestation_record_rejects_missing_recordtype():
    assert not merge_pr.is_attestation_record(
        {"tags": ["type:approval", "status:done"]}
    )
    assert merge_pr.is_attestation_record(
        {"tags": ["type:attestation"], "recordType": "response"}
    )


def test_fresh_attestation_passes():
    head_ms = NOW_MS - 7200_000  # head committed 2h ago
    records = [rec(created_ms=NOW_MS - 3600_000)]  # attested 1h ago
    ok, detail, code = merge_pr.evaluate_attestation(
        records, 487, head_ms, run_json=evidence_run_json)
    assert ok
    assert "postdates" in detail


def test_attestation_older_than_head_fails():
    head_ms = NOW_MS - 600_000  # head committed 10 min ago
    records = [rec(created_ms=NOW_MS - 3600_000)]  # attested 1h ago
    ok, detail, code = merge_pr.evaluate_attestation(records, 487, head_ms)
    assert not ok
    assert "predates head commit" in detail


def test_unknown_head_date_fails_closed():
    records = [rec()]
    ok, detail, code = merge_pr.evaluate_attestation(records, 487, None)
    assert not ok
    assert "unknown" in detail


def test_newest_of_multiple_is_used():
    head_ms = NOW_MS - 7200_000
    records = [
        rec(rec_id="old1", created_ms=NOW_MS - 86_400_000),   # old attestation
        rec(rec_id="new1", created_ms=NOW_MS - 3600_000),     # fresh attestation
    ]
    ok, detail, code = merge_pr.evaluate_attestation(
        records, 487, head_ms, run_json=evidence_run_json)
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
        args = tuple(str(a) for a in args)
        if "commits" in args:
            return commits
        if any("actions/runs/" in a for a in args):
            return evidence_run_json(*args)
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


# ── gate 3c: indexed /api/attestations lookup + scan fallback ────────────

def _dispatching_http(attestations, mentions, scan):
    """Fake nebula: dispatch by URL path so the indexed endpoint, the
    pr-tag mention lookup, and the legacy scan serve different data."""
    def http_get(url):
        if "/api/attestations?" in url:
            return {"items": attestations, "total": len(attestations)}
        if "role=tester" in url and "tag=pr:" in url:
            return {"items": mentions}
        if "/api/agent-records" in url:
            return {"items": scan}
        raise RuntimeError(f"unexpected url {url}")
    return http_get


def test_indexed_lookup_is_exact_and_preferred_over_scan():
    """The scan path contains only a poison intent row; the indexed endpoint
    returns the canonical attestation. Gate must pass via the indexed row —
    proving exact lookup, not a newest-N scan over mixed data."""
    poison = rec(
        rec_id="b8acd611", recordType="engineering_log",
        tags=["to:engineer", "type:status-update", "pr:487"],
        title="Tester intent: attest PR #487",
    )
    canonical = rec(created_ms=NOW_MS - 3600_000)
    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json,
        http_get=_dispatching_http([canonical], [poison], [poison]), env={})
    gate3 = gates[2]
    assert gate3.passed
    assert "indexed lookup" in gate3.detail


def test_fallback_to_scan_when_attestations_endpoint_unavailable():
    """Old server (endpoint raises): fall back to the bounded scan —
    fail-safe, never fail-open."""
    def http_get(url):
        if "/api/attestations?" in url:
            raise RuntimeError("404: unknown route")
        return {"items": [rec(created_ms=NOW_MS - 3600_000)]}

    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(487, run_json=run_json, http_get=http_get, env={})
    gate3 = gates[2]
    assert gate3.passed
    assert "scan fallback" in gate3.detail


def test_fallback_scan_still_fails_closed_on_empty():
    def http_get(url):
        if "/api/attestations?" in url:
            raise RuntimeError("404: unknown route")
        return {"items": []}

    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(487, run_json=run_json, http_get=http_get, env={})
    assert not gates[2].passed


def test_indexed_empty_refusal_names_markerless_mentions():
    """Indexed empty result + marker-less tester mentions: refusal explains
    the gap by name (the 84ca2388 remediation UX, now server-assisted)."""
    poison = rec(
        rec_id="b8acd611", recordType="engineering_log",
        tags=["to:engineer", "type:status-update", "pr:492"],
        title="Tester intent: attest PR #492",
    )
    finding = rec(
        rec_id="84ca2388", recordType="inspection",
        tags=["to:engineer", "type:rejection", "pr:492"],
        title="Tester finding: gate accepts non-attestation records",
    )
    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        492, run_json=run_json,
        http_get=_dispatching_http([], [poison, finding], [poison, finding]), env={})
    gate3 = gates[2]
    assert not gate3.passed
    assert "indexed lookup" in gate3.detail
    assert "b8acd611" in gate3.detail and "84ca2388" in gate3.detail
    assert "attestation marker" in gate3.detail


def test_fetch_attestations_never_raises_and_shape_checks():
    assert merge_pr.fetch_attestations(lambda url: (_ for _ in ()).throw(RuntimeError("x")), 492) is None
    assert merge_pr.fetch_attestations(lambda url: {"items": "not-a-list"}, 492) is None
    assert merge_pr.fetch_attestations(lambda url: ["legacy-list-shape"], 492) is None
    assert merge_pr.fetch_attestations(lambda url: {"items": [{"id": "x"}]}, 492) == [{"id": "x"}]
    assert merge_pr.fetch_tester_pr_mentions(lambda url: (_ for _ in ()).throw(RuntimeError("x")), 492) == []
    assert merge_pr.fetch_tester_pr_mentions(lambda url: {"items": [{"id": "y"}]}, 492) == [{"id": "y"}]


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


# ── structured gate-failure codes (spec 86017db0) ────────────────────────

def test_checks_report_codes():
    assert merge_pr.checks_report([])[2] == "CI_NO_CHECKS"
    assert merge_pr.checks_report([check("build"), check("lint", status="IN_PROGRESS")])[2] == "CI_PENDING"
    assert merge_pr.checks_report([check("build"), check("lint", conclusion="FAILURE")])[2] == "CI_FAIL"
    assert merge_pr.checks_report([check("build")])[2] is None


def test_attestation_codes_missing_vs_shape_unseen():
    assert merge_pr.evaluate_attestation([], 487, NOW_MS)[2] == "ATT_MISSING"
    # tester record that MENTIONS the PR but carries no attestation marker
    mention_only = rec(rec_id="bbbb2222", tags=["pr:487"], recordType="report")
    ok, detail, code = merge_pr.evaluate_attestation([mention_only], 487, NOW_MS)
    assert not ok and code == "ATT_SHAPE_UNSEEN"


def test_attestation_codes_freshness():
    stale = rec(created_ms=NOW_MS - 7200_000)
    assert merge_pr.evaluate_attestation([stale], 487, NOW_MS)[2] == "ATT_STALE_HEAD"
    assert merge_pr.evaluate_attestation([rec()], 487, None)[2] == "HEAD_DATE_UNKNOWN"
    assert merge_pr.evaluate_attestation(
        [rec()], 487, NOW_MS - 7200_000, run_json=evidence_run_json)[2] is None


def test_pr_ready_codes_deterministic_order():
    assert merge_pr.pr_ready_code({"state": "MERGED", "isDraft": False, "mergeable": "MERGEABLE"}) == "PR_NOT_OPEN"
    assert merge_pr.pr_ready_code({"state": "OPEN", "isDraft": True, "mergeable": "MERGEABLE"}) == "PR_DRAFT"
    assert merge_pr.pr_ready_code({"state": "OPEN", "isDraft": False, "mergeable": "CONFLICTING"}) == "MERGE_CONFLICT"
    assert merge_pr.pr_ready_code({"state": "OPEN", "isDraft": False, "mergeable": None}) == "MERGE_UNKNOWN"


def test_format_report_includes_codes():
    gates = [
        merge_pr.GateResult("pr open & ready", True, "ok"),
        merge_pr.GateResult("tester attestation", False, "predates head", code="ATT_STALE_HEAD"),
    ]
    report = merge_pr.format_report(487, gates)
    assert "[FAIL] tester attestation (ATT_STALE_HEAD): predates head" in report
    assert "[PASS] pr open & ready: ok" in report, "no code => no parens (additive)"


def test_gates_to_json_shape():
    gates = [
        merge_pr.GateResult("pr open & ready", False, "conflicting", code="MERGE_CONFLICT"),
        merge_pr.GateResult("ci green", True, "all green"),
    ]
    doc = merge_pr.gates_to_json(487, gates, {"headRefOid": "abc123"})
    assert doc["pr"] == 487 and doc["head"] == "abc123" and doc["ok"] is False
    assert doc["gates"][0] == {"name": "pr open & ready", "passed": False,
                              "bypassed": False, "code": "MERGE_CONFLICT",
                              "detail": "conflicting"}
    assert doc["gates"][1]["code"] is None


def test_extract_codes_ordered_unique_and_strict():
    gc = merge_pr.gate_codes
    text = ("  [FAIL] ci green (CI_FAIL): 2 failed\n"
            "  [FAIL] tester attestation (ATT_STALE_HEAD): predates\n"
            "  [BYPASS] tester attestation (BYPASSED) (ATT_BYPASSED): op\n")
    assert gc.extract_codes(text) == ["CI_FAIL", "ATT_STALE_HEAD", "ATT_BYPASSED"]
    assert gc.extract_codes("  [FAIL] tester attestation (BYPASSED): x") == []
    assert gc.extract_codes("no codes here") == []
    # every emitted vocabulary member round-trips
    for c in gc.ALL_CODES:
        assert gc.extract_codes(f"[FAIL] x ({c}): y") == [c]


# ── gate 3d: CI-evidence rule (run IDs + conclusions, not stated counts) ──

def test_extract_ci_run_ids_forms():
    text = ("CI run 36000000001: gate job OK. CI runs 36000000002, 36000000003 "
            "also green.")
    assert merge_pr.extract_ci_run_ids(text) == [
        "36000000001", "36000000002", "36000000003",
    ]
    # Strict grammar: a bare "run #N" (no CI prefix) is not an attested run
    # reference — avoids false positives like "rerun #123".
    assert merge_pr.extract_ci_run_ids("also green; run #36000000004 too.") == []
    assert merge_pr.extract_ci_run_ids("engine reports 54/54 tests pass") == []
    assert merge_pr.extract_ci_run_ids("") == []


def test_extract_head_shas():
    assert merge_pr.extract_head_shas("at head 09f1f6cd and head SHA 1a2b3c4d5e6f") == [
        "09f1f6cd", "1a2b3c4d5e6f",
    ]
    assert merge_pr.extract_head_shas("no sha here") == []


def test_evidence_run_lookup_failure_fails_closed():
    # The fixture raises RuntimeError (no 404/not-found markers) -> transient
    # channel: gate still fails, but the failure is RETRYABLE, not "no
    # evidence" (Decision 15 item D).
    ok, detail, transient = merge_pr.verify_ci_runs(
        ["36000000999"], "a8b1dfc6", run_json=evidence_run_json)
    assert not ok and transient and "transient" in detail


def test_evidence_run_404_is_genuinely_absent():
    """A definitive 404 (run reference does not exist) is NOT transient:
    it routes to ATT_NO_CI_EVIDENCE so the tester re-attests with a real
    run reference. Requires BOTH '404' and 'not found' markers (gh stderr
    shape) so a rate-limit body mentioning a URL never reads as absence."""
    def not_found_run(*args):
        raise RuntimeError("gh: HTTP 404: Not Found")

    ok, detail, transient = merge_pr.verify_ci_runs(
        ["36000000999"], "a8b1dfc6", run_json=not_found_run)
    assert not ok and not transient

    att = rec(title="Tester attestation: PR #487 — CI run 36000000999 success")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=not_found_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "fail closed" in detail


def test_evidence_run_transient_lookup_routes_no_post():
    """[D] A transient gh failure (429/network) must NOT surface as
    ATT_NO_CI_EVIDENCE (janitor would nag the tester to re-attest on every
    GitHub blip); it routes ATT_CI_LOOKUP_FAILED — retryable, no posts."""
    def rate_limited_run(*args):
        raise RuntimeError("gh: API rate limit exceeded (HTTP 429)")

    ok, detail, transient = merge_pr.verify_ci_runs(
        ["36000000001"], "a8b1dfc6", run_json=rate_limited_run)
    assert not ok and transient

    att = rec()
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=rate_limited_run)
    assert not ok and code == "ATT_CI_LOOKUP_FAILED"
    assert "transient" in detail


def test_stated_counts_only_fail_att_no_ci_evidence():
    """The exact regression this rule exists for: a well-formed tester row
    that only restates the engine's counts must NOT satisfy gate 3."""
    counts_only = rec(
        rec_id="counts1",
        title="Tester attestation: PR #487 — 54/54 tests pass, 0 failed, 0 skipped",
        content="Engine (opencode/big-pickle) reports 54/54. Attested.",
    )
    ok, detail, code = merge_pr.evaluate_attestation(
        [counts_only], 487, NOW_MS - 7200_000, run_json=evidence_run_json)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "self-attestation" in detail


def test_run_conclusion_failure_rejects_attestation():
    def failing_run(*args):
        return {"c": "failure", "s": "a8b1dfc600000000000000000000000000000000"}

    att = rec()
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000, run_json=failing_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "not success" in detail


def test_run_conclusion_skipped_rejects_attestation():
    """[A] Path-filtered SKIPPED runs carry the PR head_sha and execute no
    tests — accepting them would half-reopen the stated-counts gap."""
    def skipped_run(*args):
        return {"c": "skipped", "s": "a8b1dfc600000000000000000000000000000000"}

    att = rec(title="Tester attestation: PR #487 — CI run 36000000001 skipped")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=skipped_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "success-only" in detail


def test_run_conclusion_neutral_rejects_attestation():
    """[A] NEUTRAL proves no run — fail closed like any non-success."""
    def neutral_run(*args):
        return {"c": "neutral", "s": "a8b1dfc600000000000000000000000000000000"}

    att = rec(title="Tester attestation: PR #487 — CI run 36000000001 neutral")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=neutral_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "success-only" in detail


def test_run_without_head_sha_rejected():
    """[B] A run that omits head_sha has no code binding; accepting it on
    conclusion alone is exactly the pass path the tightening removes."""
    def shaless_run(*args):
        return {"c": "success", "s": ""}

    att = rec(title="Tester attestation: PR #487 — CI run 36000000001 success")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=shaless_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "head_sha" in detail and "fail closed" in detail


def test_run_at_wrong_head_rejected():
    def other_head_run(*args):
        return {"c": "success", "s": "deadbeef00000000000000000000000000000000"}

    att = rec(title="Tester attestation: PR #487 — CI run 36000000001 success")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=other_head_run)
    assert not ok and code == "ATT_NO_CI_EVIDENCE"
    assert "not this PR's head" in detail


def test_run_at_matching_head_passes_with_sha_note():
    att = rec()
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=evidence_run_json)
    assert ok and code is None
    assert "head SHA verified" in detail


def _dispatching_http_projection(attestations, mentions, scan, deep=True):
    """Fake nebula where /api/attestations returns the PROJECTION shape
    (id/recordType/role/tags/createdAt — no content), as the real endpoint's
    SELECT list does. deep=False also refuses the per-id fetches so the
    bounded-scan fallback path is exercised."""
    def http_get(url):
        if "/api/attestations?" in url:
            proj = [{k: v for k, v in a.items() if k != "content"} for a in attestations]
            return {"items": proj, "total": len(proj)}
        if "role=tester" in url and "tag=pr:" in url:
            return {"items": mentions}
        if deep and url.startswith("http://localhost:3101/api/agent-records/") and url.count("/") == 4:
            rid = url.rsplit("/", 1)[1]
            for a in attestations + scan:
                if str(a.get("id")) == rid:
                    return a
            raise RuntimeError(f"no record {rid}")
        if "/api/agent-records" in url:
            return {"items": scan}
        raise RuntimeError(f"unexpected url {url}")
    return http_get


def test_projection_rows_hydrated_from_scan_for_evidence():
    """Indexed rows lack content; the gate hydrates them so the CI-evidence
    rule can read the cited run IDs (per-id fetch, scan fallback)."""
    canonical = rec(created_ms=NOW_MS - 3600_000)
    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json,
        http_get=_dispatching_http_projection([canonical], [], [canonical]), env={})
    gate3 = gates[2]
    assert gate3.passed
    assert "CI run(s) verified" in gate3.detail


def test_projection_hydration_by_id_does_not_need_scan_window():
    """[E] Decision 15: an attestation OUTSIDE the bounded scan's newest-N
    window (scan returns an unrelated page) still hydrates via the targeted
    per-id fetch and passes — previously it failed closed with a spurious
    re-attest nudge once it slid past the window."""
    old_attestation = rec(rec_id="old0aaaa", created_ms=NOW_MS - 86400_000 * 3)
    unrelated_page = [rec(rec_id=f"fill{i:04d}", role="builder",
                          created_ms=NOW_MS - i * 1000) for i in range(5)]
    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json,
        http_get=_dispatching_http_projection(
            [old_attestation], [], unrelated_page, deep=True), env={})
    gate3 = gates[2]
    assert gate3.passed, gate3.detail
    assert "CI run(s) verified" in gate3.detail


def test_projection_hydration_falls_back_to_scan_when_id_fetch_unavailable():
    """[E] Routers/servers without the per-id route keep working: the gate
    falls back to the bounded scan for any record the targeted fetch could
    not retrieve."""
    canonical = rec(created_ms=NOW_MS - 3600_000)
    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json,
        http_get=_dispatching_http_projection(
            [canonical], [], [canonical], deep=False), env={})
    gate3 = gates[2]
    assert gate3.passed
    assert "CI run(s) verified" in gate3.detail


def test_malformed_head_tag_fails_on_shape_not_prefix_bind():
    """[C] Decision 15: a present-but-malformed head: tag must fail on SHAPE
    (ATT_SHAPE_UNSEEN -> adjudication), never silently prefix-bind — a 4-hex
    tag would bind at ~1/65536."""
    att = rec(tags=["type:approval", "status:done", "pr:487", "head:ab12"])
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7200_000,
        head_sha="ab12ef6000000000000000000000000000000000",
        run_json=evidence_run_json)
    assert not ok and code == "ATT_SHAPE_UNSEEN"
    assert "malformed" in detail and "mint-head" in detail


def test_head_tag_40hex_and_7hex_shapes_still_bind():
    """[C] boundary: the legal 7-40 hex range keeps binding; 6 hex is now
    malformed (previously it prefix-bound)."""
    ok, _, _ = merge_pr.evaluate_attestation(
        [rec(tags=["type:approval", "status:done", "pr:487",
                   "head:a8b1dfc600000000000000000000000000000000"])],
        487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=evidence_run_json)
    assert ok
    ok, _, _ = merge_pr.evaluate_attestation(
        [rec(tags=["type:approval", "status:done", "pr:487", "head:a8b1dfc"])],
        487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=evidence_run_json)
    assert ok
    ok, detail, code = merge_pr.evaluate_attestation(
        [rec(tags=["type:approval", "status:done", "pr:487", "head:a8b1df"])],
        487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=evidence_run_json)
    assert not ok and code == "ATT_SHAPE_UNSEEN"


def test_hydration_preserves_indexed_created_at_when_point_drops_it():
    """DBA 48ac13e2 regression: the point endpoint historically returned no
    camelCase createdAt, so #648's by-id hydration swapped a good timestamp
    for none and evaluate_attestation's `or 0` bound the row at epoch 0 ->
    permanent ATT_STALE_HEAD. The gate must re-attach the indexed row's
    createdAt after hydration."""
    att = rec(created_ms=NOW_MS - 3600_000)
    projection = {k: v for k, v in att.items() if k != "content"}
    point = {k: v for k, v in att.items() if k != "createdAt"}  # content, no createdAt

    def http_get(url):
        if "/api/attestations?" in url:
            return {"items": [projection], "total": 1}
        if url.startswith("http://localhost:3101/api/agent-records/") and url.count("/") == 4:
            return point
        raise RuntimeError(f"unexpected url {url}")

    run_json, _ = make_fakes()
    ok, detail, code = merge_pr.attestation_check(
        http_get, 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=run_json)
    assert ok and code is None, (detail, code)
    assert "1970" not in detail


def test_hydration_coerces_iso_created_at_from_point_payload():
    """If the point endpoint returns a snake_case created_at (ISO), the gate
    coerces it into the epoch-ms createdAt the freshness predicate reads —
    no epoch-0 binding either way."""
    from datetime import datetime, timezone as _tz
    att = rec(created_ms=NOW_MS - 3600_000)
    projection = {k: v for k, v in att.items() if k != "content"}
    iso = datetime.fromtimestamp(att["createdAt"] / 1000, tz=_tz.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    point = {k: v for k, v in att.items() if k not in ("createdAt", "content")}
    point["created_at"] = iso

    def http_get(url):
        if "/api/attestations?" in url:
            return {"items": [projection], "total": 1}
        if url.startswith("http://localhost:3101/api/agent-records/") and url.count("/") == 4:
            return point
        raise RuntimeError(f"unexpected url {url}")

    run_json, _ = make_fakes()
    ok, detail, code = merge_pr.attestation_check(
        http_get, 487, NOW_MS - 7200_000,
        head_sha="a8b1dfc600000000000000000000000000000000",
        run_json=run_json)
    assert ok and code is None, (detail, code)
    assert "1970" not in detail


def test_projection_without_hydration_fails_closed_not_open():
    """If the scan cannot provide content either, the evidence rule fails
    closed with a self-explaining detail — never a silent pass. The
    projection here strips BOTH content and the evidencing title (title and
    body are both scanned for run references by design)."""
    canonical = rec(created_ms=NOW_MS - 3600_000)

    def no_content_anywhere(url):
        if "/api/attestations?" in url:
            proj = {k: v for k, v in canonical.items() if k not in ("content", "title")}
            proj["title"] = "Tester attestation: PR #487"
            return {"items": [proj], "total": 1}
        if "/api/agent-records" in url:
            raise RuntimeError("nebula scan unavailable")
        raise RuntimeError(f"unexpected url {url}")

    run_json, _ = make_fakes()
    gates, _ = merge_pr.evaluate(
        487, run_json=run_json, http_get=no_content_anywhere, env={})
    gate3 = gates[2]
    assert not gate3.passed
    assert gate3.code == "ATT_NO_CI_EVIDENCE"
    assert "cites no CI run references" in gate3.detail


def test_new_code_in_all_codes_roundtrip():
    gc = merge_pr.gate_codes
    assert gc.ATT_NO_CI_EVIDENCE in gc.ALL_CODES
    assert gc.extract_codes(
        "[FAIL] tester attestation (ATT_NO_CI_EVIDENCE): cites no CI run") == [
        "ATT_NO_CI_EVIDENCE"]


# ── gate 3d: point-endpoint contract — the timestamp must survive ────────
#
# The two nebula read paths have different projections:
#   GET /api/attestations?pr=<N>   camelCase rows, NO content (indexed)
#   GET /api/agent-records/:id      full record; camelCase + epoch-ms
#                                  timestamps via camelCaseRow (routes.ts)
# gate 3 hydrates the content-less indexed rows by record id, REPLACING them
# with the point response. A point response that omits `createdAt` therefore
# silently re-ages a fresh attestation to epoch 0, and the freshness rule
# (created_ms >= head_date_ms) can never be satisfied again.
#
# That is not hypothetical: the point handler returned the raw pg row
# (snake_case created_at, no createdAt key) until the fix in
# typescript/nebula-srv/src/routes.ts, so every hydrated attestation bound at
# 1970-01-01T00:00:00Z and green PRs #642/#645 were refused indefinitely with
# ATT_STALE_HEAD. These tests pin the contract from the gate's side.


def _split_endpoint_http(indexed, point_rows):
    """Fake nebula with the real two-endpoint split: the indexed projection
    carries no content, the point endpoint carries the full row."""
    def http_get(url):
        if "/api/attestations?pr=" in url:
            return {"items": indexed, "total": len(indexed)}
        if "/api/agent-records/" in url:
            return point_rows[url.rsplit("/", 1)[-1]]
        if "/api/agent-records" in url:
            return {"items": []}
        raise RuntimeError(f"unexpected url {url}")
    return http_get


HEAD_SHA = "a8b1dfc600000000000000000000000000000000"
RID = "07b09151"  # the re-issued attestation held at epoch 0 in production


def _indexed_row(att_ms):
    """Indexed /api/attestations projection: camelCase, no content."""
    return {
        "id": RID, "role": "tester", "recordType": "assessment",
        "createdAt": att_ms, "tags": ["type:approval", "status:done",
                                      "pr:487", f"head:{HEAD_SHA[:7]}"],
        "title": "Tester attestation: PR #487", "content": "",
    }


def _point_row(att_ms):
    """GET /api/agent-records/:id as the server serializes it post-fix."""
    return {
        "id": RID, "role": "tester", "recordType": "assessment",
        "createdAt": att_ms, "tags": ["type:approval", "status:done",
                                      "pr:487", f"head:{HEAD_SHA[:7]}"],
        "title": EVIDENCE_TITLE, "content": EVIDENCE_CONTENT,
    }


def test_point_fetch_preserves_created_at_through_hydration():
    """Regression: hydrating an indexed row by id must not cost the
    attestation its timestamp. Attested 1h ago, head 2h ago → gate passes on
    the real timestamp, and never reports a 1970 binding."""
    att_ms, head_ms = NOW_MS - 3600_000, NOW_MS - 7200_000
    ok, detail, code = merge_pr.attestation_check(
        _split_endpoint_http([_indexed_row(att_ms)], {RID: _point_row(att_ms)}),
        487, head_ms, head_sha=HEAD_SHA, run_json=evidence_run_json)
    assert ok, f"gate 3 refused a valid, head-bound attestation: {detail}"
    assert "1970" not in detail
    assert code is None


def test_point_response_without_created_at_repaired_from_indexed():
    """The shipped defect, updated per this test's own instruction: the
    pre-fix point response carried snake_case `created_at` and no `createdAt`
    (and snake_case `record_type`). Hydration swapped that row in, freshness
    compared 0 against the head date, and every attestation was refused at
    1970 no matter how new it was — the anomaly that held #642/#645.

    Updated when the gate hardened its own timestamp handling (DBA analysis
    48ac13e2, fix A — merged as PR #654, df46b49e): the gate now repairs a
    missing createdAt from the indexed row's timestamp, so the exact payload
    that used to bind at epoch 0 now passes on the real timestamp. The
    server-side contract fix (this PR's routes.ts half) remains the primary
    repair; the gate-side repair is the defense-in-depth that makes a future
    server regression non-fatal.
    """
    att_ms, head_ms = NOW_MS - 3600_000, NOW_MS - 7200_000
    raw_row = _point_row(att_ms)
    del raw_row["createdAt"]          # what the raw-row serialization produced
    raw_row["created_at"] = "2026-09-29T03:35:12.000Z"
    raw_row["record_type"] = raw_row.pop("recordType")
    raw_row["tags"].append("type:attestation")  # legacy shape survives the swap
    ok, detail, code = merge_pr.attestation_check(
        _split_endpoint_http([_indexed_row(att_ms)], {RID: raw_row}),
        487, head_ms, head_sha=HEAD_SHA, run_json=evidence_run_json)
    assert ok, f"gate 3 should repair the timestamp from the indexed row: {detail}"
    assert code is None
    assert "1970" not in detail


def test_point_handler_serializes_with_camel_case_row():
    """Source-level guard for the server-side half of the contract.

    The two tests above only pin the gate's behavior for a given payload —
    no hermetic test can observe a running server, so a revert of the
    routes.ts fix would leave the suite green while production broke again.
    Assert instead that every registered GET /api/agent-records/:id handler
    serializes through camelCaseRow, the serializer the list route already
    uses (camelCase keys + epoch-ms timestamps). Both handlers are checked:
    the second is currently unreachable, but it is registered on the same
    path and would become live the moment the first is removed.
    """
    routes = (Path(__file__).resolve().parents[2]
              / "typescript" / "nebula-srv" / "src" / "routes.ts")
    src = routes.read_text(encoding="utf-8")
    marker = "router.get('/agent-records/:id'"
    chunks = src.split(marker)[1:]
    assert chunks, "no GET /api/agent-records/:id handler found in routes.ts"
    raw = [c for c in chunks if "camelCaseRow(" not in c.split("router.")[0]]
    assert not raw, (
        "GET /api/agent-records/:id must serialize via camelCaseRow (camelCase "
        "keys + epoch-ms createdAt); a raw res.json() row drops createdAt and "
        "makes gate 3's freshness check bind at epoch 0 (DBA analysis "
        "48ac13e2 — held PRs #642/#645)"
    )

# ── gate 3e: a missing/unusable createdAt fails closed with its own code ──
#
# DBA records 48ac13e2 / 2a51e900. The evaluator used to coerce a missing
# createdAt to 0, binding the row at epoch 0 and reporting ATT_STALE_HEAD —
# a verdict that tells the tester to RE-ATTEST for a condition that no
# re-attestation can fix, because the cause is a record-endpoint
# serialization regression. That misdiagnosis is what froze the merge queue.
# #654 repairs the timestamp inside the by-id hydration path; these tests pin
# the second, independent half: the evaluator itself must refuse to guess.


def test_missing_created_at_fails_closed_with_dedicated_code():
    att = rec()
    del att["createdAt"]
    ok, detail, code = merge_pr.evaluate_attestation([att], 487, NOW_MS - 7200_000)
    assert not ok
    assert code == "ATT_TIMESTAMP_MISSING", code
    # The whole point: never a freshness verdict built on a fabricated epoch.
    assert code != "ATT_STALE_HEAD"
    assert "1970" not in detail
    assert "re-attesting will not clear it" in detail


def test_null_created_at_is_missing_not_epoch():
    att = rec(created_ms=None)
    att["createdAt"] = None
    ok, _detail, code = merge_pr.evaluate_attestation([att], 487, NOW_MS - 7200_000)
    assert not ok
    assert code == "ATT_TIMESTAMP_MISSING", code


def test_unparseable_created_at_fails_closed():
    att = rec()
    att["createdAt"] = "not-a-timestamp"
    ok, detail, code = merge_pr.evaluate_attestation([att], 487, NOW_MS - 7200_000)
    assert not ok
    assert code == "ATT_TIMESTAMP_MISSING", code
    assert "1970" not in detail


def test_boolean_created_at_is_type_confusion_not_epoch():
    """True is truthy, so the old `or 0` would have compared against 1 ms
    since the epoch. Booleans are never a timestamp."""
    att = rec()
    att["createdAt"] = True
    ok, _detail, code = merge_pr.evaluate_attestation([att], 487, NOW_MS - 7200_000)
    assert not ok
    assert code == "ATT_TIMESTAMP_MISSING", code


def test_iso_string_created_at_is_accepted_not_rejected():
    """The assembly-srv :3107 proxy returns createdAt as an ISO string
    (DBA record ca9ba66c). A real, parseable timestamp must yield a real
    verdict — not a spurious ATT_TIMESTAMP_MISSING, and not a crash."""
    att = rec(created_ms=NOW_MS - 7_200_000)
    att["createdAt"] = datetime.fromtimestamp(
        att["createdAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Genuinely older than head -> the honest verdict is STALE, not MISSING.
    ok, detail, code = merge_pr.evaluate_attestation([att], 487, NOW_MS)
    assert not ok
    assert code == "ATT_STALE_HEAD", (detail, code)
    assert "1970" not in detail


def test_snake_case_created_at_epoch_is_accepted():
    """The post-#652 point endpoint and the raw adonis handler can both
    deliver snake_case. A parseable epoch must give the honest verdict."""
    att = rec(created_ms=NOW_MS - 3600_000)
    att["created_at"] = att.pop("createdAt")
    ok, detail, code = merge_pr.evaluate_attestation(
        [att], 487, NOW_MS - 7_200_000, run_json=evidence_run_json)
    assert code != "ATT_TIMESTAMP_MISSING", code
    assert code != "ATT_STALE_HEAD", (detail, code)
    assert "1970" not in detail


def test_missing_created_at_outranks_unknown_head_date():
    """With no attestation timestamp there is nothing to compare, so
    HEAD_DATE_UNKNOWN would be equally uninformative. The dedicated code
    must win — it names the actual defect."""
    att = rec()
    del att["createdAt"]
    ok, _detail, code = merge_pr.evaluate_attestation([att], 487, None)
    assert not ok
    assert code == "ATT_TIMESTAMP_MISSING", code


def test_newest_usable_timestamp_still_wins_over_a_missing_one():
    """Regression guard on the selection key: rows missing a timestamp must
    not outrank a real one (which is what sorting on 0 would do)."""
    dated = rec(rec_id="dated01", created_ms=NOW_MS - 3600_000)
    undated = rec(rec_id="undated1")
    del undated["createdAt"]
    rows = [undated, dated]
    ok, detail, code = merge_pr.evaluate_attestation(
        rows, 487, NOW_MS - 7200_000, run_json=evidence_run_json)
    assert ok, (detail, code)
    assert "dated01" in detail


def test_timestamp_missing_code_is_registered_for_consumers():
    gc = merge_pr.gate_codes
    assert gc.ATT_TIMESTAMP_MISSING in gc.ALL_CODES
    assert gc.extract_codes(
        "[FAIL] tester attestation (ATT_TIMESTAMP_MISSING): no usable createdAt"
    ) == ["ATT_TIMESTAMP_MISSING"]


# ── gate 3z: the 990e1d07 incident rows, verbatim ─────────────────────────
#
# The residual of the 2026-09-29 merge-queue freeze (reviewer record
# 990e1d07, cause established in engineer-ii b33c6d9a): PR #642's re-issued
# attestation 07b09151 was refused with
#   "newest attestation 1970-01-01T00:00:00Z predates head commit
#    2026-09-29T01:37:41Z"
# on every janitor cycle from 05:00:25Z until #654 landed (07:40:30Z).
# These tests run the EXACT payload of that incident through the CURRENT
# gate so the epoch-0 binding cannot return silently: if the timestamp
# repair (#654) or the fail-closed evaluator (#657) ever regresses, the
# assertions below fail instead of the merge queue.
#
# Fixture values are historical, not synthetic: attestation 07b09151
# (createdAt 1790650512126 epoch-ms = 2026-09-29T02:55:12.126Z), PR #642
# head 83cea633…, head commit 2026-09-29T01:37:41Z — the same values the
# janitor journal logged, reproduced two-sided at the #648 boundary in the
# 990e1d07 investigation (hermetic A/B: pre-#648 -> ATT_NO_CI_EVIDENCE,
# at-#648 -> the 1970 ATT_STALE_HEAD).

INCIDENT_RID = "07b09151-0000-0000-0000-000000000000"
INCIDENT_ATT_MS = 1790650512126                    # 2026-09-29T02:55:12.126Z
INCIDENT_ATT_ISO = "2026-09-29T02:55:12.126Z"
INCIDENT_HEAD = "83cea63300000000000000000000000000000000"
INCIDENT_HEAD_MS = int(
    datetime(2026, 9, 29, 1, 37, 41, tzinfo=timezone.utc).timestamp() * 1000
)
INCIDENT_RUNS = ("36014200001", "36014200002")
INCIDENT_TITLE = "ATTESTATION PR #642 @83cea633 - 26/26 hermetic verified, 62/62 CI"
INCIDENT_CITATIONS = (
    "Verified via service-test-gates. CI run 36014200001 and CI run "
    "36014200002: success at head 83cea633.\n"
    "Engine-reported counts not relied on."
)


def _incident_run_json(*args):
    """gh api fake for the two run IDs the incident attestation cites."""
    args = tuple(a for a in args if not str(a).startswith("--jq"))
    q = " ".join(str(a) for a in args)
    if "36014200001" in q or "36014200002" in q:
        return {"c": "success", "s": INCIDENT_HEAD}
    raise RuntimeError(f"gh api lookup failed: {q}")


def _incident_indexed_row(**overrides):
    """GET /api/attestations?pr=642 projection on 2026-09-29 ~05:00Z:
    real epoch-ms createdAt, content truncated to empty."""
    row = {
        "id": INCIDENT_RID, "role": "tester", "recordType": "assessment",
        "createdAt": INCIDENT_ATT_MS,
        "tags": ["type:approval", "status:done", "pr:642",
                 "head:83cea633"],
        "title": INCIDENT_TITLE, "content": "",
    }
    row.update(overrides)
    return row


def _incident_point_row(**overrides):
    """GET /api/agent-records/:id as the incident-era server serialized it
    (DBA 48ac13e2 A/B, reviewer 042d3742's 'absent, not null' correction,
    tester 492b6c88's contract split): recordType camelCase, the createdAt
    key ABSENT, the timestamp parked under snake_case created_at ISO, and
    the full content with the CI citations visible. This is the exact shape
    whose hydration bound the row at epoch 0 in production."""
    row = {
        "id": INCIDENT_RID, "role": "tester",
        "recordType": "assessment",
        "created_at": INCIDENT_ATT_ISO,
        "tags": ["type:approval", "status:done", "pr:642",
                 "head:83cea633"],
        "title": INCIDENT_TITLE, "content": INCIDENT_CITATIONS,
    }
    row.update(overrides)
    return row


def _incident_http(indexed_row, point_row):
    def http_get(url):
        if "/api/attestations?pr=" in url:
            return {"items": [indexed_row],
                    "total": 1}
        if "/api/agent-records/" in url:
            return point_row
        if "/api/agent-records" in url:
            return {"items": []}
        raise RuntimeError(f"unexpected url {url}")
    return http_get


def test_incident_rows_now_pass_on_the_real_timestamp():
    """The 990e1d07 payload, run through the current gate: the #654 repair
    must re-attach the real timestamp (from the point payload's snake ISO,
    falling back to the indexed row) and the gate must PASS — the exact
    refusal of the incident ('1970 predates head') must be unreachable for
    these rows."""
    ok, detail, code = merge_pr.attestation_check(
        _incident_http(_incident_indexed_row(), _incident_point_row()),
        642, INCIDENT_HEAD_MS, head_sha=INCIDENT_HEAD,
        run_json=_incident_run_json)
    assert ok, f"epoch-0 regression: incident rows refused again: {detail}"
    assert code is None
    assert "2026-09-29T02:55:12Z" in detail, detail   # the REAL timestamp
    assert "1970" not in detail, detail
    assert "predates head commit" not in detail, detail


def test_incident_rows_without_repair_source_fail_timestamp_missing():
    """#657's half, pinned on the incident payload: with every repair
    source stripped (no indexed timestamp, no snake fallback), the gate
    must fail closed with ATT_TIMESTAMP_MISSING — never re-coerce to 0 and
    report the ATT_STALE_HEAD/1970 verdict that told the tester to
    re-attest a condition no re-attestation could fix."""
    bare = _incident_point_row()
    del bare["created_at"]                 # no snake fallback either
    indexed = _incident_indexed_row()
    del indexed["createdAt"]               # no indexed repair source either
    ok, detail, code = merge_pr.attestation_check(
        _incident_http(indexed, bare),
        642, INCIDENT_HEAD_MS, head_sha=INCIDENT_HEAD,
        run_json=_incident_run_json)
    assert not ok
    assert code == merge_pr.gate_codes.ATT_TIMESTAMP_MISSING, (detail, code)
    assert "1970" not in detail, detail
    assert "re-attesting will not clear it" in detail, detail


def test_incident_rows_still_reject_a_genuinely_wrong_head_binding():
    """No overcorrection: the fixes must not have blurred the real signal.
    The incident rows attested head 83cea633; pointing the PR at a
    different head must still yield the specific binding-variant
    ATT_STALE_HEAD verdict ('attestation head binding ... != PR head ...')
    that the janitor routes as a queue-tester action."""
    other_head = "deadbeef" + "0" * 32
    ok, detail, code = merge_pr.attestation_check(
        _incident_http(_incident_indexed_row(), _incident_point_row()),
        642, INCIDENT_HEAD_MS, head_sha=other_head,
        run_json=_incident_run_json)
    assert not ok
    assert code == merge_pr.gate_codes.ATT_STALE_HEAD, (detail, code)
    assert "attestation head binding" in detail, detail
    assert "1970" not in detail, detail
