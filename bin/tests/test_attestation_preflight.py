"""Hermetic tests for attestation_preflight.py.

Runs under pytest and directly (`python3 bin/tests/test_attestation_preflight.py`),
like the rest of bin/tests. No database, no network: the preflight imports the
gate's pure functions, which is exactly what is being tested — that the tool
reads a draft the way gate 3 reads it.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "attestation_preflight", BIN / "attestation_preflight.py")
ap = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("attestation_preflight", ap)
_spec.loader.exec_module(ap)

HEAD = "8b6fc6566be5f24c0b916c85de6031cf52930dbf"


def make_row(content, title="APPROVED: #873 gate prefers a recognized declaration",
             tags=None, record_type="engineering_log"):
    return {
        "recordType": record_type,
        "title": title,
        "content": content,
        "tags": tags if tags is not None else [
            "type:attestation", "pr:873", "policy:attestation/v2",
            "verdict:approved", "status:done", "head:8b6fc6566",
        ],
    }


CLEAN = (
    "Verdict: approved.\n\n"
    "Read the diff and the tests; both suites green.\n\n"
    "- `CI run 42023046321`\n"
    "- `Rollup at 8b6fc6566: 76 checks, all success`\n"
)


def levels(report):
    return {f["check"]: f["level"] for f in report["findings"]}


def msg(report, check):
    return next(f["message"] for f in report["findings"] if f["check"] == check)


# ── the happy path ───────────────────────────────────────────────────────

def test_a_clean_row_passes_all_offline_checks():
    r = ap.build_report(make_row(CLEAN), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "PASS", r
    assert r["verdict"] == "approved"
    assert all(f["level"] == "PASS" for f in r["findings"]), r


# ── the trap that cost three rows on 2026-11-05 ──────────────────────────

def test_prose_after_the_declaration_is_flagged_but_not_a_refusal():
    """Post-fix behaviour: the parser picks the recognized declaration, so this
    row merges — but two declaration labels in one body is still worth saying out
    loud, because a row cannot be edited after posting."""
    body = CLEAN + (
        "\nI confirmed nothing still reads the run verdict: classifyLadder's "
        "signature takes only AttemptOutcome[].\n"
    )
    r = ap.build_report(make_row(body), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "WARN", r
    assert r["verdict"] == "approved"
    assert levels(r)["declaration"] == "PASS"
    assert levels(r)["label-in-prose"] == "WARN"
    assert "2 label hits" in msg(r, "label-in-prose")


def test_a_lone_unreadable_token_is_a_refusal():
    body = CLEAN.replace("Verdict: approved.", "verdict: approvedd")
    r = ap.build_report(make_row(body), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert "ATT_VERDICT_INVALID" in msg(r, "declaration")


def test_a_rejection_is_a_refusal_not_a_pass():
    body = CLEAN.replace("Verdict: approved.", "Verdict: rejected.")
    r = ap.build_report(make_row(body), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert "ATT_VERDICT_REJECTED" in msg(r, "declaration")


# ── evidence checks the gate performs anyway ─────────────────────────────

def test_missing_ci_citation_fails_before_posting():
    body = CLEAN.replace("- `CI run 42023046321`\n", "")
    r = ap.build_report(make_row(body), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert "ATT_NO_CI_EVIDENCE" in msg(r, "ci-citation")


def test_missing_rollup_fails_for_v2():
    body = CLEAN.replace("- `Rollup at 8b6fc6566: 76 checks, all success`\n", "")
    r = ap.build_report(make_row(body), pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert "ATT_CI_COVERAGE" in msg(r, "rollup")


def test_rollup_that_does_not_match_the_head_fails():
    r = ap.build_report(make_row(CLEAN), pr_number=873, head=HEAD, checks=69)
    assert r["result"] == "FAIL"
    assert "states 76 checks but the head has 69" in msg(r, "rollup")


def test_stale_head_tag_fails():
    r = ap.build_report(make_row(CLEAN), pr_number=873,
                        head="deadbeef0c0ffee1234567890abcdef123456789", checks=76)
    assert r["result"] == "FAIL"
    assert levels(r)["head-binding"] == "FAIL"


def test_a_row_that_never_names_its_pr_fails():
    r = ap.build_report(
        make_row(CLEAN, title="APPROVED: looks good",
                 tags=["type:attestation", "policy:attestation/v2"]),
        pr_number=873, head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert levels(r)["pr-reference"] == "FAIL"


def test_wrong_record_shape_fails():
    r = ap.build_report(make_row(CLEAN, tags=["pr:873"]), pr_number=873,
                        head=HEAD, checks=76)
    assert r["result"] == "FAIL"
    assert levels(r)["shape"] == "FAIL"


# ── legacy rows keep their meaning ───────────────────────────────────────

def test_a_legacy_row_without_a_declaration_warns_rather_than_fails():
    """The gate reads a row with no `policy:` under the pre-v2 contract, where a
    missing declaration is not a refusal. Reporting FAIL here would tell an author
    their mergeable legacy row is broken."""
    r = ap.build_report(
        make_row("Read the diff. `CI run 42023046321` shows 76 checks all success.",
                 tags=["type:attestation", "pr:873"]),
        pr_number=873)
    assert levels(r)["policy"] == "WARN"
    assert levels(r)["declaration"] == "WARN"
    assert r["result"] == "WARN", r


# ── CLI contract ─────────────────────────────────────────────────────────

def _run_cli(tmp_path, row):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(BIN / "attestation_preflight.py"),
         "--draft", str(path), "--pr", "873", "--head", HEAD, "--checks", "76"],
        capture_output=True, text=True)


def test_cli_exits_zero_on_a_clean_row(tmp_path):
    p = _run_cli(tmp_path, make_row(CLEAN))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "attestation preflight: PASS" in p.stdout


def test_cli_exits_one_with_json_on_a_bad_row(tmp_path):
    p = _run_cli(tmp_path, make_row(CLEAN.replace("Verdict: approved.", "Verdict: rejected.")))
    assert p.returncode == 1
    assert "FAIL" in p.stdout


def test_cli_json_output_is_machine_readable(tmp_path):
    row = make_row(CLEAN.replace("Verdict: approved.", "Verdict: rejected."))
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(BIN / "attestation_preflight.py"), "--draft", str(path),
         "--pr", "873", "--json"],
        capture_output=True, text=True)
    assert p.returncode == 1
    parsed = json.loads(p.stdout)
    assert parsed["result"] == "FAIL"
    assert parsed["verdict"] == "rejected"
    assert {f["level"] for f in parsed["findings"]} == {"PASS", "FAIL"}


if __name__ == "__main__":
    # Direct runner: same assertions, no pytest needed.
    import traceback
    import tempfile

    failures = 0
    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_") and callable(o)]
    for name, fn in tests:
        try:
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  ok   {name}")
        except Exception:
            failures += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    sys.exit(1 if failures else 0)
