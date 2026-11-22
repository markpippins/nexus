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


def make_record(num, role="engineer-ii", rtype="analysis", age_days=30.0, tags=None, title=None):
    days = 0.0 if age_days is None else age_days
    created_ms = int((time.time() - days * 86400) * 1000) if age_days is not None else None
    return {
        "id": f"{num:08d}-0000-0000-0000-{num:012d}",
        "role": role,
        "recordType": rtype,
        "title": title if title is not None else f"record {num}",
        "createdAt": created_ms,
        "tags": list(tags if tags is not None else ["status:open"]),
    }


def make_tag_fetch(by_tag, point_overrides=None):
    """fetch(url_suffix) serving one page per tag from `by_tag`, plus point
    lookups (used by the TOCTOU guard) over every row in every bucket."""
    point = {r["id"]: r for rows in by_tag.values() for r in rows}
    point.update(point_overrides or {})

    def fetch(url_suffix):
        if "/api/agent-records/" not in url_suffix:
            for tag, rows in by_tag.items():
                if f"tag={tag}" in url_suffix:
                    offset = int(url_suffix.split("offset=")[1])
                    return {"items": rows[offset:offset + sweep.PAGE_LIMIT]}
            return {"items": []}
        rid = url_suffix.rsplit("/", 1)[1]
        if rid in point:
            return point[rid]
        raise RuntimeError("404: " + url_suffix)

    return fetch


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


# ── rule 5: ask rows on merged/closed PRs ───────────────────────────────

def test_pr_numbers_prefers_tag_then_title():
    assert sweep.pr_numbers(make_record(1, tags=["status:open", "pr:1028"])) == ([1028], "tag")
    assert sweep.pr_numbers(
        make_record(2, tags=["status:open", "pr:1031", "pr:1028"])) == ([1028, 1031], "tag")
    four = make_record(3, tags=["status:open"],
                       title="Attestation request: four PRs green (#613 #616 #618 #620)")
    assert sweep.pr_numbers(four) == ([613, 616, 618, 620], "title")
    assert sweep.pr_numbers(
        make_record(4, tags=["status:open"], title="ATTESTATION REQUEST — PR tooling @ 7f43c6e6d")
    ) == ([], "none")
    assert sweep.pr_numbers(
        make_record(5, tags=["status:open"], title="#623 (0008 reconcile) and #624")
    ) == ([623, 624], "title"), "a migration number (#0008) is not a PR"


def test_ask_rows_needs_request_shape_and_no_terminal_status():
    rows = [
        make_record(1, tags=["to:tester", "type:verification-request", "pr:1"]),
        make_record(2, tags=["to:tester", "type:request", "gate:1", "status:open"]),
        # status-update nudge: asks the tester, but is not a request row
        make_record(3, tags=["to:tester", "type:status-update", "pr:1"]),
        # already closed out by the janitor
        make_record(4, tags=["to:tester", "type:request", "pr:1", "status:superseded"]),
        # not addressed to the tester
        make_record(5, tags=["to:architect", "type:request", "pr:1"]),
    ]
    assert [r["id"][:8] for r in sweep.ask_rows(rows)] == ["00000001", "00000002"]


def test_ask_plan_holds_open_unknown_and_numberless_rows():
    states = {1028: "OPEN", 999: "NOT-FOUND", 848: "MERGED"}
    no_att = lambda pr: (None, "no-attestation")
    p = sweep.ask_plan(make_record(1, tags=["to:tester", "type:request", "pr:1028"]),
                       states.get, no_att, time.time())
    assert p["verdict"] == "HOLD-pr-open" and "#1028" in p["reason"]
    p = sweep.ask_plan(make_record(2, tags=["to:tester", "type:request", "pr:999"]),
                       states.get, no_att, time.time())
    assert p["verdict"] == "HOLD-pr-unknown", "an unknown PR is never assumed dead"
    p = sweep.ask_plan(make_record(3, tags=["to:tester", "type:request"], title="V177 audit"),
                       states.get, no_att, time.time())
    assert p["verdict"] == "HOLD-no-pr" and p["prs"] == []


