#!/usr/bin/env python3
"""Hermetic tests for bin/check_harness_silent_success.py.

No database: every test drives the pure `silent_successes()` classifier with
constructed events and record timestamps, plus the CLI's argument handling and
its tool-error path (which is what a machine without psycopg2 sees).

Pins:
  - a completed run whose role posted a record inside its window is clean
  - a completed run with no record at all is a finding
  - a record for the role OUTSIDE the window does not count (before the run,
    and after the grace) — the window is the run, not the role's day
  - short runs are flagged by DEFAULT (job 2b5c34ae: 35s, wrote nothing) and
    --min-duration is what narrows the sweep, not a hidden threshold
  - an empty role is skipped rather than matched against ""
  - the finding carries the window it used, so a disputed verdict is checkable
  - several runs: only the silent ones are reported, in completion order
  - the grace tail admits a record written just after the run ended
  - CLI: no database → exit 2 with a "tool error" line on stderr (nothing filed)

Dual-runnable: pytest or python3 bin/tests/test_harness_silent_success.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIN = Path(__file__).resolve().parents[1]
MODULE_PATH = BIN / "check_harness_silent_success.py"

_spec = importlib.util.spec_from_file_location("check_harness_silent_success", MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(mod)

T0 = datetime(2026, 11, 5, 4, 32, 24, tzinfo=timezone.utc)


def run(
    job_id: str,
    role: str,
    duration_s: float,
    completed_at: datetime = T0,
    state: str = "completed",
) -> dict:
    return {
        "job_id": job_id,
        "role": role,
        "completed_at": completed_at,
        "duration_ms": int(duration_s * 1000),
        "state": state,
        "detail": "opencode exited 0 and the final step ended on a natural stop",
    }


def test_record_inside_window_is_clean() -> None:
    findings = mod.silent_successes(
        [run("j1", "tester", 771)],
        {"tester": [T0 - timedelta(seconds=600)]},
    )
    assert findings == [], findings


def test_no_record_at_all_is_a_finding() -> None:
    findings = mod.silent_successes([run("j1", "tester", 771)], {"tester": []})
    assert len(findings) == 1
    f = findings[0]
    assert f["job_id"] == "j1"
    assert f["role"] == "tester"
    assert f["duration_ms"] == 771_000
    # The window is reported so the verdict can be re-checked by hand:
    # 04:32:24 - 771s duration - 180s grace = 04:16:33.
    start, end = f["window"]
    assert start == "2026-11-05T04:16:33+00:00", start
    assert end == "2026-11-05T04:35:24+00:00", end


def test_record_outside_window_does_not_count() -> None:
    # Written 2 hours before the run: a different piece of work entirely.
    before = mod.silent_successes(
        [run("j1", "tester", 300)],
        {"tester": [T0 - timedelta(hours=2)]},
    )
    assert len(before) == 1, before

    # Written well past the grace tail: also not this run's record.
    after = mod.silent_successes(
        [run("j1", "tester", 300)],
        {"tester": [T0 + timedelta(minutes=30)]},
    )
    assert len(after) == 1, after


def test_grace_tail_admits_a_record_written_just_after_the_run() -> None:
    findings = mod.silent_successes(
        [run("j1", "tester", 300)],
        {"tester": [T0 + timedelta(seconds=60)]},
    )
    assert findings == [], findings

    # 179s after completion is inside the default 180s grace; 181s is not.
    inside = mod.silent_successes(
        [run("j1", "tester", 300)], {"tester": [T0 + timedelta(seconds=179)]}
    )
    outside = mod.silent_successes(
        [run("j1", "tester", 300)], {"tester": [T0 + timedelta(seconds=181)]}
    )
    assert inside == [] and len(outside) == 1


def test_short_runs_are_flagged_by_default() -> None:
    # 2b5c34ae exited 0 after 35s having written nothing; a 60s threshold is
    # what hid it. The default must catch it.
    findings = mod.silent_successes([run("2b5c34ae", "tester", 35)], {"tester": []})
    assert len(findings) == 1, findings
    assert findings[0]["duration_ms"] == 35_000

    # Narrowing is explicit and visible in the output, never silent.
    narrowed = mod.silent_successes(
        [run("probe", "tester", 3)], {"tester": []}, min_duration_ms=60_000
    )
    assert narrowed == [], "--min-duration 60 drops a 3s probe on request"
    assert mod.DEFAULT_MIN_DURATION_S == 0


def test_empty_role_is_skipped() -> None:
    findings = mod.silent_successes([run("j1", "", 600)], {"": [T0]})
    assert findings == []


def test_only_the_silent_runs_are_reported_in_order() -> None:
    # Distinct roles per run: records are matched per role, so two overlapping
    # runs of the same role would legitimately share a record and the test
    # would be measuring the fixture, not the check.
    events = [
        run("loud", "tester", 400, T0 - timedelta(minutes=10)),
        run("silent-a", "reviewer", 500, T0 - timedelta(minutes=5)),
        run("silent-b", "engineer", 900, T0),
    ]
    findings = mod.silent_successes(
        events,
        {
            "tester": [T0 - timedelta(minutes=10) - timedelta(seconds=30)],
            "reviewer": [],
            "engineer": [],
        },
    )
    assert [f["job_id"] for f in findings] == ["silent-a", "silent-b"], findings


def test_zero_duration_uses_completion_time_only() -> None:
    # duration_ms 0 still has a window (it is just the grace tail) — and it is
    # still a finding: a run that reported 0s and wrote nothing is not exempt
    # just because its clock is degenerate.
    findings = mod.silent_successes([run("j0", "tester", 0)], {"tester": []})
    assert len(findings) == 1, findings
    start, end = findings[0]["window"]
    assert start == "2026-11-05T04:29:24+00:00", start
    assert end == "2026-11-05T04:35:24+00:00", end


def test_failed_runs_are_not_this_check_s_business() -> None:
    # The caller feeds harness.completed rows only; a failed state is carried
    # through in the finding rather than silently dropped if it is ever fed.
    findings = mod.silent_successes(
        [run("jf", "tester", 600, state="failed")], {"tester": []}
    )
    assert len(findings) == 1 and findings[0]["state"] == "failed"


# --- V210: exact run attribution -------------------------------------------


def rec(ts: datetime, job_id: str | None = None) -> dict:
    return {"created_at": ts, "harness_job_id": job_id}


def test_record_stamped_with_the_run_id_clears_it() -> None:
    findings = mod.silent_successes(
        [run("j1", "tester", 300)],
        {"tester": [rec(T0 - timedelta(seconds=120), "j1")]},
    )
    assert findings == [], findings


def test_job_id_matches_even_outside_the_window() -> None:
    # The id is stronger evidence than the clock: a run whose duration was
    # mis-measured still owns its own record.
    findings = mod.silent_successes(
        [run("j1", "tester", 60)],
        {"tester": [rec(T0 - timedelta(hours=1), "j1")]},
    )
    assert findings == [], findings


def test_a_different_runs_record_does_not_clear_this_one() -> None:
    # THE defect this column exists for: the role+window match was cleared by
    # any sibling record from the same role, so a silent run stayed invisible.
    findings = mod.silent_successes(
        [run("silent", "tester", 771)],
        {"tester": [rec(T0 - timedelta(seconds=200), "other-run")]},
    )
    assert len(findings) == 1, findings
    assert findings[0]["job_id"] == "silent"
    # The report says what cleared nothing, so "my record is missing" and
    # "someone else's record is here" are distinguishable by hand.
    assert findings[0]["attributed_elsewhere"] == ["other-run"]


def test_unattributed_record_in_window_still_clears_legacy_runs() -> None:
    # Rows written before V210, and MCP rows written without the field, carry
    # no job id. Counting them is the pre-V210 behaviour and must survive it.
    findings = mod.silent_successes(
        [run("j1", "tester", 300)],
        {"tester": [rec(T0 - timedelta(seconds=90), None), T0 - timedelta(seconds=30)]},
    )
    assert findings == [], findings


def test_unattributed_record_outside_window_does_not_clear() -> None:
    findings = mod.silent_successes(
        [run("j1", "tester", 60)],
        {"tester": [rec(T0 - timedelta(hours=3), None)]},
    )
    assert len(findings) == 1, findings
    assert "attributed_elsewhere" not in findings[0]


def test_attributed_record_clears_even_when_others_are_elsewhere() -> None:
    findings = mod.silent_successes(
        [run("j1", "tester", 300)],
        {
            "tester": [
                rec(T0 - timedelta(seconds=100), "sibling"),
                rec(T0 - timedelta(seconds=80), "j1"),
            ]
        },
    )
    assert findings == [], findings


def test_clearance_counts_say_which_kind_of_clean() -> None:
    events = [
        run("by-id", "tester", 100),
        run("by-window", "reviewer", 100),
        run("silent", "engineer", 100),
    ]
    counts = mod.clearance(
        events,
        {
            "tester": [rec(T0 - timedelta(seconds=10), "by-id")],
            "reviewer": [rec(T0 - timedelta(seconds=10), None)],
            "engineer": [],
        },
    )
    assert counts == {"job-id": 1, "window": 1, "none": 1}, counts


def test_cli_without_a_database_exits_2() -> None:
    env = {
        "SRCDSN": "postgresql://nobody@127.0.0.1:1/nonexistent",
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--hours", "1"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    # psycopg2 present but unreachable → tool error; absent → also tool error.
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "tool error" in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "", "a tool error must not print a finding set"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
