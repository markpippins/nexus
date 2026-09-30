#!/usr/bin/env python3
"""Hermetic tests for bin/record_hygiene_sweep.py.

No nebula, no supersede-record.sh, no forum: HTTP goes through an injected
fetch callable, the dry-run oracle through an injected runner, the change-log
post through a patched module function; state lives in a tmp file. Pins:

  - scan pagination (offset walk, short-page termination, MAX_PAGES cap)
  - staleness filter (age cutoff + exclusion tags + AGE-UNKNOWN kept)
  - classification buckets (OBSERVATION/REQUEST -> RETIRE-PROPOSE,
    I4-EXEMPT, MANUAL-REVIEW for governing/unknown types)
  - dry-run exit-code oracle (0 actionable, 2 self-resolved, 1 tool error)
    and that the invoked command carries --dry-run (read-only contract)
  - evidence post fires once per NEW actionable candidate, deduped across
    runs via the state file
  - TOCTOU guard: a candidate that went terminal before posting is dropped
  - scan failure exits 2 with no state write; check-only writes nothing

Dual-runnable: pytest or python3 bin/tests/test_record_hygiene_sweep.py.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE.parent / "record_hygiene_sweep.py"
_spec = importlib.util.spec_from_file_location("record_hygiene_sweep", MOD_PATH)
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_record(num, role="engineer-ii", rtype="analysis", age_days=30.0, tags=None):
    days = 0.0 if age_days is None else age_days
    created_ms = int((time.time() - days * 86400) * 1000) if age_days is not None else None
    return {
        "id": f"{num:08d}-0000-0000-0000-{num:012d}",
        "role": role,
        "recordType": rtype,
        "title": f"record {num}",
        "createdAt": created_ms,
        "tags": list(tags if tags is not None else ["status:open"]),
    }


def make_fetch(list_records, point_overrides=None):
    """fetch(url_suffix) with `tag=status:open` list pages (single page) and
    point lookups (/api/agent-records/<id>) served from list_records plus
    point_overrides."""
    point = {r["id"]: r for r in list_records}
    point.update(point_overrides or {})

    def fetch(url_suffix):
        if "tag=status:open" in url_suffix:
            offset = int(url_suffix.split("offset=")[1])
            page = list_records[offset:offset + sweep.PAGE_LIMIT]
            return {"items": page, "total": len(list_records)}
        rid = url_suffix.rsplit("/", 1)[1]
        if rid in point:
            return point[rid]
        raise RuntimeError("404: " + url_suffix)

    return fetch


def make_runner(exit_by_id):
    calls = []

    def runner(cmd, **kwargs):
        cmdstr = " ".join(str(c) for c in cmd)
        calls.append(cmdstr)
        for rid, code in exit_by_id.items():
            if rid in cmdstr:
                return FakeResult(returncode=code, stdout="plan", stderr="" if code == 0 else "refused")
        raise AssertionError("unexpected command: " + cmdstr)

    runner.calls = calls
    return runner


def run_sweep(**overrides):
    buf = io.StringIO()
    tmp = Path(tempfile.mkdtemp())
    kwargs = dict(
        apply=False, min_age_days=14, max_candidates=25,
        nebula_base="http://neb", supersede_path=Path("/bin/true"),
        state_path=tmp / "state.json",
        fetch=make_fetch([]), runner=make_runner({}), out=buf,
    )
    kwargs.update(overrides)
    log_calls = []
    saved = sweep.post_change_log
    sweep.post_change_log = lambda t, b: log_calls.append((t, b)) or True
    try:
        rc = sweep.run_sweep(**kwargs)
    finally:
        sweep.post_change_log = saved
    state = None
    if kwargs["state_path"].exists():
        state = json.loads(kwargs["state_path"].read_text())
    return rc, buf.getvalue(), state, log_calls


# ── age parsing ──────────────────────────────────────────────────────────

def test_age_days_epoch_iso_and_garbage():
    from datetime import datetime, timezone
    now_s = 1790650512.126
    assert sweep._age_days(1790650512126 - 2 * 86400 * 1000, now_s) == 2.0
    iso = "2026-09-28T00:00:00Z"
    expected_ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    assert abs(sweep._age_days(iso, expected_ts + 86400 * 3) - 3.0) < 1e-6
    assert sweep._age_days(None, now_s) is None
    assert sweep._age_days(0, now_s) is None, "epoch 0 is the ATT bug — never an age"
    assert sweep._age_days("not-a-date", now_s) is None


# ── scan pagination ──────────────────────────────────────────────────────

def test_scan_paginates_until_short_page():
    records = [make_record(i) for i in range(7)]
    pages, calls = [], []

    def fetch(url_suffix):
        calls.append(url_suffix)
        offset = int(url_suffix.split("offset=")[1])
        page = records[offset:offset + sweep.PAGE_LIMIT]
        return {"items": page}

    saved_limit = sweep.PAGE_LIMIT
    sweep.PAGE_LIMIT = 3
    try:
        out = sweep.scan_open_records(fetch)
    finally:
        sweep.PAGE_LIMIT = saved_limit
    assert len(out) == 7 and len(calls) == 3, "walks until a short page"


def test_scan_respects_max_pages_cap():
    records = [make_record(i) for i in range(10)]
    saved = (sweep.PAGE_LIMIT, sweep.MAX_PAGES)
    sweep.PAGE_LIMIT, sweep.MAX_PAGES = 2, 2
    try:
        out = sweep.scan_open_records(lambda u: {"items": records[int(u.split("offset=")[1]):int(u.split("offset=")[1]) + 2]})
    finally:
        sweep.PAGE_LIMIT, sweep.MAX_PAGES = saved
    assert len(out) == 4, "hard stop at MAX_PAGES, not an infinite walk"


# ── staleness + exclusions ───────────────────────────────────────────────

def test_stale_filter_age_cutoff_and_exclusions():
    records = [
        make_record(1, age_days=1),                                  # fresh
        make_record(2, age_days=30),                                 # stale
        make_record(3, age_days=30, tags=["status:open", "type:archive"]),
        make_record(4, age_days=30, tags=["status:open", "type:incident"]),
        make_record(5, age_days=30, tags=["status:open", "status:in_progress"]),
        make_record(6, age_days=None),                               # AGE-UNKNOWN kept
    ]
    kept = sweep.stale_open_records(records, 14, time.time())
    ids = {r["id"] for r in kept}
    assert f"{2:012d}" in "".join(ids) and f"{6:012d}" in "".join(ids)
    assert len(kept) == 2, "fresh + excluded tags dropped, AGE-UNKNOWN kept"


# ── classification ───────────────────────────────────────────────────────

def test_classify_buckets():
    now_s = time.time()
    assert sweep.classify(make_record(1, rtype="analysis"), 30) == "RETIRE-PROPOSE"
    assert sweep.classify(make_record(2, rtype="inspection"), 30) == "RETIRE-PROPOSE"
    assert sweep.classify(make_record(3, rtype="engineering_log"), 30) == "RETIRE-PROPOSE"
    assert sweep.classify(
        make_record(4, rtype="assessment", tags=["status:open", "type:proposal"]), 30
    ) == "RETIRE-PROPOSE"
    assert sweep.classify(make_record(5, rtype="assessment", tags=["status:open"]), 30) == "MANUAL-REVIEW"
    assert sweep.classify(make_record(6, rtype="report"), 30) == "MANUAL-REVIEW"
    assert sweep.classify(make_record(7, rtype="architecture_note"), 30) == "MANUAL-REVIEW"
    assert sweep.classify(make_record(8, rtype="decision"), 30) == "MANUAL-REVIEW"
    assert sweep.classify(make_record(9, rtype="prompt"), 30) == "I4-EXEMPT"
    assert sweep.classify(make_record(10, rtype="response"), 30) == "I4-EXEMPT"
    assert sweep.classify(make_record(11, rtype="analysis", age_days=None), None) == "MANUAL-REVIEW"


# ── dry-run oracle ───────────────────────────────────────────────────────

def test_dry_run_oracle_exit_codes_and_readonly_flag():
    rid = "00000000-0000-0000-0000-000000000042"
    runner = make_runner({rid: 0})
    res = sweep.dry_run_retire(rid, Path("/bin/true"), 14, runner)
    assert res["exit"] == 0
    assert "--dry-run" in runner.calls[0], "proposals are validated read-only"
    assert "retire" in runner.calls[0] and rid in runner.calls[0]
    res2 = sweep.dry_run_retire(rid, Path("/bin/true"), 14, make_runner({rid: 2}))
    assert res2["exit"] == 2
    res3 = sweep.dry_run_retire(rid, Path("/bin/true"), 14, make_runner({rid: 1}))
    assert res3["exit"] == 1


# ── end-to-end sweep ─────────────────────────────────────────────────────

def test_apply_posts_evidence_once_then_dedups():
    analysis = make_record(1, rtype="analysis", age_days=30)
    report = make_record(2, rtype="report", age_days=30)
    fetch = make_fetch([analysis, report])
    runner = make_runner({analysis["id"]: 0, report["id"]: 0})
    rc, out, state, logs = run_sweep(apply=True, fetch=fetch, runner=runner)
    assert rc == 0
    assert len(logs) == 1, "one evidence post for the one NEW actionable candidate"
    title, body = logs[0]
    assert "stale status:open record(s)" in title
    assert f"retire --old {analysis['id']}" in body, "per-record proposed command"
    assert "OWNING ROLE" in body
    assert state["candidates"][analysis["id"]]["count"] == 1
    # second run: same world, same verdict — deduped, no second post
    rc2, out2, state2, logs2 = run_sweep(apply=True, fetch=fetch, runner=runner,
                                         state_path=state and Path(tempfile.mkdtemp()) / "x.json")
    # reuse the SAME state file to exercise dedup
    tmp = Path(tempfile.mkdtemp())
    sp = tmp / "s.json"
    rc1a, _, state1, _ = run_sweep(apply=True, fetch=fetch, runner=runner, state_path=sp)
    rc1b, out1b, state1b, logs1b = run_sweep(apply=True, fetch=fetch, runner=runner, state_path=sp)
    assert logs1b == [], "same stale candidate is not re-posted (dedup)"
    assert "no new actionable candidates" in out1b
    assert state1b["candidates"][analysis["id"]]["count"] == 1


def test_self_resolved_candidates_post_nothing():
    analysis = make_record(1, rtype="analysis", age_days=30)
    runner = make_runner({analysis["id"]: 2})  # dry-run policy refusal
    rc, out, state, logs = run_sweep(apply=True, fetch=make_fetch([analysis]), runner=runner)
    assert rc == 0 and logs == []
    assert "SELF-RESOLVED" in out and state["candidates"] == {}


def test_toctou_terminal_candidate_dropped():
    analysis = make_record(1, rtype="analysis", age_days=30)
    retired = dict(analysis, tags=["status:retired"])
    fetch = make_fetch([analysis], point_overrides={analysis["id"]: retired})
    runner = make_runner({analysis["id"]: 0})
    rc, out, state, logs = run_sweep(apply=True, fetch=fetch, runner=runner)
    assert rc == 0 and logs == [] and state["candidates"] == {}, \
        "went terminal between scan and post — not nagged"


def test_tool_error_is_exit_1_but_not_fatal_to_rest():
    a = make_record(1, rtype="analysis", age_days=30)
    b = make_record(2, rtype="inspection", age_days=30)  # RETIRE-PROPOSE too
    runner = make_runner({a["id"]: 1, b["id"]: 0})
    rc, out, state, logs = run_sweep(apply=True, fetch=make_fetch([a, b]), runner=runner)
    assert rc == 1, "validation tool errors surface"
    assert "TOOL-ERROR" in out
    assert len(logs) == 1, "the healthy candidate still posts"
    assert a["id"][:8] not in logs[0][1], "the errored candidate is not proposed"
    assert b["id"][:8] in logs[0][1]


def test_scan_failure_is_hard_error_no_state():
    def boom(url_suffix):
        raise RuntimeError("nebula down")
    tmp = Path(tempfile.mkdtemp())
    rc, out, _, _ = run_sweep(apply=True, fetch=boom, state_path=tmp / "s.json")
    assert rc == 2 and not (tmp / "s.json").exists()


def test_check_only_classifies_but_writes_nothing():
    analysis = make_record(1, rtype="analysis", age_days=30)
    tmp = Path(tempfile.mkdtemp())
    rc, out, state, logs = run_sweep(
        apply=False, fetch=make_fetch([analysis]), state_path=tmp / "s.json")
    assert rc == 0 and logs == [] and state is None
    assert "RETIRE-PROPOSE" in out and not (tmp / "s.json").exists()


def test_i4_exempt_listed_not_proposed():
    prompt = make_record(1, rtype="prompt", age_days=400)
    runner = make_runner({})
    rc, out, state, logs = run_sweep(apply=True, fetch=make_fetch([prompt]), runner=runner)
    assert rc == 0 and logs == [] and state["candidates"] == {}
    assert "I4-EXEMPT" in out
    assert runner.calls == [], "I4 records are never dry-run-validated for retire"


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
