"""Guard for the plan 8261645 rehearsal evidence (B1/B2/D3).

The blueprint claims AC2 is satisfied by an actual bounded rehearsal. These tests
hold that claim to the committed output:

  * the registry declares no unfilled slot and every declared slot names a file
    that exists and states how it was produced;
  * the receipt snapshot's chains obey the *production* transition law, read from
    `typescript/conduit-mcp/src/receipts.ts` — not a law restated in the evidence;
  * the snapshot proves isolation (zero live conduit receipts for rehearsal ids)
    and a real rollback (no tables left, throwaway database removed);
  * the seed-gate output matches the current artifacts' counts (10 relations,
    9 canonical sections, 25 classified columns, 6/6 checks) so evidence cannot
    survive an artifact change unnoticed;
  * nothing in the evidence claims authority, a merge, an attestation or a live
    receipt.
"""

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

import convergence_rehearsal as rehearsal  # noqa: E402

SCHEMAS = ROOT / "schemas" / "convergence"
EVIDENCE = ROOT / "docs" / "convergence" / "evidence"
REGISTRY = SCHEMAS / "work-packages.v1.json"
FREEZE = ROOT / "typespec" / "convergence" / "kg-projection-contract-freeze.v1.json"


def load(path):
    return json.loads(path.read_text())


class TestRegistrySlots(unittest.TestCase):
    def setUp(self):
        self.doc = load(REGISTRY)
        self.slots = {entry["slot"]: entry for entry in self.doc["evidence"]}

    def test_no_slot_is_left_unfilled(self):
        self.assertEqual(self.doc["unfilled_evidence"], [])

    def test_declared_slots_cover_the_three_registry_gaps(self):
        self.assertEqual(
            set(self.slots),
            {"B1.transcript", "B2.receipt_chain_snapshot", "B2.rollback", "D3.seed_gate_run"},
        )

    def test_every_slot_names_a_real_file_and_a_producer(self):
        for slot, entry in self.slots.items():
            with self.subTest(slot=slot):
                path = ROOT / entry["path"]
                self.assertTrue(path.is_file(), path)
                self.assertTrue(entry.get("produced_by"))
                self.assertTrue(entry.get("environment"))
                self.assertTrue(entry.get("authority"))

    def test_registry_still_makes_no_runtime_claim(self):
        self.assertFalse(self.doc["runtime_claim"])
        self.assertFalse(self.doc["mutates_state"])
        self.assertEqual(self.doc["blueprint_state"], "decomposition_complete")


class TestReceiptSnapshot(unittest.TestCase):
    def setUp(self):
        self.text = (EVIDENCE / "b2-receipt-chain-snapshot.txt").read_text()
        self.allowed = rehearsal.load_allowed_transitions()

    def _chains(self):
        _, snapshot = self.text.split("__SNAPSHOT__", 1)
        snapshot = snapshot.split("__TICKETS__", 1)[0]
        chains = {}
        for line in snapshot.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            plan_id, seq, type_, role = line.split("|")
            chains.setdefault(plan_id, []).append((int(seq), type_, role))
        return chains

    def test_chains_obey_the_production_transition_law(self):
        chains = self._chains()
        self.assertEqual(set(chains), {"rehearsal-R1", "rehearsal-R2", "rehearsal-R3"})
        for plan_id, rows in chains.items():
            rows.sort()
            self.assertEqual([seq for seq, _, _ in rows], list(range(1, len(rows) + 1)))
            self.assertEqual(rows[0][1], "PLAN_CREATE")
            for (_, previous, _), (_, current, _) in zip(rows, rows[1:]):
                with self.subTest(plan=plan_id, step=f"{previous}->{current}"):
                    self.assertIn(current, self.allowed.get(previous, []),
                                  f"{previous} -> {current} is not allowed by receipts.ts")

    def test_happy_paths_end_in_review_pass_and_negative_path_requeues(self):
        chains = self._chains()
        for plan_id in ("rehearsal-R1", "rehearsal-R2"):
            self.assertEqual(chains[plan_id][-1][1], "REVIEW_PASS")
        self.assertEqual([t for _, t, _ in chains["rehearsal-R3"]], ["PLAN_CREATE", "BLOCK", "REQUEUED"])

    def test_ticket_lifecycle_includes_completion_and_takeover(self):
        _, tickets = self.text.split("__TICKETS__", 1)
        tickets = tickets.split("__COUNTS__", 1)[0]
        self.assertRegex(tickets, r"ticket-rehearsal-R1-builder\|completed\|open->claimed,claimed->completed")
        self.assertRegex(tickets, r"ticket-rehearsal-R3-builder\|open\|open->claimed,claimed->stale,stale->open")

    def test_isolation_and_rollback_are_proven_not_asserted(self):
        self.assertIn("rehearsal_store_receipts=15", self.text)
        self.assertIn("live_conduit_receipts_for_rehearsal_ids=0", self.text)
        self.assertIn("rehearsal_tables_remaining=0", self.text)
        self.assertIn("rehearsal_db_present=0", self.text)

    def test_rehearsal_never_used_a_live_service_port(self):
        port = int(re.search(r"isolated_port=(\d+)", self.text).group(1))
        self.assertNotIn(port, {3100, 3103})


