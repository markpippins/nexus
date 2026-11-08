"""Reviewer attempt admission against the plan-lock contract.

Re-derived from the preserved record 80c6979a. The preserved test carried its
own PostgreSQL fake written against an older `SELECT EXISTS ... FROM tickets`
eligibility query; the adapter now derives eligibility from `plan_status` under
the shared plan advisory lock. The maintained `_AdvisoryLockPool` in
`test_execution_outcome_contract.py` already models that boundary, so this file
reuses it instead of duplicating it, and covers the reviewer role — which the
builder-only lock tests do not exercise.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db_adapter as db_adapter_module  # noqa: E402
from db_adapter import DBAdapter  # noqa: E402
from test_execution_outcome_contract import _AdvisoryLockPool  # noqa: E402


def _adapter(monkeypatch, pool):
    monkeypatch.setattr(db_adapter_module, "_get_pg_pool", lambda: pool)
    adapter = DBAdapter.__new__(DBAdapter)
    adapter._schema = "conduit"
    return adapter


def _open_reviewer_attempt(adapter):
    return adapter.create_attempt(
        "lease-1",
        "request-1",
        "conduit-worker",
        session_id="reviewer-session",
        work_role="reviewer",
    )


def test_reviewer_attempt_admitted_when_plan_is_implementation_eligible(monkeypatch):
    pool = _AdvisoryLockPool(plan_derived_status="IMPLEMENTATION")
    adapter = _adapter(monkeypatch, pool)

    attempt = _open_reviewer_attempt(adapter)

    assert attempt is not None
    assert pool.attempt_inserts == 1
    assert pool.lock_keys == pool.released_lock_keys
    assert pool.checked_out == pool.returned
    assert not pool.plan_lock.locked()


def test_reviewer_attempt_refused_when_plan_is_only_builder_eligible(monkeypatch):
    pool = _AdvisoryLockPool(plan_derived_status="PLAN_CREATE")
    adapter = _adapter(monkeypatch, pool)

    assert _open_reviewer_attempt(adapter) is None
    assert pool.attempt_inserts == 0
    assert pool.lock_keys == pool.released_lock_keys
    assert not pool.plan_lock.locked()


def test_reviewer_attempt_refused_after_review_pass_is_terminal(monkeypatch):
    pool = _AdvisoryLockPool(
        latest_receipt="REVIEW_PASS", plan_derived_status="IMPLEMENTATION"
    )
    adapter = _adapter(monkeypatch, pool)

    assert _open_reviewer_attempt(adapter) is None
    assert pool.attempt_inserts == 0
    assert pool.lock_keys == pool.released_lock_keys
    assert not pool.plan_lock.locked()
