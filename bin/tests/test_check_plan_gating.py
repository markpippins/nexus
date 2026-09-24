"""Hermetic tests for bin/check_plan_gating.py (W-B4 evidence prototype).

Covers the two-facet gating classifier, condition evaluation (with the
psql/urllib boundaries faked), and spec-consistency matching. No
database, no conduit: _psql_scalar and load_conduit_state are monkey-
patched; the live spec file is validated structurally only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # worktree root


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load("check_plan_gating", REPO / "bin" / "check_plan_gating.py")


def entry(derived="PLAN_CREATE", builder="expired", reviewer=None):
    tickets = {"builder": {"status": builder}}
    if reviewer:
        tickets["reviewer"] = {"status": reviewer}
    return {"derived": derived, "tickets": tickets}


class TestClassify:
    def test_ready_and_open_startable(self):
        verdicts = [{"id": "c", "met": True}]
        gating, f = G.classify({}, verdicts, entry(builder="open"))
        assert gating == "STARTABLE" and f == []

    def test_ready_expired_blocked_but_open(self):
        verdicts = [{"id": "c", "met": True}]
        gating, f = G.classify({}, verdicts, entry(builder="expired"))
        assert gating == "BLOCKED-BUT-OPEN"
        assert "no respawn" in f[0]

    def test_blocked_with_expired_ticket_is_exhibit(self):
        verdicts = [{"id": "c", "met": False}]
        gating, f = G.classify({}, verdicts, entry(builder="expired"))
        assert gating == "BLOCKED-AND-OPEN"
        assert "W-B4 exhibit" in f[0]

    def test_blocked_no_ticket_blocked_closed(self):
        verdicts = [{"id": "c", "met": False}]
        gating, f = G.classify({}, verdicts, {"derived": "PLAN_CREATE", "tickets": {}})
        assert gating == "BLOCKED-CLOSED" and f == []

    def test_review_reject_is_in_rework(self):
        verdicts = [{"id": "c", "met": False}]
        gating, _ = G.classify({}, verdicts, entry(derived="REVIEW_REJECT"))
        assert gating == "IN-REWORK"

    def test_ready_no_ticket_unknown(self):
        verdicts = [{"id": "c", "met": True}]
        gating, f = G.classify({}, verdicts, {"derived": "PLAN_CREATE", "tickets": {}})
        assert gating == "UNKNOWN" and "state quirk" in f[0]


class TestEvaluate:
    def test_plan_status_in_met(self):
        conds = [{"id": "dep", "evaluator": "plan_status_in",
                  "params": {"plan": "8261654", "accept": ["completed"]}}]
        state = {"8261654": {"derived": "completed", "tickets": {}}}
        verdicts, err = G.evaluate_conditions(conds, state)
        assert verdicts[0]["met"] is True and verdicts[0]["observed"] == "completed"
        assert err is False

    def test_plan_status_in_unmet(self):
        conds = [{"id": "dep", "evaluator": "plan_status_in",
                  "params": {"plan": "8261654", "accept": ["completed"]}}]
        state = {"8261654": {"derived": "REVIEW_REJECT", "tickets": {}}}
        verdicts, err = G.evaluate_conditions(conds, state)
        assert verdicts[0]["met"] is False
        assert err is False

    def test_unknown_evaluator_is_tool_error(self):
        conds = [{"id": "x", "evaluator": "moon_phase", "params": {}}]
        verdicts, err = G.evaluate_conditions(conds, {})
        assert verdicts[0]["met"] is None and err is True

    def test_db_evaluator_via_faked_psql(self, monkeypatch):
        monkeypatch.setattr(G, "_psql_scalar", lambda sql: "t")
        conds = [{"id": "st01", "evaluator": "db_table_exists",
                  "params": {"table": "resolution.promotion_batch"}}]
        verdicts, err = G.evaluate_conditions(conds, {})
        assert verdicts[0]["met"] is True and err is False

    def test_db_evaluator_failure_is_tool_error(self, monkeypatch):
        def boom(sql):
            raise RuntimeError("psql failed")
        monkeypatch.setattr(G, "_psql_scalar", boom)
        conds = [{"id": "st01", "evaluator": "db_table_exists",
                  "params": {"table": "x.y"}}]
        verdicts, err = G.evaluate_conditions(conds, {})
        assert verdicts[0]["met"] is None and err is True


def test_live_spec_structural_consistency():
    """The shipped prototype spec must parse and use only known
    evaluators + family roles."""
    spec = json.loads(
        (REPO / "bin" / "plan-gating-specs" / "w-decomposition.json")
        .read_text())
    known_ev = set(G.EVALUATORS) | {"plan_status_in"}
    known_roles = {"duplicate-parent", "child"}
    plans = spec["plans"]
    assert set(plans) == {"8261652", "8261653", "8261654", "8261655"}
    for pid, p in plans.items():
        for c in p.get("start_conditions", []):
            assert c["evaluator"] in known_ev, (pid, c["id"])
            assert "evidence_ref" in c
        fam = p.get("family", {})
        if fam:
            assert fam.get("role") in known_roles
            if fam["role"] == "child":
                assert fam["parent"] in plans
            if fam["role"] == "duplicate-parent":
                assert all(ch in plans for ch in fam["children"])
