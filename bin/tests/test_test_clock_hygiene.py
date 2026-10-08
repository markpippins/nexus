"""Guard tests for bin/check_test_clock_hygiene.py.

These are HERMETIC: every fixture is an embedded source string, so the tests do
not depend on the current state of any file in the repo and cannot go stale
when someone fixes a fixture.

The load-bearing cases are the two directions:

  * the guard FIRES on the exact rot that reddened #715 on 2026-10-03
    (``test_resolver_soak_report.py``, frozen NOW + live REAL_NOW), and
  * the guard STAYS SILENT on the legitimate frozen-date patterns this repo
    already uses correctly.

A guard that cannot do the second is worse than no guard: it trains the team
to add exclusions. See the checker docstring for why "no hardcoded dates" is
the wrong rule.

The repo-wide enforcement scan is a SEPARATE guard
(``test_test_clock_hygiene_repo.py``) so that it can be manifest-excluded
without taking these hermetic unit tests down with it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location(
    "check_test_clock_hygiene", REPO / "bin" / "check_test_clock_hygiene.py")
chk = importlib.util.module_from_spec(spec)
sys.modules["check_test_clock_hygiene"] = chk
spec.loader.exec_module(chk)


# ---------------------------------------------------------------------------
# The real rot, verbatim shape from test_resolver_soak_report.py @ main.
# ---------------------------------------------------------------------------
ROTTEN = '''
import datetime
from datetime import datetime, timezone, timedelta

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0 = "2026-09-18T09:13:44-0400"
REAL_NOW = datetime.now(timezone.utc)
OLD = (REAL_NOW - timedelta(days=15)).astimezone().isoformat()
RECENT = (REAL_NOW - timedelta(days=1)).astimezone().isoformat()

def test_it():
    r = analyze([OLD], now=NOW)
    assert r["criteria"]["a_window_ge_14d"]["status"] == "PASS"
'''

FIXED = '''
import datetime
from datetime import datetime, timezone, timedelta

REF = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
NOW = REF
T0 = "2026-09-18T09:13:44-0400"
_EDT = timezone(timedelta(hours=-4))
OLD = (REF - timedelta(days=15)).astimezone(_EDT).isoformat()
RECENT = (REF - timedelta(days=1)).astimezone(_EDT).isoformat()

def test_it():
    r = analyze([OLD], now=NOW)
    assert r["criteria"]["a_window_ge_14d"]["status"] == "PASS"
'''

# Frozen "today" passed explicitly at the call site -- deterministic, correct.
# This is the real test_contract_casing_exemptions.py shape.
LEGIT_FROZEN_CALLSITE = '''
import datetime
from datetime import datetime, timezone

FIELDS = {
    "past_field": [{"kind": "computed", "target": "2026-01-01"}],
    "future_field": [{"kind": "computed", "target": "2099-01-01"}],
}

def test_expired_is_a_violation():
    expired = expired_computed(FIELDS, "2026-09-29")
    assert set(expired) == {"past_field"}

def test_shipped_registry_not_expired():
    today = datetime.now(timezone.utc).date().isoformat()
    assert expired_computed(FIELDS, today) == {}
'''

# Fixtures derived from the live clock on both sides -- the v174 _days_ago shape.
LEGIT_LIVE_BOTH_SIDES = '''
import datetime
from datetime import datetime, timezone, timedelta

def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")

FRESH = _days_ago(1)
STALE = _days_ago(30)

def test_fresh_is_fresh():
    assert FRESH > STALE
'''

# Frozen literals but NO live clock read anywhere: fully deterministic.
LEGIT_FROZEN_ONLY = '''
from datetime import datetime, timezone

T0 = "2026-09-18T09:13:44-0400"
CUTOFF = datetime(2026, 1, 1, tzinfo=timezone.utc)

def test_parse():
    p = parse([f"{T0} node[1]: resolver-check"])
    assert p[0]["ts"] is not None
'''

# Frozen parser fixture used only for string formatting, never as an operand.
LEGIT_FROZEN_NOT_OPERAND = '''
from datetime import datetime, timezone

T0 = "2026-09-18T09:13:44-0400"
LIVE = datetime.now(timezone.utc)

def test_row_is_parsed():
    row = parse([f"{T0} node[1]: check outcome=ok"])
    assert row[0]["verdict"] == "ok"
'''


class TestDetectsRot:
    def test_fires_on_the_real_rot(self):
        f = chk.scan_source(ROTTEN)
        assert len(f) == 1, f
        assert f[0].rule == "frozen-baseline-with-live-clock"
        assert f[0].baseline == "NOW"

    def test_clear_on_the_fixed_version(self):
        assert chk.scan_source(FIXED) == []

    def test_detection_is_load_bearing_not_incidental(self):
        """The one line that separates the two fixtures is the live clock."""
        assert chk.scan_source(ROTTEN) != chk.scan_source(FIXED)
        # Removing the live read from the rotten source clears it.
        no_live = ROTTEN.replace("REAL_NOW = datetime.now(timezone.utc)",
                                 "REAL_NOW = NOW")
        assert chk.scan_source(no_live) == []


class TestLegitimatePatternsStaySilent:
    """A guard that fires on these is worse than no guard."""

    def test_frozen_today_passed_at_callsite(self):
        assert chk.scan_source(LEGIT_FROZEN_CALLSITE) == []

    def test_live_derived_fixtures_both_sides(self):
        assert chk.scan_source(LEGIT_LIVE_BOTH_SIDES) == []

    def test_frozen_only_no_live_clock(self):
        assert chk.scan_source(LEGIT_FROZEN_ONLY) == []

    def test_frozen_parser_fixture_not_an_operand(self):
        assert chk.scan_source(LEGIT_FROZEN_NOT_OPERAND) == []

    def test_module_without_any_clock_read_never_fires(self):
        assert chk.scan_source("x = 1\n") == []


class TestRobustness:
    def test_unparseable_source_is_not_a_crash(self):
        assert chk.scan_source("def broken(:\n  pass\n") == []

    def test_empty_source(self):
        assert chk.scan_source("") == []

    def test_json_output_shape(self, tmp_path, capsys):
        (tmp_path / "bin" / "tests").mkdir(parents=True)
        (tmp_path / "bin" / "tests" / "test_rot.py").write_text(ROTTEN)
        rc = chk.main(["--repo", str(tmp_path), "--json"])
        assert rc == 1
        import json
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["rule"] == "frozen-baseline-with-live-clock"
        assert payload[0]["path"].endswith("test_rot.py")

    def test_clean_repo_exits_zero(self, tmp_path):
        (tmp_path / "bin" / "tests").mkdir(parents=True)
        (tmp_path / "bin" / "tests" / "test_ok.py").write_text(FIXED)
        assert chk.main(["--repo", str(tmp_path)]) == 0

    def test_missing_repo_root_is_a_tool_error(self, tmp_path):
        assert chk.main(["--repo", str(tmp_path / "nope")]) == 2


class TestRulePins:
    """Pin the pieces of the rule that were calibrated against the corpus.

    Calibrated 2026-10-03: the rule fires on exactly ONE file in the whole
    suite (the real rot) and zero false positives. If a future edit to this
    checker broadens the rule, these pins are what should break first.
    """

    def test_frozen_detects_both_literal_forms(self):
        assert chk._is_frozen_temporal(
            __import__("ast").parse("datetime(2026, 10, 2)", mode="eval").body)
        assert chk._is_frozen_temporal(
            __import__("ast").parse('"2026-10-02"', mode="eval").body)

    def test_frozen_rejects_non_temporal_constants(self):
        import ast
        for src in ('"hello"', "42", "None", "[1, 2]", "datetime.strptime"):
            assert not chk._is_frozen_temporal(ast.parse(src, mode="eval").body)

    def test_live_clock_detection(self):
        import ast
        for src in ("datetime.now()", "date.today()", "datetime.utcnow()",
                    "time.time()"):
            assert chk._is_live_clock(ast.parse(src, mode="eval").body), src

    def test_temporal_keyword_set_covers_the_call_shapes_we_saw(self):
        for kw in ("now", "today", "as_of", "since", "until"):
            assert kw in chk.TEMPORAL_KWARGS