def test_ask_plan_supersedes_under_live_attestation_and_retires_otherwise():
    att = make_record(21, role="tester", rtype="assessment", age_days=40,
                      tags=["type:approval", "status:done", "gate:848"])
    row = make_record(1, tags=["to:tester", "type:request", "pr:848"])
    p = sweep.ask_plan(row, {848: "MERGED"}.get, lambda pr: (att, "serviced"), time.time())
    assert p["verdict"] == "SUPERSEDE" and p["attestation"]["id"] == att["id"]
    assert "serviced by attestation" in p["reason"]
    p = sweep.ask_plan(row, {848: "MERGED"}.get, lambda pr: (None, "only-withdrawn"), time.time())
    assert p["verdict"] == "RETIRE", "a withdrawn-only ask retires, never supersedes"
    assert "only-withdrawn" in p["reason"]


def test_ask_plan_multi_pr_needs_every_pr_closed():
    row = make_record(1, tags=["to:tester", "type:request"], title="#623 and #624")
    att = make_record(21, role="tester", rtype="assessment", tags=["type:attestation", "gate:623"])
    mixed = {623: "MERGED", 624: "OPEN"}.get
    p = sweep.ask_plan(row, mixed, lambda pr: (att, "serviced"), time.time())
    assert p["verdict"] == "HOLD-pr-open" and "#624" in p["reason"]
    both = {623: "MERGED", 624: "CLOSED"}.get
    p = sweep.ask_plan(row, both, lambda pr: (att, "serviced"), time.time())
    assert p["verdict"] == "SUPERSEDE" and p["prs"] == [623, 624]


def test_dry_run_ask_close_picks_mode_and_is_readonly():
    att = make_record(21, role="tester", tags=["type:attestation"])
    row = make_record(1, tags=["to:tester", "type:request", "pr:848"])
    sup = {"id": row["id"], "attestation": att, "reason": "why"}
    runner = make_runner({row["id"]: 0})
    res = sweep.dry_run_ask_close(sup, Path("/bin/true"), runner)
    cmd = runner.calls[0]
    assert res["oracle"] == "ACTIONABLE"
    assert " supersede " in cmd and f"--new {att['id']}" in cmd and "--dry-run" in cmd
    assert "--reason" in cmd, "supersede takes --reason, not --note"

    ret = {"id": row["id"], "attestation": None, "reason": "why"}
    runner2 = make_runner({row["id"]: 2})
    res2 = sweep.dry_run_ask_close(ret, Path("/bin/true"), runner2)
    cmd2 = runner2.calls[0]
    assert res2["oracle"] == "SELF-RESOLVED"
    assert " retire " in cmd2 and "--note" in cmd2 and "--dry-run" in cmd2
    assert "--new" not in cmd2
    assert sweep.dry_run_ask_close(ret, Path("/bin/true"), make_runner({row["id"]: 1}))["oracle"] == "TOOL-ERROR"


def _ask_world():
    row = make_record(11, role="engineer", rtype="assessment", age_days=60,
                      tags=["to:tester", "type:verification-request", "pr:848"],
                      title="Verification request — #848 schema suite")
    ret_row = make_record(12, role="engineer", rtype="assessment", age_days=60,
                          tags=["to:tester", "type:request", "pr:850"],
                          title="Attestation request: PR #850")
    open_row = make_record(13, role="engineer", rtype="request", age_days=2,
                           tags=["to:tester", "type:request", "pr:1040"])
    att = make_record(21, role="tester", rtype="assessment", age_days=50,
                      tags=["type:approval", "status:done", "gate:848"])
    # status:open scan sees none of the ask rows (42 of 53 live ones lack it)
    fetch = make_tag_fetch({"to:tester": [row, ret_row, open_row], "status:open": [],
                            "pr:848": [att], "gate:848": [att],
                            "pr:850": [], "gate:850": [], "pr:1040": [], "gate:1040": []})
    states = {848: "MERGED", 850: "MERGED", 1040: "OPEN"}.get

    def atts(pr):
        return (att, "serviced") if pr == 848 else (None, "no-attestation")

    return fetch, states, atts, row, ret_row, open_row


