"""Hermetic tests for todo_lifecycle_sweep.py — pure functions only, no I/O."""

import importlib.util
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "todo_lifecycle_sweep.py"
spec = importlib.util.spec_from_file_location("tls", BIN)
tls = importlib.util.module_from_spec(spec)
sys.modules["tls"] = tls
spec.loader.exec_module(tls)

ROLES = {"dba", "analyst", "architect", "engineer", "operator", "planner"}


def feats(**kw):
    base = {"rating": 0, "age_days": 5, "n_comments": 0, "addr_engaged": False,
            "has_completed": False, "has_superseded": False, "token": None}
    base.update(kw)
    return base


# ── normalize_token ──────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("[DBA] Backfill the registry", "dba"),                      # case-insensitive
    ("[engineer/design] Split the port", "engineer"),            # slash compound -> first segment (V192)
    ("[planner] Long-range docket", "planner"),
    ("no token at all", None),
    ("[] empty token", None),
    ("[barbie-parity] Deploy logs viewer", "barbie-parity"),     # hyphenated role-like token
    ("[ analyst ] padded", "analyst"),                           # whitespace tolerated
])
def test_normalize_token(title, expected):
    assert tls.normalize_token(title) == expected


# ── classify: the policy decision tree ──────────────────────────────────

def test_unrouted_dominates_when_no_token():
    assert tls.classify(feats(), ROLES) == "unrouted"


def test_orphan_token_dominates_everything():
    # Even old+completed-looking threads with orphan tokens stay orphan.
    f = feats(token="barbie-parity", rating=2, has_completed=True, age_days=90, n_comments=3)
    assert tls.classify(f, ROLES) == "STALE-ORPHANED"


def test_superseded_before_completed():
    f = feats(token="dba", has_superseded=True, has_completed=True, rating=1, age_days=60)
    assert tls.classify(f, ROLES) == "STALE-SUPERSEDED?"


def test_complete_unmarked_window():
    f = feats(token="dba", has_completed=True, rating=3, age_days=8)
    assert tls.classify(f, ROLES) == "COMPLETE-UNMARKED?"
    assert tls.classify({**f, "age_days": 7}, ROLES) != "COMPLETE-UNMARKED?"  # boundary: 7d not >7d
    assert tls.classify({**f, "rating": 4}, ROLES) != "COMPLETE-UNMARKED?"   # already marked


def test_unacked_escalation_buckets():
    f = feats(token="dba", rating=0)
    assert tls.classify({**f, "age_days": 15}, ROLES) == "STALE-UNACKED 14-30d"
    assert tls.classify({**f, "age_days": 31}, ROLES) == "STALE-UNACKED>30d"
    assert tls.classify({**f, "age_days": 10}, ROLES) == "awaiting-pickup"


def test_addressee_engagement_blocks_stale_unacked():
    f = feats(token="dba", rating=0, addr_engaged=True, age_days=90)
    assert tls.classify(f, ROLES) == "addr-engaged-unrated"


def test_rating_states():
    assert tls.classify(feats(token="dba", rating=2, age_days=1), ROLES) == "in-flight"
    assert tls.classify(feats(token="dba", rating=3), ROLES) == "in-flight"
    assert tls.classify(feats(token="dba", rating=8), ROLES) == "in-flight"
    assert tls.classify(feats(token="dba", rating=6), ROLES) == "reopened"
    assert tls.classify(feats(token="dba", rating=4), ROLES) == "terminal"
    assert tls.classify(feats(token="dba", rating=7), ROLES) == "terminal"


# ── eligible_for_auto_close: the unattended-action gates ────────────────

def test_gates_pass_only_for_pure_orphans():
    ok, _ = tls.eligible_for_auto_close(
        feats(token="barbie-parity", rating=0, age_days=55, n_comments=0), "STALE-ORPHANED")
    assert ok is True


@pytest.mark.parametrize("mutate,bucket", [
    (lambda f: f.update(rating=1), "STALE-ORPHANED"),      # pickup happened
    (lambda f: f.update(age_days=29), "STALE-ORPHANED"),   # too young (boundary 30d excluded)
    (lambda f: f.update(n_comments=2), "STALE-ORPHANED"),  # engagement exists
    (lambda f: f.update(has_completed=True), "STALE-ORPHANED"),
    (lambda f: f.update(token="dba"), "STALE-UNACKED>30d"),  # wrong bucket entirely
])
def test_gates_block(mutate, bucket):
    f = feats(token="barbie-parity", rating=0, age_days=55, n_comments=0)
    mutate(f)
    ok, why = tls.eligible_for_auto_close(f, bucket)
    assert ok is False and why


# ── selection + census + delta ──────────────────────────────────────────

def test_select_oldest_first_capped():
    rows = [
        {"id": "a", **feats(token="x-one", rating=0, age_days=40)},
        {"id": "b", **feats(token="x-two", rating=0, age_days=90)},
        {"id": "c", **feats(token="x-three", rating=0, age_days=60)},
        {"id": "d", **feats(token="x-four", rating=0, age_days=31, n_comments=1)},  # gated out
        {"id": "e", **feats(token="dba", rating=0, age_days=100)},                  # routed, not orphan
    ]
    for r in rows:
        r["bucket"] = tls.classify(r, ROLES)
    picked = tls.select_auto_closes(rows, ROLES, cap=2)
    assert [r["id"] for r in picked] == ["b", "c"]  # oldest first, capped


def test_census_counts():
    rows = [{"bucket": "unrouted"}, {"bucket": "unrouted"}, {"bucket": "in-flight"}]
    assert tls.census(rows) == {"in-flight": 1, "unrouted": 2}


def test_delta_first_run_and_movement():
    assert "baseline" in tls.render_delta(None, {"unrouted": 5})
    cur = {"unrouted": 3, "STALE-ORPHANED": 2}
    prev = {"unrouted": 5}
    d = tls.render_delta(prev, cur)
    assert "unrouted 5→3 (-2)" in d and "STALE-ORPHANED 0→2 (+2)" in d


def test_close_body_cites_evidence_and_reopen_path():
    body = tls.close_body("abc123", {"raw_token": "barbie-parity", "age_days": 55})
    assert "abc123" in body and "Retarget" in body and "I1/I2" in body
