"""Tests for the faithful per-case state builder (Jev 6, item 1).

Acceptance criteria:
- Every corpus case has an entry covering all required_state_fields
- Spot values match the case's own reference rationale (not 'present')
- Unknown ids and incomplete entries fail loudly
"""
from __future__ import annotations

import pytest

from solscript.benchmarks.jev_corpus import get_corpus
from solscript.benchmarks.jev_corpus.faithful_state import (
    FAITHFUL_STATES,
    assert_full_coverage,
    faithful_state,
)


class TestFaithfulCoverage:
    def test_every_case_has_complete_entry(self):
        assert_full_coverage()

    def test_entry_count_matches_corpus(self):
        assert set(FAITHFUL_STATES) == {c.id for c in get_corpus()}

    def test_no_present_placeholders(self):
        """No required field may carry the old synthetic 'present' string."""
        for case in get_corpus():
            state = faithful_state(case)
            for field in case.evidence_scope.required_state_fields:
                assert state[field] != "present", (
                    f"{case.id}.{field} still uses the 'present' placeholder"
                )


class TestFaithfulValues:
    """Spot-checks: values must match each case's reference rationale."""

    def test_noul_001_readiness_score(self):
        assert faithful_state("noul_001")["readiness_score"] == 0.92

    def test_noul_002_expired_license(self):
        state = faithful_state("noul_002")
        assert state["valid_license"] is False
        assert state["license_expiry"] == "2024-01-15"

    def test_noul_005_identity_match(self):
        assert faithful_state("noul_005")["identity_match"] == 0.72

    def test_noul_007_evidence_counts(self):
        state = faithful_state("noul_007")
        assert state["evidence_items"] == 7
        assert state["required_evidence"] == 12

    def test_score_005_clean_slate(self):
        state = faithful_state("score_005")
        assert state["policy_violations"] == 0
        assert state["guard_compliance"] == 0.95

    def test_accepts_case_object(self):
        for case in get_corpus()[:3]:
            assert faithful_state(case) == faithful_state(case.id)


class TestFaithfulFailures:
    def test_unknown_id_raises_key_error(self):
        with pytest.raises(KeyError):
            faithful_state("noul_999")

    def test_missing_entry_raises_key_error(self, monkeypatch):
        import solscript.benchmarks.jev_corpus.faithful_state as mod

        monkeypatch.delitem(mod.FAITHFUL_STATES, "noul_001")
        with pytest.raises(KeyError):
            faithful_state("noul_001")
