"""Hermetic tests for bin/lifecycle_status_normalize.py (ranking item 7).

No DB, no HTTP: rows are fixtures and the write path takes injected fetch/write
callables, so the precedence ladder, the refusal class, read-back verification
and the no-op rerun are all deterministic.

    python3 -m pytest bin/tests/test_lifecycle_status_normalize.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lifecycle_status_normalize as lsn  # noqa: E402

AT = "2026-11-23T18:00:00Z"


def row(tags, rid="11111111-1111-1111-1111-111111111111", role="architect",
        title="T", content="body text", metadata=None):
    return lsn.Row(rid, role, title, content, list(tags), dict(metadata or {}))


# ── precedence ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("tags,expected", [
    (["status:active", "status:superseded"], "status:superseded"),
    (["status:held", "status:ruled"], "status:held"),
    (["status:resolved", "status:closed"], "status:closed"),
    (["status:ratified", "status:implementation-authorized"], "status:implementation-authorized"),
    (["status:merged", "status:verified"], "status:verified"),
    (["status:done", "status:merged"], "status:done"),
    (["status:open", "status:needs-response"], "status:open"),
])
def test_later_stage_wins(tags, expected):
    assert lsn.authoritative(tags) == expected


def test_single_or_absent_status_is_not_planned():
    assert lsn.authoritative(["status:done", "to:engineer"]) is None
    assert lsn.authoritative(["to:engineer"]) is None
    assert lsn.classify(row(["status:done"])) is None


def test_unknown_status_tag_is_refused_not_guessed():
    plan = lsn.classify(row(["status:done", "status:cosmic"]))
    assert plan is not None and plan.reason == "unknown_status_tag"
    assert not plan.actionable
    assert plan.dropped == ["status:cosmic", "status:done"]


def test_normalize_tags_drops_every_status_and_adds_exactly_one():
    out = lsn.normalize_tags(["to:engineer", "status:open", "status:needs-response", "pr:1"],
                             "status:open")
    assert out == ["to:engineer", "pr:1", "status:open"]


def test_receipt_names_the_pair_reason_and_the_dropped_tag():
    plan = lsn.classify(row(["status:active", "status:superseded"]))
    rec = lsn.receipt(plan, AT)
    assert rec["chosen"] == "status:superseded"
    assert rec["dropped"] == ["status:active"]
    assert "supersession is terminal" in rec["reason"]
    assert rec["convention"] == lsn.CONVENTION


# ── apply: verification, staleness, bounds ───────────────────────────────

class FakeNebula:
    def __init__(self, rows, *, corrupt=None, status=200):
        self.rows = {r.id: {"id": r.id, "role": r.role, "title": r.title,
                            "content": r.content, "tags": list(r.tags),
                            "metadata": json.loads(json.dumps(r.metadata))} for r in rows}
        self.corrupt = corrupt
        self.status = status
        self.writes = 0

    def fetch(self, _base, rid):
        return json.loads(json.dumps(self.rows[rid]))

    def write(self, _base, rid, patch):
        self.writes += 1
        if self.status != 200:
            return self.status, {"error": "nope"}
        rec = self.rows[rid]
        if patch.get("tags") is not None:
            rec["tags"] = list(patch["tags"])
        if patch.get("metadata") is not None:
            rec["metadata"] = json.loads(json.dumps(patch["metadata"]))
        if self.corrupt:
            rec = self.corrupt(rec)
            self.rows[rid] = rec
        return 200, {"id": rid}


def test_apply_writes_one_status_and_leaves_a_receipt():
    r = row(["to:engineer", "status:open", "status:needs-response", "pr:1"])
    neb = FakeNebula([r])
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [r.id] and out["failed"] == []
    after = neb.rows[r.id]
    assert [t for t in after["tags"] if t.startswith("status:")] == ["status:open"]
    assert "to:engineer" in after["tags"] and "pr:1" in after["tags"]
    assert after["metadata"]["record.status_normalized"]["dropped"] == ["status:needs-response"]
    assert after["content"] == "body text"


def test_apply_fails_when_a_second_status_survives():
    r = row(["status:done", "status:merged"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "tags": rec["tags"] + ["status:merged"]})
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == []
    assert "status tags are" in out["failed"][0]["error"]


def test_apply_fails_when_a_non_status_tag_disappears():
    r = row(["to:engineer", "status:done", "status:merged"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec,
                                               "tags": [t for t in rec["tags"] if t != "to:engineer"]})
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "non-status tags changed" in out["failed"][0]["error"]


def test_apply_fails_without_a_receipt():
    r = row(["status:done", "status:merged"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "metadata": {}})
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "receipt missing" in out["failed"][0]["error"]


def test_apply_fails_when_the_body_changed():
    r = row(["status:done", "status:merged"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "content": "rewritten"})
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "body changed" in out["failed"][0]["error"]


def test_apply_records_an_http_refusal():
    r = row(["status:done", "status:merged"])
    neb = FakeNebula([r], status=422)
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [] and "HTTP 422" in out["failed"][0]["error"]


def test_apply_refuses_a_stale_row_instead_of_clobbering_it():
    r = row(["status:done", "status:merged"])
    neb = FakeNebula([r])
    neb.rows[r.id]["tags"] = ["status:done", "status:merged", "pr:new"]
    out = lsn.apply_plans(lsn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0
    assert "stale" in out["failed"][0]["error"]
    assert "pr:new" in neb.rows[r.id]["tags"]


def test_apply_is_bounded_and_writes_nothing_when_exceeded():
    rows = [row(["status:done", "status:merged"], rid=f"{i:08d}-0000-0000-0000-000000000000")
            for i in range(3)]
    neb = FakeNebula(rows)
    with pytest.raises(RuntimeError, match="exceed --max-apply"):
        lsn.apply_plans(lsn.build_plans(rows), "http://x", AT, max_apply=2,
                        fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0


def test_refused_rows_are_never_written():
    bad = row(["status:done", "status:cosmic"], rid="bad")
    good = row(["status:done", "status:merged"], rid="good")
    neb = FakeNebula([bad, good])
    out = lsn.apply_plans(lsn.build_plans([bad, good]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == ["good"] and neb.writes == 1


# ── idempotency / reporting ──────────────────────────────────────────────

def test_rerun_after_apply_is_a_verified_no_op():
    rows = [row(["status:done", "status:merged"], rid="a"),
            row(["status:open", "status:needs-response"], rid="b")]
    neb = FakeNebula(rows)
    first = lsn.apply_plans(lsn.build_plans(rows), "http://x", AT, max_apply=10,
                            fetch=neb.fetch, write=neb.write)
    assert len(first["verified"]) == 2

    rescan = [lsn.Row(k, "architect", v["title"], v["content"], v["tags"], v["metadata"])
              for k, v in neb.rows.items()]
    plans = lsn.build_plans(rescan)
    assert plans == []
    assert lsn.apply_plans(plans, "http://x", AT, max_apply=10,
                           fetch=neb.fetch, write=neb.write) == \
        {"planned": 0, "verified": [], "failed": []}
    assert neb.writes == 2


def test_summary_and_report_cover_every_row():
    plans = lsn.build_plans([
        row(["status:done", "status:merged"], rid="a"),
        row(["status:done", "status:cosmic"], rid="b"),
    ])
    summary = lsn.summarize(plans)
    assert summary["multi_status_rows"] == 2
    assert summary["rows_to_write"] == 1 and summary["rows_refused"] == 1

    lines = lsn.report_tsv(plans).strip().splitlines()
    assert lines[0].startswith("record_id\trole\ttags_before")
    assert any("\twrite\tnormalize" in ln for ln in lines[1:])
    assert any("\trefuse\tunknown_status_tag" in ln for ln in lines[1:])


def test_ladder_ranks_every_pair_the_corpus_contains():
    for dropped, chosen in lsn.PAIR_REASONS:
        assert lsn.authoritative([dropped, chosen]) == lsn.LIFECYCLE_ORDER[
            min(lsn.LIFECYCLE_ORDER.index(dropped), lsn.LIFECYCLE_ORDER.index(chosen))]
    # every ladder entry is a status tag and unique
    assert len(set(lsn.LIFECYCLE_ORDER)) == len(lsn.LIFECYCLE_ORDER)
    assert all(t.startswith("status:") for t in lsn.LIFECYCLE_ORDER)


# ── audit posting is not allowed to fail silently ────────────────────────

class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_post_audit_reports_success():
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Proc(0, "OK 201 thread=x")

    ok, detail = lsn.post_audit({"multi_status_rows": 1, "rows_refused": 0,
                                 "by_pair": {"status:active -> status:superseded": 1}},
                                {"verified": ["a"], "failed": []}, run=run)
    assert ok and "OK 201" in detail
    assert seen["argv"][0].endswith("post-change-log.sh")
    assert "--role" in seen["argv"] and "engineer" in seen["argv"]
    assert seen["kw"]["cwd"] == str(lsn.BIN_DIR.parent)


def test_post_audit_surfaces_a_failed_poster():
    ok, detail = lsn.post_audit({"multi_status_rows": 1, "rows_refused": 0, "by_pair": {}},
                                {"verified": ["a"], "failed": []},
                                run=lambda argv, **kw: _Proc(1, "", "assembly down"))
    assert not ok and "assembly down" in detail


def test_post_audit_surfaces_a_missing_poster():
    def run(argv, **kw):
        raise OSError("no such file")

    ok, detail = lsn.post_audit({"multi_status_rows": 0, "rows_refused": 0, "by_pair": {}},
                                {"verified": [], "failed": []}, run=run)
    assert not ok and "no such file" in detail
