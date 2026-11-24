"""Hermetic tests for bin/gate_name_namespace.py (ranking item 9, ruling 0633f18d).

The control is `test_control_corpus_detects_and_repairs_only_the_named_rows`:
one corpus holds a single-name row and a three-name row (both repaired), an
already-namespaced row, a withdrawn row and a numeric-gate-only row (all
untouched) — proving the tool detects exactly the legacy named-gate condition
and repairs exactly those rows, with zero false positives.

    python3 -m pytest bin/tests/test_gate_name_namespace.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate_name_namespace as gnn  # noqa: E402

AT = "2026-11-24T02:00:00Z"


def row(tags, rid="11111111-1111-1111-1111-111111111111", role="engineer",
        title="a record", content="body", metadata=None):
    return gnn.Row(rid, role, title, content, list(tags), dict(metadata or {}))


# ── classification ───────────────────────────────────────────────────────

def test_named_gate_gains_the_canonical_slug():
    plan = gnn.classify(row(["type:report", "gate:stage-2", "status:done"]))
    assert plan is not None and plan.action == "repair"
    assert plan.add_slugs == ["stage-2"]
    assert gnn.tags_after(plan) == ["type:report", "gate:stage-2", "status:done",
                                    "gate-name:stage-2"]


def test_gate_name_is_lowercased():
    plan = gnn.classify(row(["gate:G0"]))
    assert plan is not None and plan.add_slugs == ["g0"]


def test_numeric_gate_starts_with_a_digit_but_is_a_name():
    plan = gnn.classify(row(["gate:830-scope"]))
    assert plan is not None and plan.add_slugs == ["830-scope"]
    assert gnn.tags_after(plan) == ["gate:830-scope", "gate-name:830-scope"]


def test_every_name_on_a_multi_name_row_is_added():
    plan = gnn.classify(row(["gate:admission", "gate:compilation", "gate:cutover"]))
    assert plan is not None and plan.add_slugs == ["admission", "compilation", "cutover"]


def test_already_namespaced_row_is_coherent():
    plan = gnn.classify(row(["gate:stage-2", "gate-name:stage-2"]))
    assert plan is not None and plan.action == "coherent" and not plan.fixable


def test_partially_namespaced_multi_name_row_only_adds_the_missing_slugs():
    plan = gnn.classify(row(["gate:admission", "gate:cutover", "gate-name:admission"]))
    assert plan is not None and plan.add_slugs == ["cutover"]


def test_numeric_only_row_is_out_of_scope():
    assert gnn.classify(row(["gate:841", "pr:841"])) is None


def test_named_gate_never_gains_a_pr_tag():
    plan = gnn.classify(row(["gate:830-scope"]))
    assert all(not t.startswith("pr:") for t in gnn.tags_after(plan))


def test_withdrawn_row_is_held():
    plan = gnn.classify(row(["gate:stage-2", "status:superseded"]))
    assert plan is not None and plan.reason == "withdrawn" and not plan.fixable
    retired = gnn.classify(row(["gate:stage-2", "status:retired"]))
    assert retired is not None and retired.reason == "withdrawn"


def test_conflicting_case_of_an_existing_slug_is_held():
    plan = gnn.classify(row(["gate:G0", "gate-name:G0"]))
    assert plan is not None and plan.reason == "slug_case_conflict"


def test_receipt_names_gates_and_added_names():
    plan = gnn.classify(row(["gate:stage-2"]))
    rec = gnn.receipt(plan, AT)
    assert rec["gates"] == ["stage-2"]
    assert rec["gate_names"] == ["gate-name:stage-2"]
    assert rec["action"] == "added-gate-name"
    assert rec["convention"] == gnn.CONVENTION


# ── the control ──────────────────────────────────────────────────────────

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
            self.rows[rid] = self.corrupt(rec)
        return 200, {"id": rid}


def test_control_corpus_detects_and_repairs_only_the_named_rows():
    single = row(["gate:stage-2", "status:done"], rid="single")
    multi = row(["gate:admission", "gate:compilation", "gate:cutover"], rid="multi")
    already = row(["gate:tester", "gate-name:tester"], rid="already")
    withdrawn = row(["gate:stage-2", "status:superseded"], rid="old")
    numeric = row(["gate:841", "pr:841"], rid="numeric")
    corpus = [single, multi, already, withdrawn, numeric]

    plans = gnn.build_plans(corpus)
    assert {p.row.id: p.action for p in plans} == {
        "single": "repair", "multi": "repair", "already": "coherent", "old": "hold"}
    summary = gnn.summarize(plans)
    assert summary["named_gate_rows"] == 4 and summary["rows_to_repair"] == 2
    assert summary["names_to_add"] == 4 and summary["rows_coherent"] == 1
    assert summary["rows_held"] == 1

    neb = FakeNebula(corpus)
    out = gnn.apply_plans(plans, "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert sorted(out["verified"]) == ["multi", "single"] and out["failed"] == []
    assert neb.writes == 2                       # zero false positives
    assert "gate-name:stage-2" in neb.rows["single"]["tags"]
    assert {"gate-name:admission", "gate-name:compilation", "gate-name:cutover"} <= \
        set(neb.rows["multi"]["tags"])
    assert gnn.RECEIPT_KEY in neb.rows["single"]["metadata"]
    for untouched in ("already", "old", "numeric"):
        assert neb.rows[untouched] == FakeNebula([r for r in corpus if r.id == untouched]).rows[untouched]


# ── apply: verification, staleness, bounds ───────────────────────────────

def test_apply_keeps_the_legacy_gate_tag_and_the_body():
    r = row(["type:report", "gate:stage-2", "status:done"])
    neb = FakeNebula([r])
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [r.id]
    assert neb.rows[r.id]["tags"] == ["type:report", "gate:stage-2", "status:done",
                                      "gate-name:stage-2"]
    assert neb.rows[r.id]["content"] == "body"


def test_apply_fails_if_the_gate_name_tag_is_missing_after_write():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec,
                                               "tags": [t for t in rec["tags"]
                                                        if not t.startswith("gate-name:")]})
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "gate-name:stage-2 missing after write" in out["failed"][0]["error"]


def test_apply_fails_if_a_gate_tag_was_rewritten():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec,
                                               "tags": [t if not t.startswith("gate:") else "gate:stage-3"
                                                        for t in rec["tags"]]})
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "gate tags are" in out["failed"][0]["error"]


def test_apply_fails_if_another_tag_was_dropped():
    r = row(["gate:stage-2", "to:architect"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec,
                                               "tags": [t for t in rec["tags"] if t != "to:architect"]})
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "tags other than gate-name changed" in out["failed"][0]["error"]


def test_apply_fails_without_a_receipt():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "metadata": {}})
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "receipt missing" in out["failed"][0]["error"]


def test_apply_fails_when_the_body_changed():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "content": "rewritten"})
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "body changed" in out["failed"][0]["error"]


def test_apply_records_an_http_refusal():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r], status=422)
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [] and "HTTP 422" in out["failed"][0]["error"]


def test_apply_refuses_a_stale_row():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r])
    neb.rows[r.id]["tags"] = ["gate:stage-2", "to:tester"]
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0 and "stale" in out["failed"][0]["error"]


def test_apply_refuses_a_row_that_gained_the_slug_since_the_scan():
    r = row(["gate:stage-2"])
    neb = FakeNebula([r])
    neb.rows[r.id]["tags"] = ["gate:stage-2", "gate-name:stage-2"]
    out = gnn.apply_plans(gnn.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0 and "stale" in out["failed"][0]["error"]


def test_apply_is_bounded_and_writes_nothing_when_exceeded():
    rows = [row(["gate:stage-2"], rid=f"{i:08d}-0000-0000-0000-000000000000") for i in range(3)]
    neb = FakeNebula(rows)
    with pytest.raises(RuntimeError, match="exceed --max-apply"):
        gnn.apply_plans(gnn.build_plans(rows), "http://x", AT, max_apply=2,
                        fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0


def test_held_rows_are_never_written():
    bad = row(["gate:stage-2", "status:superseded"], rid="bad")
    good = row(["gate:tester"], rid="good")
    neb = FakeNebula([bad, good])
    out = gnn.apply_plans(gnn.build_plans([bad, good]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == ["good"] and neb.writes == 1


# ── idempotency, reporting, audit ────────────────────────────────────────

def test_rerun_after_apply_is_a_verified_no_op():
    rows = [row(["gate:stage-2"], rid="a"), row(["gate:admission"], rid="b")]
    neb = FakeNebula(rows)
    first = gnn.apply_plans(gnn.build_plans(rows), "http://x", AT, max_apply=10,
                            fetch=neb.fetch, write=neb.write)
    assert len(first["verified"]) == 2

    rescan = [gnn.Row(k, "engineer", v["title"], v["content"], v["tags"], v["metadata"])
              for k, v in neb.rows.items()]
    plans = gnn.build_plans(rescan)
    assert [p.action for p in plans] == ["coherent", "coherent"]
    assert gnn.apply_plans(plans, "http://x", AT, max_apply=10,
                           fetch=neb.fetch, write=neb.write) == \
        {"planned": 0, "verified": [], "failed": []}
    assert neb.writes == 2


def test_report_covers_every_row():
    plans = gnn.build_plans([
        row(["gate:stage-2"], rid="a"),
        row(["gate:tester", "gate-name:tester"], rid="b"),
        row(["gate:G0", "gate-name:G0"], rid="c"),
    ])
    lines = gnn.report_tsv(plans).strip().splitlines()
    assert lines[0].startswith("record_id\trole\tgates")
    assert any("\trepair\t" in ln for ln in lines[1:])
    assert any("\tcoherent\t" in ln for ln in lines[1:])
    assert any("slug_case_conflict" in ln for ln in lines[1:])


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_post_audit_reports_success_and_failure():
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Proc(0, "OK 201 thread=x")

    ok, detail = gnn.post_audit({"named_gate_rows": 1, "names_to_add": 1, "rows_held": 0,
                                 "rows_coherent": 0, "by_reason": {"repair": 1}},
                                {"verified": ["a"], "failed": []}, run=run)
    assert ok and "OK 201" in detail
    assert "--role" in seen["argv"] and seen["kw"]["cwd"] == str(gnn.BIN_DIR.parent)

    ok, detail = gnn.post_audit({"named_gate_rows": 0, "names_to_add": 0, "rows_held": 0,
                                 "rows_coherent": 0, "by_reason": {}},
                                {"verified": [], "failed": []},
                                run=lambda argv, **kw: _Proc(1, "", "assembly down"))
    assert not ok and "assembly down" in detail


# ── item 10: the shared classifier, and refusal instead of inference ──────

def test_dual_claim_row_is_held_by_the_namespace_tool_too():
    r = row(["gate:1003", "gate-name:1003"], rid="dual")
    plan = gnn.classify(r)
    assert plan is not None and plan.action == "hold"
    assert plan.reason == "gate_shape_conflict"


def test_partial_numeric_prefix_is_namespaced_as_a_name():
    plan = gnn.classify(row(["gate:830-scope"]))
    assert plan is not None and plan.action == "repair" and plan.add_slugs == ["830-scope"]


@pytest.mark.parametrize("tags,reason", [
    (["gate:"], "gate_shape_empty"),
    (["gate:stage-2 "], "gate_shape_malformed"),
])
def test_malformed_named_gates_are_held(tags, reason):
    plan = gnn.classify(row(tags))
    assert plan is not None and plan.action == "hold" and plan.reason == reason