def test_ask_rule_check_only_prints_a_verdict_per_row_and_writes_nothing():
    fetch, states, atts, row, ret_row, open_row = _ask_world()
    tmp = Path(tempfile.mkdtemp())
    runner = make_runner({row["id"]: 0, ret_row["id"]: 0})
    rc, out, state, logs = run_sweep(
        apply=False, fetch=fetch, runner=runner, pr_state=states,
        attestations_for=atts, state_path=tmp / "s.json")
    assert rc == 0 and logs == [] and state is None and not (tmp / "s.json").exists()
    assert "SUPERSEDE" in out and "RETIRE" in out and "HOLD-pr-open" in out
    assert row["id"][:8] in out and open_row["id"][:8] in out
    assert "3 open to:tester ask row(s)" in out, "the ask scan is its own census"
    assert all("--dry-run" in c for c in runner.calls), "verdicts are validated read-only"
    assert len(runner.calls) == 2, "HOLD rows are never validated"


def test_still_asking_guard_does_not_require_status_open():
    row = make_record(1, tags=["to:tester", "type:request", "pr:848"])
    gone = make_record(2, tags=["to:tester", "type:request", "status:superseded"])
    fetch = make_tag_fetch({"to:tester": [row, gone]})
    assert sweep._still_open(row["id"], fetch) is False, \
        "a status:open guard discards 42 of 53 live ask rows — the trap"
    assert sweep._still_asking(row["id"], fetch) is True
    assert sweep._still_asking(gone["id"], fetch) is False


def test_ask_rule_posts_new_actionable_once_then_dedups():
    fetch, states, atts, row, ret_row, open_row = _ask_world()
    sp = Path(tempfile.mkdtemp()) / "s.json"
    runner = make_runner({row["id"]: 0, ret_row["id"]: 0})
    rc, out, state, logs = run_sweep(
        apply=True, fetch=fetch, runner=runner, pr_state=states,
        attestations_for=atts, state_path=sp)
    assert rc == 0 and len(logs) == 1
    title, body = logs[0]
    assert "ask-close" in title, "the ask evidence post is its own claim, not the stale-record one"
    assert f"supersede --old {row['id']} --new " in body
    assert f"retire --old {ret_row['id']}" in body
    assert state["candidates"]["ask:" + row["id"]]["count"] == 1
    assert state["runs"][-1]["ask_rows"] == 3 and state["runs"][-1]["ask_new"] == 2
    rc2, out2, state2, logs2 = run_sweep(
        apply=True, fetch=fetch, runner=runner, pr_state=states,
        attestations_for=atts, state_path=sp)
    assert logs2 == [], "same two ask rows are not re-posted"
    assert "no new oracle-confirmed ask rows" in out2


def test_make_attestations_for_uses_the_gate_contract():
    """Real merge_pr predicates: shape, withdrawal, and PR mention."""
    live = make_record(21, role="tester", rtype="assessment", age_days=50,
                       tags=["type:approval", "status:done", "gate:848"])
    withdrawn = make_record(22, role="tester", rtype="assessment", age_days=10,
                            tags=["type:approval", "status:done", "status:retired", "gate:848"])
    intent = make_record(23, role="tester", rtype="engineering_log", age_days=5,
                         tags=["gate:848"])  # 'I will attest' is not an attestation
    fetch = make_tag_fetch({"gate:848": [live, withdrawn, intent], "pr:848": [],
                            "gate:777": [withdrawn], "pr:777": []})
    atts = sweep.make_attestations_for(fetch)
    assert atts(848)[0]["id"] == live["id"], "newest LIVE attestation wins, intent ignored"
    assert atts(848)[1] == "serviced"
    assert atts(777) == (None, "only-withdrawn"), "a tombstone is never a successor"


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
