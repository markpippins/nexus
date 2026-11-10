"""Guard: the receipt checks read the live receipt surface, not a retired one.

`vision.receipts` stopped receiving rows at the 2026-09-13 write-boundary
cutover (newest row 2026-09-13 21:19Z, zero rows in the following 30 days),
while every plan touched in that window has receipts only in
`nebula.receipts_unified`. Because checks 1 (blocked plans) and 2
(stuck-pending drift) take "the latest receipt per plan", pointing them at the
retired table yields an empty latest set for every newer plan: the sweep
printed "None — no plan has been stuck/blocked" while conduit (:3100)
correctly reported plan 8261650 as BLOCKED on a builder receipt that existed
only in the projection.

This guard pins the surface so a revert to `vision.receipts` fails here
instead of silently turning the blocker detector into a no-op.
"""
import importlib.util
import pathlib

import pytest

SWEEP = pathlib.Path(__file__).resolve().parent.parent / "pipeline-health-sweep.py"


def _load_sweep():
    """Import the sweep as a module without running main()."""
    spec = importlib.util.spec_from_file_location("pipeline_health_sweep", SWEEP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sweep():
    return _load_sweep()


def test_reads_the_unified_receipt_projection(sweep):
    assert sweep.RECEIPT_SURFACE == "nebula.receipts_unified"


def test_blocked_check_uses_the_receipt_surface(sweep):
    assert "{RECEIPT_SURFACE}" not in sweep.QUERY_BLOCKED, "f-string was not interpolated"
    assert sweep.RECEIPT_SURFACE in sweep.QUERY_BLOCKED
    assert "BLOCK" in sweep.QUERY_BLOCKED and "HOLD" in sweep.QUERY_BLOCKED


def test_drift_check_uses_the_receipt_surface_everywhere(sweep):
    assert "{RECEIPT_SURFACE}" not in sweep.QUERY_DRIFT, "f-string was not interpolated"
    # two consumers: the per-plan latest receipt, and completion evidence
    assert sweep.QUERY_DRIFT.count(sweep.RECEIPT_SURFACE) == 2
    assert "PLAN_CREATE" in sweep.QUERY_DRIFT
    assert "IMPLEMENTATION" in sweep.QUERY_DRIFT and "REVIEW_PASS" in sweep.QUERY_DRIFT


def test_drift_check_excludes_plans_already_closed(sweep):
    # A closed plan row cannot be "stuck pending"; its chain merely stops at
    # PLAN_CREATE. Guard the exclusion so a rewrite does not resurrect the six
    # archived plans that the unified surface exposed.
    assert "nebula.implementation_plans" in sweep.QUERY_DRIFT
    for terminal in ("completed", "archived", "closed"):
        assert terminal in sweep.QUERY_DRIFT


def test_report_renders_blocked_and_flagged_together(sweep):
    """Regression: a drift FLAG used to shadow the flagged-changes list.

    build_report rebound `flagged` to drift 5-tuples, then iterated it as the
    4-tuple flagged-changes result and raised "too many values to unpack" —
    but only once a plan was actually blocked/flagged, which is precisely when
    the report matters.
    """
    findings = {
        "blocked": [("8261650", "BLOCK", "2026-11-10")],
        "drift": [("8261651", "2026-09-21", 1, 0, 47, 0, True)],
        "flagged": [("inspection", "dba", "drift check", "2026-11-10 06:50")],
        "projection": [],
        "wr_assets": [],
        "wr_entity_key": [],
        "role_leases": [],
    }
    report = sweep.build_report(findings, "inspector", "test")
    assert "8261650" in report and "BLOCK" in report
    assert "**1 flagged**" in report
    assert "drift check" in report  # the flagged-changes table still renders


def test_no_query_reads_the_retired_receipt_table(sweep):
    for name in ("QUERY_BLOCKED", "QUERY_DRIFT"):
        query = getattr(sweep, name)
        assert "vision.receipts" not in query, (
            f"{name} reads vision.receipts, which has taken no rows since "
            "2026-09-13 — the check would report no findings for every newer plan"
        )
