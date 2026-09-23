"""Hermetic tests for token_retrofit_campaign.py — pure mapping logic only."""

import importlib.util
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "token_retrofit_campaign.py"
spec = importlib.util.spec_from_file_location("trc", BIN)
trc = importlib.util.module_from_spec(spec)
sys.modules["trc"] = trc
spec.loader.exec_module(trc)

ROLES = {"dba", "analyst", "architect", "engineer", "operator", "planner",
         "devops", "inspector", "design-synthesist", "lead-engineer"}


def test_normalize_first_segment_and_case():
    assert trc.normalize_token("[DBA] Backfill") == "dba"
    assert trc.normalize_token("[engineer/design] Split") == "engineer"
    assert trc.normalize_token("[peb] x") == "peb"
    assert trc.normalize_token("no token") is None
    assert trc.normalize_token("[barbie-parity] Deploy") == "barbie-parity"


def test_classify_routed_alias_unknown_unrouted():
    assert trc.classify_token("dba", ROLES) == "routed"
    assert trc.classify_token("peb", ROLES) == "alias"
    assert trc.classify_token("barbie-parity", ROLES) == "close-or-keep"
    assert trc.classify_token("github", ROLES) == "unknown-token"
    assert trc.classify_token(None, ROLES) == "unrouted"


def test_alias_plan_strips_old_token_and_prefixes_role():
    p = trc.plan_for("[peb] Fix kernel startup", "peb", "alias", ROLES)
    assert p == {"kind": "alias", "new_title": "[engineer] Fix kernel startup"}


def test_compact_plan_first_segment_of_hyphenated_unknown():
    p = trc.plan_for("[peb-forward] Wire the claim", "peb-forward", "unknown-token", ROLES)
    assert p is None  # 'peb' is not itself a ratified role -> no auto plan
    p2 = trc.plan_for("[design-x] UI", "design-x", "unknown-token", ROLES)
    assert p2 is None
    p3 = trc.plan_for("[dba-nonsense] thing", "dba-nonsense", "unknown-token", ROLES)
    assert p3 == {"kind": "compact", "new_title": "[dba] thing"}


def test_unrouted_gets_no_automatic_plan():
    assert trc.plan_for("Do the thing", None, "unrouted", ROLES) is None


def test_routed_gets_no_plan():
    assert trc.plan_for("[dba] Already routed", "dba", "routed", ROLES) is None
