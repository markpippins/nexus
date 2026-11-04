#!/usr/bin/env python3
"""Hermetic tests for bin/inbox_pointer_watchdog.py.

No HTTP, no DB: every external call is an injected callable or a patched module
function. Dual-runnable: pytest-compatible functions plus
`python3 bin/tests/test_inbox_pointer_watchdog.py` (bin/tests/test_merge_pr.py
convention).

Pins the behaviour that makes the alarm trustworthy:
  - flagged = stale watermark AND unread mail; either alone stays quiet
  - a stale watermark with an empty backlog is NOT flagged (a dormant role with
    no mail is not a problem, and crying wolf trains everyone to ignore it)
  - a missing pointer is a real loss (ruling 1fe2049de), not an empty inbox
  - an unparseable watermark is treated as stale, never as "reviewed"
  - a failing backlog count never invents zero or a flag
  - the script is read-only: it issues GETs and nothing that can move a pointer
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE.parent / "inbox_pointer_watchdog.py"
_spec = importlib.util.spec_from_file_location("inbox_pointer_watchdog", MOD_PATH)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

NOW = datetime(2026, 11, 4, 1, 0, 0, tzinfo=timezone.utc)


def days_ago(n):
    return (NOW - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fake_backlog(counts, default=0):
    calls = []

    def fn(role, since):
        calls.append((role, since))
        return counts.get(role, default)

    fn.calls = calls
    return fn


# ── classification ─────────────────────────────────────────────────────


def test_stale_and_unread_is_flagged():
    out = wd.assess({"tester": days_ago(34)}, NOW, max_age_days=7,
                    backlog_fn=fake_backlog({"tester": 200}))
    assert len(out) == 1
    assert out[0]["flagged"] is True
    assert out[0]["stale"] is True
    assert out[0]["backlog"] == 200
    assert out[0]["ageDays"] == 34.0


def test_stale_but_empty_backlog_is_quiet():
    # A dormant role with nothing waiting is not an incident.
    out = wd.assess({"analyst": days_ago(40)}, NOW, max_age_days=7,
                    backlog_fn=fake_backlog({}))
    assert out[0]["stale"] is True
    assert out[0]["flagged"] is False
    assert out[0]["backlog"] == 0


def test_fresh_with_unread_is_not_flagged():
    out = wd.assess({"engineer": days_ago(1)}, NOW, max_age_days=7,
                    backlog_fn=fake_backlog({"engineer": 3}))
    assert out[0]["stale"] is False
    assert out[0]["flagged"] is False


def test_boundary_uses_max_age_days():
    # Exactly at the threshold is not stale; a second past it is.
    at = (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    past = (NOW - timedelta(days=7, seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    a = wd.assess({"r": at}, NOW, max_age_days=7, backlog_fn=fake_backlog({"r": 1}))
    b = wd.assess({"r": past}, NOW, max_age_days=7, backlog_fn=fake_backlog({"r": 1}))
    assert a[0]["stale"] is False and a[0]["flagged"] is False
    assert b[0]["stale"] is True and b[0]["flagged"] is True


def test_missing_pointer_is_a_loss_not_an_empty_inbox():
    out = wd.assess({"reviewer": None}, NOW, backlog_fn=fake_backlog({"reviewer": 5}))
    assert out[0]["pointerMissing"] is True
    assert out[0]["stale"] is True
    assert out[0]["flagged"] is True  # unread mail is not hidden by a null pointer


def test_unparseable_pointer_counts_as_stale():
    out = wd.assess({"tester": "not-a-timestamp"}, NOW, backlog_fn=fake_backlog({"tester": 2}))
    assert out[0]["stale"] is True
    assert out[0]["pointerUnparseable"] is True
    assert out[0]["flagged"] is True


def test_backlog_failure_never_invents_zero_or_a_flag():
    def boom(role, since):
        raise RuntimeError("nebula down")

    out = wd.assess({"tester": days_ago(40)}, NOW, backlog_fn=boom)
    assert out[0]["backlog"] is None
    assert "nebula down" in out[0]["backlogError"]
    assert out[0]["flagged"] is False


def test_role_filter_limits_output():
    pointers = {"tester": days_ago(40), "planner": days_ago(40)}
    out = wd.assess(pointers, NOW, backlog_fn=fake_backlog({}), roles=["tester"])
    assert [f["role"] for f in out] == ["tester"]


def test_role_filter_ignores_unknown_role():
    out = wd.assess({"tester": days_ago(1)}, NOW, backlog_fn=fake_backlog({}), roles=["nobody"])
    assert out == []


def test_all_roles_reported_not_only_flagged():
    # The report shows healthy mailboxes too; silence would be ambiguous.
    pointers = {"tester": days_ago(40), "engineer": days_ago(1)}
    out = wd.assess(pointers, NOW, backlog_fn=fake_backlog({"tester": 9, "engineer": 0}))
    assert [f["role"] for f in out] == ["engineer", "tester"]
    assert sum(1 for f in out if f["flagged"]) == 1


# ── parsing ────────────────────────────────────────────────────────────


def test_parse_iso_accepts_z_and_offsets():
    assert wd.parse_iso("2026-10-01T05:33:23.844Z") == wd.parse_iso("2026-10-01T05:33:23.844+00:00")
    assert wd.parse_iso("2026-10-01T00:33:23.844-05:00").hour == 5


def test_parse_iso_rejects_empty():
    for bad in ("", "   ", None):
        try:
            wd.parse_iso(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


# ── transport ──────────────────────────────────────────────────────────


def test_fetch_pointers_reads_wrapper_and_null():
    seen = {}

    def get(url):
        seen["url"] = url
        return {"pointers": {"tester": "2026-10-01T05:33:23.844Z", "dba": None}}

    out = wd.fetch_pointers("http://example:3101/", http_get=get)
    assert out == {"tester": "2026-10-01T05:33:23.844Z", "dba": None}
    assert seen["url"] == "http://example:3101/api/inbox-pointers"


class _InclusiveEndpoint:
    """Fakes /api/agent-records with the REAL comparison semantics.

    nebula implements `createdAfter` as `created_at >= $n` and `createdBefore`
    as `created_at <= $n` (routes.ts). Both are inclusive. A fake that ignored
    this is exactly how the inclusive-boundary defect survived the first test
    pass, so the regression cases below drive this fake instead of hand-feeding
    backlog numbers.
    """

    def __init__(self, timestamps):
        # Compared as INSTANTS, not strings: "...000Z" sorts above "...000900Z"
        # lexicographically (Z > 9), which would silently invert the boundary.
        self.timestamps = [wd.parse_iso(t) for t in timestamps]
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        after = query.get("createdAfter", [None])[0]
        before = query.get("createdBefore", [None])[0]
        rows = self.timestamps
        if after:
            bound = wd.parse_iso(after)
            rows = [t for t in rows if t >= bound]
        if before:
            bound = wd.parse_iso(before)
            rows = [t for t in rows if t <= bound]
        return {"items": [{"id": n} for n in range(len(rows))], "total": len(rows)}


def test_count_backlog_is_strict_about_the_boundary():
    # The regression the tester found: pointer == the newest READ record.
    # With an inclusive createdAfter the mailbox reports 1 unread forever.
    pointer = "2026-10-01T18:09:48.000Z"
    endpoint = _InclusiveEndpoint([
        "2026-09-01T00:00:00.000Z",
        pointer,                                # already read
    ])
    assert wd.count_backlog("http://example:3101", "critic", pointer,
                            http_get=endpoint) == 0


def test_count_backlog_counts_only_strictly_newer_records():
    pointer = "2026-10-01T18:09:48.000Z"
    endpoint = _InclusiveEndpoint([
        "2026-09-01T00:00:00.000Z",
        pointer,
        "2026-11-03T00:18:47.000Z",
        "2026-11-04T00:19:30.000Z",
    ])
    assert wd.count_backlog("http://example:3101", "tester", pointer,
                            http_get=endpoint) == 2


def test_count_backlog_covers_the_pointer_millisecond_and_nothing_after():
    # The watermark is ms-granular (the API reports epoch ms) while created_at is
    # microsecond-precision, so the record actually read sits INSIDE the
    # pointer's millisecond: `...48.981523` behind a pointer of `...48.981`.
    # That record is read; the next millisecond is not.
    pointer = "2026-10-01T18:09:48.981Z"
    endpoint = _InclusiveEndpoint([
        "2026-10-01T18:09:48.981523Z",   # the record the role read — covered
        "2026-10-01T18:09:48.981900Z",   # same millisecond — covered
        "2026-10-01T18:09:48.982000Z",   # next millisecond — UNREAD
    ])
    assert wd.count_backlog("http://example:3101", "critic", pointer,
                            http_get=endpoint) == 1


def test_caught_up_mailbox_is_quiet_even_with_microsecond_records():
    """The exact case the tester found on live data (critic, #856).

    Pointer `2026-10-01T18:09:48.981Z`; the only record is the one it read, at
    microsecond precision. Inclusive `createdAfter` reported backlog=1 forever.
    """
    pointer = "2026-10-01T18:09:48.981Z"
    endpoint = _InclusiveEndpoint(["2026-10-01T18:09:48.981523Z"])
    backlog = wd.count_backlog("http://example:3101", "critic", pointer,
                               http_get=endpoint)
    assert backlog == 0
    verdict = wd.assess({"critic": pointer}, NOW, backlog_fn=lambda r, s: backlog)[0]
    assert verdict["flagged"] is False


def test_count_backlog_sends_tag_on_both_queries():
    endpoint = _InclusiveEndpoint(["2026-10-01T05:33:23.844Z"])
    wd.count_backlog("http://example:3101", "tester", "2026-10-01T05:33:23.844Z",
                     http_get=endpoint)
    assert len(endpoint.urls) == 2
    for url in endpoint.urls:
        assert "tag=to%3Atester" in url
    assert "createdBefore=2026-10-01T05" in endpoint.urls[1]


def test_count_backlog_without_pointer_counts_whole_mailbox():
    endpoint = _InclusiveEndpoint(["2026-09-01T00:00:00.000Z"] * 7)
    assert wd.count_backlog("http://example:3101", "tester", None, http_get=endpoint) == 7
    # No watermark means nothing has been reviewed: one query, no date filter.
    assert len(endpoint.urls) == 1
    assert "createdBefore" not in endpoint.urls[0]
    assert "createdAfter" not in endpoint.urls[0]


def test_count_backlog_falls_back_to_item_count():
    def get(url):
        return {"items": [{"id": 1}, {"id": 2}]}

    # Two queries, each answering with 2 items and no `total` field.
    assert wd.count_backlog("http://example:3101", "tester", None, http_get=get) == 2


# ── CLI exit codes ─────────────────────────────────────────────────────


def test_main_exit_zero_when_nothing_flagged():
    orig = (wd.fetch_pointers, wd.count_backlog)
    wd.fetch_pointers = lambda base: {"tester": days_ago(1)}
    wd.count_backlog = lambda base, role, since: 0
    try:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = wd.main([])
    finally:
        wd.fetch_pointers, wd.count_backlog = orig
    assert code == 0


def test_main_exit_one_when_flagged_and_names_the_role():
    orig = (wd.fetch_pointers, wd.count_backlog)
    wd.fetch_pointers = lambda base: {"tester": days_ago(34)}
    wd.count_backlog = lambda base, role, since: 200
    try:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wd.main([])
    finally:
        wd.fetch_pointers, wd.count_backlog = orig
    assert code == 1
    assert "[FLAG ] tester" in out.getvalue()
    assert "tester" in err.getvalue()
    assert "check-inbox.sh" in err.getvalue()


def test_main_exit_two_when_lookup_fails():
    orig = wd.fetch_pointers

    def boom(base):
        raise RuntimeError("connection refused")

    wd.fetch_pointers = boom
    try:
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = wd.main([])
    finally:
        wd.fetch_pointers = orig
    assert code == 2
    assert "connection refused" in err.getvalue()


def test_main_json_shape():
    orig = (wd.fetch_pointers, wd.count_backlog)
    wd.fetch_pointers = lambda base: {"tester": days_ago(34)}
    wd.count_backlog = lambda base, role, since: 3
    try:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = wd.main(["--json"])
    finally:
        wd.fetch_pointers, wd.count_backlog = orig
    payload = json.loads(out.getvalue())
    assert code == 1
    assert payload["flagged"] == 1
    assert payload["checked"] == 1
    assert payload["roles"][0]["role"] == "tester"


# ── the read-only guarantee ────────────────────────────────────────────


def test_script_can_only_issue_gets():
    """The alarm must never be able to move a watermark.

    Advancing a pointer to clear a stale alarm would hide the mail the alarm
    exists to surface, so the write verbs are asserted absent from the source.
    """
    src = MOD_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in src.splitlines()
                     if not line.strip().startswith("#"))
    assert "urllib.request.Request(" in body
    for verb in ('"PUT"', "'PUT'", '"POST"', "'POST'", '"DELETE"', "'DELETE'",
                 "methods=", "urlopen(req, data="):
        assert verb not in body, f"watchdog must not issue {verb}"


def _run_all():
    failures = []
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, exc))
    for name, exc in failures:
        print(f"FAIL {name}: {exc}")
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