class TestTranscript(unittest.TestCase):
    def setUp(self):
        self.text = (EVIDENCE / "b1-rehearsal-transcript.md").read_text()

    def test_transcript_covers_every_consumer_and_stage(self):
        for stage in ("plan", "ticket", "receipt"):
            self.assertIn(f"| {stage} |", self.text)
        for role in ("planner", "builder", "critic", "reviewer"):
            self.assertIn(f"| {role} |", self.text)

    def test_transcript_states_the_bound_and_isolation(self):
        self.assertRegex(self.text, r"work requests in the cycle: \d+ \(bound: <= 5\)")
        self.assertIn("throwaway database", self.text)
        self.assertRegex(self.text, r"isolated gateway: `http://127\.0\.0\.1:\d+`")

    def test_transcript_does_not_overclaim(self):
        lowered = self.text.lower()
        self.assertIn("non-authoritative", lowered)
        self.assertIn("no merge", lowered)
        for forbidden in ("merge issued to main", "attestation issued", "live receipt written"):
            self.assertNotIn(forbidden, lowered)


class TestSeedGateOutput(unittest.TestCase):
    def setUp(self):
        self.text = (EVIDENCE / "d3-seed-gate-output.txt").read_text()

    def test_all_six_checks_pass(self):
        self.assertEqual(self.text.count("[PASS]"), 6)
        self.assertNotIn("[FAIL]", self.text)
        self.assertIn("6/6 checks pass", self.text)

    def test_output_matches_the_current_artifacts(self):
        freeze = load(FREEZE)
        relations = freeze["relations"]
        declared = len(relations["frozen_backbone"]) + len(relations["governed_general"])
        sections = len(freeze["sections"]["canonical"])
        columns = len(load(SCHEMAS / "resolution-jsonb-classification.v1.json")["columns"])
        self.assertIn(f"{declared} declared == {declared} canonical", self.text)
        self.assertIn(f"{sections}/{sections} canonical sections", self.text)
        self.assertIn(f"{columns}/{columns} classified columns", self.text)

    def test_seed_gate_states_it_seeded_nothing(self):
        self.assertIn("0 seeded row(s)", self.text)
        self.assertIn("seeding not performed", self.text)


class TestProvenance(unittest.TestCase):
    def test_readme_names_the_regeneration_commands(self):
        text = (EVIDENCE / "README.md").read_text()
        self.assertIn("bin/convergence_rehearsal.py", text)
        self.assertIn("bin/convergence_seed_gate.py", text)
        self.assertIn("none is hand-written", text)


if __name__ == "__main__":
    unittest.main()
