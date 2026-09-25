"""Fleet spec shape guard: bin/plan-gating-specs/fleet-pending.json must stay
runnable by check_plan_gating.py. Hermetic — validates the data file only.

Guards added with the fleet census (2026-09-24):
  - every plan declares an expected_gating class the classifier emits
  - every evaluator name is one the checker knows (or plan_status_in)
  - every plan_status_in reference points at a plan present in the spec
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # worktree root
SPEC = REPO / "bin" / "plan-gating-specs" / "fleet-pending.json"
CHECKER = REPO / "bin" / "check_plan_gating.py"

KNOWN_GATING = {
    "STARTABLE", "BLOCKED-AND-OPEN", "BLOCKED-BUT-OPEN",
    "BLOCKED-WITH-EXPIRED", "BLOCKED-CLOSED", "IN-REWORK", "UNKNOWN",
}
KNOWN_EVALUATORS = {
    "db_table_exists", "record_exists", "plan_status_in", "plan_has_record",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC.read_text())


def test_spec_exists_and_parses(spec: dict) -> None:
    assert spec.get("version", "").startswith("fleet-census")
    assert isinstance(spec.get("plans"), dict) and spec["plans"]


def test_expected_gating_is_a_known_class(spec: dict) -> None:
    for pid, plan in spec["plans"].items():
        cls = plan.get("expected_gating", "").split(" ")[0].strip().upper()
        assert cls in KNOWN_GATING, f"{pid}: unknown class {cls!r}"


def test_evaluators_are_known(spec: dict) -> None:
    for pid, plan in spec["plans"].items():
        for cond in plan.get("start_conditions", []):
            assert cond.get("evaluator") in KNOWN_EVALUATORS, (
                f"{pid}/{cond.get('id')}: unknown evaluator")


def test_plan_status_in_refs_resolve_in_spec(spec: dict) -> None:
    plans = spec["plans"]
    for pid, plan in plans.items():
        for cond in plan.get("start_conditions", []):
            if cond.get("evaluator") == "plan_status_in":
                ref = cond["params"]["plan"]
                assert ref in plans, (
                    f"{pid} references {ref}, which is not in the spec")


def test_checker_imports_and_classifies() -> None:
    """The classifier module must load and produce a known class for a
    minimal in-memory plan (no DB, no network — tickets/derived empty)."""
    import importlib.util

    spec_ = importlib.util.spec_from_file_location("cpg", CHECKER)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)

    # The W-B4 exhibit class now requires a LIVE open ticket: open ticket
    # + unmet condition -> BLOCKED-AND-OPEN
    gating, findings = mod.classify(
        {}, [{"id": "c1", "met": False}],
        {"derived": None,
         "tickets": {"builder": {"status": "open"}}})
    assert gating == "BLOCKED-AND-OPEN"
    assert findings

    # Post-CD-2: expired-only ticket on a blocked plan is disposition
    # hygiene (BLOCKED-WITH-EXPIRED), not live forbidden work
    gating, findings = mod.classify(
        {}, [{"id": "c1", "met": False}],
        {"derived": None,
         "tickets": {"builder": {"status": "expired"}}})
    assert gating == "BLOCKED-WITH-EXPIRED"
    assert findings

    # Conditions met but only an expired ticket -> flow defect class
    gating, findings = mod.classify(
        {}, [{"id": "c1", "met": True}],
        {"derived": None,
         "tickets": {"builder": {"status": "expired"}}})
    assert gating == "BLOCKED-BUT-OPEN"
    assert findings

    # BLOCKED-CLOSED = unmet condition AND no ticket surface (gating working)
    gating, _findings = mod.classify(
        {}, [{"id": "c1", "met": False}],
        {"derived": None, "tickets": {}})
    assert gating == "BLOCKED-CLOSED"

    # READY + no tickets at all is the documented 'state quirk' class
    gating, findings = mod.classify(
        {}, [{"id": "c1", "met": True}],
        {"derived": None, "tickets": {}})
    assert gating == "UNKNOWN"
