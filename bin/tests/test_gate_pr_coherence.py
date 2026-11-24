"""Hermetic tests for bin/gate_pr_coherence_repair.py (ranking item 8, ruling 086cc2d1).

The control is `test_control_corpus_detects_and_repairs_only_the_fixable_rows`:
one seeded corpus holds a gate-only row (must be repaired), a coherent row
(must stay byte-identically untouched), a named-gate row and a gate/pr
mismatch (must be held) — proving the mechanism detects every coherence
problem in the corpus and repairs exactly the addable subset, with zero false
positives.

    python3 -m pytest bin/tests/test_gate_pr_coherence.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate_pr_coherence_repair as gpc  # noqa: E402

AT = "2026-11-23T22:00:00Z"


def row(tags, title="TESTER ATTESTATION", rid="11111111-1111-1111-1111-111111111111",
        role="tester", content="body", metadata=None):
    return gpc.Row(rid, role, title, content, list(tags), dict(metadata or {}))


# ── classification ───────────────────────────────────────────────────────

def test_gate_only_numeric_row_is_repaired():
    plan = gpc.classify(row(["type:approval", "gate:841", "status:done"]))
    assert plan is not None and plan.action == "repair" and plan.add_pr == "841"


def test_coherent_row_is_untouched():
    plan = gpc.classify(row(["gate:841", "pr:841"]))
    assert plan is not None and plan.action == "coherent" and not plan.fixable


def test_named_gate_is_held_not_guessed():
    for gate in ("stage-2", "tester", "G0", "830-scope", "lease"):
        plan = gpc.classify(row([f"gate:{gate}"]))
        assert plan is not None and plan.action == "hold"
        assert plan.reason == "gate_not_a_pr"


def test_gate_pr_mismatch_is_held():
    plan = gpc.classify(row(["gate:841", "pr:842"]))
    assert plan is not None and plan.reason == "gate_pr_mismatch" and not plan.fixable


def test_title_naming_a_different_pr_holds_the_row():
    plan = gpc.classify(row(["gate:841"], title="ATTESTATION for PR #900"))
    assert plan is not None and plan.reason == "title_contradicts_gate"


def test_title_naming_several_prs_including_the_gate_is_repaired():
    plan = gpc.classify(row(["gate:835"], title="#835/#836/#837 CI gating"))
    assert plan is not None and plan.action == "repair"


def test_withdrawn_row_is_held():
    plan = gpc.classify(row(["gate:841", "status:superseded"]))
    assert plan is not None and plan.reason == "withdrawn"
    retired = gpc.classify(row(["gate:841", "status:retired"]))
    assert retired is not None and retired.reason == "withdrawn"


def test_row_without_a_gate_tag_is_out_of_scope():
    assert gpc.classify(row(["pr:841", "status:done"])) is None


def test_receipt_names_gate_pr_action_and_ruling():
    plan = gpc.classify(row(["gate:841"]))
    rec = gpc.receipt(plan, AT)
    assert rec["pr"] == "841" and rec["prs_added"] == ["841"] and rec["gates"] == ["841"]
    assert rec["action"] == "added-pr-from-gate"
    assert rec["convention"] == gpc.CONVENTION
    assert rec["performed_by"] == "gate_pr_coherence_repair"


def test_repair_patch_is_additive_only():
    plan = gpc.classify(row(["type:approval", "gate:841", "status:done"], metadata={"x": 1}))
    assert gpc.tags_after(plan) == ["type:approval", "gate:841", "status:done", "pr:841"]
    meta = gpc.metadata_after(plan, AT)
    assert meta["x"] == 1 and gpc.RECEIPT_KEY in meta


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


def test_control_corpus_detects_and_repairs_only_the_fixable_rows():
    fixable = row(["type:approval", "gate:841", "status:done"], rid="fix")
    coherent = row(["gate:842", "pr:842", "status:done"], rid="ok")
    named = row(["gate:stage-2", "pr:1006"], rid="named")
    mismatch = row(["gate:843", "pr:999"], rid="mismatch")
    withdrawn = row(["gate:844", "status:superseded"], rid="old")
    corpus = [fixable, coherent, named, mismatch, withdrawn]

    plans = gpc.build_plans(corpus)
    actions = {p.row.id: p.action for p in plans}
    assert actions == {"fix": "repair", "ok": "coherent", "named": "hold",
                       "mismatch": "hold", "old": "hold"}
    summary = gpc.summarize(plans)
    assert summary["rows_to_repair"] == 1 and summary["rows_held"] == 3
    assert summary["rows_coherent"] == 1

    neb = FakeNebula(corpus)
    out = gpc.apply_plans(plans, "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == ["fix"] and out["failed"] == []
    assert neb.writes == 1                      # zero false positives
    assert "pr:841" in neb.rows["fix"]["tags"]
    assert gpc.RECEIPT_KEY in neb.rows["fix"]["metadata"]
    for untouched in ("ok", "named", "mismatch", "old"):
        assert neb.rows[untouched] == FakeNebula([r for r in corpus if r.id == untouched]).rows[untouched]


# ── apply: verification, staleness, bounds ───────────────────────────────

def test_apply_keeps_the_gate_tag_and_the_body():
    r = row(["type:approval", "gate:841", "status:done"])
    neb = FakeNebula([r])
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [r.id]
    assert neb.rows[r.id]["tags"] == ["type:approval", "gate:841", "status:done", "pr:841"]
    assert neb.rows[r.id]["content"] == "body"


def test_apply_fails_if_the_gate_tag_disappears():
    r = row(["gate:841"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "tags": [t for t in rec["tags"] if not t.startswith("gate:")]})
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "gate tags are" in out["failed"][0]["error"]


def test_apply_fails_if_the_pr_tag_is_missing_after_write():
    r = row(["gate:841"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "tags": [t for t in rec["tags"] if not t.startswith("pr:")]})
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "pr:841 missing after write" in out["failed"][0]["error"]


def test_apply_fails_without_a_receipt():
    r = row(["gate:841"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "metadata": {}})
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "receipt missing" in out["failed"][0]["error"]


def test_apply_fails_when_an_unrelated_tag_was_dropped():
    r = row(["gate:841", "status:done", "to:architect"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec,
                                               "tags": [t for t in rec["tags"] if t != "to:architect"]})
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "non-pr tags changed" in out["failed"][0]["error"]


def test_apply_fails_when_the_body_changed():
    r = row(["gate:841"])
    neb = FakeNebula([r], corrupt=lambda rec: {**rec, "content": "rewritten"})
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "body changed" in out["failed"][0]["error"]


def test_apply_records_an_http_refusal():
    r = row(["gate:841"])
    neb = FakeNebula([r], status=422)
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [] and "HTTP 422" in out["failed"][0]["error"]


def test_apply_refuses_a_stale_row_instead_of_clobbering_it():
    r = row(["gate:841"])
    neb = FakeNebula([r])
    neb.rows[r.id]["tags"] = ["gate:841", "to:tester"]
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0 and "stale" in out["failed"][0]["error"]
    assert neb.rows[r.id]["tags"] == ["gate:841", "to:tester"]


def test_apply_refuses_a_row_that_gained_a_pr_tag_since_the_scan():  # digest guard
    r = row(["gate:841"])
    neb = FakeNebula([r])
    neb.rows[r.id]["tags"] = ["gate:841", "pr:841"]
    neb.rows[r.id]["metadata"] = {}
    out = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0 and "stale" in out["failed"][0]["error"]
    assert "pr:841" in neb.rows[r.id]["tags"]


def test_apply_is_bounded_and_writes_nothing_when_exceeded():
    rows = [row(["gate:900"], rid=f"{i:08d}-0000-0000-0000-000000000000") for i in range(3)]
    neb = FakeNebula(rows)
    with pytest.raises(RuntimeError, match="exceed --max-apply"):
        gpc.apply_plans(gpc.build_plans(rows), "http://x", AT, max_apply=2,
                        fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0


def test_held_rows_are_never_written():
    bad = row(["gate:stage-2"], rid="bad")
    good = row(["gate:841"], rid="good")
    neb = FakeNebula([bad, good])
    out = gpc.apply_plans(gpc.build_plans([bad, good]), "http://x", AT, max_apply=10,
                          fetch=neb.fetch, write=neb.write)
    assert out["verified"] == ["good"] and neb.writes == 1


# ── idempotency, reporting, audit ────────────────────────────────────────

def test_rerun_after_apply_is_a_verified_no_op():
    rows = [row(["gate:841"], rid="a"), row(["gate:842"], rid="b")]
    neb = FakeNebula(rows)
    first = gpc.apply_plans(gpc.build_plans(rows), "http://x", AT, max_apply=10,
                            fetch=neb.fetch, write=neb.write)
    assert len(first["verified"]) == 2

    rescan = [gpc.Row(k, "tester", v["title"], v["content"], v["tags"], v["metadata"])
              for k, v in neb.rows.items()]
    plans = gpc.build_plans(rescan)
    assert [p.action for p in plans] == ["coherent", "coherent"]
    assert gpc.apply_plans(plans, "http://x", AT, max_apply=10,
                           fetch=neb.fetch, write=neb.write) == \
        {"planned": 0, "verified": [], "failed": []}
    assert neb.writes == 2


def test_summary_and_report_cover_every_row():
    plans = gpc.build_plans([
        row(["gate:841"], rid="a"),
        row(["gate:842", "pr:842"], rid="b"),
        row(["gate:stage-2"], rid="c"),
        row(["gate:843", "pr:999"], rid="d"),
    ])
    summary = gpc.summarize(plans)
    assert summary["gate_rows"] == 4 and summary["rows_to_repair"] == 1
    assert summary["rows_held"] == 2 and summary["rows_coherent"] == 1
    assert summary["by_reason"]["gate_pr_mismatch"] == 1

    lines = gpc.report_tsv(plans).strip().splitlines()
    assert lines[0].startswith("record_id\trole\tgate")
    assert any("\trepair\t" in ln for ln in lines[1:])
    assert any("gate_not_a_pr" in ln for ln in lines[1:])


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_post_audit_reports_success_and_failure():
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Proc(0, "OK 201 thread=x")

    ok, detail = gpc.post_audit({"gate_rows": 1, "rows_held": 0, "rows_coherent": 0,
                                 "by_reason": {"repair": 1}},
                                {"verified": ["a"], "failed": []}, run=run)
    assert ok and "OK 201" in detail
    assert "--role" in seen["argv"] and seen["kw"]["cwd"] == str(gpc.BIN_DIR.parent)

    ok, detail = gpc.post_audit({"gate_rows": 1, "rows_held": 0, "rows_coherent": 0, "by_reason": {}},
                                {"verified": ["a"], "failed": []},
                                run=lambda argv, **kw: _Proc(1, "", "assembly down"))
    assert not ok and "assembly down" in detail


# ── multi-gate rows: coherence is judged per gate value (item 9 defect fix) ─

def test_multi_gate_row_with_no_prs_mirrors_every_gate():
    r = row(["type:report", "gate:835", "gate:836", "gate:837"], rid="multi")
    plan = gpc.classify(r)
    assert plan is not None and plan.action == "repair"
    assert plan.add_prs == ["835", "836", "837"]
    assert gpc.tags_after(plan) == ["type:report", "gate:835", "gate:836", "gate:837",
                                    "pr:835", "pr:836", "pr:837"]


def test_partially_mirrored_multi_gate_row_is_still_detected():
    # The item-8 defect: pr:835 exists, so the old "row has a pr" test called
    # this coherent and gates 836/837 stayed unmirrored.
    r = row(["gate:835", "gate:836", "gate:837", "pr:835"], rid="partial")
    plan = gpc.classify(r)
    assert plan is not None and plan.action == "repair"
    assert plan.add_prs == ["836", "837"]


def test_fully_mirrored_multi_gate_row_is_coherent():
    r = row(["gate:835", "gate:836", "pr:835", "pr:836"])
    plan = gpc.classify(r)
    assert plan is not None and plan.action == "coherent" and not plan.fixable


def test_prs_disjoint_from_every_gate_is_a_mismatch():
    plan = gpc.classify(row(["gate:835", "gate:836", "pr:900"]))
    assert plan is not None and plan.reason == "gate_pr_mismatch"


def test_multi_gate_rerun_is_a_no_op():
    r = row(["gate:835", "gate:836"], rid="m")
    neb = FakeNebula([r])
    first = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                            fetch=neb.fetch, write=neb.write)
    assert first["verified"] == ["m"]
    rescan = [gpc.Row(k, "tester", v["title"], v["content"], v["tags"], v["metadata"])
              for k, v in neb.rows.items()]
    assert [p.action for p in gpc.build_plans(rescan)] == ["coherent"]


# ── item 10: the shared classifier, and refusal instead of inference ──────

def test_leading_zero_gate_is_the_same_pr_as_its_mirror():
    # ruling 4963dee4 fork 2: gate:0830 reads as PR 830, so pr:830 satisfies it
    # and the tool never writes a second, zero-padded spelling.
    plan = gpc.classify(row(["gate:0830", "pr:830"]))
    assert plan is not None and plan.action == "coherent" and not plan.fixable


def test_partial_numeric_prefix_is_a_name_not_a_pr():
    plan = gpc.classify(row(["gate:830-scope", "gate-name:830-scope"]))
    assert plan is not None and plan.action == "hold" and plan.reason == "gate_not_a_pr"
    assert plan.add_prs == []


def test_dual_claim_row_is_held_and_never_gains_a_pr():
    r = row(["gate:1003", "gate-name:1003"], rid="dual")
    plan = gpc.classify(r)
    assert plan is not None and plan.action == "hold"
    assert plan.reason == "gate_shape_conflict"
    neb = FakeNebula([r])
    result = gpc.apply_plans(gpc.build_plans([r]), "http://x", AT, max_apply=10,
                             fetch=neb.fetch, write=neb.write)
    assert result == {"planned": 0, "verified": [], "failed": []}
    assert neb.rows["dual"]["tags"] == ["gate:1003", "gate-name:1003"]


@pytest.mark.parametrize("tags,reason", [
    (["gate:"], "gate_shape_empty"),
    (["gate: 830"], "gate_shape_malformed"),
])
def test_malformed_gate_values_are_held(tags, reason):
    plan = gpc.classify(row(tags))
    assert plan is not None and plan.action == "hold" and plan.reason == reason
