"""Hermetic tests for bin/reconcile_plan_provenance.py.

Exercises the pure classification and verification logic — the
five-class provenance taxonomy from the architect's 18:37 analysis
(f9390b52) — with canned signals. No database, no network: the psql
surface is never touched (only classify() and verify() are imported).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "bin" / "reconcile_plan_provenance.py"
SPEC = REPO / "bin" / "provenance-specs" / "fleet-22.json"


@pytest.fixture(scope="module")
def mod():
    spec_ = importlib.util.spec_from_file_location("rpp", TOOL)
    m = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC.read_text())


# ---------------------------------------------------------------- classify

def test_completed_stamp_classifies_as_wrapper(mod):
    cls, reasons = mod.classify("completed", "review_pass", {})
    assert cls == "completed-wrapper"
    assert any("bulk stamp" in r for r in reasons)


def test_archived_with_disposition_is_wrapper(mod):
    cls, reasons = mod.classify(
        "archived", None,
        {"class": "completed-wrapper",
         "archived_disposition": "planner D-2"})
    assert cls == "completed-wrapper"
    assert any("D-2" in r for r in reasons)


def test_archived_without_wrapper_declaration_stays_declared(mod):
    # The live archive of a NON-wrapper plan would fall through to the
    # declared class — precedence keeps the declared class if it is not
    # contradicted.
    cls, _reasons = mod.classify(
        "archived", "plan_create", {"class": "active",
                                    "evidence": "x"})
    assert cls == "active"


def test_declared_wrapper_still_pending_is_wrapper(mod):
    # The definitional case: 8261639-style stale wrapper.
    cls, reasons = mod.classify("pending", "plan_create",
                                {"class": "completed-wrapper",
                                 "evidence": "C7 closeout"})
    assert cls == "completed-wrapper"
    assert any("C7" in r for r in reasons)


def test_active_with_non_terminal_receipt(mod):
    cls, reasons = mod.classify("pending", "plan_create",
                                {"class": "active", "evidence": "W2a"})
    assert cls == "active"
    assert any("non-terminal" in r for r in reasons)


def test_docketed_orphan_unverified_fall_through(mod):
    assert mod.classify("pending", None,
                        {"class": "docketed"})[0] == "docketed"
    assert mod.classify("pending", None, {"class": "orphan"})[0] == "orphan"
    cls, reasons = mod.classify("pending", None, {})
    assert cls == "unverified"
    assert reasons


# ------------------------------------------------------------------ verify

def test_verify_wrapper_pending_ok(mod):
    # Still-pending wrapper: NOT a discrepancy (class definition).
    assert mod.verify("pending", {"class": "completed-wrapper",
                                  "evidence": "C7"},
                      {"latest_receipt": "plan_create",
                       "tickets": {}, "evidence_mass": 30}) == []


def test_verify_wrapper_completed_without_terminal_receipt(mod):
    bad = mod.verify("completed", {"class": "completed-wrapper"},
                     {"latest_receipt": None, "tickets": {},
                      "evidence_mass": 0})
    assert bad == []


def test_verify_wrapper_completed_with_wrong_receipt(mod):
    bad = mod.verify("completed", {"class": "completed-wrapper"},
                     {"latest_receipt": "plan_create", "tickets": {},
                      "evidence_mass": 0})
    assert bad and "terminal" in bad[0]


def test_verify_wrapper_archived_needs_disposition(mod):
    bad = mod.verify("archived", {"class": "completed-wrapper"},
                     {"latest_receipt": None, "tickets": {},
                      "evidence_mass": 0})
    assert bad and "archived_disposition" in bad[0]


def test_verify_active_needs_pending(mod):
    bad = mod.verify("archived", {"class": "active", "evidence": "x"},
                     {"latest_receipt": "plan_create", "tickets": {},
                      "evidence_mass": 0})
    assert bad and "expected pending" in bad[0]


def test_verify_active_rejects_terminal_receipt(mod):
    bad = mod.verify("pending", {"class": "active", "evidence": "x"},
                     {"latest_receipt": "review_pass", "tickets": {},
                      "evidence_mass": 0})
    assert bad and "terminal receipt" in bad[0]


def test_verify_docketed_needs_pending(mod):
    bad = mod.verify("completed", {"class": "docketed"},
                     {"latest_receipt": None, "tickets": {},
                      "evidence_mass": 0})
    assert bad and "expected pending" in bad[0]


def test_verify_orphan_rejects_live_open_ticket(mod):
    ok = mod.verify("pending", {"class": "orphan"},
                    {"latest_receipt": None,
                     "tickets": {"builder": "expired"},
                     "evidence_mass": 0})
    assert ok == []
    bad = mod.verify("pending", {"class": "orphan"},
                     {"latest_receipt": None,
                      "tickets": {"builder": "open"},
                      "evidence_mass": 0})
    assert bad and "live-open" in bad[0]


def test_verify_unverified_flags_high_evidence(mod):
    ok = mod.verify("pending", {},
                    {"latest_receipt": None, "tickets": {},
                     "evidence_mass": 2})
    assert ok == []
    bad = mod.verify("pending", {},
                     {"latest_receipt": None, "tickets": {},
                      "evidence_mass": 5})
    assert bad and "re-classify" in bad[0]


# -------------------------------------------------------------- spec shape

def test_spec_declares_all_five_classes(spec):
    declared = {p["class"] for p in spec["plans"].values()}
    assert declared == set(mod.__dict__["CLASSES"]) if False else (
        declared == {"completed-wrapper", "active", "docketed", "orphan",
                     "unverified"})


def test_spec_plans_are_the_22_population(spec):
    assert len(spec["plans"]) == 22
    # Every plan the architect's analysis names by number is declared.
    for pid in ("8261638", "8261639", "8261645", "8261650", "8261654",
                "8261655"):
        assert pid in spec["plans"]


def test_archived_plan_has_disposition(spec):
    # 8261652 is archived live; the verify leg demands a disposition.
    assert spec["plans"]["8261652"].get("archived_disposition")


def test_bulk_stamped_are_all_wrappers(spec):
    bulk = ("8261640", "8261641", "8261642", "8261643", "8261644",
            "0007", "0013", "0014", "0016")
    for pid in bulk:
        assert spec["plans"][pid]["class"] == "completed-wrapper"
