"""Hermetic tests: G3 close-code remap (V185 + inspector post 5d45f921).

Pins the mapping contract in conversation_coordinator — no database, no
network. The epistemic rule under test: a governance column must not carry
a false specific claim. OUTCOME_CLOSED_LEASE (the legacy aggregate for any
lease closure without release granularity) maps to the explicit
no-granularity code 'unknown' (V185 vocabulary), not 'lease_expired'.

Run:
    cd /home/codex/dev/nexus/python/cascade
    python3 -m pytest test_conformance_close_code_g3.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import conversation_coordinator as cc  # noqa: E402


class G3CloseCodeTests(unittest.TestCase):
    def test_legacy_lease_aggregate_maps_to_unknown(self):
        """The G3 defect: CLOSED_LEASE asserted lease_expired specificity."""
        self.assertEqual(
            cc.close_code_for_outcome(cc.OUTCOME_CLOSED_LEASE),
            cc.CLOSE_CODE_UNKNOWN,
        )

    def test_unknown_is_in_the_module_vocabulary(self):
        self.assertEqual(cc.CLOSE_CODE_UNKNOWN, "unknown")

    def test_unknown_is_a_valid_v185_member(self):
        # V185 CHECK vocabulary: the eight legal values.
        v185_vocab = {
            "lease_revoked", "lease_exhausted", "lease_expired",
            "turns", "agent", "idle", "natural", "unknown",
        }
        self.assertIn(cc.CLOSE_CODE_UNKNOWN, v185_vocab)

    def test_granular_lease_codes_unchanged(self):
        """Only the no-granularity aggregate changed; specific codes stay."""
        self.assertEqual(
            cc.close_code_for_outcome(cc.OUTCOME_CLOSED_LEASE_REVOKED),
            "lease_revoked")
        self.assertEqual(
            cc.close_code_for_outcome(cc.OUTCOME_CLOSED_LEASE_EXHAUSTED),
            "lease_exhausted")
        self.assertEqual(
            cc.close_code_for_outcome(cc.OUTCOME_CLOSED_LEASE_EXPIRED),
            "lease_expired")

    def test_closed_aggregate_still_natural(self):
        self.assertEqual(
            cc.close_code_for_outcome(cc.OUTCOME_CLOSED), "natural")

    def test_non_lease_codes_unchanged(self):
        for outcome, code in (
            (cc.OUTCOME_CLOSED_TURNS, "turns"),
            (cc.OUTCOME_CLOSED_AGENT, "agent"),
            (cc.OUTCOME_CLOSED_IDLE, "idle"),
            (cc.OUTCOME_CLOSED_NATURAL, "natural"),
        ):
            self.assertEqual(cc.close_code_for_outcome(outcome), code)

    def test_unknown_outcome_fallback_still_natural(self):
        self.assertEqual(cc.close_code_for_outcome("SOMETHING_NEW"),
                         "natural")

    def test_lease_aggregate_still_terminal(self):
        """The remap must not change closure semantics — only the code."""
        self.assertTrue(cc.is_terminal(cc.OUTCOME_CLOSED_LEASE))


if __name__ == "__main__":
    unittest.main()
